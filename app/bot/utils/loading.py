"""
Loading indicator utilities for long-running operations.
Provides visual feedback to users during operations like payment processing, server sync, etc.
"""
from aiogram.types import Message
from typing import Optional

from app.bot.utils.i18n import _


async def show_loading(
    message: Message,
    text_key: str = "common:loading:processing",
    **format_kwargs
) -> Optional[Message]:
    """
    Edit message to show loading indicator.
    
    Args:
        message: Message object to edit
        text_key: Localization key for loading text
        **format_kwargs: Formatting arguments for the text
    
    Returns:
        Edited message or None if failed
    """
    try:
        text = _(text_key)
        if format_kwargs:
            text = text.format(**format_kwargs)
        
        return await message.edit_text(text)
    except Exception:
        # Message might be deleted or unchanged
        return None


async def hide_loading(
    message: Message,
    final_text: str,
    **edit_kwargs
) -> Optional[Message]:
    """
    Edit message with final result after operation completes.
    
    Args:
        message: Message object to edit
        final_text: Final text to display
        **edit_kwargs: Additional arguments for edit_text (e.g., reply_markup)
    
    Returns:
        Edited message or None if failed
    """
    try:
        return await message.edit_text(final_text, **edit_kwargs)
    except Exception:
        # Message might be deleted or unchanged
        return None


async def show_loading_answer(
    message: Message,
    text_key: str = "common:loading:processing",
    **format_kwargs
) -> Optional[Message]:
    """
    Send a new message with loading indicator.
    
    Args:
        message: Message object to answer to
        text_key: Localization key for loading text
        **format_kwargs: Formatting arguments for the text
    
    Returns:
        Sent message or None if failed
    """
    try:
        text = _(text_key)
        if format_kwargs:
            text = text.format(**format_kwargs)
        
        return await message.answer(text)
    except Exception:
        return None
