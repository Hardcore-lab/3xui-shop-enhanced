import json
import logging
from typing import TYPE_CHECKING

from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.models import Plan as PlanModel
from app.bot.utils.constants import Currency
from app.db.models import Plan, PlanDuration, PlanPrice

if TYPE_CHECKING:
    from app.bot.models import ServicesContainer

logger = logging.getLogger(__name__)

# Cache TTL in seconds (1 hour)
PLAN_CACHE_TTL = 3600
DURATION_CACHE_TTL = 3600


class PlanService:
    def __init__(
        self,
        session_factory: async_sessionmaker | None = None,
        redis: Redis | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.redis = redis
        logger.info("Plan Service initialized.")
    
    async def clear_cache(self) -> None:
        """
        Clear all plan-related cache from Redis.
        """
        if not self.redis:
            logger.debug("Redis not available, skipping cache clear")
            return
        
        try:
            # Clear all plans cache
            await self.redis.delete("plans:all")
            # Clear individual plan caches (pattern: plan:devices:*)
            # Note: Redis doesn't support pattern deletion directly, so we'd need to scan
            # For now, just clear the main cache
            logger.info("Cleared plans cache")
        except Exception as e:
            logger.warning(f"Error clearing plans cache: {e}")

    async def get_plan(self, devices: int, session: AsyncSession | async_sessionmaker | None = None) -> PlanModel | None:
        """
        Get a plan by number of devices.
        
        Args:
            devices: Number of devices
            session: Optional session (if not provided, uses self.session_factory)
        
        Returns:
            Plan model or None
        """
        if not self.session_factory and not session:
            logger.error("No session factory provided")
            return None

        # Try to get from cache
        cache_key = f"plan:devices:{devices}"
        if self.redis:
            try:
                cached_data = await self.redis.get(cache_key)
                if cached_data:
                    plan_dict = json.loads(cached_data)
                    logger.debug(f"Plan {devices} devices loaded from cache")
                    return PlanModel.from_dict(plan_dict)
            except Exception as e:
                logger.warning(f"Error reading plan from cache: {e}")

        # Handle both AsyncSession and async_sessionmaker
        if isinstance(session, AsyncSession):
            # If session is already an AsyncSession, use it directly
            db_session = session
            db_plan = await Plan.get_by_devices(session=db_session, devices=devices)
            if not db_plan:
                logger.warning(f"Plan with {devices} devices not found.")
                return None

            # Convert DB plan to Plan model
            prices_dict: dict[str, dict[int, float]] = {}
            for price in db_plan.prices:
                if price.currency not in prices_dict:
                    prices_dict[price.currency] = {}
                prices_dict[price.currency][price.duration] = price.price

            plan_model = PlanModel(devices=db_plan.devices, prices=prices_dict)

            # Cache the plan
            if self.redis:
                try:
                    plan_dict = plan_model.to_dict()
                    await self.redis.setex(
                        cache_key,
                        PLAN_CACHE_TTL,
                        json.dumps(plan_dict),
                    )
                    logger.debug(f"Plan {devices} devices cached")
                except Exception as e:
                    logger.warning(f"Error caching plan: {e}")

            return plan_model
        else:
            # If session is async_sessionmaker or None, create a new session
            session_to_use = session or self.session_factory
            async with session_to_use() as db_session:
                db_plan = await Plan.get_by_devices(session=db_session, devices=devices)
                if not db_plan:
                    logger.warning(f"Plan with {devices} devices not found.")
                    return None

                # Convert DB plan to Plan model
                prices_dict: dict[str, dict[int, float]] = {}
                for price in db_plan.prices:
                    if price.currency not in prices_dict:
                        prices_dict[price.currency] = {}
                    prices_dict[price.currency][price.duration] = price.price

                plan_model = PlanModel(devices=db_plan.devices, prices=prices_dict)

                # Cache the plan
                if self.redis:
                    try:
                        plan_dict = plan_model.to_dict()
                        await self.redis.setex(
                            cache_key,
                            PLAN_CACHE_TTL,
                            json.dumps(plan_dict),
                        )
                        logger.debug(f"Plan {devices} devices cached")
                    except Exception as e:
                        logger.warning(f"Error caching plan: {e}")

                return plan_model

    async def get_all_plans(self, session: AsyncSession | async_sessionmaker | None = None) -> list[PlanModel]:
        """
        Get all active plans.
        
        Args:
            session: Optional session (if not provided, uses self.session_factory)
        
        Returns:
            List of Plan models
        """
        if not self.session_factory and not session:
            logger.error("No session factory provided")
            return []

        # Try to get from cache
        cache_key = "plans:all"
        if self.redis:
            try:
                cached_data = await self.redis.get(cache_key)
                if cached_data:
                    plans_data = json.loads(cached_data)
                    cached_plans = [PlanModel.from_dict(plan_dict) for plan_dict in plans_data]
                    logger.info(f"get_all_plans: Loaded {len(cached_plans)} plans from cache: {[p.devices for p in cached_plans]}")
                    
                    # CRITICAL FIX: If cache has less than 5 plans, clear it and reload from DB
                    if len(cached_plans) < 5:
                        logger.error(f"[CRITICAL FIX H2] Cache has only {len(cached_plans)} plans (expected 5): {[p.devices for p in cached_plans]}. Clearing cache and reloading from database...")
                        try:
                            await self.redis.delete(cache_key)
                            logger.info(f"[CRITICAL FIX H2] Cache cleared successfully. Now loading from database...")
                        except Exception as del_err:
                            logger.error(f"[CRITICAL FIX H2] Failed to clear cache: {del_err}")
                        # Fall through to load from database - DO NOT RETURN
                    else:
                        logger.error(f"[CRITICAL RETURN] Cache has {len(cached_plans)} plans: {[p.devices for p in cached_plans]}, returning from cache")
                        if len(cached_plans) != 5:
                            logger.error(f"[CRITICAL ERROR] Expected 5 plans but cache has {len(cached_plans)}! This is the bug!")
                        return cached_plans
            except Exception as e:
                logger.warning(f"Error reading plans from cache: {e}")

        # Handle both AsyncSession and async_sessionmaker
        if isinstance(session, AsyncSession):
            # If session is already an AsyncSession, use it directly
            db_session = session
            db_plans = await Plan.get_all(session=db_session, include_inactive=False)
            logger.info(f"get_all_plans: Found {len(db_plans)} active plans in database: {[p.devices for p in db_plans]}")
            plans: list[PlanModel] = []

            for db_plan in db_plans:
                prices_dict: dict[str, dict[int, float]] = {}
                for price in db_plan.prices:
                    if price.currency not in prices_dict:
                        prices_dict[price.currency] = {}
                    prices_dict[price.currency][price.duration] = price.price

                plans.append(PlanModel(devices=db_plan.devices, prices=prices_dict))
            
            if len(plans) == 0:
                logger.warning("get_all_plans: No plans found in database! This will result in empty device selection.")

            # Cache all plans
            if self.redis:
                try:
                    plans_data = [plan.to_dict() for plan in plans]
                    await self.redis.setex(
                        cache_key,
                        PLAN_CACHE_TTL,
                        json.dumps(plans_data),
                    )
                    logger.info(f"get_all_plans: Cached {len(plans)} plans")
                except Exception as e:
                    logger.warning(f"Error caching plans: {e}")

            return plans
        else:
            # If session is async_sessionmaker or None, create a new session
            session_to_use = session or self.session_factory
            async with session_to_use() as db_session:
                db_plans = await Plan.get_all(session=db_session, include_inactive=False)
                logger.info(f"get_all_plans: Found {len(db_plans)} active plans in database: {[p.devices for p in db_plans]}")
                plans: list[PlanModel] = []

                for db_plan in db_plans:
                    prices_dict: dict[str, dict[int, float]] = {}
                    for price in db_plan.prices:
                        if price.currency not in prices_dict:
                            prices_dict[price.currency] = {}
                        prices_dict[price.currency][price.duration] = price.price

                    plans.append(PlanModel(devices=db_plan.devices, prices=prices_dict))
                
                if len(plans) == 0:
                    logger.warning("get_all_plans: No plans found in database! This will result in empty device selection.")

                # Cache all plans
                if self.redis:
                    try:
                        plans_data = [plan.to_dict() for plan in plans]
                        await self.redis.setex(
                            cache_key,
                            PLAN_CACHE_TTL,
                            json.dumps(plans_data),
                        )
                        logger.info(f"get_all_plans: Cached {len(plans)} plans")
                    except Exception as e:
                        logger.warning(f"Error caching plans: {e}")

                return plans

    async def get_durations(self, session: AsyncSession | async_sessionmaker | None = None) -> list[int]:
        """
        Get all active durations.
        
        Args:
            session: Optional session (if not provided, uses self.session_factory)
        
        Returns:
            List of durations in days
        """
        if not self.session_factory and not session:
            logger.error("No session factory provided")
            return []

        # Try to get from cache
        cache_key = "durations:all"
        if self.redis:
            try:
                cached_data = await self.redis.get(cache_key)
                if cached_data:
                    durations = json.loads(cached_data)
                    logger.debug("Durations loaded from cache")
                    return durations
            except Exception as e:
                logger.warning(f"Error reading durations from cache: {e}")

        # Handle both AsyncSession and async_sessionmaker
        if isinstance(session, AsyncSession):
            # If session is already an AsyncSession, use it directly
            db_session = session
            durations = await PlanDuration.get_durations_list(session=db_session)

            # Cache durations
            if self.redis:
                try:
                    await self.redis.setex(
                        cache_key,
                        DURATION_CACHE_TTL,
                        json.dumps(durations),
                    )
                    logger.debug("Durations cached")
                except Exception as e:
                    logger.warning(f"Error caching durations: {e}")

            return durations
        else:
            # If session is async_sessionmaker or None, create a new session
            session_to_use = session or self.session_factory
            async with session_to_use() as db_session:
                durations = await PlanDuration.get_durations_list(session=db_session)

                # Cache durations
                if self.redis:
                    try:
                        await self.redis.setex(
                            cache_key,
                            DURATION_CACHE_TTL,
                            json.dumps(durations),
                        )
                        logger.debug("Durations cached")
                    except Exception as e:
                        logger.warning(f"Error caching durations: {e}")

                return durations

    async def invalidate_cache(self) -> None:
        """Invalidate all plan-related cache."""
        if not self.redis:
            return

        try:
            # Delete all plan cache keys
            keys_to_delete = [
                "plans:all",
                "durations:all",
            ]
            # Also delete individual plan caches (pattern matching)
            async for key in self.redis.scan_iter(match="plan:devices:*"):
                keys_to_delete.append(key)

            if keys_to_delete:
                await self.redis.delete(*keys_to_delete)
                logger.info(f"Invalidated {len(keys_to_delete)} plan cache keys")
        except Exception as e:
            logger.warning(f"Error invalidating plan cache: {e}")
