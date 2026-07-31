import time
import os
import json
import logging
from threading import Lock
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import redis
except ImportError:
    redis = None

class HybridCache:
    """
    A cache that prefers Redis if a REDIS_URL is provided in the environment,
    but gracefully falls back to an in-memory TTL cache if Redis is unavailable
    or not installed. This solves cross-instance staleness in serverless environments.
    """
    def __init__(self, ttl_seconds: int = 60):
        self.ttl = ttl_seconds
        
        redis_url = os.environ.get("REDIS_URL")
        
        self.use_redis = False
        if redis and redis_url:
            try:
                self.redis_client = redis.from_url(redis_url)
                self.redis_client.ping()
                self.use_redis = True
                logger.info("HybridCache: Redis connected successfully.")
            except Exception as e:
                logger.warning("HybridCache: Redis connection failed (%s). Falling back to memory cache.", e)
        else:
            if not redis_url:
                logger.info("HybridCache: No REDIS_URL found. Using in-memory cache.")
            elif not redis:
                logger.warning("HybridCache: REDIS_URL found but 'redis' python package is not installed. Using in-memory cache.")
                
        self.cache = {}
        self.lock = Lock()

    def get(self, key: str) -> Optional[Any]:
        if self.use_redis:
            try:
                val = self.redis_client.get(key)
                if val:
                    return json.loads(val)
                return None
            except Exception as e:
                logger.error("HybridCache Redis get error: %s", e)
                
        # Memory fallback
        with self.lock:
            if key in self.cache:
                value, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl:
                    return value
                else:
                    del self.cache[key]
            return None

    def set(self, key: str, value: Any):
        if self.use_redis:
            try:
                self.redis_client.setex(key, self.ttl, json.dumps(value))
                return
            except Exception as e:
                logger.error("HybridCache Redis set error: %s", e)

        # Memory fallback
        with self.lock:
            self.cache[key] = (value, time.time())

    def invalidate(self, key: str):
        if self.use_redis:
            try:
                self.redis_client.delete(key)
                return
            except Exception as e:
                logger.error("HybridCache Redis invalidate error: %s", e)
                
        with self.lock:
            if key in self.cache:
                del self.cache[key]

    def invalidate_all(self):
        if self.use_redis:
            try:
                self.redis_client.flushdb()
                return
            except Exception as e:
                logger.error("HybridCache Redis invalidate_all error: %s", e)
                
        with self.lock:
            self.cache.clear()

# Global cache instance (30s TTL to speed up Vercel loading without massive staleness)
global_cache = HybridCache(ttl_seconds=30)
