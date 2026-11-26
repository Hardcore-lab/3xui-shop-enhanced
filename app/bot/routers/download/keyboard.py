from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.i18n import gettext as _
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.routers.misc.keyboard import back_button, back_to_main_menu_button
from app.bot.utils.constants import (
    APP_ANDROID_LINK,
    APP_IOS_LINK,
    APP_WINDOWS_LINK,
)
from app.bot.utils.navigation import NavDownload, NavMain, NavSupport


def platforms_keyboard(previous_callback: str = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("download:button:ios"),
            callback_data=NavDownload.PLATFORM_IOS,
        ),
        InlineKeyboardButton(
            text=_("download:button:android"),
            callback_data=NavDownload.PLATFORM_ANDROID,
        ),
        InlineKeyboardButton(
            text=_("download:button:windows"),
            callback_data=NavDownload.PLATFORM_WINDOWS,
        ),
    )

    if previous_callback == NavMain.MAIN_MENU:
        builder.row(back_to_main_menu_button())
    else:
        back_callback = previous_callback if previous_callback else NavSupport.HOW_TO_CONNECT
        builder.row(back_button(back_callback))

    return builder.as_markup()


def download_keyboard(
    platform: NavDownload,
    subscription_url: str | None = None,
    subscription_links: list[tuple[int, str]] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    match platform:
        case NavDownload.PLATFORM_IOS:
            download = APP_IOS_LINK
        case NavDownload.PLATFORM_ANDROID:
            download = APP_ANDROID_LINK
        case _:
            download = APP_WINDOWS_LINK

    builder.button(text=_("download:button:download"), url=download)

    if subscription_links:
        if len(subscription_links) == 1:
            builder.button(
                text=_("download:button:subscription_link"),
                url=subscription_links[0][1],
            )
        else:
            builder.button(
                text=_("download:button:subscription_link"),
                callback_data=NavDownload.SUBSCRIPTION_LINK,
            )
    elif subscription_url:
        builder.button(text=_("download:button:subscription_link"), url=subscription_url)

    builder.row(back_button(NavDownload.MAIN))
    return builder.as_markup()
