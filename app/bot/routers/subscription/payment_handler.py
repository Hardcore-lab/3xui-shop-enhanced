import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery
from aiogram.utils.i18n import gettext as _
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.bot.filters.is_dev import IsDev
from app.bot.models import ServicesContainer, SubscriptionData
from app.bot.payment_gateways import GatewayFactory
from app.bot.utils.constants import TransactionStatus
from app.bot.utils.formatting import format_subscription_period
from app.bot.utils.navigation import NavSubscription
from app.db.models import Transaction, User

from .keyboard import pay_keyboard

logger = logging.getLogger(__name__)
router = Router(name=__name__)


class PaymentState(StatesGroup):
    processing = State()


@router.callback_query(SubscriptionData.filter(F.state.startswith(NavSubscription.PAY)))
async def callback_payment_method_selected(
    callback: CallbackQuery,
    user: User,
    callback_data: SubscriptionData,
    services: ServicesContainer,
    bot: Bot,
    gateway_factory: GatewayFactory,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if await state.get_state() == PaymentState.processing:
        logger.debug(f"User {user.tg_id} is already processing payment.")
        return

    await state.set_state(PaymentState.processing)

    # Show loading indicator
    try:
        await callback.message.edit_text(_("common:loading:creating_payment"))
    except Exception:
        pass

    try:
        method = callback_data.state
        devices = callback_data.devices
        duration = callback_data.duration
        logger.debug(f"User {user.tg_id} selected payment method: {method}, devices={devices}, duration={duration}")
        logger.info(f"User {user.tg_id} selected payment method: {method}")
        logger.info(f"User {user.tg_id} selected {devices} devices and {duration} days.")
        gateway = gateway_factory.get_gateway(method)
        plan = await services.plan.get_plan(devices, session=session)
        if not plan:
            logger.error(f"Plan not found for devices={devices}")
            await services.notification.show_popup(
                callback=callback,
                text=_("subscription:popup:error_fetching_plan"),
            )
            return
        price = plan.get_price(currency=gateway.currency, duration=duration)
        if price is None:
            logger.error(f"User {user.tg_id}: No price found for devices={devices}, duration={duration}, currency={gateway.currency.code}")
            await services.notification.show_popup(
                callback=callback,
                text=_("subscription:popup:error_fetching_plan"),
            )
            return
        callback_data.price = price
        logger.debug(f"Price calculated: {price} {gateway.currency.code} for user {user.tg_id}")

        logger.debug(f"Creating payment for user {user.tg_id}")
        pay_url = await gateway.create_payment(callback_data)
        logger.debug(f"Payment URL created for user {user.tg_id}")

        if callback_data.is_extend:
            text = _("payment:message:order_extend")
        elif callback_data.is_change:
            text = _("payment:message:order_change")
        else:
            text = _("payment:message:order")

        await callback.message.edit_text(
            text=text.format(
                devices=devices,
                duration=format_subscription_period(duration),
                price=price,
                currency=gateway.currency.symbol,
            ),
            reply_markup=pay_keyboard(pay_url=pay_url, callback_data=callback_data),
        )
    except (ValueError, AttributeError, KeyError) as e:
        logger.error(f"Invalid payment data: {e}", exc_info=True)
        await services.notification.show_popup(callback=callback, text=_("payment:popup:error"))
    except ConnectionError as e:
        logger.error(f"Network error processing payment: {e}", exc_info=True)
        await services.notification.show_popup(callback=callback, text=_("payment:popup:error"))
    except Exception as exception:
        logger.error(f"Unexpected error processing payment: {exception}", exc_info=True)
        await services.notification.show_popup(callback=callback, text=_("payment:popup:error"))
    finally:
        await state.set_state(None)


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery) -> None:
    """
    Handle pre-checkout query.
    
    CRITICAL: Telegram requires response within 10 seconds.
    We MUST answer immediately without waiting for DB operations.
    All validation and DB operations should happen AFTER answering.
    """
    tg_user_id = pre_checkout_query.from_user.id if pre_checkout_query.from_user else None
    logger.debug(f"Pre-checkout query received from user {tg_user_id}, query_id={pre_checkout_query.id}")
    logger.info(f"Pre-checkout query received from user {tg_user_id}, query_id={pre_checkout_query.id}")
    
    # CRITICAL: Validate payload before answering
    # We need to respond within 10 seconds, but payload validation is fast (< 100ms)
    has_payload = bool(pre_checkout_query.invoice_payload)
    payload_valid = False
    error_message = None
    
    if has_payload:
        try:
            # Try to unpack payload to validate structure
            from app.bot.models import SubscriptionData
            data = SubscriptionData.unpack(pre_checkout_query.invoice_payload)
            # Basic validation: check required fields
            if data.devices and data.duration and data.user_id:
                payload_valid = True
            else:
                error_message = _("payment:error:invalid_subscription_data")
                logger.warning(f"Pre-checkout payload validation failed for user {tg_user_id}: missing required fields")
        except (ValueError, AttributeError, KeyError) as e:
            error_message = _("payment:error:invalid_data_format")
            logger.warning(f"Pre-checkout payload unpacking failed for user {tg_user_id}: {e}")
        except Exception as e:
            error_message = _("payment:error:validation_failed")
            logger.error(f"Pre-checkout unexpected error for user {tg_user_id}: {e}", exc_info=True)
    else:
        error_message = _("payment:error:missing_payment_data")
        logger.warning(f"Pre-checkout query rejected for user {tg_user_id}: no invoice payload")
    
    # Answer immediately - this is critical for avoiding timeout
    await pre_checkout_query.answer(ok=payload_valid, error_message=error_message if not payload_valid else None)
    
    # Now we can log additional information
    if payload_valid:
        logger.info(f"Pre-checkout query approved for user {tg_user_id}, query_id={pre_checkout_query.id}")
    else:
        logger.warning(f"Pre-checkout query rejected for user {tg_user_id}: {error_message}")


@router.message(F.successful_payment)
async def successful_payment(
    message: Message,
    user: User,
    session: AsyncSession,
    bot: Bot,
    gateway_factory: GatewayFactory,
) -> None:
    logger.info(f"Successful payment received from user {user.tg_id}")
    if await IsDev()(user_id=user.tg_id):
        await bot.refund_star_payment(
            user_id=user.tg_id,
            telegram_payment_charge_id=message.successful_payment.telegram_payment_charge_id,
        )

    # Safely unpack subscription data with error handling
    try:
        data = SubscriptionData.unpack(message.successful_payment.invoice_payload)
        logger.debug(f"Payment data unpacked: devices={data.devices}, duration={data.duration} for user {user.tg_id}")
    except (ValueError, AttributeError, KeyError) as e:
        logger.error(
            f"[PURCHASE FLOW] CRITICAL: Failed to unpack payment payload for user {user.tg_id}, "
            f"payment_id={message.successful_payment.telegram_payment_charge_id}: {e}",
            exc_info=True
        )
        # Notify user about the issue
        await message.answer(
            _("payment:error:processing_failed").format(payment_id=message.successful_payment.telegram_payment_charge_id)
        )
        return
    except Exception as e:
        logger.error(
            f"[PURCHASE FLOW] CRITICAL: Unexpected error unpacking payload for user {user.tg_id}, "
            f"payment_id={message.successful_payment.telegram_payment_charge_id}: {e}",
            exc_info=True
        )
        await message.answer(
            _("payment:error:critical_error").format(payment_id=message.successful_payment.telegram_payment_charge_id)
        )
        return
    
    logger.debug(f"Creating transaction for user {user.tg_id}")
    try:
        transaction = await Transaction.create(
            session=session,
            tg_id=user.tg_id,
            subscription=data.pack(),
            payment_id=message.successful_payment.telegram_payment_charge_id,
            status=TransactionStatus.PENDING,
        )
        if not transaction:
            logger.warning(
                f"Transaction already exists for payment_id={message.successful_payment.telegram_payment_charge_id}"
            )
            transaction = await Transaction.get_by_id(
                session=session,
                payment_id=message.successful_payment.telegram_payment_charge_id,
            )
            if not transaction:
                logger.error(
                    "Failed to load existing transaction after duplicate create attempt."
                )
                return
        logger.debug(f"Transaction ready: {transaction.payment_id} for user {user.tg_id}")
    except IntegrityError as e:
        logger.error(f"[PURCHASE FLOW] STEP 7.2.2: TRANSACTION_CREATE_ERROR - Database integrity error: {e}", exc_info=True)
        await session.rollback()
        return
    except SQLAlchemyError as e:
        logger.error(f"[PURCHASE FLOW] STEP 7.2.2: TRANSACTION_CREATE_ERROR - Database error: {e}", exc_info=True)
        await session.rollback()
        return
    except Exception as e:
        logger.error(f"[PURCHASE FLOW] STEP 7.2.2: TRANSACTION_CREATE_ERROR - Unexpected error creating transaction: {e}", exc_info=True)
        return

    logger.debug(f"Getting gateway for PAY_TELEGRAM_STARS")
    try:
        gateway = gateway_factory.get_gateway(NavSubscription.PAY_TELEGRAM_STARS)
        logger.debug(f"Gateway obtained: {type(gateway).__name__}")
    except (ValueError, KeyError) as e:
        logger.error(f"[PURCHASE FLOW] STEP 7.2.4: GATEWAY_ERROR - Invalid gateway configuration: {e}", exc_info=True)
        return
    except Exception as e:
        logger.error(f"[PURCHASE FLOW] STEP 7.2.4: GATEWAY_ERROR - Unexpected error getting gateway: {e}", exc_info=True)
        return

    if transaction.status == TransactionStatus.COMPLETED:
        logger.info(
            f"Transaction {transaction.payment_id} already completed, skipping handler."
        )
        return

    logger.debug(f"Calling handle_payment_succeeded for payment_id={transaction.payment_id}")
    try:
        await gateway.handle_payment_succeeded(payment_id=transaction.payment_id)
        logger.debug(f"handle_payment_succeeded completed successfully")
    except (ConnectionError, TimeoutError) as e:
        logger.error(f"[PURCHASE FLOW] STEP 7.3: CALL_HANDLE_PAYMENT - Network error in handle_payment_succeeded: {e}", exc_info=True)
    except SQLAlchemyError as e:
        logger.error(f"[PURCHASE FLOW] STEP 7.3: CALL_HANDLE_PAYMENT - Database error in handle_payment_succeeded: {e}", exc_info=True)
        await session.rollback()
    except Exception as e:
        logger.error(f"[PURCHASE FLOW] STEP 7.3: CALL_HANDLE_PAYMENT - Unexpected error in handle_payment_succeeded: {e}", exc_info=True)
