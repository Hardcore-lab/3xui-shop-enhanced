"""
Script to initialize plans from JSON file to database.
This should be run once after migration or when plans.json is updated.
"""
import json
import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import DEFAULT_PLANS_DIR
from app.db.models import Plan, PlanDuration, PlanPrice

logger = logging.getLogger(__name__)


async def init_plans_from_json(session_factory: async_sessionmaker) -> bool:
    """
    Initialize plans from JSON file to database.
    
    Args:
        session_factory: Database session factory
    
    Returns:
        True if successful, False otherwise
    """
    file_path = Path(DEFAULT_PLANS_DIR)
    
    logger.error(f"[CRITICAL INIT] Looking for plans file at: {file_path}, exists={file_path.exists()}")
    
    if not file_path.exists():
        logger.error(f"[CRITICAL INIT] Plans file '{file_path}' does not exist. Skipping initialization.")
        return False
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse plans file '{file_path}': {e}")
        return False
    except Exception as e:
        logger.error(f"Error reading plans file '{file_path}': {e}")
        return False
    
    if "plans" not in data or not isinstance(data["plans"], list):
        logger.error(f"'plans' key is missing or not a list in '{file_path}'.")
        return False
    
    if "durations" not in data or not isinstance(data["durations"], list):
        logger.error(f"'durations' key is missing or not a list in '{file_path}'.")
        return False
    
    async with session_factory() as session:
        try:
            # Initialize durations first (global, not per-plan) - without committing
            for idx, duration_val in enumerate(data["durations"]):
                duration_int = int(duration_val)
                # Check if duration exists
                query = await session.execute(
                    select(PlanDuration).where(PlanDuration.duration == duration_int)
                )
                existing_duration = query.scalar_one_or_none()
                
                if existing_duration:
                    existing_duration.is_active = True
                    existing_duration.display_order = idx
                else:
                    new_duration = PlanDuration(
                    duration=duration_int,
                    is_active=True,
                    display_order=idx,
                )
                    session.add(new_duration)
            logger.info(f"Initialized {len(data['durations'])} durations.")
            
            # Initialize plans
            plans_initialized = 0
            logger.info(f"Starting initialization of {len(data['plans'])} plans from JSON")
            for plan_data in data["plans"]:
                devices = plan_data.get("devices")
                if not devices:
                    logger.warning("Plan missing 'devices' field, skipping.")
                    continue
                
                # Find plan by devices (including inactive ones)
                query = await session.execute(
                    select(Plan).where(Plan.devices == devices)
                )
                plan = query.scalar_one_or_none()
                
                if not plan:
                    # Plan doesn't exist, create it
                    plan = Plan(devices=devices, is_active=True)
                    session.add(plan)
                    try:
                        await session.flush()  # Flush to get the ID
                        logger.error(f"[CRITICAL INIT] Created new plan with {devices} devices.")
                    except IntegrityError as e:
                        await session.rollback()
                        logger.error(f"[CRITICAL INIT] Failed to create plan with {devices} devices: {e}")
                        continue
                else:
                    # Plan exists, ensure it's active
                    if not plan.is_active:
                        plan.is_active = True
                        logger.error(f"[CRITICAL INIT] Reactivated plan with {devices} devices.")
                    else:
                        logger.error(f"[CRITICAL INIT] Plan with {devices} devices already exists and is active.")
                
                # Add/update prices (without committing - we'll commit all at once)
                prices = plan_data.get("prices", {})
                prices_added = 0
                for currency, durations_prices in prices.items():
                    for duration_str, price in durations_prices.items():
                        duration = int(duration_str)
                        # Check if price exists
                        existing_price = await PlanPrice.get_by_plan_currency_duration(
                            session=session,
                            plan_id=plan.id,
                            currency=currency.upper(),
                            duration=duration,
                        )
                        
                        if existing_price:
                            existing_price.price = float(price)
                        else:
                            new_price = PlanPrice(
                                plan_id=plan.id,
                                currency=currency.upper(),
                                duration=duration,
                            price=float(price),
                        )
                            session.add(new_price)
                        prices_added += 1
                
                logger.error(f"[CRITICAL INIT] Initialized plan with {devices} devices ({prices_added} prices).")
                plans_initialized += 1
            
            await session.commit()
            logger.error(f"[CRITICAL INIT] Successfully initialized {plans_initialized} plans from JSON. Total plans initialized: {plans_initialized}")
            return True
            
        except Exception as e:
            await session.rollback()
            logger.error(f"Error initializing plans: {e}", exc_info=True)
            return False

