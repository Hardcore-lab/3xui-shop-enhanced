"""
Metrics service for Prometheus monitoring.
"""
import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from aiohttp import web
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import Server, Transaction, User, UserClient

if TYPE_CHECKING:
    from app.bot.models import ServicesContainer

logger = logging.getLogger(__name__)

# Metrics definitions
active_users_gauge = Gauge(
    "vpn_shopbot_active_users_total",
    "Total number of active users (with active subscription)",
)

total_users_gauge = Gauge(
    "vpn_shopbot_users_total",
    "Total number of registered users",
)

transactions_total = Counter(
    "vpn_shopbot_transactions_total",
    "Total number of transactions",
    ["status"],
)

servers_online_gauge = Gauge(
    "vpn_shopbot_servers_online_total",
    "Number of online servers",
)

servers_clients_gauge = Gauge(
    "vpn_shopbot_server_clients",
    "Number of clients per server",
    ["server_id", "server_name"],
)

api_request_duration = Histogram(
    "vpn_shopbot_api_request_duration_seconds",
    "API request duration in seconds",
    ["method", "endpoint"],
)

api_request_total = Counter(
    "vpn_shopbot_api_requests_total",
    "Total number of API requests",
    ["method", "endpoint", "status"],
)

active_subscriptions_gauge = Gauge(
    "vpn_shopbot_active_subscriptions_total",
    "Total number of active subscriptions",
)

multi_device_users_gauge = Gauge(
    "vpn_shopbot_multi_device_users_total",
    "Number of users with multiple devices",
)

migrations_total = Counter(
    "vpn_shopbot_migrations_total",
    "Total number of user migrations",
    ["status"],  # success, failed
)

subscription_deletions_total = Counter(
    "vpn_shopbot_subscription_deletions_total",
    "Total number of subscription deletions",
    ["status"],  # success, failed
)

operation_duration = Histogram(
    "vpn_shopbot_operation_duration_seconds",
    "Duration of critical operations in seconds",
    ["operation"],  # purchase, deletion, migration, extend, change
)


class MetricsService:
    """Service for collecting and exposing metrics."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory
        logger.info("Metrics Service initialized.")

    async def update_user_metrics(self) -> None:
        """Update user-related metrics."""
        try:
            async with self.session_factory() as session:
                # Total users
                total_users_query = await session.execute(select(func.count(User.id)))
                total_users = total_users_query.scalar() or 0
                total_users_gauge.set(total_users)

                # Active users (with server_id)
                active_users_query = await session.execute(
                    select(func.count(User.id)).where(User.server_id.isnot(None))
                )
                active_users = active_users_query.scalar() or 0
                active_users_gauge.set(active_users)

                logger.debug(f"Updated user metrics: total={total_users}, active={active_users}")
        except Exception as e:
            logger.error(f"Error updating user metrics: {e}", exc_info=True)

    async def update_server_metrics(self) -> None:
        """Update server-related metrics."""
        try:
            async with self.session_factory() as session:
                servers = await Server.get_all(session=session)
                online_count = sum(1 for server in servers if server.online)
                servers_online_gauge.set(online_count)

                # Reset all server client gauges
                servers_clients_gauge.clear()

                # Set client counts for each server
                for server in servers:
                    servers_clients_gauge.labels(
                        server_id=str(server.id), server_name=server.name
                    ).set(server.current_clients)

                logger.debug(f"Updated server metrics: online={online_count}, total={len(servers)}")
        except Exception as e:
            logger.error(f"Error updating server metrics: {e}", exc_info=True)

    async def update_transaction_metrics(self) -> None:
        """Update transaction-related metrics."""
        try:
            async with self.session_factory() as session:
                from app.bot.utils.constants import TransactionStatus

                # Note: Prometheus counters are cumulative, so we don't reset them
                # We just track new transactions as they happen
                # This method can be used to verify counts if needed
                logger.debug("Transaction metrics are updated incrementally on each transaction")
        except Exception as e:
            logger.error(f"Error updating transaction metrics: {e}", exc_info=True)

    async def update_all_metrics(self) -> None:
        """Update all metrics."""
        await self.update_user_metrics()
        await self.update_server_metrics()
        await self.update_transaction_metrics()

    @asynccontextmanager
    async def track_api_request(self, method: str, endpoint: str):
        """Context manager to track API request duration and count."""
        start_time = time.time()
        status = "success"
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            duration = time.time() - start_time
            api_request_duration.labels(method=method, endpoint=endpoint).observe(duration)
            api_request_total.labels(method=method, endpoint=endpoint, status=status).inc()


async def metrics_handler(request: web.Request) -> web.Response:
    """
    Handler for /metrics endpoint.
    Returns Prometheus metrics in OpenMetrics format.
    """
    try:
        metrics_data = generate_latest()
        # CONTENT_TYPE_LATEST is "application/openmetrics-text; version=1.0.0; charset=utf-8"
        # aiohttp doesn't accept charset in content_type, so we extract just the base type
        content_type_base = CONTENT_TYPE_LATEST.split(';')[0].strip()
        return web.Response(
            body=metrics_data,
            content_type=content_type_base,
        )
    except Exception as e:
        logger.error(f"Error generating metrics: {e}", exc_info=True)
        return web.Response(
            body=b"# Error generating metrics\n",
            content_type="text/plain",
            status=500,
        )


def register_metrics_routes(app: web.Application) -> None:
    """Register metrics endpoint."""
    app.router.add_get("/metrics", metrics_handler)
    logger.info("Metrics endpoint registered at /metrics")

