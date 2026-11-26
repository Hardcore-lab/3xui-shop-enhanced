import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.i18n import gettext as _
from redis.asyncio.client import Redis

from app.bot.models import ServicesContainer
from app.bot.utils.navigation import NavSubscription
from app.config import Config
from app.db.models import User

logger = logging.getLogger(__name__)
router = Router(name=__name__)


@router.callback_query(F.data.startswith(NavSubscription.ACCEPT_EXPIRY_OFFER), flags={"throttle_key": "user"})
async def callback_accept_expiry_offer(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    config: Config,
    redis: Redis,
) -> None:
    """Обработчик принятия предложения 3 дней бесплатно при истечении подписки."""
    # Извлекаем user_tg_id из callback_data (формат: "accept_expiry_offer_{user_tg_id}")
    callback_data = callback.data
    if "_" in callback_data:
        try:
            callback_user_id = int(callback_data.split("_")[-1])
            # Проверяем, что это тот же пользователь
            if callback_user_id != user.tg_id:
                await callback.answer(
                    text=_("expiry_offer:popup:failed"),
                    show_alert=True,
                )
                return
        except (ValueError, IndexError):
            logger.error(f"Invalid callback data format: {callback_data}")
            await callback.answer(
                text=_("expiry_offer:popup:failed"),
                show_alert=True,
            )
            return
    
    logger.info(f"User {user.tg_id} accepted expiry offer.")
    
    # Проверяем, не использовано ли уже предложение
    offer_used_key = f"user:expiry_offer_used:{user.tg_id}"
    try:
        offer_used = await redis.get(offer_used_key)
    except Exception as e:
        logger.error(f"Redis error for user {user.tg_id}: {e}")
        await callback.answer(
            text=_("expiry_offer:popup:failed"),
            show_alert=True,
        )
        return
    if offer_used:
        await callback.answer(
            text=_("expiry_offer:popup:already_used"),
            show_alert=True,
        )
        return
    
    # Проверяем, есть ли у пользователя активная подписка или истекшая (для восстановления)
    client_data = await services.vpn.get_client_data(user)
    if not client_data:
        await callback.answer(
            text=_("expiry_offer:popup:no_subscription"),
            show_alert=True,
        )
        return
    
    # Добавляем 3 дня бесплатно
    success = await services.vpn.process_bonus_days(
        user=user,
        duration=3,
        devices=config.shop.BONUS_DEVICES_COUNT,
    )
    
    if success:
        # Отмечаем, что предложение использовано
        try:
            await redis.set(offer_used_key, "true", ex=timedelta(days=7))
        except Exception as e:
            logger.error(f"Redis error for user {user.tg_id}: {e}")
        
        await callback.answer(
            text=_("expiry_offer:popup:success"),
            show_alert=True,
        )
        
        # Обновляем сообщение
        if callback.message:
            try:
                await callback.message.edit_text(
                    text=_("expiry_offer:message:success"),
                    reply_markup=None,
                )
            except Exception as e:
                logger.error(f"Failed to edit message: {e}")
        
        logger.info(f"Successfully added 3 free days to user {user.tg_id}.")
    else:
        await callback.answer(
            text=_("expiry_offer:popup:failed"),
            show_alert=True,
        )
        logger.error(f"Failed to add 3 free days to user {user.tg_id}.")


@router.callback_query(F.data.startswith(NavSubscription.DECLINE_EXPIRY_OFFER), flags={"throttle_key": "user"})
async def callback_decline_expiry_offer(
    callback: CallbackQuery,
    user: User,
    redis: Redis,
) -> None:
    """Обработчик отклонения предложения (напомнить позже)."""
    # Извлекаем user_tg_id из callback_data
    callback_data = callback.data
    if "_" in callback_data:
        try:
            callback_user_id = int(callback_data.split("_")[-1])
            # Проверяем, что это тот же пользователь
            if callback_user_id != user.tg_id:
                await callback.answer(
                    text=_("expiry_offer:popup:failed"),
                    show_alert=True,
                )
                return
        except (ValueError, IndexError):
            logger.error(f"Invalid callback data format: {callback_data}")
            await callback.answer(
                text=_("expiry_offer:popup:failed"),
                show_alert=True,
            )
            return
    
    logger.info(f"User {user.tg_id} declined expiry offer (remind later).")
    
    # Удаляем сообщение
    try:
        await callback.message.delete()
    except Exception as e:
        logger.error(f"Failed to delete message: {e}")
    
    await callback.answer(
        text=_("expiry_offer:popup:remind_later"),
        show_alert=False,
    )
    
    # Сбрасываем ключ уведомления, чтобы напомнить позже (через 6 часов)
    user_notified_key = f"user:notified:{user.tg_id}"
    try:
        await redis.set(user_notified_key, "true", ex=timedelta(hours=6))
    except Exception as e:
        logger.error(f"Redis error for user {user.tg_id}: {e}")

