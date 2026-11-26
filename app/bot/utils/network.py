import asyncio
import time
from hmac import digest
from urllib.parse import urlencode, urljoin, urlparse

import aiohttp

from app.bot.utils.constants import CONNECT_SCHEME_DIRECT, CONNECTION_WEBHOOK


def connection_signature(secret: str, scheme: str, key: str) -> str:
    message = urlencode({"scheme": scheme, "key": key})
    return digest(secret.encode(), message.encode(), "sha256").hex()


def connection_url(base: str, key: str, secret: str) -> str:
    scheme = CONNECT_SCHEME_DIRECT
    query = urlencode({"scheme": scheme, "key": key, "sig": connection_signature(secret, scheme, key)})
    return f"{base.rstrip('/')}{CONNECTION_WEBHOOK}?{query}"


async def ping_url(url: str, timeout: int = 5) -> float | None:
    """
    Measure HTTP latency to a server (not ICMP ping).
    
    Args:
        url: Server URL (can be with or without scheme, e.g., "https://host:port" or "host:port")
        timeout: Request timeout in seconds
        
    Returns:
        Latency in milliseconds or None if unreachable
    """
    try:
        # Normalize URL - ensure it has a scheme
        normalized_url = url
        if not url.startswith(('http://', 'https://')):
            # Default to https if no scheme provided
            normalized_url = f'https://{url}'
        
        async with aiohttp.ClientSession() as session:
            start_time = time.time()
            async with session.get(url=normalized_url, timeout=timeout) as response:
                # Accept any response code - we're just measuring reachability
                # 200, 301, 401, etc. all indicate the server is reachable
                return round((time.time() - start_time) * 1000)
    except aiohttp.ClientError as e:
        # Network/connection errors
        return None
    except asyncio.TimeoutError:
        # Timeout
        return None
    except Exception as e:
        # Unexpected errors - log them for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Ping failed for URL {url}: {e}")
        return None


def extract_base_url(url: str, port: int, path: str) -> str:
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.hostname}:{port}"
    return urljoin(base_url, path)
