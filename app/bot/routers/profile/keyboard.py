from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.i18n import gettext as _
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.routers.misc.keyboard import back_to_main_menu_button
from app.bot.utils.navigation import NavDownload, NavProfile, NavSubscription


def keys_viewer_keyboard(current_index: int, total_keys: int) -> InlineKeyboardMarkup:
    """Keyboard for viewing keys with navigation."""
    builder = InlineKeyboardBuilder()
    
    # Previous button (disabled if on first key)
    if current_index > 1:
        builder.button(
            text="◀️ " + _("profile:button:prev_key"),
            callback_data=NavProfile.KEY_PREV,
        )
    
    # Next button (disabled if on last key)
    if current_index < total_keys:
        builder.button(
            text=_("profile:button:next_key") + " ▶️",
            callback_data=NavProfile.KEY_NEXT,
        )
    
    builder.adjust(2)
    
    # Close button
    builder.row(
        InlineKeyboardButton(
            text="❌ " + _("profile:button:close_keys"),
            callback_data=NavProfile.KEY_CLOSE,
        )
    )
    
    return builder.as_markup()


def buy_subscription_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("profile:button:buy_subscription"),
            callback_data=NavSubscription.MAIN,
        )
    )

    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def profile_keyboard(
    subscription_link_url: str | None = None,
    subscription_links: list[tuple[int, str]] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("profile:button:show_key"),
            callback_data=NavProfile.SHOW_KEY,
        )
    )
    if subscription_links:
        if len(subscription_links) == 1:
            builder.row(
                InlineKeyboardButton(
                    text=_("download:button:subscription_link"),
                    url=subscription_links[0][1],
                )
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=_("download:button:subscription_link"),
                    callback_data=NavProfile.SUBSCRIPTION_LINK,
                )
            )
    elif subscription_link_url:
        builder.row(
            InlineKeyboardButton(
                text=_("download:button:subscription_link"),
                url=subscription_link_url,
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=_("profile:button:connect"),
            callback_data=NavDownload.MAIN,
        )
    )

    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def subscription_link_choice_keyboard(
    links: list[tuple[int, str]],
    back_callback: str,
) -> InlineKeyboardMarkup:
    """Keyboard for choosing subscription link by device. links = (client_index, full_redirect_url)."""
    builder = InlineKeyboardBuilder()
    for client_index, url in links:
        builder.row(
            InlineKeyboardButton(
                text=_("profile:button:device_n").format(n=client_index),
                url=url,
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="◀️ " + _("profile:button:back_to_profile"),
            callback_data=back_callback,
        )
    )
    return builder.as_markup()
