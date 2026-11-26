import logging
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import DatabaseConfig

from . import models

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, config: DatabaseConfig, driver: str = "sqlite+aiosqlite") -> None:
        self.config = config
        self.driver = driver
        self.engine = create_async_engine(
            url=config.url(driver=driver),
            pool_pre_ping=True,
            pool_size=20,           # Base pool size (concurrent connections)
            max_overflow=10,        # Additional connections when pool is exhausted
            pool_timeout=30,        # Wait time for available connection (seconds)
            pool_recycle=3600,      # Recycle connections after 1 hour
            echo=False,             # Set to True for SQL debugging
        )
        self.session = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.debug(f"Database engine initialized with {driver} driver and connection pooling (pool_size=20, max_overflow=10).")

    async def initialize(self) -> Self:
        try:
            async with self.engine.begin() as connection:
                await connection.run_sync(models.Base.metadata.create_all)
            logger.debug("Database schema initialized successfully.")
        except Exception as exception:
            logger.error(f"Error initializing database schema: {exception}")
            raise
        return self

    async def close(self) -> None:
        try:
            await self.engine.dispose()
            logger.debug("Database engine closed successfully.")
        except Exception as exception:
            logger.error(f"Error closing database engine: {exception}")
            raise
