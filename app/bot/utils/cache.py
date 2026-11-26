"""
Redis caching utilities for performance optimization.
Provides TTL-based caching for frequently accessed data.

Usage:
------

1. CacheManager is automatically initialized in ServicesContainer and available as services.cache

2. Manual caching in handlers:
   
   # Get user data from cache or database
   if services.cache:
       cached_user = await services.cache.get_user(user.tg_id)
       if cached_user:
           # Use cached data
           pass
   
   # Set user data in cache after fetching from DB
   if services.cache:
       await services.cache.set_user(user.tg_id, user_dict, ttl=60)
   
   # Invalidate cache after updates
   if services.cache:
       await services.cache.invalidate_user(user.tg_id)

3. Cache invalidation locations:
   
   - User.update(): Call services.cache.invalidate_user(tg_id) after updates
   - Server status updates: Call services.cache.invalidate_server(server_id)
   - VPN client updates: Call services.cache.invalidate_user(tg_id)

4. Graceful degradation:
   
   The CacheManager gracefully handles Redis unavailability. If Redis is None,
   all cache operations return immediately without errors, ensuring the bot
   continues to function normally without caching.
"""
import json
import logging
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, ParamSpec, Dict
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


class CacheManager:
    """
    Manager for Redis-based caching with TTL support.
    Provides graceful fallback if Redis is unavailable.
    """
    
    def __init__(self, redis: Optional[Redis] = None):
        self.redis = redis
        self.enabled = redis is not None
    
    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired/disabled
        """
        if not self.enabled:
            return None
            
        try:
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.warning(f"Cache get failed for key {key}: {e}")
            return None
    
    async def set(self, key: str, value: Any, ttl: int) -> bool:
        """
        Set value in cache with TTL.
        
        Args:
            key: Cache key
            value: Value to cache (must be JSON serializable)
            ttl: Time to live in seconds
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
            
        try:
            serialized = json.dumps(value)
            await self.redis.setex(key, ttl, serialized)
            return True
        except Exception as e:
            logger.warning(f"Cache set failed for key {key}: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """
        Delete value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
            
        try:
            await self.redis.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Cache delete failed for key {key}: {e}")
            return False
    
    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.
        
        Args:
            pattern: Key pattern (e.g., "cache:user:*")
            
        Returns:
            Number of keys deleted
        """
        if not self.enabled:
            return 0
            
        try:
            keys = []
            async for key in self.redis.scan_iter(match=pattern):
                keys.append(key)
            
            if keys:
                deleted = await self.redis.delete(*keys)
                return deleted
            return 0
        except Exception as e:
            logger.warning(f"Cache delete_pattern failed for pattern {pattern}: {e}")
            return 0
    
    async def get_user(self, tg_id: int) -> Optional[Dict[str, Any]]:
        """Get cached user data."""
        return await self.get(f"cache:user:{tg_id}")
    
    async def set_user(self, tg_id: int, user_data: Dict[str, Any], ttl: int = 60) -> bool:
        """Cache user data with 60 second TTL."""
        return await self.set(f"cache:user:{tg_id}", user_data, ttl)
    
    async def invalidate_user(self, tg_id: int) -> bool:
        """Invalidate user cache."""
        await self.delete(f"cache:user:{tg_id}")
        await self.delete(f"cache:client:{tg_id}")
        return True
    
    async def get_server_status(self, server_id: int) -> Optional[Dict[str, Any]]:
        """Get cached server status."""
        return await self.get(f"cache:server:{server_id}:status")
    
    async def set_server_status(self, server_id: int, status_data: Dict[str, Any], ttl: int = 10) -> bool:
        """Cache server status with 10 second TTL."""
        return await self.set(f"cache:server:{server_id}:status", status_data, ttl)
    
    async def invalidate_server(self, server_id: int) -> bool:
        """Invalidate server cache."""
        return await self.delete(f"cache:server:{server_id}:status")
    
    async def get_client_data(self, tg_id: int) -> Optional[Dict[str, Any]]:
        """Get cached client data."""
        return await self.get(f"cache:client:{tg_id}")
    
    async def set_client_data(self, tg_id: int, client_data: Dict[str, Any], ttl: int = 30) -> bool:
        """Cache client data with 30 second TTL."""
        return await self.set(f"cache:client:{tg_id}", client_data, ttl)


def cache_result(
    key_pattern: str,
    ttl: int,
    cache_manager_arg: str = "cache_manager"
):
    """
    Decorator to cache function results in Redis.
    
    Args:
        key_pattern: Cache key pattern with placeholders (e.g., "user:{tg_id}")
        ttl: Time to live in seconds
        cache_manager_arg: Name of the CacheManager argument in the function
        
    Example:
        @cache_result(key_pattern="cache:user:{tg_id}", ttl=60)
        async def get_user(tg_id: int, cache_manager: CacheManager):
            # ... fetch user from database
            return user
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # Extract cache_manager from kwargs or args
            cache_manager = kwargs.get(cache_manager_arg)
            
            if not cache_manager or not isinstance(cache_manager, CacheManager):
                # Cache manager not available, call function directly
                return await func(*args, **kwargs)
            
            # Build cache key from pattern and function arguments
            try:
                # Extract variables from kwargs for key formatting
                key = f"cache:{key_pattern}".format(**kwargs)
            except KeyError:
                # If key formatting fails, skip caching
                logger.warning(f"Cache key formatting failed for pattern {key_pattern}")
                return await func(*args, **kwargs)
            
            # Try to get from cache
            cached = await cache_manager.get(key)
            if cached is not None:
                logger.debug(f"Cache hit for key: {key}")
                return cached
            
            # Cache miss, call function
            logger.debug(f"Cache miss for key: {key}")
            result = await func(*args, **kwargs)
            
            # Store in cache if result is not None
            if result is not None:
                await cache_manager.set(key, result, ttl)
            
            return result
        
        return wrapper
    return decorator

