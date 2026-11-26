import asyncio
import logging
from typing import Dict

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.i18n import gettext as _, ngettext

from app.bot.models import ClientData
from app.bot.services import ServicesContainer
from app.bot.utils.constants import PREVIOUS_CALLBACK_KEY
from app.bot.utils.navigation import NavProfile
from app.bot.utils.network import connection_url
from app.config import Config
from app.db.models import User

from .keyboard import (
    buy_subscription_keyboard,
    keys_viewer_keyboard,
    profile_keyboard,
    subscription_link_choice_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name=__name__)

# Global storage for auto-delete tasks: {user_id: task}
_auto_delete_tasks: Dict[int, asyncio.Task] = {}


async def prepare_message(user: User, client_data: ClientData | None, session) -> str:
    profile = _("profile:message:main").format(name=user.first_name, id=user.tg_id)
    
    # Информация о регистрации
    registration_date = user.created_at.strftime("%Y-%m-%d") if user.created_at else "N/A"
    profile += _("profile:message:registration_date").format(date=registration_date)

    if not client_data:
        subscription = _("profile:message:subscription_none")
        # Добавляем информацию о сервере, даже если нет подписки
        if user.server:
            server_info = _("profile:message:server_info").format(
                server_name=user.server.name,
                server_location=user.server.location or _("profile:message:no_location"),
                server_status=_("profile:message:server_status_online") if user.server.online else _("profile:message:server_status_offline"),
            )
            subscription += server_info
        else:
            subscription += _("profile:message:no_server")
        
        # Добавляем дополнительную информацию
        transactions_count = len(user.transactions) if hasattr(user, 'transactions') else 0
        promocodes_used = len(user.activated_promocodes) if hasattr(user, 'activated_promocodes') else 0
        referrals_count = len(user.referrals_sent) if hasattr(user, 'referrals_sent') else 0
        
        additional_info = _("profile:message:additional_info").format(
            transactions_count=transactions_count,
            promocodes_used=promocodes_used,
            referrals_count=referrals_count,
        )
        subscription += additional_info
        
        return profile + subscription

    subscription = _("profile:message:subscription").format(devices=client_data.max_devices)

    subscription += (
        _("profile:message:subscription_expiry_time").format(expiry_time=client_data.expiry_time)
        if not client_data.has_subscription_expired
        else _("profile:message:subscription_expired")
    )
    
    # Информация о сервере
    if user.server:
        server_info = _("profile:message:server_info").format(
            server_name=user.server.name,
            server_location=user.server.location or _("profile:message:no_location"),
            server_status=_("profile:message:server_status_online") if user.server.online else _("profile:message:server_status_offline"),
        )
        subscription += server_info
    else:
        subscription += _("profile:message:no_server")

    statistics = _("profile:message:statistics").format(
        total=client_data.traffic_used,
        up=client_data.traffic_up,
        down=client_data.traffic_down,
    )
    
    # Дополнительная информация
    transactions_count = len(user.transactions) if hasattr(user, 'transactions') else 0
    promocodes_used = len(user.activated_promocodes) if hasattr(user, 'activated_promocodes') else 0
    referrals_count = len(user.referrals_sent) if hasattr(user, 'referrals_sent') else 0
    
    additional_info = _("profile:message:additional_info").format(
        transactions_count=transactions_count,
        promocodes_used=promocodes_used,
        referrals_count=referrals_count,
    )

    return profile + subscription + statistics + additional_info


@router.callback_query(F.data == NavProfile.MAIN)
async def callback_profile(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    config: Config,
    state: FSMContext,
    session,
) -> None:
    logger.info(f"User {user.tg_id} opened profile page.")
    await state.update_data({PREVIOUS_CALLBACK_KEY: NavProfile.MAIN})

    # Загружаем сервер с relationship
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select
    
    user_clients_list = []  # Initialize before try block
    try:
        user_query = await session.execute(
            select(User)
            .options(
                selectinload(User.server),
                selectinload(User.transactions),
                selectinload(User.activated_promocodes),
                selectinload(User.referrals_sent),
            )
            .where(User.tg_id == user.tg_id)
        )
        user_with_relations = user_query.scalar_one_or_none()
        if not user_with_relations:
            user_with_relations = user
        logger.debug(f"[PROFILE FLOW] STEP 2: USER_LOADED - User loaded: server_id={user_with_relations.server_id}, has_server={bool(user_with_relations.server)}")
        
        # Check if user has UserClient records (new schema) even if server_id is None
        logger.debug(f"[PROFILE FLOW] STEP 3: CHECK_USER_CLIENTS - Checking UserClient records for user {user.tg_id}, server_id={user_with_relations.server_id}")
        
        from app.db.models import UserClient
        user_clients_check = await session.execute(
            select(UserClient).where(UserClient.user_id == user_with_relations.id)
        )
        user_clients_list = user_clients_check.scalars().all()
        logger.debug(f"[PROFILE FLOW] STEP 3: CHECK_USER_CLIENTS - Found {len(user_clients_list)} UserClient records")
        
        # If user has UserClient records but no server_id, try to get server_id from first client
        if user_clients_list and not user_with_relations.server_id:
            logger.debug(f"[PROFILE FLOW] STEP 3: FIX_SERVER_ID - User has {len(user_clients_list)} clients but no server_id, getting from first client")
            first_client = user_clients_list[0]
            if first_client.server_id:
                logger.debug(f"[PROFILE FLOW] STEP 3: FIX_SERVER_ID - Updating user.server_id={first_client.server_id} from UserClient")
                await User.update(session=session, tg_id=user.tg_id, server_id=first_client.server_id)
                await session.commit()
                # Reload user with updated server_id
                user_query = await session.execute(
                    select(User).options(selectinload(User.server)).where(User.tg_id == user.tg_id)
                )
                user_with_relations = user_query.scalar_one_or_none() or user_with_relations
                logger.debug(f"[PROFILE FLOW] STEP 3: FIX_SERVER_ID - User reloaded, server_id={user_with_relations.server_id}")
    except Exception as e:
        logger.error(f"[PROFILE FLOW] ERROR: Error loading user relationships: {e}")
        logger.error(f"Error loading user relationships: {e}", exc_info=True)
        user_with_relations = user
        user_clients_list = []
        # Try to check UserClient even if there was an error
        try:
            from app.db.models import UserClient
            from sqlalchemy import select
            user_clients_check = await session.execute(
                select(UserClient).where(UserClient.user_id == user_with_relations.id)
            )
            user_clients_list = user_clients_check.scalars().all()
            logger.debug(f"[PROFILE FLOW] STEP 3: CHECK_USER_CLIENTS (fallback) - Found {len(user_clients_list)} UserClient records")
        except Exception as e2:
                logger.error(f"[PROFILE FLOW] ERROR: Failed to check UserClient: {e2}")
                user_clients_list = []

    client_data = None
    # Check if we have server_id or UserClient records
    has_clients = len(user_clients_list) > 0 if 'user_clients_list' in locals() else False
    if user_with_relations.server_id or has_clients:
        logger.debug(f"[PROFILE FLOW] STEP 3: GET_CLIENT_DATA - Calling get_client_data for user {user.tg_id}, server_id={user_with_relations.server_id}, has_clients={has_clients}, clients_count={len(user_clients_list) if 'user_clients_list' in locals() else 0}")
        client_data = await services.vpn.get_client_data(user_with_relations)
        logger.debug(f"[PROFILE FLOW] STEP 3: GET_CLIENT_DATA - Result: {client_data is not None}, max_devices={client_data.max_devices if client_data else None}")
        # Don't return error if client_data is None - prepare_message handles None client_data gracefully
        if not client_data:
            logger.info(f"[PROFILE FLOW] INFO: No client data returned for user {user.tg_id} - will show profile without subscription data")
    else:
        logger.debug(f"[PROFILE FLOW] STEP 3: GET_CLIENT_DATA - Skipped: user.server_id is None and no UserClient records")

    try:
        subscription_links: list[tuple[int, str]] | None = None
        if client_data and not client_data.has_subscription_expired:
            keys_per_client = await services.vpn.get_subscription_keys_per_client(
                user_with_relations
            )
            base = (config.bot.public_base_url() or "").rstrip("/")
            if keys_per_client and base:
                subscription_links = [
                    (idx, connection_url(base, key, config.bot.TOKEN))
                    for idx, key in keys_per_client
                ]
        reply_markup = (
            profile_keyboard(subscription_links=subscription_links)
            if client_data and not client_data.has_subscription_expired
            else buy_subscription_keyboard()
        )
        message_text = await prepare_message(user=user_with_relations, client_data=client_data, session=session)
        await callback.message.edit_text(
            text=message_text,
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"[PROFILE FLOW] ERROR: Error preparing/displaying message: {e}", exc_info=True)
        await services.notification.show_popup(
            callback=callback,
            text=_("subscription:popup:error_fetching_data"),
        )


@router.callback_query(F.data == NavProfile.SUBSCRIPTION_LINK)
async def callback_subscription_link_menu(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    config: Config,
) -> None:
    """Show subscription link choice by device (only when user has 2+ devices)."""
    await callback.answer()
    keys_per_client = await services.vpn.get_subscription_keys_per_client(user)
    if not keys_per_client:
        return
    base = (config.bot.public_base_url() or "").rstrip("/")
    if not base:
        return
    links = [
        (idx, connection_url(base, key, config.bot.TOKEN))
        for idx, key in keys_per_client
    ]
    await callback.message.edit_text(
        text=_("profile:message:subscription_link_choice"),
        reply_markup=subscription_link_choice_keyboard(links, back_callback=NavProfile.MAIN),
    )


@router.callback_query(F.data == NavProfile.SHOW_KEY)
async def callback_show_key(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    state: FSMContext,
) -> None:
    import time
    start_time = time.time()
    logger.info(f"[TIMING] SHOW_KEY_START - User {user.tg_id} clicked Show Key button at {start_time}")
    
    # Answer callback IMMEDIATELY to remove loading spinner and make UI responsive
    await callback.answer()
    answer_time = time.time()
    logger.info(f"[TIMING] SHOW_KEY_ANSWERED - Callback answered in {(answer_time - start_time)*1000:.2f}ms")
    
    try:
        # Get all user keys
        all_keys = await services.vpn.get_all_user_keys(user)
        keys_time = time.time()
        logger.info(f"[TIMING] SHOW_KEY_KEYS_RETRIEVED - Keys retrieved in {(keys_time - answer_time)*1000:.2f}ms, count={len(all_keys) if all_keys else 0}")
        logger.info(f"[PROFILE FLOW] STEP 4: SHOW_KEY - Keys retrieved: count={len(all_keys) if all_keys else 0}")
        
        if not all_keys:
            logger.error(f"[PROFILE FLOW] ERROR: No keys found for user {user.tg_id}")
            await services.notification.show_popup(
                callback=callback,
                text=_("profile:popup:no_keys"),
            )
            return
        
        # Store keys in state for navigation
        await state.update_data({
            "user_keys": all_keys,
            "current_key_index": 0,
            "key_view_start_time": asyncio.get_event_loop().time(),
        })
        
        logger.info(f"[PROFILE FLOW] STEP 4: SHOW_KEY - Calling _show_key_at_index with {len(all_keys)} keys")
        # Show first key
        await _show_key_at_index(callback, user, services, state, 0)
        complete_time = time.time()
        logger.info(f"[TIMING] SHOW_KEY_COMPLETE - Total time: {(complete_time - start_time)*1000:.2f}ms")
        logger.info(f"[PROFILE FLOW] STEP 4: SHOW_KEY - _show_key_at_index completed successfully")
    except Exception as e:
        logger.error(f"[PROFILE FLOW] ERROR: Exception in callback_show_key for user {user.tg_id}: {e}", exc_info=True)
        await services.notification.show_popup(
            callback=callback,
            text=_("common:error:unexpected_error"),
        )


async def _show_key_at_index(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    state: FSMContext,
    index: int,
) -> None:
    """Show key at specific index."""
    try:
        data = await state.get_data()
        all_keys = data.get("user_keys", [])
        
        logger.info(f"[PROFILE FLOW] STEP 4.6: _show_key_at_index - index={index}, all_keys_count={len(all_keys) if all_keys else 0}")
        
        if not all_keys or index < 0 or index >= len(all_keys):
            logger.warning(f"[PROFILE FLOW] WARNING: Invalid index or no keys: index={index}, keys_count={len(all_keys) if all_keys else 0}")
            return
        
        key_data = all_keys[index]
        current_index = index + 1
        total_keys = len(all_keys)
        logger.info(f"[PROFILE FLOW] STEP 4.6: _show_key_at_index - Processing key {current_index}/{total_keys}")
        
        # Format vless_key for display
        vless_key_text = ""
        if key_data.get("vless_key"):
            vless_key_text = f"\n{_('profile:message:vless_key_label')}\n<pre>{key_data['vless_key']}</pre>\n"
        
        logger.debug(f"[PROFILE FLOW] STEP 4.6: FORMAT_MESSAGE - Formatting message for key {current_index}/{total_keys}, key_present={bool(key_data.get('key'))}, vless_key_present={bool(key_data.get('vless_key'))}")
        key_text = _("profile:message:key_with_index").format(
            key=key_data.get("key", ""),
            vless_key=vless_key_text,
            current_index=current_index,
            total_keys=total_keys,
            seconds_text=_("60 seconds"),
        )
        
        logger.debug(f"[PROFILE FLOW] STEP 4.7: MESSAGE_FORMATTED - Message formatted, length={len(key_text)}")
        # Update state
        await state.update_data({"current_key_index": index, "key_view_start_time": asyncio.get_event_loop().time()})
        
        # Send or edit message
        message_id = data.get("key_message_id")
        logger.debug(f"[PROFILE FLOW] STEP 4.8: SEND_MESSAGE - Sending/editing message, message_id={message_id}, text_length={len(key_text)}")
        
        if message_id:
            try:
                await callback.bot.edit_message_text(
                    chat_id=callback.message.chat.id,
                    message_id=message_id,
                    text=key_text,
                    reply_markup=keys_viewer_keyboard(current_index, total_keys),
                )
                logger.debug(f"[PROFILE FLOW] STEP 4.8: SEND_MESSAGE - Message edited successfully, message_id={message_id}")
            except Exception as e:
                logger.error(f"[PROFILE FLOW] STEP 4.8: SEND_MESSAGE - Edit failed: {e}, sending new message")
                # If edit fails, send new message
                message = await callback.message.answer(
                    text=key_text,
                    reply_markup=keys_viewer_keyboard(current_index, total_keys),
                )
                await state.update_data({"key_message_id": message.message_id})
                message_id = message.message_id
                logger.debug(f"[PROFILE FLOW] STEP 4.8: SEND_MESSAGE - New message sent, message_id={message_id}")
        else:
            message = await callback.message.answer(
                text=key_text,
                reply_markup=keys_viewer_keyboard(current_index, total_keys),
            )
            await state.update_data({"key_message_id": message.message_id})
            message_id = message.message_id
            logger.debug(f"[PROFILE FLOW] STEP 4.8: SEND_MESSAGE - First message sent, message_id={message_id}")
        
        # Start auto-delete timer with countdown (60 seconds)
        # Cancel previous task if exists for this user
        user_id = user.tg_id
        if user_id in _auto_delete_tasks:
            old_task = _auto_delete_tasks[user_id]
            if not old_task.done():
                old_task.cancel()
                logger.debug(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_CANCEL - Cancelled previous timer for user {user_id}")
        
        # Create new task with countdown and store it
        new_task = asyncio.create_task(_auto_delete_key_message_with_countdown(callback, state, 60, message_id, user_id, current_index, total_keys, key_data))
        _auto_delete_tasks[user_id] = new_task
        logger.debug(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_START - Starting auto-delete timer with countdown for message {message_id}, timeout=60s, user_id={user_id}")
    except Exception as e:
        logger.error(f"[PROFILE FLOW] ERROR: Exception in _show_key_at_index: {e}", exc_info=True)
        raise


async def _auto_delete_key_message_with_countdown(
    callback: CallbackQuery,
    state: FSMContext,
    timeout: int,
    message_id: int,
    user_id: int,
    current_index: int,
    total_keys: int,
    key_data: dict,
) -> None:
    """Auto-delete key message after timeout with visual countdown."""
    logger.debug(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_START - Starting auto-delete timer with countdown for message {message_id}, timeout={timeout}s, user_id={user_id}, current_index={current_index}, total_keys={total_keys}")
    
    try:
        # Update message with countdown (every 5 seconds to avoid flood control)
        # Update at: 60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 4, 3, 2, 1
        logger.debug(f"[PROFILE FLOW] STEP 4.9.1: COUNTDOWN_START - Starting countdown loop for message {message_id}, timeout={timeout}")
        
        # Update immediately with initial countdown
        remaining = timeout
        last_update_remaining = timeout + 1  # Initialize to different value to ensure first update
        
        while remaining > 0:
            # Determine if we should update (every 5 seconds, or last 5 seconds)
            should_update = (
                remaining % 5 == 0 or  # Every 5 seconds
                remaining <= 5  # Last 5 seconds
            )
            
            # Check if task was cancelled
            if user_id not in _auto_delete_tasks:
                logger.debug(f"[PROFILE FLOW] STEP 4.9.3: AUTO_DELETE_CANCELLED - Task removed from storage for user {user_id}")
                return
            if _auto_delete_tasks[user_id].done():
                logger.debug(f"[PROFILE FLOW] STEP 4.9.3: AUTO_DELETE_CANCELLED - Task done for user {user_id}")
                return
            
            # Verify that the message_id hasn't changed
            data = await state.get_data()
            current_message_id = data.get("key_message_id")
            
            # If message_id changed, stop countdown
            if current_message_id != message_id:
                logger.debug(f"[PROFILE FLOW] STEP 4.9.4: AUTO_DELETE_STOP - Message ID changed, stopping countdown (current={current_message_id}, original={message_id})")
                return
            
            # Update message only when needed (every 5 seconds, or last 5 seconds)
            if should_update and remaining != last_update_remaining:
                logger.debug(f"[PROFILE FLOW] STEP 4.9.2.1: COUNTDOWN_CHECK - should_update={should_update}, remaining={remaining}, last_update_remaining={last_update_remaining}")
                logger.debug(f"[PROFILE FLOW] STEP 4.9.2: COUNTDOWN_TICK - Remaining: {remaining}s for message {message_id}")
                
                # Format vless_key for display
                vless_key_text = ""
                if key_data.get("vless_key"):
                    vless_key_text = f"\n{_('profile:message:vless_key_label')}\n<pre>{key_data['vless_key']}</pre>\n"
                
                # Format seconds text with pluralization
                # Use ngettext for proper pluralization - ngettext(singular, plural, count)
                # For Russian: msgstr[0] = "1 секунду", msgstr[1] = "{} секунды", msgstr[2] = "{} секунд"
                seconds_text = ngettext("1 second", "{} seconds", remaining).format(remaining)
                
                logger.debug(f"[PROFILE FLOW] STEP 4.9.4.1: SECONDS_TEXT - Formatted seconds_text='{seconds_text}' for remaining={remaining}")
                
                # Update message with countdown
                key_text = _("profile:message:key_with_index").format(
                    key=key_data.get("key", ""),
                    vless_key=vless_key_text,
                    current_index=current_index,
                    total_keys=total_keys,
                    seconds_text=seconds_text,
                )
                
                logger.debug(f"[PROFILE FLOW] STEP 4.9.5: COUNTDOWN_UPDATE - Updating message {message_id} with remaining={remaining}s, text_length={len(key_text)}, seconds_text='{seconds_text}'")
                try:
                    await callback.bot.edit_message_text(
                        chat_id=callback.message.chat.id,
                        message_id=message_id,
                        text=key_text,
                        reply_markup=keys_viewer_keyboard(current_index, total_keys),
                    )
                    logger.debug(f"[PROFILE FLOW] STEP 4.9.6: COUNTDOWN_UPDATED - Message {message_id} updated successfully, remaining={remaining}s")
                    last_update_remaining = remaining
                except Exception as e:
                    error_msg = str(e)
                    # If error is "message is not modified", continue countdown (text might be the same due to caching)
                    if "message is not modified" in error_msg.lower():
                        logger.debug(f"[PROFILE FLOW] STEP 4.9.7: AUTO_DELETE_EDIT_SKIP - Message not modified (text same), continuing countdown: {error_msg}")
                        # Continue countdown, don't stop
                        last_update_remaining = remaining
                    elif "flood control" in error_msg.lower():
                        logger.error(f"[PROFILE FLOW] STEP 4.9.7: AUTO_DELETE_EDIT_FLOOD - Flood control, waiting before retry: {error_msg}")
                        # Wait a bit longer and continue
                        await asyncio.sleep(2)
                        last_update_remaining = remaining
                    else:
                        # For other errors (message deleted, etc.), stop countdown
                        logger.error(f"[PROFILE FLOW] STEP 4.9.7: AUTO_DELETE_EDIT_ERROR - Failed to update countdown: {e}", exc_info=True)
                        return
            
            # Sleep 1 second before next iteration
            await asyncio.sleep(1)
            remaining -= 1
        
        # Timeout reached - delete message
        logger.debug(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_TIMEOUT - Timeout reached, deleting message {message_id}")
        
        # Verify that the message_id hasn't changed
        data = await state.get_data()
        current_message_id = data.get("key_message_id")
        
        # Remove task from global storage after completion
        if user_id in _auto_delete_tasks:
            del _auto_delete_tasks[user_id]
        
        # Delete message if ID matches or if current is None (user closed manually)
        if current_message_id == message_id or (current_message_id is None and message_id):
            try:
                await callback.bot.delete_message(
                    chat_id=callback.message.chat.id,
                    message_id=message_id,
                )
                logger.debug(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_SUCCESS - Message {message_id} deleted successfully")
                # Clear state after successful delete
                await state.update_data({
                    "user_keys": None,
                    "current_key_index": None,
                    "key_message_id": None,
                    "key_view_start_time": None,
                })
            except Exception as e:
                logger.error(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_ERROR - Failed to delete message {message_id}: {e}")
                # Clear state even if delete failed
                await state.update_data({
                    "user_keys": None,
                    "current_key_index": None,
                    "key_message_id": None,
                    "key_view_start_time": None,
                })
        else:
            logger.debug(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_SKIP - Message ID changed, skipping delete (current={current_message_id}, original={message_id})")
            
    except asyncio.CancelledError:
        logger.debug(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_CANCELLED - Timer cancelled for user {user_id}")
        if user_id in _auto_delete_tasks:
            del _auto_delete_tasks[user_id]
    except Exception as e:
        logger.error(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_EXCEPTION - Unexpected error: {e}", exc_info=True)
        if user_id in _auto_delete_tasks:
            del _auto_delete_tasks[user_id]


async def _auto_delete_key_message(
    callback: CallbackQuery,
    state: FSMContext,
    timeout: int,
    message_id: int,
) -> None:
    """Auto-delete key message after timeout.
    
    Args:
        callback: CallbackQuery object
        state: FSMContext for state management
        timeout: Timeout in seconds
        message_id: Message ID to delete (to verify it's still the same message)
    """
    user_id = callback.from_user.id
    logger.error(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_START - Starting auto-delete timer for message {message_id}, timeout={timeout}s, user_id={user_id}")
    
    try:
        await asyncio.sleep(timeout)
        # Remove task from global storage after completion
        if user_id in _auto_delete_tasks:
            del _auto_delete_tasks[user_id]
        logger.error(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_TIMEOUT - Timeout reached, checking message_id")
        
        # Verify that the message_id hasn't changed (user might have navigated to another key)
        data = await state.get_data()
        current_message_id = data.get("key_message_id")
        logger.error(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_CHECK - Current message_id={current_message_id}, original={message_id}, match={current_message_id == message_id}")
        
        # Only delete if the message_id matches (same message is still being viewed)
        # Also delete if current_message_id is None but message_id exists (user closed keys manually but timer still running)
        if current_message_id == message_id or (current_message_id is None and message_id):
            try:
                await callback.bot.delete_message(
                    chat_id=callback.message.chat.id,
                    message_id=message_id,
                )
                logger.error(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_SUCCESS - Message {message_id} deleted successfully (current={current_message_id})")
            except Exception as e:
                logger.error(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_ERROR - Failed to delete message {message_id}: {e}")
        else:
            logger.error(f"[PROFILE FLOW] STEP 4.9: AUTO_DELETE_SKIP - Message ID changed, skipping delete (current={current_message_id}, original={message_id})")
            
            # Clear state only if we deleted the message
        await state.update_data({
            "user_keys": None,
            "current_key_index": None,
            "key_message_id": None,
        })
    except asyncio.CancelledError:
        # Task was cancelled (user navigated to another key)
        pass


@router.callback_query(F.data == NavProfile.KEY_NEXT)
async def callback_key_next(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    state: FSMContext,
) -> None:
    """Show next key."""
    data = await state.get_data()
    current_index = data.get("current_key_index", 0)
    all_keys = data.get("user_keys", [])
    
    if current_index < len(all_keys) - 1:
        await _show_key_at_index(callback, user, services, state, current_index + 1)
    await callback.answer()


@router.callback_query(F.data == NavProfile.KEY_PREV)
async def callback_key_prev(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    state: FSMContext,
) -> None:
    """Show previous key."""
    data = await state.get_data()
    current_index = data.get("current_key_index", 0)
    
    if current_index > 0:
        await _show_key_at_index(callback, user, services, state, current_index - 1)
    await callback.answer()


@router.callback_query(F.data == NavProfile.KEY_CLOSE)
async def callback_key_close(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Close key viewer and cancel auto-delete timer."""
    import time
    start_time = time.time()
    user_id = callback.from_user.id
    logger.info(f"[TIMING] KEY_CLOSE_START - User {user_id} clicked Close button at {start_time}")
    
    # Answer callback IMMEDIATELY to make UI responsive
    await callback.answer(_("profile:popup:keys_closed"))
    answer_time = time.time()
    logger.info(f"[TIMING] KEY_CLOSE_ANSWERED - Callback answered in {(answer_time - start_time)*1000:.2f}ms")
    
    # Cancel auto-delete timer if exists
    if user_id in _auto_delete_tasks:
        old_task = _auto_delete_tasks[user_id]
        if not old_task.done():
            old_task.cancel()
            logger.debug(f"[PROFILE FLOW] STEP 4.10: KEY_CLOSE - Cancelled auto-delete timer for user {user_id}")
        try:
            await old_task  # Wait for cancellation to complete
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[PROFILE FLOW] KEY_CLOSE - Error waiting for task cancellation: {e}")
        # Remove from dict using pop() to avoid KeyError if already removed
        _auto_delete_tasks.pop(user_id, None)
    
    data = await state.get_data()
    message_id = data.get("key_message_id")
    
    if message_id:
        try:
            await callback.bot.delete_message(
                chat_id=callback.message.chat.id,
                message_id=message_id,
            )
            logger.debug(f"[PROFILE FLOW] KEY_CLOSE - Deleted key message {message_id} for user {user_id}")
        except TelegramBadRequest as e:
            # Message already deleted - this is OK
            error_str = str(e).lower()
            if "message to delete not found" in error_str or "message can't be deleted" in error_str:
                logger.debug(f"[PROFILE FLOW] KEY_CLOSE - Key message {message_id} already deleted for user {user_id}")
            else:
                logger.warning(f"[PROFILE FLOW] KEY_CLOSE - Failed to delete key message {message_id}: {e}")
        except Exception as e:
            logger.error(f"[PROFILE FLOW] KEY_CLOSE - Error deleting key message {message_id}: {e}")
    
    # Clear state
    await state.update_data({
        "user_keys": None,
        "current_key_index": None,
        "key_message_id": None,
        "key_view_start_time": None,
    })
    complete_time = time.time()
    logger.info(f"[TIMING] KEY_CLOSE_COMPLETE - Total time: {(complete_time - start_time)*1000:.2f}ms")
