"""Caching utilities and decorators."""

import time
from functools import wraps
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class MemoryCache:
    """Simple in-memory cache with TTL support."""

    def __init__(self):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        """Get value from cache if not expired."""
        if key in self._cache:
            value, expires_at = self._cache[key]
            if time.time() < expires_at:
                self._hits += 1
                return value
            del self._cache[key]
        self._misses += 1
        return None

    def set(self, key: str, value: Any, ttl: int) -> None:
        """Set value with TTL in seconds."""
        self._cache[key] = (value, time.time() + ttl)

    def clear(self) -> None:
        """Clear all cached values."""
        self._cache.clear()

    @property
    def stats(self) -> dict[str, int]:
        """Get cache statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total * 100, 2) if total > 0 else 0,
            "size": len(self._cache),
        }


# Global cache instance
_cache = MemoryCache()


def cached(ttl: int = 300) -> Callable:
    """
    Decorator to cache async function results.

    Args:
        ttl: Time-to-live in seconds (default: 5 minutes)

    Usage:
        @cached(ttl=300)
        async def fetch_data(id: int) -> Data:
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            # Generate cache key
            key = f"{func.__module__}.{func.__name__}:{args}:{kwargs}"

            # Check cache
            result = _cache.get(key)
            if result is not None:
                return result

            # Call function and cache result
            result = await func(*args, **kwargs)
            _cache.set(key, result, ttl)
            return result

        return wrapper
    return decorator


def get_cache() -> MemoryCache:
    """Get global cache instance."""
    return _cache
