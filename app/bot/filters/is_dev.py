import logging

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject
from aiogram.types import User as TelegramUser

logger = logging.getLogger(__name__)


class IsDev(BaseFilter):
    developer_ids: list[int] = []

    async def __call__(
        self,
        event: TelegramObject | None = None,
        user_id: int | None = None,
    ) -> bool:
        if user_id:
            return user_id in self.developer_ids

        user: TelegramUser | None = event.from_user

        if not user:
            return False

        return user.id in self.developer_ids

    @classmethod
    def set_developer(cls, developer_ids: list[int]) -> None:
        cls.developer_ids = developer_ids
        logger.info(f"Developers set: {developer_ids}")
