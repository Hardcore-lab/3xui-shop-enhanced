from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.bot.services import PlanService

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)
from aiogram.utils.i18n import gettext as _
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.models import SubscriptionData
from app.bot.models.plan import Plan
from app.bot.payment_gateways import PaymentGateway
from app.bot.routers.misc.keyboard import (
    back_button,
    back_to_main_menu_button,
    close_notification_button,
)
from app.bot.utils.constants import Currency
from app.bot.utils.formatting import format_device_count, format_subscription_period
from app.bot.utils.location import format_server_location
from app.bot.utils.navigation import NavDownload, NavMain, NavSubscription
from app.db.models import Server


def change_subscription_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=_("subscription:button:change"),
        callback_data=NavSubscription.CHANGE,
    )


def subscription_keyboard(
    has_subscription: bool,
    callback_data: SubscriptionData,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if not has_subscription:
        builder.button(
            text=_("subscription:button:buy"),
            callback_data=callback_data,
        )
    else:
        callback_data.state = NavSubscription.EXTEND
        builder.button(
            text=_("subscription:button:extend"),
            callback_data=callback_data,
        )
        callback_data.state = NavSubscription.CHANGE
        builder.button(
            text=_("subscription:button:change"),
            callback_data=callback_data,
        )

    builder.button(
        text=_("subscription:button:activate_promocode"),
        callback_data=NavSubscription.PROMOCODE,
    )
    builder.adjust(1)
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def devices_keyboard(
    plans: list[Plan],
    callback_data: SubscriptionData,
    custom_offer_url: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    logger.info(f"devices_keyboard: Processing {len(plans)} plans")
    if not plans:
        logger.warning("devices_keyboard: Plans list is empty! No device buttons will be created.")
    
    buttons_created = 0
    for plan in plans:
        callback_data.devices = plan.devices
        button_text = format_device_count(plan.devices)
        logger.debug(f"devices_keyboard: Adding button for {plan.devices} devices: '{button_text}'")
        builder.button(
            text=button_text,
            callback_data=callback_data,
        )
        buttons_created += 1

    logger.info(f"devices_keyboard: Created {len(plans)} device buttons")
    builder.adjust(2)
    
    # Add custom offer button if URL is configured
    if custom_offer_url:
        builder.row(
            InlineKeyboardButton(
                text=_("subscription:button:custom_offer"),
                url=custom_offer_url,
            )
        )
    
    builder.row(back_button(NavSubscription.MAIN))
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


async def duration_keyboard(
    plan_service: PlanService,
    callback_data: SubscriptionData,
    currency: str,
    session,
) -> InlineKeyboardMarkup:
    import logging
    logger = logging.getLogger(__name__)
    
    builder = InlineKeyboardBuilder()
    durations = await plan_service.get_durations(session=session)
    
    if not durations:
        logger.warning(f"duration_keyboard: No durations found!")
        return builder.as_markup()
    
    currency: Currency = Currency.from_code(currency)
    
    # Get plan once before the loop to avoid multiple DB queries
    plan = await plan_service.get_plan(callback_data.devices, session=session)
    
    if not plan:
        logger.error(f"duration_keyboard: Plan not found for devices={callback_data.devices}")
        return builder.as_markup()

    for duration in durations:
        callback_data.duration = duration
        period = format_subscription_period(duration)
        price = plan.get_price(currency=currency, duration=duration)
        
        if price is not None:
            builder.button(
                text=f"{period} | {price} {currency.symbol}",
                callback_data=callback_data,
            )
        else:
            logger.warning(f"duration_keyboard: No price found for duration={duration}, currency={currency.code}, devices={callback_data.devices}")

    builder.adjust(2)

    if callback_data.is_extend:
        builder.row(back_button(NavSubscription.MAIN))
    else:
        callback_data.state = NavSubscription.PROCESS
        builder.row(
            back_button(
                callback_data.pack(),
                text=_("subscription:button:change_devices"),
            )
        )

    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def pay_keyboard(pay_url: str, callback_data: SubscriptionData) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(InlineKeyboardButton(text=_("subscription:button:pay"), url=pay_url))

    callback_data.state = NavSubscription.DURATION
    builder.row(
        back_button(
            callback_data.pack(),
            text=_("subscription:button:change_payment_method"),
        )
    )
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def payment_method_keyboard(
    plan: Plan,
    callback_data: SubscriptionData,
    gateways: list[PaymentGateway],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for gateway in gateways:
        price = plan.get_price(currency=gateway.currency, duration=callback_data.duration)
        if price is None:
            continue

        callback_data.state = gateway.callback
        builder.row(
            InlineKeyboardButton(
                text=f"{gateway.name} | {price} {gateway.currency.symbol}",
                callback_data=callback_data.pack(),
            )
        )

    callback_data.state = NavSubscription.DEVICES
    builder.row(
        back_button(
            callback_data.pack(),
            text=_("subscription:button:change_duration"),
        )
    )

    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def payment_success_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("subscription:button:download_app"),
            callback_data=NavMain.REDIRECT_TO_DOWNLOAD,
        )
    )

    builder.row(close_notification_button())
    return builder.as_markup()


def trial_success_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("subscription:button:connect"),
            callback_data=NavDownload.MAIN,
        )
    )

    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def promocode_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(back_button(NavSubscription.MAIN))
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def servers_selection_keyboard(
    servers: list[Server],
    callback_data: SubscriptionData,
    locale: str = "ru",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # Группировка серверов по локации
    servers_by_location: dict[str, list[Server]] = {}
    for server in servers:
        location = server.location or _("subscription:server:no_location")
        if location not in servers_by_location:
            servers_by_location[location] = []
        servers_by_location[location].append(server)

    # Создание кнопок, сгруппированных по локации
    for location, location_servers in sorted(servers_by_location.items()):
        for server in location_servers:
            status = "🟢" if server.online else "🔴"
            load_info = f"{server.current_clients}/{server.max_clients}"
            
            # Форматируем локацию с флагом и локализацией
            formatted_location = format_server_location(
                location=location,
                server_name=server.name,
                locale=locale,
            )
            
            # Если локация не была локализована, используем оригинальное название
            if not formatted_location or formatted_location == location:
                formatted_location = location
            
            # Формируем текст: "флаг локация - название сервера (нагрузка)"
            # Если локация и название сервера совпадают, показываем оба
            text = f"{status} {formatted_location} - {server.name} ({load_info})"
            
            callback_data.server_id = server.id
            builder.row(
                InlineKeyboardButton(
                    text=text,
                    callback_data=callback_data.pack(),
                )
            )

    builder.row(back_button(NavSubscription.MAIN))
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def expiry_reminder_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для напоминания об истечении подписки с кнопкой продления."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text=_("expiry_offer:button:renew_now"),
            callback_data=NavSubscription.MAIN,
        )
    )
    
    return builder.as_markup()
