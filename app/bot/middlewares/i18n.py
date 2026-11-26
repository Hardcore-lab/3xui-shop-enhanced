import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from aiogram.utils.i18n import I18n

logger = logging.getLogger(__name__)


class CustomI18nMiddleware(BaseMiddleware):
    """Custom i18n middleware that uses locale from data dict (set by DBSessionMiddleware)."""

    def __init__(self, i18n: I18n) -> None:
        self.i18n = i18n
        logger.debug("Custom I18n Middleware initialized.")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Get locale from data dict (set by DBSessionMiddleware) or fallback to Telegram's language_code
        locale = data.get("locale")
        
        if not locale and isinstance(event, Update):
            # Fallback to Telegram's language_code if locale not set in data
            if event.event and hasattr(event.event, "from_user") and event.event.from_user:
                locale = event.event.from_user.language_code
        
        # Use default locale if still not set
        if not locale:
            locale = self.i18n.default_locale
        
        # Set locale in i18n context
        with self.i18n.use_locale(locale):
            return await handler(event, data)

