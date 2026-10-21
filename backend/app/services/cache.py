import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from app.config import settings

logger = logging.getLogger(__name__)

# Try importing pymemcache
try:
    from pymemcache.client.base import Client as MemcacheClient
    from pymemcache import serde
    HAS_PYMEMCACHE = True
except ImportError:
    HAS_PYMEMCACHE = False


class MemoryCache:
    """
    High-performance, thread-safe in-memory TTL cache with prefix invalidation.
    Designed for sub-millisecond retrieval of user sessions, aggregates, and quotes.
    """
    def __init__(self, max_entries: int = 10000):
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
            if len(self._cache) >= self._max_entries:
                self._prune_expired(now)
                if len(self._cache) >= self._max_entries:
                    keys_to_pop = list(self._cache.keys())[:100]
                    for k in keys_to_pop:
                        self._cache.pop(k, None)

            self._cache[key] = (value, now + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def delete_by_prefix(self, prefix: str) -> int:
        """Invalidate all keys beginning with prefix (e.g. 'data:user:42:')."""
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
                "type": "memory",
                "total_entries": len(self._cache),
                "active_entries": active,
                "max_entries": self._max_entries,
            }


class CentralizedCache:
    """
    Centralized caching abstraction supporting Memcached (SEC-10) with automatic
    in-memory fallback when Memcached is offline or unconfigured.
    """
    def __init__(self):
        self._memcache_client: Optional[Any] = None
        self._memory_fallback = MemoryCache(max_entries=10000)
        self._tracked_keys: Set[str] = set()
        self._lock = threading.RLock()
        self._init_memcached()

    def _init_memcached(self):
        servers = settings.MEMCACHED_SERVERS
        if not servers or not HAS_PYMEMCACHE:
            return

        try:
            # Parse host:port or default to localhost:11211
            parts = servers.strip().split(",")
            first_server = parts[0].strip()
            if ":" in first_server:
                host, port = first_server.split(":", 1)
                server_tuple = (host.strip(), int(port.strip()))
            else:
                server_tuple = (first_server, 11211)

            client = MemcacheClient(
                server_tuple,
                serde=serde.pickle_serde,
                connect_timeout=1.0,
                timeout=1.0,
                ignore_exc=True,
            )
            # Test connectivity
            client.version()
            self._memcache_client = client
            logger.info(f"[Cache] Connected to centralized Memcached at {server_tuple}")
        except Exception as exc:
            logger.warning(f"[Cache] Memcached unavailable ({exc}); operating with in-memory cache")
            self._memcache_client = None

    def _clean_key(self, key: str) -> str:
        # Memcached keys cannot contain whitespace or control chars and max 250 bytes
        return re.sub(r"[^\w:.-]", "_", key)[:240]

    def get(self, key: str) -> Optional[Any]:
        if self._memcache_client:
            try:
                clean_k = self._clean_key(key)
                val = self._memcache_client.get(clean_k)
                if val is not None:
                    return val
            except Exception:
                pass
        return self._memory_fallback.get(key)

    def set(self, key: str, value: Any, ttl: float = 60.0) -> None:
        clean_k = self._clean_key(key)
        with self._lock:
            self._tracked_keys.add(key)
            if len(self._tracked_keys) > 50000:
                self._tracked_keys.clear()

        # Always update memory cache for instant local retrieval
        self._memory_fallback.set(key, value, ttl=ttl)

        if self._memcache_client:
            try:
                self._memcache_client.set(clean_k, value, expire=int(ttl))
            except Exception:
                pass

    def delete(self, key: str) -> None:
        clean_k = self._clean_key(key)
        with self._lock:
            self._tracked_keys.discard(key)

        self._memory_fallback.delete(key)
        if self._memcache_client:
            try:
                self._memcache_client.delete(clean_k)
            except Exception:
                pass

    def delete_by_prefix(self, prefix: str) -> int:
        count = self._memory_fallback.delete_by_prefix(prefix)
        with self._lock:
            matching = [k for k in self._tracked_keys if k.startswith(prefix)]
            for k in matching:
                self._tracked_keys.discard(k)

        if self._memcache_client and matching:
            try:
                for k in matching:
                    self._memcache_client.delete(self._clean_key(k))
            except Exception:
                pass
        return max(count, len(matching))

    def clear(self) -> None:
        with self._lock:
            self._tracked_keys.clear()
        self._memory_fallback.clear()
        if self._memcache_client:
            try:
                self._memcache_client.flush_all()
            except Exception:
                pass

    def stats(self) -> dict:
        base = self._memory_fallback.stats()
        base["memcached_connected"] = bool(self._memcache_client is not None)
        base["memcached_servers"] = settings.MEMCACHED_SERVERS
        return base


# Global centralized cache instance (Memcached + In-Memory Fallback)
cache = CentralizedCache()

# Specialized User Auth Cache Helpers
USER_CACHE_TTL = 60.0  # seconds

def get_cached_user(user_id: int):
    return cache.get(f"auth:user:{user_id}")

def set_cached_user(user_id: int, user: Any, ttl: float = USER_CACHE_TTL):
    cache.set(f"auth:user:{user_id}", user, ttl=ttl)

def invalidate_user_cache(user_id: int):
    cache.delete(f"auth:user:{user_id}")
    cache.delete_by_prefix(f"data:user:{user_id}:")


# ── Rate Limiting Utilities (SEC-03) ──
def check_rate_limit(key: str, max_attempts: int = 5) -> bool:
    """Return True if under rate limit, False if limit exceeded."""
    current = cache.get(f"ratelimit:{key}")
    if current is None:
        return True
    return int(current) < max_attempts


def record_rate_limit(key: str, window_seconds: int = 60) -> int:
    """Increment attempt count for key, returning new count."""
    r_key = f"ratelimit:{key}"
    current = cache.get(r_key)
    new_val = 1 if current is None else int(current) + 1
    cache.set(r_key, new_val, ttl=window_seconds)
    return new_val


def reset_rate_limit(key: str) -> None:
    """Reset attempt count for key upon successful authentication."""
    cache.delete(f"ratelimit:{key}")
