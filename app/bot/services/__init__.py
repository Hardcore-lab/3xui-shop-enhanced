from typing import TYPE_CHECKING

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.models import ServicesContainer
from app.bot.utils.cache import CacheManager
from app.config import Config

if TYPE_CHECKING:
    from redis.asyncio.client import Redis

from .client_operations import ClientOperationsService
from .invite_stats import InviteStatsService
from .metrics import MetricsService
from .notification import NotificationService
from .payment_stats import PaymentStatsService
from .plan import PlanService
from .referral import ReferralService
from .server_pool import ServerPoolService
from .subscription import SubscriptionService
from .vpn import VPNService


async def initialize(
    config: Config,
    session: async_sessionmaker,
    bot: Bot,
    redis: "Redis | None" = None,
) -> ServicesContainer:
    # Initialize cache manager
    cache = CacheManager(redis) if redis else CacheManager(None)
    
    server_pool = ServerPoolService(config=config, session=session)
    client_operations = ClientOperationsService(
        server_pool_service=server_pool,
        session_factory=session,
    )
    plan = PlanService(session_factory=session, redis=redis)
    vpn = VPNService(config=config, session=session, server_pool_service=server_pool, cache=cache)
    notification = NotificationService(config=config, bot=bot)
    referral = ReferralService(config=config, session_factory=session, vpn_service=vpn)
    subscription = SubscriptionService(config=config, session_factory=session, vpn_service=vpn)
    payment_stats = PaymentStatsService(session_factory=session)
    invite_stats = InviteStatsService(session_factory=session, payment_stats_service=payment_stats)

    return ServicesContainer(
        server_pool=server_pool,
        client_operations=client_operations,
        plan=plan,
        vpn=vpn,
        notification=notification,
        referral=referral,
        subscription=subscription,
        payment_stats=payment_stats,
        invite_stats=invite_stats,
        cache=cache,
    )
