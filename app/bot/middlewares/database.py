import logging
import uuid
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.utils.constants import DEFAULT_LANGUAGE
from app.db.models import User

logger = logging.getLogger(__name__)


class DBSessionMiddleware(BaseMiddleware):
    def __init__(self, session: async_sessionmaker) -> None:
        self.session = session
        logger.debug("Database Session Middleware initialized.")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # CRITICAL: Check if this is a pre-checkout query
        # In aiogram 3.x, pre_checkout_query is in Update.pre_checkout_query
        # The handler receives PreCheckoutQuery directly, but middleware receives Update
        # For pre-checkout queries, skip DB operations to avoid timeout
        # Telegram requires response within 10 seconds, so we must answer immediately
        # Set locale so CustomI18nMiddleware can use it for any follow-up messages
        if isinstance(event, Update) and event.pre_checkout_query is not None:
            from_user = event.pre_checkout_query.from_user
            if from_user and from_user.language_code:
                lang = from_user.language_code.split("-")[0].lower()
                data["locale"] = lang if lang in ("ru", "en") else DEFAULT_LANGUAGE
            else:
                data["locale"] = DEFAULT_LANGUAGE
            return await handler(event, data)
        
        session: AsyncSession
        async with self.session() as session:
            # Get user from appropriate source based on event type
            tg_user: TelegramUser | None = None
            if isinstance(event, Update):
                if event.callback_query:
                    tg_user = event.callback_query.from_user
                elif event.message:
                    tg_user = event.message.from_user
                elif event.event:
                    tg_user = event.event.from_user

            if tg_user is not None and not tg_user.is_bot:
                user = await User.get(session=session, tg_id=tg_user.id)
                is_new_user = False

                if not user:
                    is_new_user = True
                    # Auto-detect language from Telegram user, fallback to default
                    detected_language = tg_user.language_code or DEFAULT_LANGUAGE
                    # Normalize language code (e.g., "ru-RU" -> "ru", "en-US" -> "en")
                    if detected_language:
                        detected_language = detected_language.split("-")[0].lower()
                    # Only allow supported languages (ru, en), fallback to default
                    if detected_language not in ["ru", "en"]:
                        detected_language = DEFAULT_LANGUAGE
                    
                    user = await User.create(
                        session=session,
                        tg_id=tg_user.id,
                        vpn_id=str(uuid.uuid4()),
                        first_name=tg_user.first_name,
                        username=tg_user.username,
                        language_code=detected_language,
                    )
                    logger.info(f"New user {user.tg_id} created.")

                data["user"] = user
                data["session"] = session
                data["is_new_user"] = is_new_user
                # Set locale from database user language_code for i18n middleware
                # This ensures SimpleI18nMiddleware uses our database language, not Telegram's
                # We use data dict instead of modifying frozen Pydantic model
                data["locale"] = user.language_code
                logger.debug(f"User {user.tg_id}: Set locale to {user.language_code} from database")
            else:
                logger.debug("No user found in event data.")
                # Set locale from Telegram user or default so i18n always has a locale
                if isinstance(event, Update):
                    if event.message and event.message.from_user:
                        u = event.message.from_user
                    elif event.callback_query and event.callback_query.from_user:
                        u = event.callback_query.from_user
                    elif event.event and getattr(event.event, "from_user", None):
                        u = event.event.from_user
                    else:
                        u = None
                    if u and u.language_code:
                        lang = u.language_code.split("-")[0].lower()
                        data["locale"] = lang if lang in ("ru", "en") else DEFAULT_LANGUAGE
                    else:
                        data["locale"] = DEFAULT_LANGUAGE
                else:
                    data["locale"] = DEFAULT_LANGUAGE

            return await handler(event, data)
