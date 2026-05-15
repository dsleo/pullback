"""TTL cache helpers for discovery.

We wrap `cachetools.TTLCache` because it is not thread-safe by default, and
some metadata fetch paths use `asyncio.to_thread`.
"""

from __future__ import annotations

from collections.abc import Mapping
import threading
from typing import Generic, TypeVar

from cachetools import TTLCache

KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


class ThreadSafeTTLCache(Generic[KeyT, ValueT]):
    """Thin thread-safe wrapper around `cachetools.TTLCache`."""

    def __init__(self, *, maxsize: int, ttl: float) -> None:
        self._cache: TTLCache[KeyT, ValueT] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = threading.Lock()

    def get(self, key: KeyT) -> ValueT | None:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: KeyT, value: ValueT) -> None:
        with self._lock:
            self._cache[key] = value

    def set_many(self, values: Mapping[KeyT, ValueT]) -> None:
        if not values:
            return
        with self._lock:
            for key, value in values.items():
                self._cache[key] = value

