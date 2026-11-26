import logging
from datetime import datetime, timedelta, timezone

from aiogram.utils.i18n import I18n
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.routers.subscription.keyboard import expiry_reminder_keyboard
from app.bot.services import NotificationService, VPNService
from app.db.models import User

logger = logging.getLogger(__name__)


async def notify_users_with_expiring_subscription(
    session_factory: async_sessionmaker,
    redis: Redis,
    i18n: I18n,
    vpn_service: VPNService,
    notification_service: NotificationService,
) -> None:
    """
    Check subscription expiry for all users and send notifications.
    Uses pagination to avoid loading all users into memory at once.
    """
    BATCH_SIZE = 100  # Process 100 users at a time
    offset = 0
    total_processed = 0
    
    logger.info("[Background task] Starting subscription expiration check with pagination.")
    
    while True:
        session: AsyncSession
        async with session_factory() as session:
            # Load users in batches using pagination
            from sqlalchemy import select
            query = select(User).offset(offset).limit(BATCH_SIZE)
            result = await session.execute(query)
            users = result.scalars().all()
            
            if not users:
                break  # No more users to process
            
            logger.info(f"[Background task] Processing batch: offset={offset}, count={len(users)}")
            
            for user in users:
                user_notified_key = f"user:notified:{user.tg_id}"
                user_offer_key = f"user:expiry_offer:{user.tg_id}"

                # Check if user was recently notified
                try:
                    notified = await redis.get(user_notified_key)
                except Exception as e:
                    logger.error(f"Redis error for user {user.tg_id}: {e}")
                    continue
                if notified:
                    continue

                client_data = await vpn_service.get_client_data(user)

                # Skip if no client data or subscription is unlimited
                if not client_data or client_data._expiry_time == -1:
                    continue

                now = datetime.now(timezone.utc)
                expiry_datetime = datetime.fromtimestamp(
                    client_data._expiry_time / 1000, timezone.utc
                )
                time_left = expiry_datetime - now

                # Уведомление за 3 дня до истечения
                if timedelta(hours=72) >= time_left > timedelta(hours=48):
                    # Проверяем, не отправляли ли уже уведомление
                    try:
                        notified = await redis.get(user_notified_key)
                    except Exception as e:
                        logger.error(f"Redis error for user {user.tg_id}: {e}")
                        continue
                    if notified:
                        continue
                    
                    # Отправляем напоминание с кнопкой продления
                    with i18n.use_locale(user.language_code):
                        from aiogram.utils.i18n import gettext as _
                        text = _("task:message:subscription_expiry_3days").format(
                            devices=client_data.max_devices,
                            expiry_time=client_data.expiry_time,
                        )
                        keyboard = expiry_reminder_keyboard()
                    
                    await notification_service.notify_by_id(
                        chat_id=user.tg_id,
                        text=text,
                        reply_markup=keyboard,
                        duration=0,  # Не удалять сообщение
                    )
                    
                    try:
                        await redis.set(user_notified_key, "true", ex=timedelta(hours=24))
                    except Exception as e:
                        logger.error(f"Redis error for user {user.tg_id}: {e}")
                    logger.info(
                        f"[Background task] Sent expiry reminder (3 days) to user {user.tg_id}."
                    )
                    continue

                # Уведомление за 1 день до истечения
                if timedelta(hours=24) >= time_left > timedelta(hours=12):
                    # Проверяем, не отправляли ли уже уведомление
                    try:
                        notified = await redis.get(user_notified_key)
                    except Exception as e:
                        logger.error(f"Redis error for user {user.tg_id}: {e}")
                        continue
                    if notified:
                        continue
                    
                    with i18n.use_locale(user.language_code):
                        from aiogram.utils.i18n import gettext as _
                        text = _("task:message:subscription_expiry_1day").format(
                            devices=client_data.max_devices,
                            expiry_time=client_data.expiry_time,
                        )
                        keyboard = expiry_reminder_keyboard()
                    
                    await notification_service.notify_by_id(
                        chat_id=user.tg_id,
                        text=text,
                        reply_markup=keyboard,
                        duration=0,
                    )

                    try:
                        await redis.set(user_notified_key, "true", ex=timedelta(hours=24))
                    except Exception as e:
                        logger.error(f"Redis error for user {user.tg_id}: {e}")
                    logger.info(
                        f"[Background task] Sent expiry reminder (1 day) to user {user.tg_id}."
                    )
                    continue

                # Уведомление в день истечения (последние 12 часов или до 24 часов после истечения)
                if timedelta(hours=12) >= time_left and time_left > timedelta(hours=-24):
                    # Проверяем, не отправляли ли уже уведомление сегодня
                    today_notified_key = f"user:expiry_notified_today:{user.tg_id}"
                    try:
                        today_notified = await redis.get(today_notified_key)
                    except Exception as e:
                        logger.error(f"Redis error for user {user.tg_id}: {e}")
                        continue
                    if today_notified:
                        continue
                    
                    with i18n.use_locale(user.language_code):
                        from aiogram.utils.i18n import gettext as _
                        text = _("task:message:subscription_expiry_today").format(
                            devices=client_data.max_devices,
                            expiry_time=client_data.expiry_time,
                        )
                        keyboard = expiry_reminder_keyboard()
                    
                    await notification_service.notify_by_id(
                        chat_id=user.tg_id,
                        text=text,
                        reply_markup=keyboard,
                        duration=0,
                    )
                    
                    try:
                        await redis.set(today_notified_key, "true", ex=timedelta(hours=12))
                        await redis.set(user_notified_key, "true", ex=timedelta(hours=12))
                    except Exception as e:
                        logger.error(f"Redis error for user {user.tg_id}: {e}")
                    logger.info(
                        f"[Background task] Sent final expiry reminder to user {user.tg_id}."
                    )
                    continue
            
            total_processed += len(users)
            offset += BATCH_SIZE
            
            # If we got less than BATCH_SIZE users, we've reached the end
            if len(users) < BATCH_SIZE:
                break
    
    logger.info(f"[Background task] Subscription check finished. Total users processed: {total_processed}.")


def start_scheduler(
    session_factory: async_sessionmaker,
    redis: Redis,
    i18n: I18n,
    vpn_service: VPNService,
    notification_service: NotificationService,
) -> None:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        notify_users_with_expiring_subscription,
        "interval",
        minutes=15,
        args=[session_factory, redis, i18n, vpn_service, notification_service],
        next_run_time=datetime.now(tz=timezone.utc),
    )
    scheduler.start()
