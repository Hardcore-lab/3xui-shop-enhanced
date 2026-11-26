import logging
from typing import Any, Self

from sqlalchemy import *
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from app.bot.utils.encryption import encrypt_password, decrypt_password
from . import Base
from .user import User

logger = logging.getLogger(__name__)


class Server(Base):
    """
    Represents a VPN server in the database.

    Attributes:
        id (int): Unique identifier for the server.
        name (str): Unique server name.
        host (str): Server host address or IP.
        max_clients (int): Maximum allowed number of clients.
        location (str | None): Server location if available.
        online (bool): Indicates whether the server is online.
        users (list[User]): List of users associated with the server.
    """

    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    max_clients: Mapped[int] = mapped_column(Integer, nullable=False)
    location: Mapped[str | None] = mapped_column(String(32), nullable=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    _encrypted_password: Mapped[str | None] = mapped_column("password", String(512), nullable=True)
    users: Mapped[list["User"]] = relationship("User", back_populates="server")  # type: ignore
    
    @property
    def password(self) -> str | None:
        """Decrypt and return the password."""
        if not self._encrypted_password:
            return None
        try:
            return decrypt_password(self._encrypted_password)
        except Exception as e:
            logger.error(f"Failed to decrypt password for server {self.name}: {e}")
            return None
    
    @password.setter
    def password(self, value: str | None) -> None:
        """Encrypt and store the password."""
        if value is None:
            self._encrypted_password = None
        else:
            try:
                self._encrypted_password = encrypt_password(value)
            except Exception as e:
                logger.error(f"Failed to encrypt password for server {self.name}: {e}")
                raise

    @hybrid_property
    def current_clients(self) -> int:
        return len(self.users)

    @current_clients.expression
    def current_clients(cls) -> Select:
        return (
            select(func.count(User.id)).where(User.server_id == Server.id).label("current_clients")
        )

    def __repr__(self) -> str:
        return (
            f"<Server(id={self.id}, name='{self.name}', host={self.host}, "
            f"max_clients={self.max_clients}, location={self.location}, online={self.online}, "
            f"username={'***' if self.username else None})>"
        )

    @classmethod
    async def get_by_id(cls, session: AsyncSession, id: int) -> Self | None:
        filter = [Server.id == id]
        query = await session.execute(
            select(Server).options(selectinload(Server.users)).where(*filter)
        )
        return query.scalar_one_or_none()

    @classmethod
    async def get_by_name(cls, session: AsyncSession, name: str) -> Self | None:
        filter = [Server.name == name]
        query = await session.execute(
            select(Server).options(selectinload(Server.users)).where(*filter)
        )
        return query.scalar_one_or_none()

    @classmethod
    async def get_all(cls, session: AsyncSession) -> list[Self]:
        query = await session.execute(select(Server).options(selectinload(Server.users)))
        return query.scalars().all()

    @classmethod
    async def create(cls, session: AsyncSession, name: str, **kwargs: Any) -> Self | None:
        server = await Server.get_by_name(session=session, name=name)

        if server:
            logger.warning(f"Server {name} already exists.")
            return None

        server = Server(name=name, **kwargs)
        session.add(server)

        try:
            await session.commit()
            logger.info(f"Server {name} created.")
            return server
        except IntegrityError as exception:
            await session.rollback()
            logger.error(f"Error occurred while creating server {name}: {exception}")
            return None

    @classmethod
    async def update(cls, session: AsyncSession, name: str, **kwargs: Any) -> Self | None:
        server = await Server.get_by_name(session=session, name=name)

        if server:
            filter = [Server.id == server.id]
            await session.execute(update(Server).where(*filter).values(**kwargs))
            await session.commit()
            logger.debug(f"Server {name} updated.")
            return server

        logger.warning(f"Server {name} not found for update.")
        return None

    @classmethod
    async def delete(cls, session: AsyncSession, name: str) -> bool:
        server = await Server.get_by_name(session=session, name=name)

        if server:
            await session.delete(server)
            await session.commit()
            logger.info(f"Server {name} deleted.")
            return True

        logger.warning(f"Server {name} not found for deletion.")
        return False
