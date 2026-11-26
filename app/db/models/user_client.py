import logging
from datetime import datetime
from typing import Any, Self
from uuid import uuid4

from sqlalchemy import ForeignKey, String, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base

logger = logging.getLogger(__name__)


class UserClient(Base):
    """
    Represents a VPN client associated with a user.
    Each user can have multiple clients (one per device).

    Attributes:
        id (int): Unique identifier for the client record.
        user_id (int): Foreign key referencing the user.
        client_index (int): Index of the client (1, 2, 3...).
        client_email (str): Unique email for the client ({tg_id}_{index}).
        vpn_id (str): UUID of the client in X-UI panel.
        server_id (int | None): Foreign key referencing the server.
        created_at (datetime): Timestamp when the client was created.
        expires_at (datetime): Timestamp when the client expires.
        user (User): Relationship to the user.
        server (Server | None): Relationship to the server.
    """

    __tablename__ = "user_clients"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    client_index: Mapped[int] = mapped_column(nullable=False)
    client_email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    vpn_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False, index=True)

    user: Mapped["User"] = relationship("User", back_populates="clients")  # type: ignore
    server: Mapped["Server | None"] = relationship("Server")  # type: ignore

    def __repr__(self) -> str:
        return (
            f"<UserClient(id={self.id}, user_id={self.user_id}, "
            f"client_index={self.client_index}, client_email='{self.client_email}', "
            f"vpn_id='{self.vpn_id}', server_id={self.server_id})>"
        )

    @classmethod
    async def get_by_user_id(cls, session: AsyncSession, user_id: int) -> list[Self]:
        """Get all clients for a user."""
        query = await session.execute(
            select(UserClient).where(UserClient.user_id == user_id).order_by(UserClient.client_index.asc())
        )
        return list(query.scalars().all())

    @classmethod
    async def get_by_email(cls, session: AsyncSession, email: str) -> Self | None:
        """Get a client by its email."""
        query = await session.execute(select(UserClient).where(UserClient.client_email == email))
        return query.scalar_one_or_none()

    @classmethod
    async def get_by_vpn_id(cls, session: AsyncSession, vpn_id: str) -> Self | None:
        """Get a client by its VPN ID."""
        query = await session.execute(select(UserClient).where(UserClient.vpn_id == vpn_id))
        return query.scalar_one_or_none()

    @classmethod
    async def create(
        cls,
        session: AsyncSession,
        user_id: int,
        client_index: int,
        client_email: str,
        expires_at: datetime,
        server_id: int | None = None,
        vpn_id: str | None = None,
    ) -> Self | None:
        """Create a new user client."""
        if vpn_id is None:
            vpn_id = str(uuid4())

        user_client = UserClient(
            user_id=user_id,
            client_index=client_index,
            client_email=client_email,
            vpn_id=vpn_id,
            server_id=server_id,
            expires_at=expires_at,
        )
        session.add(user_client)

        try:
            await session.commit()
            await session.refresh(user_client)
            logger.info(f"UserClient created: {client_email} for user {user_id}")
            return user_client
        except IntegrityError as exception:
            await session.rollback()
            logger.error(f"Error creating UserClient {client_email}: {exception}")
            return None

    @classmethod
    async def delete_by_user_id(cls, session: AsyncSession, user_id: int) -> int:
        """Delete all clients for a user. Returns number of deleted clients."""
        query = await session.execute(select(UserClient).where(UserClient.user_id == user_id))
        clients = query.scalars().all()
        count = len(clients)
        
        for client in clients:
            await session.delete(client)
        
        try:
            await session.commit()
            logger.info(f"Deleted {count} clients for user {user_id}")
            return count
        except Exception as exception:
            await session.rollback()
            logger.error(f"Error deleting clients for user {user_id}: {exception}")
            return 0

    @classmethod
    async def update_server_id(
        cls, session: AsyncSession, user_id: int, new_server_id: int | None
    ) -> bool:
        """Update server_id for all clients of a user."""
        query = await session.execute(select(UserClient).where(UserClient.user_id == user_id))
        clients = query.scalars().all()
        
        for client in clients:
            client.server_id = new_server_id
        
        try:
            await session.commit()
            logger.info(f"Updated server_id to {new_server_id} for {len(clients)} clients of user {user_id}")
            return True
        except Exception as exception:
            await session.rollback()
            logger.error(f"Error updating server_id for user {user_id}: {exception}")
            return False

    @classmethod
    async def count_by_user_id(cls, session: AsyncSession, user_id: int) -> int:
        """Count clients for a user."""
        from sqlalchemy import func
        query = await session.execute(
            select(func.count(UserClient.id)).where(UserClient.user_id == user_id)
        )
        return query.scalar() or 0

