import logging
import math
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from aiogram.utils.i18n import gettext as _

from app.bot.utils.constants import UNLIMITED

if TYPE_CHECKING:
    from app.db.models import Server, User

logger = logging.getLogger(__name__)


def format_size(size_bytes: int) -> str:
    try:
        if size_bytes == -1:
            return UNLIMITED
        if size_bytes == 0:
            return f"0 {_('MB')}"

        size_units = [_("MB"), _("GB"), _("TB"), _("PB"), _("EB"), _("ZB"), _("YB")]
        size_in_mb = max(size_bytes / 1024**2, 1)
        i = min(int(math.log(size_in_mb, 1024)), len(size_units) - 1)
        s = round(size_in_mb / (1024**i), 2)

        return f"{int(s) if s.is_integer() else s} {size_units[i]}"
    except Exception as exception:
        logger.error(f"Error converting size: {exception}")
        return f"0 {_('MB')}"


def format_remaining_time(timestamp: int) -> str:
    try:
        if timestamp == -1:
            return UNLIMITED

        now = datetime.now(timezone.utc)
        expiry_datetime = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
        time_left = expiry_datetime - now

        days, remainder = divmod(time_left.total_seconds(), 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60

        parts = []
        if days > 0:
            parts.append(f"{int(days)}{_('d')}")
        if hours > 0:
            parts.append(f"{int(hours)}{_('h')}")
        if minutes > 0 or not parts:
            parts.append(f"{int(minutes)}{_('m')}")

        return " ".join(parts)
    except Exception as exception:
        logger.error(f"Error calculating time to expiry: {exception}")
        return f"0{_('m')}"


def format_device_count(devices: int) -> str:
    return (
        UNLIMITED + _("devices")
        if devices == -1
        else _("1 device", "{} devices", devices).format(devices)
    )


def format_subscription_period(days: int) -> str:
    if days == -1:
        return UNLIMITED
    if days % 365 == 0 and days != 0:
        return _("1 year", "{} years", days // 365).format(days // 365)
    if days % 30 == 0 and days != 0:
        return _("1 month", "{} months", days // 30).format(days // 30)
    return _("1 day", "{} days", days).format(days)


def to_decimal(amount: float | str | Decimal | int) -> Decimal:
    DECIMAL_SCALE = 18
    DECIMAL_FORMAT = f"1.{'0' * DECIMAL_SCALE}"

    if isinstance(amount, Decimal):
        result = amount
    else:
        result = Decimal(str(amount))

    return result.quantize(Decimal(DECIMAL_FORMAT), rounding=ROUND_DOWN)


def format_subscription_status(user: "User", expiry_timestamp: int | None = None) -> str:
    """
    Форматирует статус подписки с визуальными индикаторами.
    
    Args:
        user: Пользователь
        expiry_timestamp: Временная метка истечения подписки (опционально)
    
    Returns:
        Строка с форматированным статусом подписки
    """
    if not user.server_id:
        return "🔴 " + _("subscription:status:expired")
    
    if expiry_timestamp is None or expiry_timestamp == -1:
        return "🟢 " + _("subscription:status:active_unlimited")
    
    now = datetime.now(timezone.utc)
    expiry_datetime = datetime.fromtimestamp(expiry_timestamp / 1000, timezone.utc)
    time_left = expiry_datetime - now
    
    if time_left.total_seconds() < 0:
        return "🔴 " + _("subscription:status:expired")
    
    days_left = time_left.days
    
    # Цветовая индикация
    if days_left >= 7:
        status_emoji = "🟢"
        status_text = _("subscription:status:active")
    elif days_left >= 3:
        status_emoji = "🟡"
        status_text = _("subscription:status:expiring_soon")
    else:
        status_emoji = "🟠"
        status_text = _("subscription:status:expiring_very_soon")
    
    # Прогресс-бар (текстовый)
    total_days = 30  # Предполагаем стандартный период подписки
    progress_percent = min(100, max(0, int((days_left / total_days) * 100)))
    filled = int(progress_percent / 10)
    progress_bar = "▰" * filled + "▱" * (10 - filled)
    
    return f"{status_emoji} {status_text} ({days_left} {_('days')}) [{progress_bar}]"


def format_server_status(server: "Server", current_clients: int | None = None) -> str:
    """
    Форматирует статус сервера с визуальными индикаторами.
    
    Args:
        server: Сервер
        current_clients: Текущее количество клиентов (опционально)
    
    Returns:
        Строка с форматированным статусом сервера
    """
    if server.online:
        status_emoji = "🟢"
        status_text = _("server:status:online")
    else:
        status_emoji = "🔴"
        status_text = _("server:status:offline")
    
    if current_clients is None:
        current_clients = server.current_clients if hasattr(server, 'current_clients') else 0
    
    load_percent = int((current_clients / server.max_clients * 100)) if server.max_clients > 0 else 0
    
    # Цветовая индикация загрузки
    if load_percent < 50:
        load_emoji = "🟢"
    elif load_percent < 80:
        load_emoji = "🟡"
    else:
        load_emoji = "🔴"
    
    # Прогресс-бар загрузки
    filled = int(load_percent / 10)
    progress_bar = "▰" * filled + "▱" * (10 - filled)
    
    return (
        f"{status_emoji} {status_text} | "
        f"{load_emoji} {current_clients}/{server.max_clients} ({load_percent}%) [{progress_bar}]"
    )


def format_payment_status(status: str) -> str:
    """
    Форматирует статус платежа с визуальными индикаторами.
    
    Args:
        status: Статус транзакции
    
    Returns:
        Строка с форматированным статусом платежа
    """
    status_map = {
        "pending": ("⏳", _("payment:status:pending")),
        "completed": ("✅", _("payment:status:completed")),
        "canceled": ("❌", _("payment:status:canceled")),
        "refunded": ("↩️", _("payment:status:refunded")),
    }
    
    emoji, text = status_map.get(status.lower(), ("❓", _("payment:status:unknown")))
    return f"{emoji} {text}"
