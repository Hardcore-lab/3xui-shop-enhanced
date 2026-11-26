import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.i18n import gettext as _
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsDev
from app.bot.models import ServicesContainer
from app.bot.routers.misc.keyboard import back_keyboard
from app.bot.utils.constants import (
    MAIN_MESSAGE_ID_KEY,
    SERVER_HOST_KEY,
    SERVER_MAX_CLIENTS_KEY,
    SERVER_NAME_KEY,
    SERVER_USERNAME_KEY,
    SERVER_PASSWORD_KEY,
    SERVER_LOCATION_KEY,
)
from app.bot.utils.navigation import NavAdminTools
from app.bot.utils.network import ping_url
from app.bot.utils.validation import (
    is_valid_client_count,
    is_valid_host,
    is_valid_location,
    is_valid_password,
    is_valid_username,
)
from app.config import Config
from app.db.models import Server, User

from .keyboard import confirm_add_server_keyboard, confirm_delete_server_keyboard, server_keyboard, servers_keyboard

logger = logging.getLogger(__name__)
router = Router(name=__name__)


class AddServerStates(StatesGroup):
    name = State()
    host = State()
    max_clients = State()
    location = State()
    username = State()
    password = State()
    confirmation = State()


@router.callback_query(F.data == NavAdminTools.SERVER_MANAGEMENT, IsDev())
async def callback_server_management(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    logger.info(f"Dev {user.tg_id} opened servers.")
    await state.set_state(None)
    
    # Автоматическая синхронизация статуса серверов в реальном времени
    await services.server_pool.sync_servers()
    
    text = _("server_management:message:main")
    # Перезагружаем серверы после синхронизации для получения актуальных данных
    servers = await Server.get_all(session)

    if not servers:
        text += _("server_management:message:empty")
    else:
        # Добавляем статистику с актуальными данными
        online_count = sum(1 for s in servers if s.online)
        # current_clients - это hybrid_property, возвращающий int (len(users))
        total_clients = sum(int(s.current_clients) for s in servers)
        total_max = sum(int(s.max_clients) for s in servers)
        text += f"\n\n{_('server_management:message:statistics_header')}\n"
        text += _("server_management:message:statistics_online").format(online_count=online_count, total_servers=len(servers)) + "\n"
        text += _("server_management:message:statistics_clients").format(total_clients=total_clients, total_max=total_max)

    user_locale = user.language_code or "ru"
    try:
        keyboard = servers_keyboard(servers, locale=user_locale)
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    except TelegramBadRequest as e:
        # Игнорируем ошибку "message is not modified" - это нормально, если содержимое не изменилось
        if "message is not modified" in str(e).lower():
            # Это нормально - сообщение уже актуально, просто игнорируем
            pass
        else:
            # Другие ошибки TelegramBadRequest - логируем и пытаемся отправить новое сообщение
            logger.error(f"Failed to edit message in callback_server_management: {e}")
            # Попытка отправить новое сообщение
            try:
                keyboard = servers_keyboard(servers, locale=user_locale)
                await callback.message.answer(text=text, reply_markup=keyboard)
            except Exception as e2:
                logger.error(f"Failed to send new message: {e2}")
    except Exception as e:
        logger.error(f"Failed to edit message in callback_server_management: {e}")
        # Попытка отправить новое сообщение
        try:
            keyboard = servers_keyboard(servers, locale=user_locale)
            await callback.message.answer(text=text, reply_markup=keyboard)
        except Exception as e2:
            logger.error(f"Failed to send new message: {e2}")


@router.callback_query(F.data == NavAdminTools.SYNC_SERVERS, IsDev())
async def callback_sync_servers(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    logger.info(f"Dev {user.tg_id} sync servers.")
    
    # Show loading indicator
    try:
        await callback.message.edit_text(_("common:loading:syncing_servers"))
    except Exception:
        pass
    
    await services.server_pool.sync_servers()

    try:
        await callback_server_management(callback=callback, user=user, session=session, state=state, services=services)
    except Exception:
        pass

    await services.notification.show_popup(
        callback=callback,
        text=_("server_management:popup:synced"),
    )


# region Add Server
async def show_add_server(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    data = await state.get_data()
    main_message_id = data.get(MAIN_MESSAGE_ID_KEY)

    text = _("server_management:message:add")
    name = _("server_management:message:name").format(server_name=data.get(SERVER_NAME_KEY))
    host = _("server_management:message:host").format(server_host=data.get(SERVER_HOST_KEY))
    max_clients = _("server_management:message:max_clients").format(
        server_max_clients=data.get(SERVER_MAX_CLIENTS_KEY)
    )
    location = _("server_management:message:location").format(
        server_location=data.get(SERVER_LOCATION_KEY) or _("server_management:message:not_set")
    )
    username = _("server_management:message:username").format(
        server_username=data.get(SERVER_USERNAME_KEY) or _("server_management:message:not_set")
    )
    password = _("server_management:message:password").format(
        server_password="***" if data.get(SERVER_PASSWORD_KEY) else _("server_management:message:not_set")
    )
    reply_markup = back_keyboard(NavAdminTools.ADD_SERVER_BACK)

    match current_state:
        case AddServerStates.name:
            text += _("server_management:message:enter_name")
            reply_markup = back_keyboard(NavAdminTools.SERVER_MANAGEMENT)
        case AddServerStates.host:
            text += name + "\n"
            text += _("server_management:message:enter_host")
        case AddServerStates.max_clients:
            text += name + host + "\n"
            text += _("server_management:message:enter_max_clients")
        case AddServerStates.location:
            text += name + host + max_clients + "\n"
            text += _("server_management:message:enter_location")
        case AddServerStates.username:
            text += name + host + max_clients + location + "\n"
            text += _("server_management:message:enter_username")
        case AddServerStates.password:
            text += name + host + max_clients + location + username + "\n"
            text += _("server_management:message:enter_password")
        case AddServerStates.confirmation:
            text += name + host + max_clients + location + username + password + "\n"
            text += _("server_management:message:confirm")
            reply_markup = confirm_add_server_keyboard()

    # Если main_message_id отсутствует, отправляем новое сообщение и сохраняем его ID
    if main_message_id is None:
        sent_message = await message.bot.send_message(
            text=text,
            chat_id=message.chat.id,
            reply_markup=reply_markup,
        )
        await state.update_data({MAIN_MESSAGE_ID_KEY: sent_message.message_id})
    else:
        try:
            await message.bot.edit_message_text(
                text=text,
                chat_id=message.chat.id,
                message_id=main_message_id,
                reply_markup=reply_markup,
            )
        except Exception as e:
            # Если редактирование не удалось, отправляем новое сообщение
            sent_message = await message.bot.send_message(
                text=text,
                chat_id=message.chat.id,
                reply_markup=reply_markup,
            )
            await state.update_data({MAIN_MESSAGE_ID_KEY: sent_message.message_id})


@router.callback_query(StateFilter("*"), F.data == NavAdminTools.ADD_SERVER_BACK, IsDev())
async def callback_add_server_back(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()

    match current_state:
        case AddServerStates.host:
            await state.set_state(AddServerStates.name)
        case AddServerStates.max_clients:
            await state.set_state(AddServerStates.host)
        case AddServerStates.location:
            await state.set_state(AddServerStates.max_clients)
        case AddServerStates.username:
            await state.set_state(AddServerStates.location)
        case AddServerStates.password:
            await state.set_state(AddServerStates.username)
        case AddServerStates.confirmation:
            await state.set_state(AddServerStates.password)

    await show_add_server(message=callback.message, state=state)


@router.callback_query(F.data == NavAdminTools.ADD_SERVER, IsDev())
async def callback_add_server(callback: CallbackQuery, user: User, state: FSMContext) -> None:
    logger.info(f"Dev {user.tg_id} started adding server.")
    # Сохраняем message_id для последующего редактирования
    if callback.message:
        await state.update_data({MAIN_MESSAGE_ID_KEY: callback.message.message_id})
    await state.set_state(AddServerStates.name)
    await show_add_server(message=callback.message, state=state)


@router.message(AddServerStates.name, IsDev())
async def message_name(
    message: Message,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    server_name = message.text.strip()
    logger.info(f"Dev {user.tg_id} entered server name: {server_name}")
    existing_server = await Server.get_by_name(session=session, name=server_name)

    if not existing_server:
        await state.set_state(AddServerStates.host)
        await state.update_data({SERVER_NAME_KEY: server_name})
        await show_add_server(message=message, state=state)
    else:
        await services.notification.notify_by_message(
            message=message,
            text=_("server_management:ntf:name_exists"),
            duration=5,
        )


@router.message(AddServerStates.host, IsDev())
async def message_host(
    message: Message,
    user: User,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    server_host = message.text.strip()
    logger.info(f"Dev {user.tg_id} entered server host: {server_host}")

    if is_valid_host(server_host):
        await state.set_state(AddServerStates.max_clients)
        await state.update_data({SERVER_HOST_KEY: server_host})
        await show_add_server(message=message, state=state)
    else:
        await services.notification.notify_by_message(
            message=message,
            text=_("server_management:ntf:invalid_host"),
            duration=5,
        )


@router.message(AddServerStates.max_clients, IsDev())
async def message_max_clients(
    message: Message,
    user: User,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    server_max_clients = message.text.strip()
    logger.info(f"Dev {user.tg_id} entered server max clients: {server_max_clients}")

    if is_valid_client_count(server_max_clients):
        await state.set_state(AddServerStates.location)
        await state.update_data({SERVER_MAX_CLIENTS_KEY: server_max_clients})
        await show_add_server(message=message, state=state)
    else:
        await services.notification.notify_by_message(
            message=message,
            text=_("server_management:ntf:invalid_max_clients"),
            duration=5,
        )


@router.message(AddServerStates.location, IsDev())
async def message_location(
    message: Message,
    user: User,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    server_location = message.text.strip()
    logger.info(f"Dev {user.tg_id} entered server location: {server_location}")

    # Пропуск поля
    if server_location.lower() == "/skip":
        await state.set_state(AddServerStates.username)
        await state.update_data({SERVER_LOCATION_KEY: None})
        await show_add_server(message=message, state=state)
        return

    is_valid = is_valid_location(server_location)
    if is_valid:
        await state.set_state(AddServerStates.username)
        await state.update_data({SERVER_LOCATION_KEY: server_location})
        await show_add_server(message=message, state=state)
    else:
        await services.notification.notify_by_message(
            message=message,
            text=_("server_management:ntf:invalid_location"),
            duration=5,
        )


@router.message(AddServerStates.username, IsDev())
async def message_username(
    message: Message,
    user: User,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    server_username = message.text.strip()
    logger.info(f"Dev {user.tg_id} entered server username: {server_username}")

    # Пропуск поля
    if server_username.lower() == "/skip":
        await state.set_state(AddServerStates.password)
        await state.update_data({SERVER_USERNAME_KEY: None})
        await show_add_server(message=message, state=state)
        return

    if is_valid_username(server_username):
        await state.set_state(AddServerStates.password)
        await state.update_data({SERVER_USERNAME_KEY: server_username})
        await show_add_server(message=message, state=state)
    else:
        await services.notification.notify_by_message(
            message=message,
            text=_("server_management:ntf:invalid_username"),
            duration=5,
        )


@router.message(AddServerStates.password, IsDev())
async def message_password(
    message: Message,
    user: User,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    server_password = message.text.strip()
    logger.info(f"Dev {user.tg_id} entered server password")

    # Пропуск поля
    if server_password.lower() == "/skip":
        await state.set_state(AddServerStates.confirmation)
        await state.update_data({SERVER_PASSWORD_KEY: None})
        await show_add_server(message=message, state=state)
        return

    if is_valid_password(server_password):
        await state.set_state(AddServerStates.confirmation)
        await state.update_data({SERVER_PASSWORD_KEY: server_password})
        await show_add_server(message=message, state=state)
    else:
        await services.notification.notify_by_message(
            message=message,
            text=_("server_management:ntf:invalid_password"),
            duration=5,
        )


@router.callback_query(AddServerStates.confirmation, IsDev())
async def callback_confirmation(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
    config: Config,
) -> None:
    logger.info(f"Dev {user.tg_id} confirmed adding server.")
    data = await state.get_data()

    # Проверка подключения к серверу
    # Не показываем промежуточный popup, так как callback.answer() можно вызвать только один раз
    # Покажем только финальный результат с пингом
    host = data.get(SERVER_HOST_KEY)
    username = data.get(SERVER_USERNAME_KEY) or config.xui.USERNAME
    password = data.get(SERVER_PASSWORD_KEY) or config.xui.PASSWORD

    try:
        from py3xui import AsyncApi
        test_api = AsyncApi(
            host=host,
            username=username,
            password=password,
            token=config.xui.TOKEN,
            logger=logger,
        )
        await test_api.login()
        connection_ok = True
        
        # Проверяем пинг сервера
        ping_result = await ping_url(host)
        
        # Показываем popup об успешной проверке подключения и пинге
        if ping_result:
            popup_text = _("server_management:popup:connection_test_success") + _("server_management:message:ping_result").format(ping=ping_result)
            await services.notification.show_popup(
                callback=callback,
                text=popup_text,
                cache_time=3,
            )
        else:
            popup_text = _("server_management:popup:connection_test_success") + _("server_management:message:ping_unavailable")
            await services.notification.show_popup(
                callback=callback,
                text=popup_text,
                cache_time=3,
            )
    except Exception as exception:
        logger.warning(f"Connection test failed for {host}: {exception}")
        connection_ok = False

    if not connection_ok:
        await services.notification.show_popup(
            callback=callback,
            text=_("server_management:popup:connection_test_failed"),
            cache_time=5,
        )
        return

    # Создаем сервер
    try:
        server = await Server.create(
            session=session,
            name=data.get(SERVER_NAME_KEY),
            host=host,
            max_clients=data.get(SERVER_MAX_CLIENTS_KEY),
            location=data.get(SERVER_LOCATION_KEY) or None,
            username=data.get(SERVER_USERNAME_KEY) or None,
            password=data.get(SERVER_PASSWORD_KEY) or None,
            online=True,  # Устанавливаем онлайн, так как подключение успешно
        )
    except Exception as create_error:
        logger.error(f"Error creating server: {create_error}", exc_info=True)
        await services.notification.show_popup(
            callback=callback,
            text=_("server_management:popup:add_failed"),
        )
        return

    if server:
        try:
            await services.server_pool.sync_servers()
            await state.set_state(None)
            try:
                await callback_server_management(callback=callback, user=user, session=session, state=state, services=services)
            except Exception as e:
                logger.error(f"Error updating server management view after adding server: {e}", exc_info=True)
                # Попытка отправить новое сообщение вместо редактирования
                try:
                    user_locale = user.language_code or "ru"
                    await callback.message.answer(
                        text=_("server_management:message:main") + "\n\n" + _("server_management:message:server_added_successfully").format(server_name=server.name),
                        reply_markup=servers_keyboard(await Server.get_all(session), locale=user_locale)
                    )
                except Exception as e2:
                    logger.error(f"Failed to send new message: {e2}")
            await services.notification.show_popup(
                callback=callback,
                text=_("server_management:popup:added_success"),
            )
        except Exception as e:
            logger.error(f"Unexpected error after server creation: {e}", exc_info=True)
            await services.notification.show_popup(
                callback=callback,
                text=_("server_management:popup:added_success") + _("server_management:error:interface_update_failed"),
            )

    else:
        await services.notification.show_popup(
            callback=callback,
            text=_("server_management:popup:add_failed"),
        )


# endregion


# region Server
@router.callback_query(F.data.startswith(NavAdminTools.SHOW_SERVER), IsDev())
async def callback_show_server(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
) -> None:
    server_name = callback.data.removeprefix(NavAdminTools.SHOW_SERVER + "_")
    logger.info(f"Dev {user.tg_id} open server {server_name}.")
    server = await Server.get_by_name(session=session, name=server_name)
    
    if not server:
        await callback.answer(_("server_management:popup:server_not_found"), show_alert=True)
        return
    
    status = (
        _("server_management:message:status_online")
        if server.online
        else _("server_management:message:status_offline")
    )
    
    # Вычисляем загрузку в процентах
    load_percent = int((server.current_clients / server.max_clients * 100)) if server.max_clients > 0 else 0
    
    # Получаем пинг сервера
    ping_result = await ping_url(server.host)
    ping_text = _("server_management:message:ping_result").format(ping=ping_result) if ping_result else _("server_management:message:ping_status_unavailable")
    
    # Формируем информацию о сервере
    text = _("server_management:message:server_info").format(
        server_name=server.name,
        host=server.host,
        status=status,
        clients=server.current_clients,
        max_clients=server.max_clients,
    )
    
    # Добавляем дополнительную информацию
    if server.location:
        text += f"\n<b>{_('server_management:label:location')}</b> {server.location}"
    
    if server.username:
        masked_username = server.username[:3] + "***" if len(server.username) > 3 else "***"
        text += f"\n<b>{_('server_management:label:username')}</b> {masked_username}"
    
    text += f"\n<b>{_('server_management:label:load')}</b> {load_percent}% ({server.current_clients}/{server.max_clients})"
    text += f"\n<b>{_('server_management:label:ping')}</b> {ping_text}"
    
    await callback.message.edit_text(
        text=text,
        reply_markup=server_keyboard(server_name),
    )


@router.callback_query(F.data.startswith(NavAdminTools.PING_SERVER), IsDev())
async def callback_ping_server(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    services: ServicesContainer,
) -> None:
    server_name = callback.data.removeprefix(NavAdminTools.PING_SERVER + "_")
    logger.info(f"Dev {user.tg_id} pinging server {server_name}.")
    
    # Show loading indicator in popup
    await services.notification.show_popup(
        callback=callback,
        text=_("common:loading:pinging_server"),
    )
    
    server = await Server.get_by_name(session=session, name=server_name)
    ping = await ping_url(server.host)
    # online = True if ping else False
    # if online != server.online:
    #    await Server.update(session=session, name=server.name, online=online)
    if ping:
        await services.notification.show_popup(
            callback=callback,
            text=_("server_management:popup:ping").format(server_name=server_name, ping=ping),
        )
    else:
        await services.notification.show_popup(
            callback=callback,
            text=_("server_management:popup:ping_failed").format(server_name=server_name),
        )


@router.callback_query(F.data.startswith(NavAdminTools.DELETE_SERVER), IsDev())
async def callback_delete_server(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    server_name = callback.data.removeprefix(NavAdminTools.DELETE_SERVER + "_")
    logger.info(f"Dev {user.tg_id} requesting deletion confirmation for server {server_name}.")
    
    server = await Server.get_by_name(session=session, name=server_name)
    
    if not server:
        await services.notification.show_popup(
            callback=callback,
            text=_("server_management:popup:server_not_found"),
        )
        return
    
    # Формируем сообщение подтверждения с информацией о сервере
    status = (
        _("server_management:message:status_online")
        if server.online
        else _("server_management:message:status_offline")
    )
    
    text = _("server_management:message:confirm_delete").format(
        server_name=server.name,
        host=server.host,
        status=status,
        clients=server.current_clients,
        max_clients=server.max_clients,
    )
    
    if server.location:
        text += f"\n<b>{_('server_management:label:location')}</b> {server.location}"
    
    await callback.message.edit_text(
        text=text,
        reply_markup=confirm_delete_server_keyboard(server_name),
    )


@router.callback_query(F.data.startswith(NavAdminTools.CONFIRM_DELETE_SERVER), IsDev())
async def callback_confirm_delete_server(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    server_name = callback.data.removeprefix(NavAdminTools.CONFIRM_DELETE_SERVER + "_")
    logger.info(f"Dev {user.tg_id} confirmed deletion of server {server_name}.")
    deleted = await Server.delete(session=session, name=server_name)
    
    if deleted:
        await services.server_pool.sync_servers()
        await services.notification.show_popup(
            callback=callback,
            text=_("server_management:popup:deleted_success"),
        )
    else:
        await services.notification.show_popup(
            callback=callback,
            text=_("server_management:popup:delete_failed"),
        )
    
    try:
        await callback_server_management(callback=callback, user=user, session=session, state=state, services=services)
    except Exception as e:
        logger.error(f"Error updating server management view after deletion: {e}")
        try:
            user_locale = user.language_code or "ru"
            deletion_msg = _("server_management:message:server_deleted").format(server_name=server_name) if deleted else _("server_management:error:server_delete_failed").format(server_name=server_name)
            await callback.message.answer(
                text=_("server_management:message:main") + f"\n\n{deletion_msg}",
                reply_markup=servers_keyboard(await Server.get_all(session), locale=user_locale)
            )
        except Exception as e2:
            logger.error(f"Failed to send new message: {e2}")


# endregion
