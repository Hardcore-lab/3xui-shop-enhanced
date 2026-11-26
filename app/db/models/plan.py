import logging
from datetime import datetime
from typing import Any, Self

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from . import Base

logger = logging.getLogger(__name__)


class Plan(Base):
    """
    Represents a subscription plan in the database.

    Attributes:
        id (int): Unique identifier for the plan.
        devices (int): Number of devices supported by the plan.
        is_active (bool): Whether the plan is active and available for purchase.
        created_at (datetime): Timestamp when the plan was created.
        updated_at (datetime): Timestamp when the plan was last updated.
        prices (list[PlanPrice]): List of prices for different currencies and durations.
    """

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    devices: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    prices: Mapped[list["PlanPrice"]] = relationship(  # type: ignore
        "PlanPrice", back_populates="plan", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Plan(id={self.id}, devices={self.devices}, "
            f"is_active={self.is_active}, created_at={self.created_at})>"
        )

    @classmethod
    async def get_by_id(cls, session: AsyncSession, plan_id: int) -> Self | None:
        query = await session.execute(
            select(Plan)
            .options(selectinload(Plan.prices))
            .where(Plan.id == plan_id)
        )
        return query.scalar_one_or_none()

    @classmethod
    async def get_by_devices(cls, session: AsyncSession, devices: int) -> Self | None:
        query = await session.execute(
            select(Plan)
            .options(selectinload(Plan.prices))
            .where(Plan.devices == devices, Plan.is_active == True)
        )
        return query.scalar_one_or_none()

    @classmethod
    async def get_all(cls, session: AsyncSession, include_inactive: bool = False) -> list[Self]:
        query = select(Plan).options(selectinload(Plan.prices))
        if not include_inactive:
            query = query.where(Plan.is_active == True)
        query = query.order_by(Plan.devices.asc())
        result = await session.execute(query)
        return list(result.scalars().all())

    @classmethod
    async def create(cls, session: AsyncSession, devices: int, **kwargs: Any) -> Self | None:
        existing_plan = await Plan.get_by_devices(session=session, devices=devices)
        if existing_plan:
            logger.warning(f"Plan with {devices} devices already exists.")
            return None

        plan = Plan(devices=devices, **kwargs)
        session.add(plan)

        try:
            await session.commit()
            await session.refresh(plan)
            logger.info(f"Plan with {devices} devices created.")
            return plan
        except IntegrityError as exception:
            await session.rollback()
            logger.error(f"Error occurred while creating plan: {exception}")
            return None

    @classmethod
    async def update(cls, session: AsyncSession, plan_id: int, **kwargs: Any) -> Self | None:
        plan = await Plan.get_by_id(session=session, plan_id=plan_id)
        if not plan:
            logger.warning(f"Plan {plan_id} not found for update.")
            return None

        for key, value in kwargs.items():
            setattr(plan, key, value)
        plan.updated_at = func.now()

        try:
            await session.commit()
            await session.refresh(plan)
            logger.info(f"Plan {plan_id} updated.")
            return plan
        except IntegrityError as exception:
            await session.rollback()
            logger.error(f"Error occurred while updating plan {plan_id}: {exception}")
            return None

    @classmethod
    async def delete(cls, session: AsyncSession, plan_id: int) -> bool:
        plan = await Plan.get_by_id(session=session, plan_id=plan_id)
        if not plan:
            logger.warning(f"Plan {plan_id} not found for deletion.")
            return False

        # Soft delete - mark as inactive instead of deleting
        plan.is_active = False
        try:
            await session.commit()
            logger.info(f"Plan {plan_id} deactivated.")
            return True
        except Exception as exception:
            await session.rollback()
            logger.error(f"Error occurred while deactivating plan {plan_id}: {exception}")
            return False


class PlanPrice(Base):
    """
    Represents a price for a plan with specific currency and duration.

    Attributes:
        id (int): Unique identifier for the price.
        plan_id (int): Foreign key referencing the plan.
        currency (str): Currency code (e.g., RUB, USD, XTR).
        duration (int): Duration in days.
        price (float): Price for this currency and duration.
        plan (Plan): Relationship to the plan.
    """

    __tablename__ = "plan_prices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(nullable=False)
    plan: Mapped["Plan"] = relationship("Plan", back_populates="prices")  # type: ignore

    __table_args__ = (
        UniqueConstraint("plan_id", "currency", "duration", name="uq_plan_currency_duration"),
    )

    def __repr__(self) -> str:
        return (
            f"<PlanPrice(id={self.id}, plan_id={self.plan_id}, "
            f"currency='{self.currency}', duration={self.duration}, price={self.price})>"
        )

    @classmethod
    async def get_by_plan_currency_duration(
        cls,
        session: AsyncSession,
        plan_id: int,
        currency: str,
        duration: int,
    ) -> Self | None:
        query = await session.execute(
            select(PlanPrice).where(
                PlanPrice.plan_id == plan_id,
                PlanPrice.currency == currency,
                PlanPrice.duration == duration,
            )
        )
        return query.scalar_one_or_none()

    @classmethod
    async def create_or_update(
        cls,
        session: AsyncSession,
        plan_id: int,
        currency: str,
        duration: int,
        price: float,
    ) -> Self | None:
        existing_price = await PlanPrice.get_by_plan_currency_duration(
            session=session, plan_id=plan_id, currency=currency, duration=duration
        )

        if existing_price:
            existing_price.price = price
            try:
                await session.commit()
                await session.refresh(existing_price)
                logger.info(
                    f"PlanPrice updated for plan {plan_id}, currency {currency}, duration {duration}"
                )
                return existing_price
            except Exception as exception:
                await session.rollback()
                logger.error(f"Error updating PlanPrice: {exception}")
                return None

        new_price = PlanPrice(plan_id=plan_id, currency=currency, duration=duration, price=price)
        session.add(new_price)

        try:
            await session.commit()
            await session.refresh(new_price)
            logger.info(
                f"PlanPrice created for plan {plan_id}, currency {currency}, duration {duration}"
            )
            return new_price
        except IntegrityError as exception:
            await session.rollback()
            logger.error(f"Error occurred while creating PlanPrice: {exception}")
            return None


class PlanDuration(Base):
    """
    Represents available subscription durations.

    Attributes:
        id (int): Unique identifier.
        duration (int): Duration in days (unique).
        is_active (bool): Whether this duration is available.
        display_order (int): Order for display purposes.
    """

    __tablename__ = "plan_durations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    duration: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<PlanDuration(id={self.id}, duration={self.duration}, "
            f"is_active={self.is_active}, display_order={self.display_order})>"
        )

    @classmethod
    async def get_all(cls, session: AsyncSession, include_inactive: bool = False) -> list[Self]:
        query = select(PlanDuration)
        if not include_inactive:
            query = query.where(PlanDuration.is_active == True)
        query = query.order_by(PlanDuration.display_order.asc(), PlanDuration.duration.asc())
        result = await session.execute(query)
        return list(result.scalars().all())

    @classmethod
    async def get_durations_list(cls, session: AsyncSession) -> list[int]:
        durations = await PlanDuration.get_all(session=session)
        return [d.duration for d in durations]

    @classmethod
    async def create_or_update(
        cls,
        session: AsyncSession,
        duration: int,
        is_active: bool = True,
        display_order: int = 0,
    ) -> Self | None:
        query = await session.execute(
            select(PlanDuration).where(PlanDuration.duration == duration)
        )
        existing = query.scalar_one_or_none()

        if existing:
            existing.is_active = is_active
            existing.display_order = display_order
            try:
                await session.commit()
                await session.refresh(existing)
                logger.info(f"PlanDuration {duration} updated.")
                return existing
            except Exception as exception:
                await session.rollback()
                logger.error(f"Error updating PlanDuration: {exception}")
                return None

        new_duration = PlanDuration(
            duration=duration, is_active=is_active, display_order=display_order
        )
        session.add(new_duration)

        try:
            await session.commit()
            await session.refresh(new_duration)
            logger.info(f"PlanDuration {duration} created.")
            return new_duration
        except IntegrityError as exception:
            await session.rollback()
            logger.error(f"Error occurred while creating PlanDuration: {exception}")
            return None

