import logging
from mailbox import Message
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import TelegramObject, Update

from app.bot.utils.navigation import NavMain

logger = logging.getLogger(__name__)


class GarbageMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        logger.debug("Garbage Middleware initialized.")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update) and event.message:
            user_id = event.message.from_user.id

            if user_id == event.bot.id:
                logger.debug(f"Message from bot {event.bot.id} skipped.")
            else:
                # Проверяем, есть ли активное состояние FSM
                # Если есть, не удаляем сообщение - оно нужно для обработки состояния
                has_active_state = False
                try:
                    # Пробуем получить FSMContext из data (aiogram автоматически добавляет его)
                    state: FSMContext | None = data.get("state")
                    
                    # Если нет state в data, пробуем создать его из storage
                    if not state:
                        storage = data.get("storage")
                        if not storage:
                            dispatcher = data.get("dispatcher")
                            if dispatcher and hasattr(dispatcher, "fsm"):
                                storage = dispatcher.fsm.storage
                        
                        if storage:
                            bot_id = event.bot.id
                            chat_id = event.message.chat.id
                            user_id_fsm = event.message.from_user.id
                            key = StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id_fsm)
                            state = FSMContext(storage=storage, key=key)
                    
                    if state:
                        # Проверяем текущее состояние через FSMContext
                        current_state = await state.get_state()
                        
                        if current_state:
                            has_active_state = True
                            logger.debug(f"User {user_id} has active FSM state: {current_state}, skipping message deletion.")
                except Exception as e:
                    logger.debug(f"Could not check FSM state for user {user_id}: {e}")
                
                # Удаляем сообщение только если нет активного состояния FSM
                # ВАЖНО: Не удаляем сообщения, которые могут быть частью FSM состояния
                should_delete = not has_active_state and (
                    (event.message.text and not event.message.text.endswith(NavMain.START))
                or event.message.forward_from
                )
                
                if should_delete:
                    try:
                        await event.message.delete()
                        logger.debug(f"Message '{event.message.text[:50]}...' from user {user_id} deleted.")
                    except Exception as exception:
                        logger.error(f"Failed to delete message from user {user_id}: {exception}")
                    if has_active_state:
                        logger.debug(f"Message from user {user_id} NOT deleted due to active FSM state")
                    else:
                        logger.debug(f"Message from user {user_id} NOT deleted (other reason)")

        return await handler(event, data)
