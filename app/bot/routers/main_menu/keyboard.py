from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.i18n import gettext as _
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.utils.navigation import (
    NavAdminTools,
    NavMain,
    NavProfile,
    NavReferral,
    NavSubscription,
    NavSupport,
)


def main_menu_keyboard(
    is_admin: bool = False,
    is_referral_available: bool = False,
    is_trial_available: bool = False,
    is_referred_trial_available: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if is_referred_trial_available:
        builder.row(
            InlineKeyboardButton(
                text=_("referral:button:get_referred_trial"),
                callback_data=NavReferral.GET_REFERRED_TRIAL,
            )
        )
    elif is_trial_available:
        builder.row(
            InlineKeyboardButton(
                text=_("subscription:button:get_trial"), callback_data=NavSubscription.GET_TRIAL
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=_("main_menu:button:profile"),
            callback_data=NavProfile.MAIN,
        ),
        InlineKeyboardButton(
            text=_("main_menu:button:subscription"),
            callback_data=NavSubscription.MAIN,
        ),
    )
    builder.row(
        *(
            [
                InlineKeyboardButton(
                    text=_("main_menu:button:referral"),
                    callback_data=NavReferral.MAIN,
                )
            ]
            if is_referral_available
            else []
        ),
        InlineKeyboardButton(
            text=_("main_menu:button:support"),
            callback_data=NavSupport.MAIN,
        ),
    )

    if is_admin:
        builder.row(
            InlineKeyboardButton(
                text=_("main_menu:button:admin_tools"),
                callback_data=NavAdminTools.MAIN,
            )
        )

    # Add language selection button
    builder.row(
        InlineKeyboardButton(
            text=_("main_menu:button:language"),
            callback_data=NavMain.LANGUAGE,
        )
    )

    return builder.as_markup()


def language_selection_keyboard(current_language: str | None = None) -> InlineKeyboardMarkup:
    """Keyboard for language selection."""
    builder = InlineKeyboardBuilder()
    
    # Russian button
    ru_callback = f"{NavMain.LANGUAGE_SELECT.value}_ru"
    builder.row(
        InlineKeyboardButton(
            text="🇷🇺 Русский" + (" ✓" if current_language == "ru" else ""),
            callback_data=ru_callback,
        )
    )
    
    # English button
    en_callback = f"{NavMain.LANGUAGE_SELECT.value}_en"
    builder.row(
        InlineKeyboardButton(
            text="🇬🇧 English" + (" ✓" if current_language == "en" else ""),
            callback_data=en_callback,
        )
    )
    
    builder.row(
        InlineKeyboardButton(
            text=_("misc:button:back"),
            callback_data=NavMain.MAIN_MENU,
            )
        )

    return builder.as_markup()
