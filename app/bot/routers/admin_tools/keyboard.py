import logging
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.i18n import gettext as _
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

from app.bot.routers.misc.keyboard import (
    back_button,
    back_to_main_menu_button,
    cancel_button,
)
from app.bot.utils.location import format_server_location
from app.bot.utils.navigation import NavAdminTools
from app.db.models import Server
from app.db.models.invite import Invite


def admin_tools_keyboard(is_dev: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if is_dev:
        builder.row(
            InlineKeyboardButton(
                text=_("admin_tools:button:server_management"),
                callback_data=NavAdminTools.SERVER_MANAGEMENT,
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=_("admin_tools:button:statistics"),
            callback_data=NavAdminTools.STATISTICS,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_tools:button:user_editor"),
            callback_data=NavAdminTools.USER_EDITOR,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_tools:button:invite_editor"),
            callback_data=NavAdminTools.INVITE_EDITOR,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_tools:button:promocode_editor"),
            callback_data=NavAdminTools.PROMOCODE_EDITOR,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_tools:button:notification"),
            callback_data=NavAdminTools.NOTIFICATION,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_tools:button:create_backup"),
            callback_data=NavAdminTools.CREATE_BACKUP,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_tools:button:maintenance_mode"),
            callback_data=NavAdminTools.MAINTENANCE_MODE,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_tools:button:restart_bot"),
            callback_data=NavAdminTools.RESTART_BOT,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_tools:button:test_button"),
            callback_data=NavAdminTools.TEST,
        )
    )

    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def statistics_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для страницы статистики."""
    builder = InlineKeyboardBuilder()
    builder.row(back_button(NavAdminTools.MAIN))
    return builder.as_markup()


def promocode_editor_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("promocode_editor:button:create"),
            callback_data=NavAdminTools.CREATE_PROMOCODE,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("promocode_editor:button:delete"),
            callback_data=NavAdminTools.DELETE_PROMOCODE,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("promocode_editor:button:edit"),
            callback_data=NavAdminTools.EDIT_PROMOCODE,
        )
    )

    builder.adjust(3)
    builder.row(back_button(NavAdminTools.MAIN))
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def promocode_duration_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    duration_options = [1, 7, 30, 90, 365]

    for duration in duration_options:
        duration_text = _("1 day", "{} days", duration).format(duration)
        button = InlineKeyboardButton(
            text=duration_text,
            callback_data=f"{duration}",
        )
        builder.row(button)

    builder.adjust(2)
    builder.row(back_button(NavAdminTools.PROMOCODE_EDITOR))
    return builder.as_markup()


def maintenance_mode_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    from app.bot.middlewares import MaintenanceMiddleware

    if MaintenanceMiddleware.active:
        builder.row(
            InlineKeyboardButton(
                text=_("maintenance_mode:button:disable"),
                callback_data=NavAdminTools.MAINTENANCE_MODE_DISABLE,
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text=_("maintenance_mode:button:enable"),
                callback_data=NavAdminTools.MAINTENANCE_MODE_ENABLE,
            )
        )

    builder.adjust(2)
    builder.row(back_button(NavAdminTools.MAIN))
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def servers_keyboard(servers: list, locale: str = "ru") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.add(
        InlineKeyboardButton(
            text=_("server_management:button:sync"),
            callback_data=NavAdminTools.SYNC_SERVERS,
        )
    )

    builder.add(
        InlineKeyboardButton(
            text=_("server_management:button:add"),
            callback_data=NavAdminTools.ADD_SERVER,
        )
    )

    server: Server
    for server in servers:
        status = "🟢" if server.online else "🔴"
        
        # Форматируем локацию с флагом и локализацией
        try:
            formatted_location = format_server_location(
                location=server.location,
                server_name=server.name,
                locale=locale,
            )
        except Exception as format_error:
            formatted_location = server.location or ""
        
        # Если локация не была локализована, используем оригинальное название
        if not formatted_location or formatted_location == (server.location or ""):
            location_info = f" ({server.location})" if server.location else ""
        else:
            location_info = f" ({formatted_location})"
        
        load_info = f" {server.current_clients}/{server.max_clients}"
        text = f"{status} {server.name}{location_info}{load_info}"
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=NavAdminTools.SHOW_SERVER + f"_{server.name}",
            )
        )

    builder.row(back_button(NavAdminTools.MAIN))
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def server_keyboard(server_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("server_management:button:ping"),
            callback_data=NavAdminTools.PING_SERVER + f"_{server_name}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("server_management:button:delete"),
            callback_data=NavAdminTools.DELETE_SERVER + f"_{server_name}",
        )
    )

    builder.adjust(2)
    builder.row(back_button(NavAdminTools.SERVER_MANAGEMENT))
    return builder.as_markup()


def confirm_delete_server_keyboard(server_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("server_management:button:confirm_delete"),
            callback_data=NavAdminTools.CONFIRM_DELETE_SERVER + f"_{server_name}",
        ),
    )
    builder.row(back_button(NavAdminTools.SHOW_SERVER + f"_{server_name}"))
    return builder.as_markup()


def confirm_add_server_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("server_management:button:confirm"),
            callback_data=NavAdminTools.CONFIRM_ADD_SERVER,
        )
    )

    builder.adjust(2)
    builder.row(back_button(NavAdminTools.ADD_SERVER_BACK))
    return builder.as_markup()


def notification_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("notification:button:send_to_user"),
            callback_data=NavAdminTools.SEND_NOTIFICATION_USER,
        ),
        InlineKeyboardButton(
            text=_("notification:button:send_to_all"),
            callback_data=NavAdminTools.SEND_NOTIFICATION_ALL,
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text=_("notification:button:last_notification"),
            callback_data=NavAdminTools.LAST_NOTIFICATION,
        )
    )

    builder.row(back_button(NavAdminTools.MAIN))
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def last_notification_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.add(
        InlineKeyboardButton(
            text=_("notification:button:edit"),
            callback_data=NavAdminTools.EDIT_NOTIFICATION,
        )
    )

    builder.add(
        InlineKeyboardButton(
            text=_("notification:button:delete"),
            callback_data=NavAdminTools.DELETE_NOTIFICATION,
        )
    )

    builder.row(back_button(NavAdminTools.NOTIFICATION))
    return builder.as_markup()


def confirm_send_notification_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("notification:button:confirm"),
            callback_data=NavAdminTools.CONFIRM_SEND_NOTIFICATION,
        )
    )
    builder.row(cancel_button(NavAdminTools.NOTIFICATION))
    return builder.as_markup()


def invite_editor_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("invite_editor:button:create_invite"),
            callback_data=NavAdminTools.CREATE_INVITE,
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=_("invite_editor:button:list_invites"),
            callback_data=NavAdminTools.LIST_INVITES,
        )
    )

    builder.row(back_button(NavAdminTools.MAIN))
    builder.row(back_to_main_menu_button())
    return builder.as_markup()


def invite_list_keyboard(
    invites: list[Invite], page: int = 0, limit: int = 5
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_invites = len(invites)
    start_idx = page * limit
    end_idx = min(start_idx + limit, total_invites)

    for i in range(start_idx, end_idx):
        invite = invites[i]
        builder.row(
            InlineKeyboardButton(
                text=f"{invite.name} ({invite.clicks} clicks)",
                callback_data=NavAdminTools.SHOW_INVITE_DETAILS + f"_{invite.id}",
            )
        )

    row = []
    if page > 0:
        row.append(
            InlineKeyboardButton(
                text=_("invite_editor:button:previous_page"),
                callback_data=NavAdminTools.SHOW_INVITE_PAGE + f"_{page-1}",
            )
        )

    if (page + 1) * limit < total_invites:
        row.append(
            InlineKeyboardButton(
                text=_("invite_editor:button:next_page"),
                callback_data=NavAdminTools.SHOW_INVITE_PAGE + f"_{page+1}",
            )
        )

    if row:
        builder.row(*row)

    builder.row(back_button(NavAdminTools.INVITE_EDITOR))

    return builder.as_markup()


def invite_details_keyboard(invite: Invite) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if invite.is_active:
        builder.row(
            InlineKeyboardButton(
                text=_("invite_editor:button:disable"),
                callback_data=NavAdminTools.TOGGLE_INVITE_STATUS + f"_{invite.id}",
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text=_("invite_editor:button:enable"),
                callback_data=NavAdminTools.TOGGLE_INVITE_STATUS + f"_{invite.id}",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=_("invite_editor:button:delete"),
            callback_data=NavAdminTools.CONFIRM_DELETE_INVITE + f"_{invite.id}",
        )
    )

    builder.row(back_button(NavAdminTools.LIST_INVITES))

    return builder.as_markup()


def confirm_delete_invite_keyboard(invite_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=_("invite_editor:button:confirm_delete"),
            callback_data=NavAdminTools.DELETE_INVITE + f"_{invite_id}",
        ),
    )
    builder.row(cancel_button(NavAdminTools.SHOW_INVITE_DETAILS + f"_{invite_id}"))
    return builder.as_markup()


def user_editor_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для редактора пользователей."""
    
    builder = InlineKeyboardBuilder()
    
    # Получаем переводы - они должны работать через SimpleI18nMiddleware
    search_text_translated = _("user_editor:button:search")
    export_text_translated = _("user_editor:button:export")
    
    # Если переводы не работают (возвращают ключи), используем fallback
    # Определяем локаль из контекста
    current_locale = "en"
    try:
        from aiogram.utils.i18n import I18n
        current_i18n = I18n.get_current()
        if current_i18n:
            try:
                current_locale = getattr(current_i18n, "_current_locale", None) or "en"
                # Нормализуем локаль (может быть "ru-RU" -> "ru")
                if current_locale and "-" in current_locale:
                    current_locale = current_locale.split("-")[0]
            except Exception:
                pass
    except Exception:
        pass
    
    if search_text_translated.startswith("user_editor:"):
        search_text_translated = _("user_editor:button:search")
    if export_text_translated.startswith("user_editor:"):
        export_text_translated = _("user_editor:button:export")
    
    list_text_translated = _("user_editor:button:list_all")
    if list_text_translated.startswith("user_editor:"):
        list_text_translated = _("user_editor:button:list_all")
    
    builder.row(
        InlineKeyboardButton(
            text=search_text_translated,
            callback_data=NavAdminTools.USER_SEARCH,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=list_text_translated,
            callback_data=NavAdminTools.USER_LIST,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=export_text_translated,
            callback_data=NavAdminTools.USER_EXPORT,
        )
    )
    
    builder.row(back_button(NavAdminTools.MAIN))
    return builder.as_markup()


def user_search_keyboard(page: int = 0, has_next: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура для поиска пользователей с пагинацией."""
    builder = InlineKeyboardBuilder()
    
    # Кнопки пагинации
    pagination_row = []
    if page > 0:
        pagination_row.append(
            InlineKeyboardButton(
                text=_("user_editor:button:previous_page"),
                callback_data=f"{NavAdminTools.USER_SEARCH}_page_{page-1}",
            )
        )
    if has_next:
        pagination_row.append(
            InlineKeyboardButton(
                text=_("user_editor:button:next_page"),
                callback_data=f"{NavAdminTools.USER_SEARCH}_page_{page+1}",
            )
        )
    
    if pagination_row:
        builder.row(*pagination_row)
    
    builder.row(back_button(NavAdminTools.USER_EDITOR))
    return builder.as_markup()


def user_filter_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для фильтров пользователей."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text=_("user_editor:filter:subscription_active"),
            callback_data=f"{NavAdminTools.USER_FILTER}_subscription_active",
        ),
        InlineKeyboardButton(
            text=_("user_editor:filter:subscription_expired"),
            callback_data=f"{NavAdminTools.USER_FILTER}_subscription_expired",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=_("user_editor:filter:date_today"),
            callback_data=f"{NavAdminTools.USER_FILTER}_date_today",
        ),
        InlineKeyboardButton(
            text=_("user_editor:filter:date_week"),
            callback_data=f"{NavAdminTools.USER_FILTER}_date_week",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=_("user_editor:filter:date_month"),
            callback_data=f"{NavAdminTools.USER_FILTER}_date_month",
        ),
        InlineKeyboardButton(
            text=_("user_editor:filter:date_all"),
            callback_data=f"{NavAdminTools.USER_FILTER}_date_all",
        ),
    )
    
    builder.row(back_button(NavAdminTools.USER_SEARCH))
    return builder.as_markup()


def user_details_keyboard(tg_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для детальной информации о пользователе."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text=_("user_editor:button:extend"),
            callback_data=f"{NavAdminTools.USER_EXTEND.value}_{tg_id}",
        ),
        InlineKeyboardButton(
            text=_("user_editor:button:add_bonus"),
            callback_data=f"{NavAdminTools.USER_ADD_BONUS.value}_{tg_id}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=_("user_editor:button:remove_days"),
            callback_data=f"{NavAdminTools.USER_REMOVE_DAYS.value}_{tg_id}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=_("user_editor:button:change_server"),
            callback_data=f"{NavAdminTools.USER_CHANGE_SERVER.value}_{tg_id}",
        ),
        InlineKeyboardButton(
            text=_("user_editor:button:delete_subscription"),
            callback_data=f"{NavAdminTools.USER_DELETE_SUBSCRIPTION.value}_{tg_id}",
        ),
    )
    
    builder.row(back_button(NavAdminTools.USER_SEARCH))
    return builder.as_markup()


def user_confirm_action_keyboard(action: str, tg_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения действия над пользователем."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text=_("user_editor:button:confirm"),
            callback_data=f"{NavAdminTools.USER_CONFIRM_ACTION.value}_{action}_{tg_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("user_editor:button:cancel"),
            callback_data=f"{NavAdminTools.USER_CANCEL_ACTION.value}_{tg_id}",
        )
    )
    
    return builder.as_markup()
