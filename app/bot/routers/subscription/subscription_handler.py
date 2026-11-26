import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.i18n import gettext as _
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.models import ClientData, ServicesContainer, SubscriptionData
from app.bot.payment_gateways import GatewayFactory
from app.bot.utils.navigation import NavSubscription
from app.config import Config
from app.db.models import Server, User

from .keyboard import (
    devices_keyboard,
    duration_keyboard,
    payment_method_keyboard,
    servers_selection_keyboard,
    subscription_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name=__name__)


async def show_subscription(
    callback: CallbackQuery,
    client_data: ClientData | None,
    callback_data: SubscriptionData,
) -> None:
    if client_data:

        if client_data.has_subscription_expired:
            text = _("subscription:message:expired")
        else:
            text = _("subscription:message:active").format(
                devices=client_data.max_devices,
                expiry_time=client_data.expiry_time,
            )
    else:
        text = _("subscription:message:not_active")

    await callback.message.edit_text(
        text=text,
        reply_markup=subscription_keyboard(
            has_subscription=client_data,
            callback_data=callback_data,
        ),
    )


@router.callback_query(F.data == NavSubscription.MAIN)
async def callback_subscription(
    callback: CallbackQuery,
    user: User,
    state: FSMContext,
    services: ServicesContainer,
) -> None:
    logger.info(f"[PURCHASE FLOW] STEP 1: MAIN - User {user.tg_id} opened subscription page")
    logger.info(f"User {user.tg_id} opened subscription page.")
    await state.set_state(None)

    client_data = None
    if user.server_id:
        client_data = await services.vpn.get_client_data(user)
        # Don't return error if client_data is None - show_subscription handles None client_data gracefully
        if not client_data:
            logger.info(f"[SUBSCRIPTION FLOW] INFO: No client data returned for user {user.tg_id} - will show subscription page without active subscription")

    try:
        callback_data = SubscriptionData(state=NavSubscription.PROCESS, user_id=user.tg_id)
        await show_subscription(callback=callback, client_data=client_data, callback_data=callback_data)
    except Exception as e:
        logger.error(f"[SUBSCRIPTION FLOW] ERROR: Error showing subscription: {e}", exc_info=True)
        await services.notification.show_popup(
            callback=callback,
            text=_("subscription:popup:error_fetching_data"),
        )


@router.callback_query(SubscriptionData.filter(F.state == NavSubscription.EXTEND))
async def callback_subscription_extend(
    callback: CallbackQuery,
    user: User,
    callback_data: SubscriptionData,
    config: Config,
    services: ServicesContainer,
    session: AsyncSession,
) -> None:
    logger.info(f"User {user.tg_id} started extend subscription.")
    logger.info(f"[EXTEND FLOW] STEP 1: EXTEND_STARTED - User {user.tg_id} started extend subscription, server_id={user.server_id}")
    
    # Get client data to determine current device count
    logger.info(f"[EXTEND FLOW] STEP 2: GET_CLIENT_DATA - Getting client data for user {user.tg_id}")
    client_data = await services.vpn.get_client_data(user)
    if not client_data:
        logger.error(f"[EXTEND FLOW] ERROR: Failed to get client data for user {user.tg_id}")
        await services.notification.show_popup(
            callback=callback,
            text=_("subscription:popup:error_fetching_data"),
        )
        return
    
    logger.info(f"[EXTEND FLOW] STEP 3: CLIENT_DATA_RECEIVED - Client data received: max_devices={client_data.max_devices}")
    # For multiple clients, max_devices is the count of clients
    # For single client, max_devices is limit_ip (or -1 for unlimited)
    current_devices = client_data.max_devices if client_data.max_devices > 0 else 1
    logger.info(f"[EXTEND FLOW] STEP 4: CURRENT_DEVICES - Current devices: {current_devices}")
    
    plan = await services.plan.get_plan(current_devices, session=session)
    if not plan:
        await services.notification.show_popup(
            callback=callback,
            text=_("subscription:popup:error_fetching_plan"),
        )
        return

    callback_data.devices = current_devices
    callback_data.state = NavSubscription.DURATION
    callback_data.is_extend = True
    durations = await services.plan.get_durations(session=session)
    await callback.message.edit_text(
        text=_("subscription:message:duration"),
        reply_markup=await duration_keyboard(
            plan_service=services.plan,
            callback_data=callback_data,
            currency=config.shop.CURRENCY,
            session=session,
        ),
    )


@router.callback_query(SubscriptionData.filter(F.state == NavSubscription.CHANGE))
async def callback_subscription_change(
    callback: CallbackQuery,
    user: User,
    callback_data: SubscriptionData,
    services: ServicesContainer,
    config: Config,
    session: AsyncSession,
) -> None:
    logger.info(f"User {user.tg_id} started change subscription.")
    callback_data.state = NavSubscription.DEVICES
    callback_data.is_change = True
    plans = await services.plan.get_all_plans(session=session)
    logger.info(f"User {user.tg_id}: Retrieved {len(plans)} plans for device selection (change): {[p.devices for p in plans]}")
    if not plans:
        logger.warning(f"User {user.tg_id}: No plans found! This will result in empty keyboard.")
    # Get custom offer URL safely (handle old config versions)
    custom_offer_url = getattr(config.shop, 'CUSTOM_OFFER_URL', None)
    await callback.message.edit_text(
        text=_("subscription:message:devices"),
        reply_markup=devices_keyboard(plans, callback_data, custom_offer_url=custom_offer_url),
    )


@router.callback_query(SubscriptionData.filter(F.state == NavSubscription.PROCESS))
async def callback_subscription_process(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    callback_data: SubscriptionData,
    services: ServicesContainer,
) -> None:
    logger.info(f"[PURCHASE FLOW] STEP 2: PROCESS - User {user.tg_id} started subscription process")
    logger.info(f"User {user.tg_id} started subscription process.")
    
    # Получаем все доступные серверы
    await services.server_pool.sync_servers()
    available_servers = await Server.get_all(session)
    
    # Фильтруем только онлайн серверы с доступными слотами
    online_servers = [
        server
        for server in available_servers
        if server.online and server.current_clients < server.max_clients
    ]
    
    
    if not online_servers:
        await services.notification.show_popup(
            callback=callback,
            text=_("subscription:popup:no_available_servers"),
            cache_time=120,
        )
        return
    
    # Показываем выбор серверов (всегда, даже если сервер один)
    callback_data.state = NavSubscription.SERVER_SELECTION
    user_locale = user.language_code or "ru"
    await callback.message.edit_text(
        text=_("subscription:message:select_server"),
        reply_markup=servers_selection_keyboard(online_servers, callback_data, locale=user_locale),
    )


@router.callback_query(SubscriptionData.filter(F.state == NavSubscription.SERVER_SELECTION))
async def callback_server_selected(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    callback_data: SubscriptionData,
    services: ServicesContainer,
    config: Config,
) -> None:
    logger.info(f"[PURCHASE FLOW] STEP 3: SERVER_SELECTION - User {user.tg_id} selected server: {callback_data.server_id}")
    logger.info(f"User {user.tg_id} selected server: {callback_data.server_id}")
    
    if not callback_data.server_id:
        await services.notification.show_popup(
            callback=callback,
            text=_("subscription:popup:invalid_server"),
            cache_time=5,
        )
        return
    
    # Проверяем, что сервер существует и доступен
    server = await Server.get_by_id(session=session, id=callback_data.server_id)
    if not server or not server.online or server.current_clients >= server.max_clients:
        await services.notification.show_popup(
            callback=callback,
            text=_("subscription:popup:server_unavailable"),
            cache_time=5,
        )
        # Возвращаемся к выбору серверов
        await services.server_pool.sync_servers()
        available_servers = await Server.get_all(session)
        online_servers = [
            s for s in available_servers
            if s.online and s.current_clients < s.max_clients
        ]
        if online_servers:
            user_locale = user.language_code or "ru"
            await callback.message.edit_text(
                text=_("subscription:message:select_server"),
                reply_markup=servers_selection_keyboard(online_servers, callback_data, locale=user_locale),
            )
        return
    
    callback_data.state = NavSubscription.DEVICES
    plans = await services.plan.get_all_plans(session=session)
    logger.info(f"User {user.tg_id}: Retrieved {len(plans)} plans for device selection: {[p.devices for p in plans]}")
    if not plans:
        logger.warning(f"User {user.tg_id}: No plans found! This will result in empty keyboard.")
    # Get custom offer URL safely (handle old config versions)
    custom_offer_url = getattr(config.shop, 'CUSTOM_OFFER_URL', None)
    await callback.message.edit_text(
        text=_("subscription:message:devices"),
        reply_markup=devices_keyboard(plans, callback_data, custom_offer_url=custom_offer_url),
    )


@router.callback_query(SubscriptionData.filter(F.state == NavSubscription.DEVICES))
async def callback_devices_selected(
    callback: CallbackQuery,
    user: User,
    callback_data: SubscriptionData,
    config: Config,
    services: ServicesContainer,
    session: AsyncSession,
) -> None:
    logger.info(f"[PURCHASE FLOW] STEP 4: DEVICES - User {user.tg_id} selected devices: {callback_data.devices}")
    logger.info(f"User {user.tg_id} selected devices: {callback_data.devices}")
    callback_data.state = NavSubscription.DURATION
    await callback.message.edit_text(
        text=_("subscription:message:duration"),
        reply_markup=await duration_keyboard(
            plan_service=services.plan,
            callback_data=callback_data,
            currency=config.shop.CURRENCY,
            session=session,
        ),
    )


@router.callback_query(SubscriptionData.filter(F.state == NavSubscription.DURATION))
async def callback_duration_selected(
    callback: CallbackQuery,
    user: User,
    callback_data: SubscriptionData,
    services: ServicesContainer,
    gateway_factory: GatewayFactory,
    session: AsyncSession,
) -> None:
    logger.info(f"[PURCHASE FLOW] STEP 5: DURATION - User {user.tg_id} selected duration: {callback_data.duration} days")
    logger.info(f"User {user.tg_id} selected duration: {callback_data.duration}")
    callback_data.state = NavSubscription.PAY
    plan = await services.plan.get_plan(callback_data.devices, session=session)
    if not plan:
        await services.notification.show_popup(
            callback=callback,
            text=_("subscription:popup:error_fetching_plan"),
        )
        return
    await callback.message.edit_text(
        text=_("subscription:message:payment_method"),
        reply_markup=payment_method_keyboard(
            plan=plan,
            callback_data=callback_data,
            gateways=gateway_factory.get_gateways(),
        ),
    )
