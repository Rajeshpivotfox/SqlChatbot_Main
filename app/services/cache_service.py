import hashlib
import json
import time
import structlog
from typing import Any

logger = structlog.get_logger(__name__)


class CacheService:
    """In-memory cache with TTL. Can be swapped for Redis in production."""

    def __init__(self, ttl_seconds: int = 3600, max_size: int = 1000):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        logger.debug("cache_hit", key=key[:32])
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if len(self._store) >= self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k][1])
            del self._store[oldest_key]

        expires_at = time.time() + (ttl or self._ttl)
        self._store[key] = (value, expires_at)
        logger.debug("cache_set", key=key[:32])

    @staticmethod
    def make_key(question: str, page: int, page_size: int) -> str:
        """Generate a deterministic cache key from query parameters."""
        raw = json.dumps({"q": question.lower().strip(),
                          "p": page, "ps": page_size}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()
