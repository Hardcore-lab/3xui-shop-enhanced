"""Distributed locking using Redis for preventing race conditions."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisLock:
    """Distributed lock using Redis SETNX."""
    
    def __init__(self, redis_client: redis.Redis, lock_key: str, timeout: int = 30, retry_interval: float = 0.1):
        """
        Initialize Redis lock.
        
        Args:
            redis_client: Async Redis client
            lock_key: Key for the lock in Redis
            timeout: Lock timeout in seconds (auto-release)
            retry_interval: Time to wait between lock acquisition attempts
        """
        self.redis_client = redis_client
        self.lock_key = f"lock:{lock_key}"
        self.timeout = timeout
        self.retry_interval = retry_interval
        self.lock_value = None
    
    async def acquire(self, blocking: bool = True, timeout: Optional[int] = None) -> bool:
        """
        Acquire the lock.
        
        Args:
            blocking: If True, wait for lock. If False, return immediately if locked.
            timeout: Maximum time to wait for lock (None = wait indefinitely)
            
        Returns:
            True if lock acquired, False otherwise
        """
        start_time = time.time()
        self.lock_value = str(time.time())
        
        while True:
            # Try to set the key with NX (only if not exists) and EX (expiration)
            acquired = await self.redis_client.set(
                self.lock_key,
                self.lock_value,
                nx=True,  # Only set if key doesn't exist
                ex=self.timeout  # Auto-expire after timeout seconds
            )
            
            if acquired:
                logger.debug(f"Lock acquired: {self.lock_key}")
                return True
            
            if not blocking:
                logger.debug(f"Lock not acquired (non-blocking): {self.lock_key}")
                return False
            
            # Check timeout
            if timeout is not None and (time.time() - start_time) >= timeout:
                logger.warning(f"Lock acquisition timeout: {self.lock_key}")
                return False
            
            # Wait before retrying
            await asyncio.sleep(self.retry_interval)
    
    async def release(self) -> bool:
        """
        Release the lock.
        
        Returns:
            True if lock was released, False if lock didn't exist or value mismatch
        """
        if not self.lock_value:
            return False
        
        # Use Lua script to ensure atomicity: only delete if value matches
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        
        try:
            result = await self.redis_client.eval(lua_script, 1, self.lock_key, self.lock_value)
            if result:
                logger.debug(f"Lock released: {self.lock_key}")
                return True
            else:
                logger.warning(f"Lock release failed (value mismatch or expired): {self.lock_key}")
                return False
        except Exception as e:
            logger.error(f"Error releasing lock {self.lock_key}: {e}")
            return False
    
    async def __aenter__(self):
        """Context manager entry."""
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        await self.release()


@asynccontextmanager
async def redis_lock(redis_client: redis.Redis, lock_key: str, timeout: int = 30, blocking: bool = True, wait_timeout: Optional[int] = None):
    """
    Context manager for Redis-based distributed lock.
    
    Usage:
        async with redis_lock(redis_client, f"user:{user_id}:create_client"):
            # Critical section - only one process can execute this at a time
            await create_vpn_client(user_id)
    
    Args:
        redis_client: Async Redis client
        lock_key: Unique key for the lock
        timeout: Lock auto-release timeout (seconds)
        blocking: Wait for lock if True, return immediately if False
        wait_timeout: Maximum time to wait for lock acquisition
    """
    lock = RedisLock(redis_client, lock_key, timeout=timeout)
    
    try:
        acquired = await lock.acquire(blocking=blocking, timeout=wait_timeout)
        if not acquired:
            raise LockAcquisitionError(f"Could not acquire lock: {lock_key}")
        yield lock
    finally:
        await lock.release()


class LockAcquisitionError(Exception):
    """Raised when lock cannot be acquired."""
    pass
