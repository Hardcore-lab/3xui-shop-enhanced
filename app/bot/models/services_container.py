from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.bot.services import (
        ClientOperationsService,
        InviteStatsService,
        NotificationService,
        PaymentStatsService,
        PlanService,
        ReferralService,
        ServerPoolService,
        SubscriptionService,
        VPNService,
    )
    from app.bot.utils.cache import CacheManager

from dataclasses import dataclass


@dataclass
class ServicesContainer:
    server_pool: ServerPoolService
    client_operations: ClientOperationsService
    plan: PlanService
    vpn: VPNService
    notification: NotificationService
    referral: ReferralService
    subscription: SubscriptionService
    payment_stats: PaymentStatsService
    invite_stats: InviteStatsService
    cache: Optional[CacheManager] = None
