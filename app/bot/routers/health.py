"""
Health check endpoints for monitoring and load balancers.
"""
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from aiohttp import web
from aiohttp.web import Request, Response
from redis.asyncio.client import Redis
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Plan, Server, User, UserClient

if TYPE_CHECKING:
    from app.bot.models import ServicesContainer

logger = logging.getLogger(__name__)


async def health_check(
    request: Request,
    session_factory: async_sessionmaker,
    redis: Redis | None = None,
) -> Response:
    """
    Basic health check endpoint.
    Returns 200 if service is healthy, 503 if unhealthy.
    """
    try:
        # Check database connection
        async with session_factory() as session:
            await session.execute(select(1))
        
        # Check Redis connection if available
        if redis:
            await redis.ping()
        
        return web.json_response(
            {
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "service": "vpn_shopbot",
            },
            status=200,
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return web.json_response(
            {
                "status": "unhealthy",
                "timestamp": datetime.utcnow().isoformat(),
                "error": "Health check failed",
            },
            status=503,
        )


async def readiness_check(
    request: Request,
    session_factory: async_sessionmaker,
    redis: Redis | None = None,
) -> Response:
    """
    Readiness check endpoint.
    Checks if the service is ready to accept traffic.
    """
    try:
        # Check database connection and basic queries
        async with session_factory() as session:
            # Test basic query
            await session.execute(select(1))
            
            # Check if critical tables exist and are accessible
            await session.execute(select(User.id).limit(1))
            await session.execute(select(Server.id).limit(1))
            await session.execute(select(Plan.id).limit(1))
        
        # Check Redis connection if available
        if redis:
            await redis.ping()
            # Test Redis write/read
            test_key = "health_check_test"
            await redis.set(test_key, "ok", ex=10)
            value = await redis.get(test_key)
            if value != b"ok":
                raise Exception("Redis read/write test failed")
        
        return web.json_response(
            {
                "status": "ready",
                "timestamp": datetime.utcnow().isoformat(),
                "checks": {
                    "database": "ok",
                    "redis": "ok" if redis else "not_configured",
                },
            },
            status=200,
        )
    except Exception as e:
        logger.error(f"Readiness check failed: {e}", exc_info=True)
        return web.json_response(
            {
                "status": "not_ready",
                "timestamp": datetime.utcnow().isoformat(),
                "error": "Health check failed",
            },
            status=503,
        )


async def liveness_check(request: Request) -> Response:
    """
    Liveness check endpoint.
    Simple check to see if the process is alive.
    """
    return web.json_response(
        {
            "status": "alive",
            "timestamp": datetime.utcnow().isoformat(),
        },
        status=200,
    )


async def xui_health_check(
    request: Request,
    services: "ServicesContainer | None" = None,
) -> Response:
    """
    Check X-UI API connectivity.
    Tests connection to at least one server in the pool.
    """
    if not services:
        return web.json_response(
            {
                "status": "unavailable",
                "timestamp": datetime.utcnow().isoformat(),
                "error": "Services container not available",
            },
            status=503,
        )
    
    try:
        # Try to get at least one server from pool
        if not services.server_pool._servers:
            return web.json_response(
                {
                    "status": "unhealthy",
                    "timestamp": datetime.utcnow().isoformat(),
                    "error": "No servers in pool",
                },
                status=503,
            )
        
        # Test connection to first server
        first_server_id = list(services.server_pool._servers.keys())[0]
        connection = services.server_pool._servers[first_server_id]
        
        # Try to get inbound list (lightweight operation)
        await connection.api.inbound.get_list()
        
        return web.json_response(
            {
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "servers_tested": 1,
                "server_id": first_server_id,
            },
            status=200,
        )
    except Exception as e:
        logger.error(f"X-UI health check failed: {e}", exc_info=True)
        return web.json_response(
            {
                "status": "unhealthy",
                "timestamp": datetime.utcnow().isoformat(),
                "error": "Health check failed",
            },
            status=503,
        )


async def servers_health_check(
    request: Request,
    session_factory: async_sessionmaker,
    services: "ServicesContainer | None" = None,
) -> Response:
    """
    Check server pool status.
    Returns status of all servers.
    """
    try:
        from sqlalchemy.orm import selectinload
        
        async with session_factory() as session:
            servers_query = await session.execute(
                select(Server).options(selectinload(Server.users))
            )
            servers = servers_query.scalars().all()
        
        server_statuses = []
        online_count = 0
        total_count = len(servers)
        
        for server in servers:
            is_online = server.online if hasattr(server, 'online') else False
            if is_online:
                online_count += 1
            
            # Access current_clients within session context
            current_clients = len(server.users) if server.users else 0
            
            server_statuses.append({
                "id": server.id,
                "name": server.name,
                "host": server.host,
                "online": is_online,
                "current_clients": current_clients,
                "max_clients": server.max_clients if hasattr(server, 'max_clients') else 0,
            })
        
        return web.json_response(
            {
                "status": "ok" if online_count > 0 else "degraded",
                "timestamp": datetime.utcnow().isoformat(),
                "servers": {
                    "total": total_count,
                    "online": online_count,
                    "offline": total_count - online_count,
                },
                "details": server_statuses,
            },
            status=200 if online_count > 0 else 503,
        )
    except Exception as e:
        logger.error(f"Servers health check failed: {e}", exc_info=True)
        return web.json_response(
            {
                "status": "error",
                "timestamp": datetime.utcnow().isoformat(),
                "error": "Health check failed",
            },
            status=503,
        )


async def data_integrity_check(
    request: Request,
    session_factory: async_sessionmaker,
) -> Response:
    """
    Check data integrity.
    Verifies User-UserClient relationships and orphaned records.
    """
    try:
        async with session_factory() as session:
            # Check for orphaned UserClient records (user_id doesn't exist)
            orphaned_query = await session.execute(
                select(func.count(UserClient.id))
                .select_from(UserClient)
                .outerjoin(User, UserClient.user_id == User.id)
                .where(User.id.is_(None))
            )
            orphaned_count = orphaned_query.scalar() or 0
            
            # Check for users with server_id but no UserClient records (old schema is OK)
            users_with_server = await session.execute(
                select(func.count(User.id)).where(User.server_id.isnot(None))
            )
            users_with_server_count = users_with_server.scalar() or 0
            
            # Check for UserClient records with invalid server_id
            invalid_server_query = await session.execute(
                select(func.count(UserClient.id))
                .select_from(UserClient)
                .outerjoin(Server, UserClient.server_id == Server.id)
                .where(UserClient.server_id.isnot(None), Server.id.is_(None))
            )
            invalid_server_count = invalid_server_query.scalar() or 0
            
            issues = []
            if orphaned_count > 0:
                issues.append(f"{orphaned_count} orphaned UserClient records")
            if invalid_server_count > 0:
                issues.append(f"{invalid_server_count} UserClient records with invalid server_id")
            
            return web.json_response(
                {
                    "status": "ok" if not issues else "degraded",
                    "timestamp": datetime.utcnow().isoformat(),
                    "checks": {
                        "orphaned_user_clients": orphaned_count,
                        "users_with_server": users_with_server_count,
                        "invalid_server_references": invalid_server_count,
                    },
                    "issues": issues if issues else None,
                },
                status=200 if not issues else 503,
            )
    except Exception as e:
        logger.error(f"Data integrity check failed: {e}", exc_info=True)
        return web.json_response(
            {
                "status": "error",
                "timestamp": datetime.utcnow().isoformat(),
                "error": "Health check failed",
            },
            status=503,
    )


def register_health_routes(
    app: web.Application,
    session_factory: async_sessionmaker,
    redis: Redis | None = None,
    services: "ServicesContainer | None" = None,
) -> None:
    """
    Register health check routes.
    
    Args:
        app: aiohttp Application
        session_factory: Database session factory
        redis: Optional Redis client
        services: Optional services container for extended checks
    """
    # Health check (basic)
    async def health_handler(request: Request) -> Response:
        return await health_check(request, session_factory, redis)
    
    # Readiness check (detailed)
    async def readiness_handler(request: Request) -> Response:
        return await readiness_check(request, session_factory, redis)
    
    # Liveness check (simple)
    async def liveness_handler(request: Request) -> Response:
        return await liveness_check(request)
    
    # X-UI API check
    async def xui_handler(request: Request) -> Response:
        return await xui_health_check(request, services)
    
    # Servers check
    async def servers_handler(request: Request) -> Response:
        return await servers_health_check(request, session_factory, services)
    
    # Data integrity check
    async def data_handler(request: Request) -> Response:
        return await data_integrity_check(request, session_factory)
    
    app.router.add_get("/health", health_handler)
    app.router.add_get("/health/ready", readiness_handler)
    app.router.add_get("/health/live", liveness_handler)
    app.router.add_get("/health/xui", xui_handler)
    app.router.add_get("/health/servers", servers_handler)
    app.router.add_get("/health/data", data_handler)
    
    logger.info("Health check routes registered: /health, /health/ready, /health/live, /health/xui, /health/servers, /health/data")

