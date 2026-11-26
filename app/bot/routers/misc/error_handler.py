import logging
import traceback

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import BufferedInputFile, ErrorEvent, Update
from aiogram.utils.formatting import Bold, Code, Text

from app.bot.models import ServicesContainer
from app.bot.utils.misc import split_text
from app.config import Config

logger = logging.getLogger(__name__)
router = Router(name=__name__)


def _extract_user_id(update: Update) -> int | None:
    candidates = (
        update.message,
        update.callback_query,
        update.pre_checkout_query,
        update.shipping_query,
        update.inline_query,
        update.chosen_inline_result,
        update.my_chat_member,
        update.chat_member,
        update.chat_join_request,
    )
    for item in candidates:
        if item and getattr(item, "from_user", None):
            return item.from_user.id
    return None


@router.errors(ExceptionTypeFilter(Exception))
async def errors_handler(event: ErrorEvent, config: Config, services: ServicesContainer) -> bool:
    user_id = _extract_user_id(event.update)
    if isinstance(event.exception, TelegramForbiddenError):
        logger.info(f"User {user_id} blocked the bot.")
        return True

    if isinstance(event.exception, TelegramBadRequest):
        logger.warning(
            f"User {user_id} bad request for edit/send message."
        )
        return True

    logger.exception(f"Update: {event.update}\nException: {event.exception}")

    if not config.bot.DEV_ID:
        return True

    try:
        text = Text(Bold((type(event.exception).__name__)), f": {str(event.exception)[:1021]}...")
        await services.notification.notify_developer(
            text=text.as_html(),
            document=BufferedInputFile(
                file=traceback.format_exc().encode(),
                filename=f"error_{event.update.update_id}.txt",
            ),
        )

        update_json = event.update.model_dump_json(indent=2, exclude_none=True)
        for chunk in split_text(update_json):
            await services.notification.notify_developer(
                text=Code(chunk).as_html(),
            )

    except TelegramBadRequest as exception:
        logger.warning(f"Failed to send error details: {exception}")
    except Exception as exception:
        logger.error(f"Unexpected error in error handler: {exception}")

    return True
