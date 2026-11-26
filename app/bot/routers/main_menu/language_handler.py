import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.i18n import gettext as _
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.routers.main_menu.keyboard import language_selection_keyboard, main_menu_keyboard
from app.bot.services import ServicesContainer
from app.bot.utils.constants import DEFAULT_LANGUAGE
from app.bot.utils.navigation import NavMain
from app.config import Config
from app.db.models import User

logger = logging.getLogger(__name__)
router = Router(name=__name__)

# Register language handler router BEFORE other routers to ensure it's checked first
# This router should be registered early to catch language selection callbacks



@router.callback_query(F.data == NavMain.LANGUAGE)
async def callback_language_selection(
    callback: CallbackQuery,
    user: User,
    state: FSMContext,
    services: ServicesContainer,
    config: Config,
) -> None:
    """Show language selection keyboard."""
    await state.set_state(None)
    
    keyboard = language_selection_keyboard(current_language=user.language_code)
    
    await callback.message.edit_text(
        text=_("language:message:select"),
        reply_markup=keyboard,
    )


# Exact match handlers - MUST be registered BEFORE startswith handler
# In aiogram, exact matches are checked first, but order still matters
@router.callback_query(F.data == "language_select_en")
async def callback_language_selected_en(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
    config: Config,
) -> None:
    """Handle English language selection - exact match."""
    await callback_language_selected_impl(callback, user, session, state, services, config, "en")


@router.callback_query(F.data == "language_select_ru")
async def callback_language_selected_ru(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
    config: Config,
) -> None:
    """Handle Russian language selection - exact match."""
    await callback_language_selected_impl(callback, user, session, state, services, config, "ru")


async def callback_language_selected_impl(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
    config: Config,
    language_code: str,
) -> None:
    """Implementation of language selection handler."""
    if language_code not in ["ru", "en"]:
        logger.warning(f"User {user.tg_id} selected invalid language: {language_code}")
        await callback.answer(_("language:popup:invalid"), show_alert=True)
        return
    
    # Update user's language in database
    user.language_code = language_code
    
    try:
        await session.commit()
        # Refresh user object to ensure it has the latest data
        await session.refresh(user)
        logger.info(f"User {user.tg_id} changed language to {language_code}")
    except Exception as e:
        logger.error(f"Failed to update language for user {user.tg_id}: {e}", exc_info=True)
        await session.rollback()
        await callback.answer(_("language:popup:error"), show_alert=True)
        return
    
    # Use explicit locale context for the response
    from aiogram.utils.i18n import I18n
    i18n = I18n.get_current()
    
    # Show confirmation with new language
    with i18n.use_locale(language_code):
        from aiogram.utils.i18n import gettext as _
        await callback.answer(_("language:popup:changed").format(language=language_code.upper()))
    
    # Update main menu with new language using explicit locale context
    with i18n.use_locale(language_code):
        from app.bot.routers.main_menu.handler import callback_main_menu
        await callback_main_menu(
            callback=callback,
            user=user,
            services=services,
            state=state,
            config=config,
        )

