import asyncio
import logging
from urllib.parse import urljoin

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.utils.i18n import I18n
from aiohttp.web import Application, _run_app, TCPSite
from aiohttp.web_runner import AppRunner
from redis.asyncio.client import Redis

from app import logger
from app.bot import filters, middlewares, routers, services, tasks
from app.bot.middlewares import MaintenanceMiddleware
from app.bot.models import ServicesContainer
from app.bot.payment_gateways import GatewayFactory
from app.bot.utils import commands
from app.bot.utils.constants import (
    BOT_STARTED_TAG,
    BOT_STOPPED_TAG,
    DEFAULT_LANGUAGE,
    I18N_DOMAIN,
    TELEGRAM_WEBHOOK,
)
from app.config import DEFAULT_BOT_HOST, DEFAULT_LOCALES_DIR, Config, load_config
from app.db.database import Database


def _verify_locales_at_startup(locales_dir) -> None:
    """Verify locales directory and compiled .mo files exist for i18n to work."""
    from pathlib import Path
    locales_path = Path(locales_dir)
    if not locales_path.is_dir():
        logging.error(f"Locales directory not found: {locales_path.resolve()}. Buttons may show translation keys.")
        return
    for lang in ("en", "ru"):
        mo_file = locales_path / lang / "LC_MESSAGES" / "bot.mo"
        if not mo_file.is_file():
            logging.error(f"Locale file not found: {mo_file}. Buttons may show translation keys for {lang}.")
    logging.debug(f"Locales verified: {locales_path.resolve()}")


async def on_shutdown(db: Database, bot: Bot, services: ServicesContainer) -> None:
    await services.notification.notify_developer(BOT_STOPPED_TAG)
    await commands.delete(bot)
    await bot.delete_webhook()
    await bot.session.close()
    await db.close()
    logging.info("Bot stopped.")


async def on_startup(
    config: Config,
    bot: Bot,
    services: ServicesContainer,
    db: Database,
    redis: Redis,
    i18n: I18n,
) -> None:
    # Disable webhook setup for polling mode
    # Delete webhook if exists
    try:
        webhook_info = await bot.get_webhook_info()
        if webhook_info.url:
            await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logging.error(f"Error deleting webhook: {e}")
    
    logging.info("Bot started in polling mode.")

    await services.notification.notify_developer(BOT_STARTED_TAG)

    tasks.transactions.start_scheduler(db.session)
    if config.shop.REFERRER_REWARD_ENABLED:
        tasks.referral.start_scheduler(
            session_factory=db.session, referral_service=services.referral
        )
    tasks.subscription_expiry.start_scheduler(
        session_factory=db.session,
        redis=redis,
        i18n=i18n,
        vpn_service=services.vpn,
        notification_service=services.notification,
    )


async def main() -> None:
    # Create web application
    app = Application()

    # Load configuration
    config = load_config()

    # Set up logging
    logger.setup_logging(config.logging)

    # Initialize database with appropriate driver
    # Use PostgreSQL if DB_HOST is configured, otherwise use SQLite
    if config.database.HOST:
        db_driver = "postgresql+asyncpg"
        logging.info("Using PostgreSQL database")
    else:
        db_driver = "sqlite+aiosqlite"
        logging.info("Using SQLite database")
    
    db = Database(config.database, driver=db_driver)
    await db.initialize()

    # Set up storage for FSM (Finite State Machine)
    storage = RedisStorage.from_url(url=config.redis.url())
    # storage = MemoryStorage()

    # Initialize the bot with the token and default properties
    bot = Bot(
        token=config.bot.TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML, link_preview_is_disabled=True
        ),
    )

    # Set up internationalization (i18n) - use absolute path so it works in Docker
    from pathlib import Path
    locales_path = Path(DEFAULT_LOCALES_DIR).resolve()
    i18n = I18n(
        path=str(locales_path),
        default_locale=DEFAULT_LANGUAGE,
        domain=I18N_DOMAIN,
    )
    I18n.set_current(i18n)
    _verify_locales_at_startup(locales_path)

    # Initialize services
    services_container = await services.initialize(
        config=config,
        session=db.session,
        bot=bot,
        redis=storage.redis,
    )

    # Initialize plans from JSON if needed
    # Check if all required plans (1-5 devices) exist and are active
    async with db.session() as init_session:
        from app.db.models import Plan
        from app.bot.services.plan_init import init_plans_from_json
        
        existing_plans = await Plan.get_all(session=init_session, include_inactive=True)
        existing_devices = {plan.devices for plan in existing_plans}
        required_devices = {1, 2, 3, 4, 5}  # All plans from plans.json

        active_plans = await Plan.get_all(session=init_session, include_inactive=False)
        active_devices = {plan.devices for plan in active_plans}
        missing_devices = required_devices - active_devices

        if not existing_plans or missing_devices:
            if missing_devices:
                logging.info(f"Missing active plans for devices: {missing_devices}. Initializing from JSON...")
            else:
                logging.info("No plans found in database, initializing from JSON...")
            await services_container.plan.clear_cache()
            await init_plans_from_json(session_factory=db.session)
            await services_container.plan.clear_cache()
        else:
            if len(active_devices) < 5:
                logging.info(f"Only {len(active_devices)} active plans found, reinitializing from JSON...")
                await services_container.plan.clear_cache()
                await init_plans_from_json(session_factory=db.session)
                await services_container.plan.clear_cache()
            else:
                logging.debug("All required plans exist. Clearing plans cache.")
                await services_container.plan.clear_cache()

    # Sync servers
    await services_container.server_pool.sync_servers()

    # Register payment gateways
    gateway_factory = GatewayFactory()
    gateway_factory.register_gateways(
        app=app,
        config=config,
        session=db.session,
        storage=storage,
        bot=bot,
        i18n=i18n,
        services=services_container,
    )

    # Create the dispatcher
    dispatcher = Dispatcher(
        db=db,
        storage=storage,
        config=config,
        bot=bot,
        services=services_container,
        gateway_factory=gateway_factory,
        redis=storage.redis,
        i18n=i18n,
    )

    # Register event handlers
    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)

    # Enable Maintenance mode for developing # WARNING: remove before production
    MaintenanceMiddleware.set_mode(False)

    # Register middlewares
    middlewares.register(dispatcher=dispatcher, i18n=i18n, session=db.session)

    # Register filters
    filters.register(
        dispatcher=dispatcher,
        developer_ids=config.bot.DEV_ID,
        admins_ids=config.bot.ADMINS,
    )

    # Include bot routers
    routers.include(app=app, dispatcher=dispatcher, connection_secret=config.bot.TOKEN)
    
    # Register health check routes
    from app.bot.routers.health import register_health_routes
    register_health_routes(app=app, session_factory=db.session, redis=storage.redis, services=services_container)
    
    # Register metrics routes
    from app.bot.services.metrics import register_metrics_routes
    register_metrics_routes(app=app)
    
    # Initialize metrics service and update metrics periodically
    from app.bot.services.metrics import MetricsService
    metrics_service = MetricsService(session_factory=db.session)
    
    # Update metrics on startup (in background, don't block startup)
    try:
        await metrics_service.update_all_metrics()
    except Exception as e:
        logging.warning(f"Failed to update metrics on startup: {e}")
    
    # Schedule periodic metrics updates (every 5 minutes)
    async def update_metrics_periodically():
        while True:
            await asyncio.sleep(300)  # 5 minutes
            try:
                await metrics_service.update_all_metrics()
            except Exception as e:
                logging.error(f"Error updating metrics: {e}", exc_info=True)
    
    # Start metrics update task
    asyncio.create_task(update_metrics_periodically())

    # Set up bot commands
    await commands.setup(bot)

    # Set up webhook request handler (disabled for polling)
    # webhook_requests_handler = SimpleRequestHandler(dispatcher=dispatcher, bot=bot)
    # webhook_requests_handler.register(app, path=TELEGRAM_WEBHOOK)

    # Start web server in background for health checks and metrics
    runner = AppRunner(app, access_log_format="%a %s %Tf")
    await runner.setup()
    site = TCPSite(runner, host=DEFAULT_BOT_HOST, port=config.bot.PORT)
    await site.start()
    logging.info(f"Web server started on {DEFAULT_BOT_HOST}:{config.bot.PORT} for health checks and metrics")
    
    # Use polling instead of webhook
    logging.info("Starting bot in polling mode...")
    # CRITICAL: Include "pre_checkout_query" in allowed_updates to receive payment pre-checkout queries
    try:
        await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"])
    finally:
        await site.stop()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
