"""
Handlers для управления пользователями в админ-панели.
"""
import logging
from datetime import datetime
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.i18n import gettext as _
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdmin
from app.bot.models import ServicesContainer
from app.bot.services.export_service import ExportService
from app.bot.services.user_management import UserManagementService
from app.bot.utils.location import format_server_location
from app.bot.utils.navigation import NavAdminTools
from app.bot.utils.validation import (
    is_valid_days,
    is_valid_search_query,
    sanitize_search_query,
)
from app.db.models import Server, User

from .keyboard import (
    user_confirm_action_keyboard,
    user_details_keyboard,
    user_editor_keyboard,
    user_filter_keyboard,
    user_search_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name=__name__)

class UserSearchStates(StatesGroup):
    """Состояния для поиска пользователей."""
    search_query = State()
    filter_subscription = State()
    filter_date = State()
    filter_server = State()
    extend_days = State()
    add_bonus_days = State()
    remove_days = State()
    change_server = State()


@router.callback_query(F.data == NavAdminTools.USER_EDITOR, IsAdmin())
async def callback_user_editor(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
) -> None:
    """Открыть редактор пользователей."""
    logger.info(f"Admin {user.tg_id} opened user editor.")
    
    try:
        
        # Устанавливаем локаль явно на основе языка пользователя
        from aiogram.utils.i18n import I18n
        current_i18n = I18n.get_current()
        
        # Нормализуем язык пользователя (может быть "ru-RU" -> "ru")
        user_lang = user.language_code if hasattr(user, 'language_code') else "en"
        original_lang = user_lang
        if user_lang and "-" in user_lang:
            user_lang = user_lang.split("-")[0]
        # Убеждаемся, что язык поддерживается
        if user_lang not in ["ru", "en"]:
            user_lang = "en"
        
        # Получаем перевод с явной установкой локали
        test_key = "user_editor:message:main"
        text = None
        translation_worked = False
        
        if current_i18n:
            try:
                with current_i18n.use_locale(user_lang):
                    text = _(test_key)
                    translation_worked = text != test_key and not text.startswith("user_editor:")
            except Exception as e:
                text = _(test_key)
        
        if not text:
            text = _(test_key)
        
        # Если перевод не сработал (вернул ключ), используем fallback
        if text == test_key or text.startswith("user_editor:"):
            text = _("user_editor:message:main")
            translation_worked = False
        
        # Создаем клавиатуру в контексте правильной локали
        try:
            if current_i18n:
                with current_i18n.use_locale(user_lang):
                    keyboard = user_editor_keyboard()
            else:
                keyboard = user_editor_keyboard()
        except Exception as e:
            keyboard = user_editor_keyboard()
        
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
        )
        
    except Exception as e:
        
        logger.error(f"Error opening user editor: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(F.data == NavAdminTools.USER_LIST, IsAdmin())
async def callback_user_list(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Показать список всех пользователей."""
    try:
        logger.info(f"Admin {user.tg_id} requested user list")
        
        # Получаем всех пользователей (без поиска)
        user_service = UserManagementService(session)
        users, total = await user_service.search_users(
            query="",  # Пустой запрос = все пользователи
            page=0,
            limit=10,
        )
        
        if not users:
            await callback.message.edit_text(
                text=_("user_editor:message:no_users_found"),
            reply_markup=user_editor_keyboard(),
        )
            return
        
        # Сохраняем результаты в state для пагинации
        await state.update_data({
            "search_query": "",
            "search_page": 0,
            "search_total": total,
        })
        
        # Формируем список пользователей с кнопками
        text = _("user_editor:message:search_results").format(
            total=total,
            found=len(users),
        )
        
        # Создаем клавиатуру с кнопками для каждого пользователя
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        from .keyboard import back_button
        
        builder = InlineKeyboardBuilder()
        
        for idx, u in enumerate(users, 1):
            server_info = f" ({u.server.name})" if u.server else ""
            username_display = u.username or 'N/A'
            button_text = f"{idx}. {u.first_name[:12]} (@{username_display[:12]}) [ID: {u.tg_id}]{server_info[:8]}"
            if len(button_text) > 64:
                button_text = button_text[:61] + "..."
            
            callback_data = f"{NavAdminTools.USER_DETAILS.value}_{u.tg_id}"
            
            builder.row(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=callback_data,
                )
            )
        
        # Кнопки пагинации
        pagination_row = []
        if total > 10:
            pagination_row.append(
                InlineKeyboardButton(
                    text=_("user_editor:button:next_page"),
                    callback_data=f"{NavAdminTools.USER_LIST.value}_page_1",
                )
            )
        
        if pagination_row:
            builder.row(*pagination_row)
        
        builder.row(back_button(NavAdminTools.USER_EDITOR))
        
        await callback.message.edit_text(
            text=text,
            reply_markup=builder.as_markup(),
        )
        
    except Exception as e:
        logger.error(f"Error listing users for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(F.data == NavAdminTools.USER_SEARCH, IsAdmin())
async def callback_user_search(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Начать поиск пользователей."""
    logger.info(f"Admin {user.tg_id} started user search.")
    
    try:
        logger.info(f"Setting FSM state to search_query for admin {user.tg_id}")
        await state.set_state(UserSearchStates.search_query)
        
        text = _("user_editor:message:search_prompt")
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=user_filter_keyboard(),
            )
        except Exception as edit_error:
            # Если сообщение не изменилось, просто отвечаем на callback
            if "message is not modified" in str(edit_error).lower():
                await callback.answer()
            else:
                raise
    except Exception as e:
        logger.error(f"Error starting user search: {e}")
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.message(UserSearchStates.search_query, IsAdmin())
async def process_search_query(
    message: Message,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Обработать поисковый запрос."""
    
    try:
        query = message.text.strip() if message.text else ""
        
        if not query:
            await message.answer(
                text=_("user_editor:message:empty_query"),
            )
            return
        
        if not is_valid_search_query(query):
            await message.answer(
                text=_("user_editor:message:invalid_query"),
            )
            return
        
        # Санитизируем запрос
        sanitized_query = sanitize_search_query(query)
        logger.info(f"Admin {user.tg_id} searching users with query: {sanitized_query}")
        
        # Выполняем поиск
        user_service = UserManagementService(session)
        
        users, total = await user_service.search_users(
            query=sanitized_query,
            page=0,
            limit=10,
        )
        
        if not users:
            await message.answer(
                text=_("user_editor:message:no_users_found"),
            )
            await state.clear()
            return
        
        # Сохраняем результаты поиска в state для пагинации
        await state.update_data({
            "search_query": sanitized_query,
            "search_page": 0,
            "search_total": total,
        })
        
        # Формируем список пользователей с кнопками
        # ВАЖНО: Информация о пользователях должна быть ТОЛЬКО в кнопках, НЕ в тексте сообщения
        text = _("user_editor:message:search_results").format(
            total=total,
            found=len(users),
        )
        
        # Создаем клавиатуру с кнопками для каждого пользователя
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        from .keyboard import back_button
        
        builder = InlineKeyboardBuilder()
        
        for idx, u in enumerate(users, 1):
            server_info = f" ({u.server.name})" if u.server else ""
            # Ограничиваем длину текста кнопки (Telegram ограничение - 64 символа)
            username_display = u.username or 'N/A'
            # Формируем текст кнопки БЕЗ добавления в текст сообщения
            button_text = f"{idx}. {u.first_name[:12]} (@{username_display[:12]}) [ID: {u.tg_id}]{server_info[:8]}"
            # Обрезаем до 64 символов
            if len(button_text) > 64:
                button_text = button_text[:61] + "..."
            
            callback_data = f"{NavAdminTools.USER_DETAILS.value}_{u.tg_id}"
            
            builder.row(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=callback_data,
                )
            )
        
        has_next = total > 10
        if has_next:
            builder.row(
                InlineKeyboardButton(
                    text=_("user_editor:button:next_page"),
                    callback_data=f"{NavAdminTools.USER_SEARCH.value}_page_1",
                )
            )
        
        builder.row(back_button(NavAdminTools.USER_EDITOR))
        
        keyboard = builder.as_markup()
        
        result = await message.answer(
            text=text,
            reply_markup=keyboard,
        )
        
        logger.info(f"Admin {user.tg_id} found {len(users)} users (total: {total})")
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error processing search query for admin {user.tg_id}: {e}", exc_info=True)
        await message.answer(
            text=_("user_editor:message:error"),
        )
        await state.clear()


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_DETAILS.value}_"),
    IsAdmin(),
)
async def callback_user_details(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
) -> None:
    """Показать детальную информацию о пользователе."""
    
    try:
        # Извлекаем tg_id из callback_data
        # Формат: user_details_{tg_id}
        prefix = f"{NavAdminTools.USER_DETAILS.value}_"
        if not callback.data.startswith(prefix):
            logger.error(f"Invalid callback_data format: {callback.data}")
            await callback.answer(
                text=_("user_editor:message:invalid_user_id"),
                show_alert=True,
            )
            return
        
        tg_id_str = callback.data[len(prefix):]
        
        try:
            tg_id = int(tg_id_str)
        except (ValueError, IndexError) as e:
            logger.warning(f"Invalid tg_id in callback_data: {callback.data}, error: {e}")
            await callback.answer(
                text=_("user_editor:message:invalid_user_id"),
                show_alert=True,
            )
            return
        
        logger.info(f"Admin {user.tg_id} viewing details for user {tg_id}")
        
        user_service = UserManagementService(session)
        target_user = await user_service.get_user_details(tg_id)
        
        if not target_user:
            logger.warning(f"User {tg_id} not found for admin {user.tg_id}")
            await callback.answer(
                text=_("user_editor:message:user_not_found"),
                show_alert=True,
            )
            return
        
        # Формируем детальную информацию
        try:
            text = _("user_editor:message:user_details").format(
                tg_id=target_user.tg_id,
                username=target_user.username or "N/A",
                first_name=target_user.first_name,
                created_at=target_user.created_at.strftime("%Y-%m-%d %H:%M:%S") if target_user.created_at else "N/A",
                server_name=target_user.server.name if target_user.server else "None",
                server_location=target_user.server.location if target_user.server else "N/A",
                transactions_count=len(target_user.transactions),
                referrals_count=len(target_user.referrals_sent),
            )
            
            keyboard = user_details_keyboard(tg_id)
            
            await callback.message.edit_text(
                text=text,
                reply_markup=keyboard,
            )
            await callback.answer()
        except Exception as e:
            logger.error(f"Error displaying user details: {e}", exc_info=True)
            await callback.answer(
                text=_("user_editor:message:error"),
                show_alert=True,
        )
        
    except Exception as e:
        logger.error(f"Error showing user details for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(
    F.data == NavAdminTools.USER_EXPORT,
    IsAdmin(),
)
async def callback_user_export(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
) -> None:
    """Экспортировать пользователей."""
    try:
        logger.info(f"Admin {user.tg_id} exporting users.")
        
        # Получаем всех пользователей (можно добавить фильтры)
        from app.db.models import User as UserModel
        
        query = await session.execute(select(UserModel).limit(1000))
        users = query.scalars().all()
        
        if not users:
            await callback.answer(
                text=_("user_editor:message:no_users_to_export"),
                show_alert=True,
            )
            return
        
        # Экспортируем
        export_service = ExportService(session)
        csv_data = await export_service.export_users(
            users=list(users),
            include_transactions=True,
            include_server=True,
        )
        
        if not csv_data:
            logger.warning(f"Export service returned empty data for admin {user.tg_id}")
            await callback.answer(
                text=_("user_editor:message:export_error"),
                show_alert=True,
            )
            return
        
        # Отправляем файл
        file = BufferedInputFile(
            csv_data.encode("utf-8"),
            filename=f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        
        await callback.message.answer_document(file)
        logger.info(f"Admin {user.tg_id} successfully exported {len(users)} users.")
        await callback.answer(
            text=_("user_editor:message:export_success"),
            show_alert=True,
        )
        
    except Exception as e:
        logger.error(f"Error exporting users for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_LIST.value}_page_"),
    IsAdmin(),
)
async def callback_user_list_page(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Обработка пагинации списка всех пользователей."""
    try:
        # Извлекаем номер страницы
        page_str = callback.data.split("_page_")[-1]
        page = int(page_str)
        
        # Получаем всех пользователей
        user_service = UserManagementService(session)
        users, total = await user_service.search_users(
            query="",  # Пустой запрос = все пользователи
            page=page,
            limit=10,
        )
        
        if not users:
            await callback.answer(
                text=_("user_editor:message:no_users_found"),
                show_alert=True,
            )
            return
        
        # Обновляем данные в state
        await state.update_data({
            "search_page": page,
            "search_total": total,
        })
        
        # Формируем список пользователей
        text = _("user_editor:message:search_results").format(
            total=total,
            found=len(users),
        )
        
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        from .keyboard import back_button
        
        builder = InlineKeyboardBuilder()
        
        for idx, u in enumerate(users, 1):
            server_info = f" ({u.server.name})" if u.server else ""
            username_display = u.username or 'N/A'
            button_text = f"{idx}. {u.first_name[:12]} (@{username_display[:12]}) [ID: {u.tg_id}]{server_info[:8]}"
            if len(button_text) > 64:
                button_text = button_text[:61] + "..."
            builder.row(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"{NavAdminTools.USER_DETAILS.value}_{u.tg_id}",
                )
            )
        
        # Кнопки пагинации
        pagination_row = []
        if page > 0:
            pagination_row.append(
                InlineKeyboardButton(
                    text=_("user_editor:button:previous_page"),
                    callback_data=f"{NavAdminTools.USER_LIST.value}_page_{page-1}",
                )
            )
        if (page + 1) * 10 < total:
            pagination_row.append(
                InlineKeyboardButton(
                    text=_("user_editor:button:next_page"),
                    callback_data=f"{NavAdminTools.USER_LIST.value}_page_{page+1}",
                )
            )
        
        if pagination_row:
            builder.row(*pagination_row)
        
        builder.row(back_button(NavAdminTools.USER_EDITOR))
        
        await callback.message.edit_text(
            text=text,
            reply_markup=builder.as_markup(),
        )
        
    except Exception as e:
        logger.error(f"Error handling user list pagination for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_SEARCH.value}_page_"),
    IsAdmin(),
)
async def callback_user_search_page(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Обработка пагинации результатов поиска."""
    try:
        # Извлекаем номер страницы
        page_str = callback.data.split("_page_")[-1]
        page = int(page_str)
        
        # Получаем сохраненные данные поиска
        state_data = await state.get_data()
        search_query = state_data.get("search_query", "")
        
        if not search_query:
            await callback.answer(
                text=_("user_editor:message:search_expired"),
                show_alert=True,
            )
            return
        
        # Выполняем поиск с новой страницей
        user_service = UserManagementService(session)
        users, total = await user_service.search_users(
            query=search_query,
            page=page,
            limit=10,
        )
        
        if not users:
            await callback.answer(
                text=_("user_editor:message:no_users_found"),
                show_alert=True,
            )
            return
        
        # Обновляем данные в state
        await state.update_data({
            "search_page": page,
            "search_total": total,
        })
        
        # Формируем список пользователей
        # ВАЖНО: Информация о пользователях должна быть ТОЛЬКО в кнопках, НЕ в тексте сообщения
        text = _("user_editor:message:search_results").format(
            total=total,
            found=len(users),
        )
        
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        from .keyboard import back_button
        
        builder = InlineKeyboardBuilder()
        
        for idx, u in enumerate(users, 1):
            server_info = f" ({u.server.name})" if u.server else ""
            # Ограничиваем длину текста кнопки (Telegram ограничение - 64 символа)
            username_display = u.username or 'N/A'
            # Формируем текст кнопки БЕЗ добавления в текст сообщения
            button_text = f"{idx}. {u.first_name[:12]} (@{username_display[:12]}) [ID: {u.tg_id}]{server_info[:8]}"
            # Обрезаем до 64 символов
            if len(button_text) > 64:
                button_text = button_text[:61] + "..."
            builder.row(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"{NavAdminTools.USER_DETAILS.value}_{u.tg_id}",
                )
            )
        
        # Кнопки пагинации
        pagination_row = []
        if page > 0:
            pagination_row.append(
                InlineKeyboardButton(
                    text=_("user_editor:button:previous_page"),
                    callback_data=f"{NavAdminTools.USER_SEARCH.value}_page_{page-1}",
                )
            )
        if (page + 1) * 10 < total:
            pagination_row.append(
                InlineKeyboardButton(
                    text=_("user_editor:button:next_page"),
                    callback_data=f"{NavAdminTools.USER_SEARCH.value}_page_{page+1}",
                )
            )
        
        if pagination_row:
            builder.row(*pagination_row)
        
        builder.row(back_button(NavAdminTools.USER_EDITOR))
        
        keyboard = builder.as_markup()
        
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
        )
        
    except Exception as e:
        logger.error(f"Error handling search pagination for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_EXTEND.value}_"),
    IsAdmin(),
)
async def callback_user_extend(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Начать процесс продления подписки пользователя."""
    
    try:
        # Извлекаем tg_id из callback_data
        # Формат: user_extend_{tg_id}
        prefix = f"{NavAdminTools.USER_EXTEND.value}_"
        logger.info(f"callback_user_extend: callback.data={callback.data}, prefix={prefix}")
        if not callback.data.startswith(prefix):
            logger.error(f"Invalid callback_data format: {callback.data}, expected prefix: {prefix}")
            await callback.answer(
                text=_("user_editor:message:error"),
                show_alert=True,
            )
            return
        
        tg_id_str = callback.data[len(prefix):]
        logger.info(f"callback_user_extend: extracted tg_id_str={tg_id_str}")
        tg_id = int(tg_id_str)
        logger.info(f"callback_user_extend: parsed tg_id={tg_id}")
        
        await state.update_data({"action_user_id": tg_id, "action_type": "extend"})
        await state.set_state(UserSearchStates.extend_days)
        
        text = _("user_editor:message:enter_days")
        logger.info(f"callback_user_extend: about to edit message, text length={len(text)}")
        
        # Для extend и add_bonus не нужна кнопка "Подтвердить" - пользователь просто вводит количество дней
        # Нужна только кнопка "Отмена" для возврата к деталям пользователя
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        from .keyboard import back_button
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=_("user_editor:button:cancel"),
                callback_data=f"{NavAdminTools.USER_CANCEL_ACTION.value}_{tg_id}",
            )
        )
        
        keyboard = builder.as_markup()
        logger.info(f"callback_user_extend: keyboard created, type={type(keyboard)}")
        
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
        )
        logger.info(f"callback_user_extend: message edited successfully")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error starting extend for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_ADD_BONUS.value}_"),
    IsAdmin(),
)
async def callback_user_add_bonus(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Начать процесс добавления бонусных дней."""
    
    try:
        # Извлекаем tg_id из callback_data
        # Формат: user_add_bonus_{tg_id}
        prefix = f"{NavAdminTools.USER_ADD_BONUS.value}_"
        if not callback.data.startswith(prefix):
            logger.error(f"Invalid callback_data format: {callback.data}")
            await callback.answer(
                text=_("user_editor:message:error"),
                show_alert=True,
            )
            return
        
        tg_id_str = callback.data[len(prefix):]
        tg_id = int(tg_id_str)
        
        await state.update_data({"action_user_id": tg_id, "action_type": "add_bonus"})
        await state.set_state(UserSearchStates.add_bonus_days)
        
        text = _("user_editor:message:enter_bonus_days")
        logger.info(f"callback_user_add_bonus: about to edit message, text length={len(text)}")
        
        # Для extend и add_bonus не нужна кнопка "Подтвердить" - пользователь просто вводит количество дней
        # Нужна только кнопка "Отмена" для возврата к деталям пользователя
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        from .keyboard import back_button
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=_("user_editor:button:cancel"),
                callback_data=f"{NavAdminTools.USER_CANCEL_ACTION.value}_{tg_id}",
            )
        )
        
        keyboard = builder.as_markup()
        logger.info(f"callback_user_add_bonus: keyboard created, type={type(keyboard)}")
        
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
        )
        logger.info(f"callback_user_add_bonus: message edited successfully")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error starting add bonus for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_REMOVE_DAYS.value}_"),
    IsAdmin(),
)
async def callback_user_remove_days(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Начать процесс вычета дней из подписки пользователя."""
    
    try:
        # Извлекаем tg_id из callback_data
        # Формат: user_remove_days_{tg_id}
        prefix = f"{NavAdminTools.USER_REMOVE_DAYS.value}_"
        logger.info(f"callback_user_remove_days: callback.data={callback.data}, prefix={prefix}")
        if not callback.data.startswith(prefix):
            logger.error(f"Invalid callback_data format: {callback.data}, expected prefix: {prefix}")
            await callback.answer(
                text=_("user_editor:message:error"),
                show_alert=True,
            )
            return
        
        tg_id_str = callback.data[len(prefix):]
        logger.info(f"callback_user_remove_days: extracted tg_id_str={tg_id_str}")
        tg_id = int(tg_id_str)
        logger.info(f"callback_user_remove_days: parsed tg_id={tg_id}")
        
        await state.update_data({"action_user_id": tg_id, "action_type": "remove_days"})
        await state.set_state(UserSearchStates.remove_days)
        
        text = _("user_editor:message:enter_remove_days")
        logger.info(f"callback_user_remove_days: about to edit message, text length={len(text)}")
        
        # Для remove_days не нужна кнопка "Подтвердить" - пользователь просто вводит количество дней
        # Нужна только кнопка "Отмена" для возврата к деталям пользователя
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=_("user_editor:button:cancel"),
                callback_data=f"{NavAdminTools.USER_DETAILS.value}_{tg_id}",
            )
        )
        
        keyboard = builder.as_markup()
        logger.info(f"callback_user_remove_days: keyboard created, type={type(keyboard)}")
        
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
        )
        logger.info(f"callback_user_remove_days: message edited successfully")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error starting remove_days for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_CHANGE_SERVER.value}_"),
    IsAdmin(),
)
async def callback_user_change_server(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    """Начать процесс смены сервера пользователя."""
    
    try:
        # Извлекаем tg_id из callback_data
        # Формат: user_change_server_{tg_id}
        prefix = f"{NavAdminTools.USER_CHANGE_SERVER.value}_"
        if not callback.data.startswith(prefix):
            logger.error(f"Invalid callback_data format: {callback.data}")
            await callback.answer(
                text=_("user_editor:message:error"),
                show_alert=True,
            )
            return
        
        tg_id_str = callback.data[len(prefix):]
        tg_id = int(tg_id_str)
        
        
        # Получаем список серверов и синхронизируем статусы
        # services уже передается через dependency injection
        await services.server_pool.sync_servers()
        
        # Загружаем серверы с их пользователями для вычисления current_clients
        from sqlalchemy.orm import selectinload
        servers_query = await session.execute(
            select(Server).options(selectinload(Server.users))
        )
        servers = servers_query.scalars().all()
        
        if not servers:
            await callback.answer(
                text=_("user_editor:message:no_servers"),
                show_alert=True,
            )
            return
        
        # Фильтруем только онлайн серверы с доступными слотами
        # current_clients теперь вычисляется из загруженных users без дополнительных SQL запросов
        online_servers = [s for s in servers if s.online and s.current_clients < s.max_clients]
        all_servers = servers  # Показываем все серверы для админа, но выделяем онлайн
        
        
        await state.update_data({"action_user_id": tg_id, "action_type": "change_server"})
        await state.set_state(UserSearchStates.change_server)
        
        # Формируем клавиатуру с серверами
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        from .keyboard import back_button
        
        builder = InlineKeyboardBuilder()
        admin_locale = user.language_code or "ru"
        for server in all_servers:
            status = "🟢" if server.online else "🔴"
            
            # Форматируем локацию с флагом и локализацией
            formatted_location = format_server_location(
                location=server.location,
                server_name=server.name,
                locale=admin_locale,
            )
            
            # Если локация не была локализована, используем оригинальное название
            if not formatted_location or formatted_location == (server.location or ""):
                location_info = f" ({server.location})" if server.location else ""
            else:
                location_info = f" ({formatted_location})"
            
            load_info = f" {server.current_clients}/{server.max_clients}"
            is_available = server.online and server.current_clients < server.max_clients
            availability_mark = " ✅" if is_available else " ⚠️"
            
            builder.row(
                InlineKeyboardButton(
                    text=f"{status}{availability_mark} {server.name}{location_info}{load_info}",
                    callback_data=f"{NavAdminTools.USER_CONFIRM_ACTION.value}_change_server_{tg_id}_{server.id}",
                )
            )
        
        builder.row(back_button(f"{NavAdminTools.USER_DETAILS.value}_{tg_id}"))
        
        text = _("user_editor:message:select_server")
        if online_servers:
            text += f"\n\n✅ Доступно серверов: {len(online_servers)}/{len(all_servers)}"
        logger.info(f"callback_user_change_server: about to edit message, text length={len(text)}")
        
        keyboard = builder.as_markup()
        logger.info(f"callback_user_change_server: keyboard created, type={type(keyboard)}")
        
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
        )
        logger.info(f"callback_user_change_server: message edited successfully")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error starting change server for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_DELETE_SUBSCRIPTION.value}_"),
    IsAdmin(),
)
async def callback_user_delete_subscription(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    services: ServicesContainer,
) -> None:
    """Удалить подписку пользователя."""
    
    try:
        # Извлекаем tg_id из callback_data
        # Формат: user_delete_subscription_{tg_id}
        prefix = f"{NavAdminTools.USER_DELETE_SUBSCRIPTION.value}_"
        if not callback.data.startswith(prefix):
            logger.error(f"Invalid callback_data format: {callback.data}")
            await callback.answer(
                text=_("user_editor:message:error"),
                show_alert=True,
            )
            return
        
        tg_id_str = callback.data[len(prefix):]
        tg_id = int(tg_id_str)
        
        user_service = UserManagementService(session)
        target_user = await user_service.get_user_details(tg_id)
        
        if not target_user:
            await callback.answer(
                text=_("user_editor:message:user_not_found"),
                show_alert=True,
            )
            return
        
        # Удаляем подписку через VPNService
        success = await user_service.delete_subscription(tg_id, services)
        
        if success:
            logger.info(f"callback_user_delete_subscription: subscription deleted successfully")
            await callback.answer(
                text=_("user_editor:message:subscription_deleted"),
                show_alert=True,
            )
            # Обновляем информацию о пользователе
            await callback_user_details(callback, user, session)
        else:
            logger.error(f"callback_user_delete_subscription: failed to delete subscription")
            await callback.answer(
                text=_("user_editor:message:error"),
                show_alert=True,
            )
        
    except Exception as e:
        logger.error(f"Error deleting subscription for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )


@router.message(UserSearchStates.extend_days, IsAdmin())
@router.message(UserSearchStates.add_bonus_days, IsAdmin())
@router.message(UserSearchStates.remove_days, IsAdmin())
async def process_days_input(
    message: Message,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    """Обработать ввод количества дней."""
    try:
        days_str = message.text.strip()
        
        if not is_valid_days(days_str):
            await message.answer(
                text=_("user_editor:message:invalid_days"),
            )
            return
        
        days = int(days_str)
        state_data = await state.get_data()
        tg_id = state_data.get("action_user_id")
        action_type = state_data.get("action_type")
        
        if not tg_id or not action_type:
            await message.answer(
                text=_("user_editor:message:error"),
            )
            await state.clear()
            return
        
        user_service = UserManagementService(session)
        target_user = await user_service.get_user_details(tg_id)
        
        if not target_user:
            await message.answer(
                text=_("user_editor:message:user_not_found"),
            )
            await state.clear()
            return
        
        success = False
        if action_type == "extend":
            success = await user_service.extend_subscription(
                tg_id, days, services=services
            )
        elif action_type == "add_bonus":
            success = await user_service.add_bonus_days(
                tg_id, days, services=services
            )
        elif action_type == "remove_days":
            success = await user_service.remove_days(
                tg_id, days, services=services
            )
        
        if success:
            if action_type == "extend":
                action_text = _("user_editor:action:extend").format(days=days)
            elif action_type == "add_bonus":
                action_text = _("user_editor:action:add_bonus").format(days=days)
            elif action_type == "remove_days":
                action_text = _("user_editor:action:remove_days").format(days=days)
            else:
                action_text = _("user_editor:action:extend").format(days=days)
            
            await message.answer(
                text=_("user_editor:message:action_success").format(action=action_text),
            )
        else:
            await message.answer(
                text=_("user_editor:message:action_failed"),
            )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error processing days input for admin {user.tg_id}: {e}", exc_info=True)
        await message.answer(
            text=_("user_editor:message:error"),
        )
        await state.clear()


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_CONFIRM_ACTION.value}_"),
    IsAdmin(),
)
async def callback_user_confirm_action(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    services: ServicesContainer,
) -> None:
    """Подтвердить действие над пользователем."""
    # Сохраняем tg_id в начале функции для использования в except блоке
    admin_tg_id = user.tg_id
    
    try:
        # Формат: user_confirm_action_{action}_{tg_id} или user_confirm_action_{action}_{tg_id}_{extra}
        # Примеры:
        # - user_confirm_action_extend_407926245
        # - user_confirm_action_add_bonus_407926245
        # - user_confirm_action_change_server_407926245_1
        
        prefix = f"{NavAdminTools.USER_CONFIRM_ACTION.value}_"
        if not callback.data.startswith(prefix):
            logger.error(f"Invalid callback_data format: {callback.data}")
            await callback.answer(
                text=_("user_editor:message:invalid_action"),
                show_alert=True,
            )
            return
        
        # Убираем префикс
        data_without_prefix = callback.data[len(prefix):]
        logger.info(f"callback_user_confirm_action: data_without_prefix={data_without_prefix}")
        
        # Разбираем оставшуюся часть
        # Формат: {action}_{tg_id} или {action}_{tg_id}_{extra}
        # Действие может содержать подчеркивания, поэтому разбираем с конца
        parts = data_without_prefix.split("_")
        
        if len(parts) < 2:
            logger.error(f"Invalid callback_data format: {callback.data}, parts={parts}")
            await callback.answer(
                text=_("user_editor:message:invalid_action"),
                show_alert=True,
            )
            return
        
        # Формат для change_server: change_server_{tg_id}_{server_id}
        # parts = ['change', 'server', '407926245', '2']
        # Последний элемент - это server_id (если есть extra)
        # Предпоследний элемент - это tg_id (если есть extra)
        
        logger.info(f"callback_user_confirm_action: parts={parts}, len={len(parts)}")
        
        # Проверяем, есть ли extra (для change_server)
        if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
            # Есть extra (server_id)
            # Формат: {action}_{tg_id}_{server_id}
            server_id = int(parts[-1])  # Последний - server_id
            tg_id = int(parts[-2])  # Предпоследний - tg_id
            action = "_".join(parts[:-2])  # Все кроме последних двух
            logger.info(f"callback_user_confirm_action: WITH EXTRA - action={action}, tg_id={tg_id}, server_id={server_id}")
        else:
            # Нет extra
            # Формат: {action}_{tg_id}
            tg_id = int(parts[-1])  # Последний - tg_id
            action = "_".join(parts[:-1])  # Все кроме последнего
            server_id = None
            logger.info(f"callback_user_confirm_action: action={action}, tg_id={tg_id}, server_id=None")
        
        user_service = UserManagementService(session)
        target_user = await user_service.get_user_details(tg_id)
        
        if not target_user:
            await callback.answer(
                text=_("user_editor:message:user_not_found"),
                show_alert=True,
            )
            return
        
        success = False
        action_text = ""
        
        if action == "change_server" and server_id is not None:
            # Синхронизируем статусы перед миграцией для получения актуальных данных
            await services.server_pool.sync_servers()
            
            # Проверяем количество клиентов пользователя перед миграцией
            from app.db.models import UserClient
            from sqlalchemy import select
            user_query_for_count = await session.execute(
                select(User).where(User.tg_id == tg_id)
            )
            user_for_count = user_query_for_count.scalar_one_or_none()
            if not user_for_count:
                await services.notification.show_popup(
                    callback=callback,
                    text=_("user_editor:message:user_not_found"),
                    cache_time=5,
                )
                return
            
            user_clients_count = await UserClient.count_by_user_id(session=session, user_id=user_for_count.id)
            if user_clients_count == 0:
                user_clients_count = 1  # Старая схема - один клиент
            
            # Проверяем доступность места на новом сервере
            await services.server_pool.sync_servers()
            # Перезагружаем сервер с актуальными данными
            from sqlalchemy.orm import selectinload
            server_query = await session.execute(
                select(Server).options(selectinload(Server.users)).where(Server.id == server_id)
            )
            new_server = server_query.scalar_one_or_none()
            
            if new_server and (new_server.current_clients + user_clients_count) > new_server.max_clients:
                await services.notification.show_popup(
                    callback=callback,
                    text=_("user_editor:popup:server_no_space"),
                    cache_time=10,
                )
                return
            
            try:
                success = await user_service.change_user_server(
                tg_id, server_id, services=services
            )
            except Exception as migration_error:
                logger.error(f"Error during server migration for user {tg_id}: {migration_error}", exc_info=True)
                await services.notification.show_popup(
                    callback=callback,
                    text=_("user_editor:message:error"),
                    cache_time=10,
                )
                return
            
            if success:
                action_text = _("user_editor:action:change_server")
                
                # Синхронизируем статусы после успешной миграции
                await services.server_pool.sync_servers()
                
                # Отправляем новый конфиг пользователю после успешной миграции
                try:
                    # Обновляем данные пользователя из БД для получения актуального server_id
                    from sqlalchemy.orm import selectinload
                    from sqlalchemy import select
                    user_query = await session.execute(
                        select(User)
                        .options(selectinload(User.server))
                        .where(User.tg_id == tg_id)
                    )
                    migrated_user = user_query.scalar_one_or_none()
                    
                    if migrated_user and migrated_user.server_id and migrated_user.server:
                        new_key = await services.vpn.get_key(migrated_user)
                        new_vless_key = await services.vpn.get_vless_key(migrated_user)
                        
                        # Формируем сообщение с новым конфигом
                        from aiogram.utils.i18n import I18n
                        current_i18n = I18n.get_current()
                        user_lang = migrated_user.language_code or "en"
                        if "-" in user_lang:
                            user_lang = user_lang.split("-")[0]
                        if user_lang not in ["ru", "en"]:
                            user_lang = "en"
                        
                        # Формируем сообщение с новым конфигом (без переопределения _)
                        vless_text = f"\n🔐 <b>Vless ключ:</b>\n<pre>{new_vless_key}</pre>\n" if new_vless_key else ""
                        config_message = _("user_editor:notification:server_changed").format(
                            new_key=new_key,
                            vless_text=vless_text
                        )
                        
                        await services.notification.notify_by_id(
                            chat_id=tg_id,
                            text=config_message,
                            duration=0,  # Не удалять сообщение
                        )
                        logger.info(f"Sent new config to user {tg_id} after server migration")
                except Exception as e:
                    logger.error(f"Failed to send new config to user {tg_id}: {e}", exc_info=True)
                    # Не критично, пользователь может получить конфиг через профиль
        elif action == "delete_subscription":
            # Удаление подписки обрабатывается отдельным обработчиком
            # Но если кто-то использует эту кнопку, обработаем здесь
            success = await user_service.delete_subscription(tg_id, services)
            if success:
                action_text = _("user_editor:action:delete_subscription")
        else:
            # Для extend и add_bonus не должно быть кнопки "Подтвердить"
            # Эти действия обрабатываются через FSM после ввода количества дней
            logger.warning(f"Unexpected action in confirm handler: {action}")
            await callback.answer(
                text=_("user_editor:message:invalid_action"),
                show_alert=True,
            )
            return
        
        if success and action_text:
            await callback.answer(
                text=_("user_editor:message:action_success").format(
                    action=action_text,
                ),
                show_alert=True,
            )
            # Обновляем информацию о пользователе
            # Обновляем сообщение напрямую, так как CallbackQuery - frozen объект и его нельзя изменить
            try:
                user_service = UserManagementService(session)
                # Загружаем пользователя с сервером для отображения
                from sqlalchemy.orm import selectinload
                from sqlalchemy import select
                user_with_server_query = await session.execute(
                    select(User)
                    .options(selectinload(User.server))
                    .where(User.tg_id == tg_id)
                )
                target_user = user_with_server_query.scalar_one_or_none()
                
                if target_user:
                    logger.info(f"[MIGRATION] Updating user details display for user {tg_id}, server_id={target_user.server_id}")
                    # Формируем детальную информацию (используем тот же формат, что и в callback_user_details)
                    from .keyboard import user_details_keyboard
                    try:
                        # Загружаем транзакции и рефералы, если они не загружены
                        if not hasattr(target_user, 'transactions') or target_user.transactions is None:
                            from sqlalchemy.orm import selectinload
                            user_with_relations_query = await session.execute(
                                select(User)
                                .options(
                                    selectinload(User.server),
                                    selectinload(User.transactions),
                                    selectinload(User.referrals_sent)
                                )
                                .where(User.tg_id == tg_id)
                            )
                            target_user = user_with_relations_query.scalar_one_or_none()
                        
                        # Безопасно получаем количество транзакций и рефералов
                        transactions_count = len(target_user.transactions) if hasattr(target_user, 'transactions') and target_user.transactions else 0
                        referrals_count = len(target_user.referrals_sent) if hasattr(target_user, 'referrals_sent') and target_user.referrals_sent else 0
                        
                        logger.debug(f"[MIGRATION] User details: transactions={transactions_count}, referrals={referrals_count}")
                        
                        text = _("user_editor:message:user_details").format(
                            tg_id=target_user.tg_id,
                            username=target_user.username or "N/A",
                            first_name=target_user.first_name or "N/A",
                            created_at=target_user.created_at.strftime("%Y-%m-%d %H:%M:%S") if target_user.created_at else "N/A",
                            server_name=target_user.server.name if target_user.server else "None",
                            server_location=target_user.server.location if target_user.server and target_user.server.location else "N/A",
                            transactions_count=transactions_count,
                            referrals_count=referrals_count,
                        )
                        keyboard = user_details_keyboard(target_user.tg_id)
                        logger.info(f"[MIGRATION] About to edit message with text length={len(text)}")
                        await callback.message.edit_text(
                            text=text,
                            reply_markup=keyboard,
                        )
                        logger.info(f"[MIGRATION] Successfully updated user details after migration for user {tg_id}")
                    except Exception as format_error:
                        logger.error(f"[MIGRATION] Error formatting user details: {format_error}", exc_info=True)
                        raise
            except Exception as details_error:
                logger.error(f"Failed to update user details after migration: {details_error}", exc_info=True)
                # Не критично, пользователь может обновить информацию вручную
        elif not success:
            await callback.answer(
                text=_("user_editor:message:action_failed"),
                show_alert=True,
            )
        
    except Exception as e:
        # Сохраняем tg_id до обращения к user, чтобы избежать MissingGreenlet
        admin_tg_id = getattr(user, 'tg_id', None) or (callback.from_user.id if callback.from_user else None)
        logger.error(f"Error confirming action for admin {admin_tg_id}: {e}", exc_info=True)
        try:
            await callback.answer(
                text=_("user_editor:message:error"),
                show_alert=True,
            )
        except Exception as e2:
            logger.error(f"Failed to send error message: {e2}")


@router.callback_query(
    F.data.startswith(f"{NavAdminTools.USER_CANCEL_ACTION.value}_"),
    IsAdmin(),
)
async def callback_user_cancel_action(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Отменить действие над пользователем."""
    try:
        tg_id_str = callback.data.split("_")[-1]
        tg_id = int(tg_id_str)
        
        await state.clear()
        
        # Возвращаемся к деталям пользователя
        await callback_user_details(callback, user, session)
        
    except Exception as e:
        logger.error(f"Error canceling action for admin {user.tg_id}: {e}", exc_info=True)
        await callback.answer(
            text=_("user_editor:message:error"),
            show_alert=True,
        )
