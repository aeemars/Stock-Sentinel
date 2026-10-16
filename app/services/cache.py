import threading
import time
from typing import Any, Dict, Optional, Tuple

class MemoryCache:
    """
    High-performance, thread-safe in-memory TTL cache with prefix invalidation.
    Designed for sub-millisecond retrieval of user sessions, aggregates, and quotes.
    """
    def __init__(self, max_entries: int = 5000):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = threading.RLock()
        self._max_entries = max_entries

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            val, expire_at = entry
            if now > expire_at:
                del self._cache[key]
                return None
            return val

    def set(self, key: str, value: Any, ttl: float = 60.0) -> None:
        now = time.time()
        with self._lock:
            # Simple eviction if exceeding max size
            if len(self._cache) >= self._max_entries:
                self._prune_expired(now)
                if len(self._cache) >= self._max_entries:
                    # Pop first 100 items if still saturated
                    keys_to_pop = list(self._cache.keys())[:100]
                    for k in keys_to_pop:
                        self._cache.pop(k, None)

            self._cache[key] = (value, now + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def delete_by_prefix(self, prefix: str) -> int:
        """Invalidate all keys beginning with prefix (e.g. 'user:42:', 'portfolio:1:')."""
        with self._lock:
            keys_to_remove = [k for k in self._cache.keys() if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._cache[k]
            return len(keys_to_remove)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _prune_expired(self, now: float) -> None:
        expired = [k for k, (_, expire_at) in self._cache.items() if now > expire_at]
        for k in expired:
            del self._cache[k]

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            active = sum(1 for _, expire_at in self._cache.values() if expire_at > now)
            return {
                "total_entries": len(self._cache),
                "active_entries": active,
                "max_entries": self._max_entries
            }

# Global in-memory cache instance
cache = MemoryCache(max_entries=10000)

# Specialized User Auth Cache Helpers
USER_CACHE_TTL = 60.0  # seconds

def get_cached_user(user_id: int):
    return cache.get(f"auth:user:{user_id}")

def set_cached_user(user_id: int, user: Any, ttl: float = USER_CACHE_TTL):
    cache.set(f"auth:user:{user_id}", user, ttl=ttl)

def invalidate_user_cache(user_id: int):
    cache.delete(f"auth:user:{user_id}")
    cache.delete_by_prefix(f"data:user:{user_id}:")
