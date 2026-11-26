import re
from urllib.parse import urlparse

IP_PATTERN = re.compile(
    r"^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}" r"(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
)


def is_valid_host(data: str) -> bool:
    parsed = urlparse(data)
    if all([parsed.scheme, parsed.netloc]):
        return True
    return bool(IP_PATTERN.match(data))


def is_valid_client_count(data: str) -> bool:
    return data.isdigit() and 1 <= int(data) <= 10000


def is_valid_user_id(data: str) -> bool:
    return data.isdigit() and 1 <= int(data) <= 1000000000000


def is_valid_message_text(data: str) -> bool:
    return len(data) <= 4096


def is_valid_location(data: str) -> bool:
    """Validate server location (max 32 characters, not empty)."""
    return bool(data.strip()) and len(data.strip()) <= 32


def is_valid_username(data: str) -> bool:
    """Validate server username (max 255 characters, not empty)."""
    return bool(data.strip()) and len(data.strip()) <= 255


def is_valid_password(data: str) -> bool:
    """Validate server password (max 255 characters, not empty)."""
    return bool(data.strip()) and len(data.strip()) <= 255


def is_valid_search_query(data: str) -> bool:
    """Validate search query (min 3 characters, max 100 characters)."""
    return bool(data.strip()) and 3 <= len(data.strip()) <= 100


def is_valid_days(data: str) -> bool:
    """Validate number of days (1-365)."""
    try:
        days = int(data)
        return 1 <= days <= 365
    except (ValueError, TypeError):
        return False


def is_valid_page(data: str) -> bool:
    """Validate page number (>= 0)."""
    try:
        page = int(data)
        return page >= 0
    except (ValueError, TypeError):
        return False


def is_valid_limit(data: str) -> bool:
    """Validate limit (1-1000)."""
    try:
        limit = int(data)
        return 1 <= limit <= 1000
    except (ValueError, TypeError):
        return False


def sanitize_search_query(query: str) -> str:
    """
    Санитизирует поисковый запрос для защиты от SQL injection.
    
    Args:
        query: Поисковый запрос
    
    Returns:
        Санитизированный запрос
    """
    # Удаляем опасные символы
    dangerous_chars = ["'", '"', ";", "--", "/*", "*/", "xp_", "sp_"]
    sanitized = query
    for char in dangerous_chars:
        sanitized = sanitized.replace(char, "")
    
    # Ограничиваем длину
    sanitized = sanitized[:100]
    
    return sanitized.strip()
