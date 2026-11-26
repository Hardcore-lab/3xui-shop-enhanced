from aiogram import Dispatcher
from aiohttp.web import Application, Request, Response

from app.bot.utils.constants import CONNECTION_WEBHOOK

from . import (
    admin_tools,
    download,
    main_menu,
    misc,
    profile,
    referral,
    subscription,
    support,
)


def include(app: Application, dispatcher: Dispatcher, *, connection_secret: str) -> None:
    async def connection_redirect(request: Request) -> Response:
        return await download.handler.redirect_to_connection(request, secret=connection_secret)

    app.router.add_get(CONNECTION_WEBHOOK, connection_redirect)
    
    # Router registration order is CRITICAL - routers are checked in order
    # More specific handlers MUST be registered before more general ones
    # to prevent callback collision
    #
    # IMPORTANT PRECEDENCE RULES:
    # 1. Error handler FIRST - catches all exceptions before other handlers
    # 2. Specific callback patterns before general patterns
    # 3. Language handler early - needs to catch language selection callbacks
    # 4. Admin handlers after user handlers to avoid conflicts
    #
    # Order rationale:
    # - misc.error_handler: Global exception handler, must be first
    # - misc.notification_handler: Handles notification close/redirect callbacks
    # - main_menu.language_handler: MUST be before main_menu.handler to intercept language callbacks
    # - Then feature handlers (profile, referral, support, download) in logical order
    # - subscription handlers grouped together (subscription_handler, payment_handler, etc.)
    # - admin_tools handlers last as they have IsDev/IsAdmin filters
    dispatcher.include_routers(
        misc.error_handler.router,
        misc.notification_handler.router,
        main_menu.language_handler.router,  # Register language handler FIRST to catch language callbacks
        main_menu.handler.router,
        profile.handler.router,
        referral.handler.router,
        support.handler.router,
        download.handler.router,
        subscription.subscription_handler.router,
        subscription.payment_handler.router,
        subscription.promocode_handler.router,
        subscription.trial_handler.router,
        subscription.expiry_offer_handler.router,
        admin_tools.admin_tools_handler.router,
        admin_tools.backup_handler.router,
        admin_tools.invites_handler.router,
        admin_tools.maintenance_handler.router,
        admin_tools.notification_handler.router,
        admin_tools.promocode_handler.router,
        admin_tools.restart_handler.router,
        admin_tools.server_handler.router,
        admin_tools.statistics_handler.router,
        admin_tools.user_handler.router,
    )
