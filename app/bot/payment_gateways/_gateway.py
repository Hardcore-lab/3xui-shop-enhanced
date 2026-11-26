import logging
import time
from abc import ABC, abstractmethod

from aiogram import Bot
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.utils.i18n import I18n
from aiogram.utils.i18n import gettext as _
from aiogram.utils.i18n import lazy_gettext as __
from aiohttp.web import Application
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.models import ServicesContainer, SubscriptionData
from app.bot.routers.main_menu.handler import redirect_to_main_menu
from app.bot.utils.constants import (
    DEFAULT_LANGUAGE,
    EVENT_PAYMENT_CANCELED_TAG,
    EVENT_PAYMENT_SUCCEEDED_TAG,
    Currency,
    TransactionStatus,
)
from app.bot.utils.formatting import format_device_count, format_subscription_period
from app.config import Config
from app.db.models import Transaction, User

logger = logging.getLogger(__name__)


class PaymentGateway(ABC):
    name: str
    currency: Currency
    callback: str

    def __init__(
        self,
        app: Application,
        config: Config,
        session: async_sessionmaker,
        storage: RedisStorage,
        bot: Bot,
        i18n: I18n,
        services: ServicesContainer,
    ) -> None:
        self.app = app
        self.config = config
        self.session = session
        self.storage = storage
        self.bot = bot
        self.i18n = i18n
        self.services = services

    @abstractmethod
    async def create_payment(self, data: SubscriptionData) -> str:
        pass

    @abstractmethod
    async def handle_payment_succeeded(self, payment_id: str) -> None:
        pass

    @abstractmethod
    async def handle_payment_canceled(self, payment_id: str) -> None:
        pass

    async def _on_payment_succeeded(self, payment_id: str) -> None:
        start_time = time.time()
        
        logger.info(f"Payment succeeded {payment_id}")

        async with self.session() as session:
            # Use SELECT FOR UPDATE to prevent race conditions
            transaction = await Transaction.get_by_id_for_update(session=session, payment_id=payment_id)
            if not transaction:
                logger.warning(f"Transaction {payment_id} not found on payment success.")
                return
            if transaction.status == TransactionStatus.COMPLETED:
                logger.info(f"Transaction {payment_id} already completed, skipping.")
                return
            
            # Unpack subscription data while we have the lock
            data = SubscriptionData.unpack(transaction.subscription)
            logger.debug(f"Subscription data unpacked: devices={data.devices}, duration={data.duration}, server_id={getattr(data,'server_id',None)}")
            user = await User.get(session=session, tg_id=data.user_id)
            if not user:
                logger.warning(f"User {data.user_id} not found for payment {payment_id}.")
                return
            
            # Mark as completed immediately while we hold the lock
            # This prevents duplicate processing even if subscription provisioning fails
            transaction.status = TransactionStatus.COMPLETED
            await session.commit()
            logger.info(f"Transaction {payment_id} marked as completed")

        operation_success = True
        await self.services.notification.notify_developer(
            text=EVENT_PAYMENT_SUCCEEDED_TAG
            + "\n\n"
            + _("payment:event:payment_succeeded").format(
                payment_id=payment_id,
                user_id=user.tg_id,
                devices=format_device_count(data.devices),
                duration=format_subscription_period(data.duration),
            ),
        )

        locale = user.language_code if user else DEFAULT_LANGUAGE
        with self.i18n.use_locale(locale):
            await redirect_to_main_menu(
                bot=self.bot,
                user=user,
                services=self.services,
                config=self.config,
                storage=self.storage,
            )

            if data.is_extend:
                if not await self.services.vpn.extend_subscription(
                    user=user,
                    devices=data.devices,
                    duration=data.duration,
                ):
                    logger.error(f"Subscription extend failed for user {user.tg_id}")
                    operation_success = False
                else:
                    logger.info(f"Subscription extended for user {user.tg_id}")
                    await self.services.notification.notify_extend_success(
                        user_id=user.tg_id,
                        data=data,
                    )
            elif data.is_change:
                if not await self.services.vpn.change_subscription(
                    user=user,
                    devices=data.devices,
                    duration=data.duration,
                ):
                    logger.error(f"Subscription change failed for user {user.tg_id}")
                    operation_success = False
                else:
                    logger.info(f"Subscription changed for user {user.tg_id}")
                    await self.services.notification.notify_change_success(
                        user_id=user.tg_id,
                        data=data,
                    )
            else:
                server_id = data.server_id if hasattr(data, 'server_id') and data.server_id else None
                logger.debug(f"Creating subscription for user {user.tg_id}, devices={data.devices}, duration={data.duration}, server_id={server_id}")
                result = await self.services.vpn.create_subscription(
                    user=user,
                    devices=data.devices,
                    duration=data.duration,
                    server_id=server_id,
                )
                if not result:
                    logger.error(f"Subscription creation failed for user {user.tg_id}, skipping key retrieval")
                else:
                    logger.info(f"Subscription created for user {user.tg_id}")

                logger.debug(f"Getting keys for user {user.tg_id}, user.server_id={user.server_id}")
                # Reload user from database to ensure we have the latest data
                from sqlalchemy import select
                async with self.services.vpn.session() as session:
                    user_query = await session.execute(
                        select(User).where(User.tg_id == user.tg_id)
                    )
                    updated_user = user_query.scalar_one_or_none()
                    if updated_user:
                        user.server_id = updated_user.server_id
                        logger.debug(f"Reloaded user.server_id={user.server_id} from DB")
                    else:
                        logger.warning(f"Failed to reload user from DB")
                
                # Get all keys for all clients
                all_keys = await self.services.vpn.get_all_user_keys(user)
                logger.debug(f"Got {len(all_keys) if all_keys else 0} keys for user {user.tg_id}")
                if not result and not all_keys:
                    operation_success = False
                
                if operation_success:
                    logger.debug(f"Notifying purchase success for user {user.tg_id} with {len(all_keys) if all_keys else 0} keys")
                    await self.services.notification.notify_purchase_success(
                        user_id=user.tg_id,
                        all_keys=all_keys if all_keys else [],
                    )
                    logger.info(f"Purchase flow completed successfully for user {user.tg_id}")
        
        if not operation_success:
            logger.error(f"Payment {payment_id} succeeded but subscription provisioning failed for user {user.tg_id}")
            # Transaction already marked as COMPLETED to prevent duplicate attempts
            # Admin should investigate and manually provision if needed
            return

        if self.config.shop.REFERRER_REWARD_ENABLED:
            await self.services.referral.add_referrers_rewards_on_payment(
                referred_tg_id=data.user_id,
                payment_amount=data.price,  # Note: Currency processing handled in referral service
                payment_id=payment_id,
            )

        # Update metrics
        try:
            from app.bot.services.metrics import operation_duration, transactions_total
            transactions_total.labels(status=TransactionStatus.COMPLETED.value).inc()
            duration = time.time() - start_time
            operation_duration.labels(operation="purchase").observe(duration)
        except Exception as metrics_error:
            logger.warning(f"Failed to update purchase metrics: {metrics_error}")

    async def _on_payment_canceled(self, payment_id: str) -> None:
        logger.info(f"Payment canceled {payment_id}")
        
        # Update metrics
        try:
            from app.bot.services.metrics import transactions_total
            from app.bot.utils.constants import TransactionStatus
            transactions_total.labels(status=TransactionStatus.CANCELED.value).inc()
        except Exception as e:
            logger.warning(f"Failed to update payment metrics: {e}")
        async with self.session() as session:
            transaction = await Transaction.get_by_id(session=session, payment_id=payment_id)
            if not transaction:
                logger.warning(f"Transaction {payment_id} not found on payment cancel.")
                return
            if transaction.status == TransactionStatus.CANCELED:
                logger.info(f"Transaction {payment_id} already canceled, skipping.")
                return
            data = SubscriptionData.unpack(transaction.subscription)

            await Transaction.update(
                session=session,
                payment_id=payment_id,
                status=TransactionStatus.CANCELED,
            )

        await self.services.notification.notify_developer(
            text=EVENT_PAYMENT_CANCELED_TAG
            + "\n\n"
            + _("payment:event:payment_canceled").format(
                payment_id=payment_id,
                user_id=data.user_id,
                devices=format_device_count(data.devices),
                duration=format_subscription_period(data.duration),
            ),
        )
