"""
Optional Redis helpers for shared rate limiting.
"""

from __future__ import annotations

import logging
import os
from typing import Optional


logger = logging.getLogger(__name__)

try:
    import redis
except ImportError:  # pragma: no cover - optional dependency
    redis = None


class RedisService:
    """Lazy Redis client wrapper with safe fallback semantics."""

    def __init__(self) -> None:
        self.url = (os.getenv("REDIS_URL") or "").strip()
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.url and redis is not None)

    @property
    def client(self):
        if not self.enabled:
            return None
        if self._client is None:
            self._client = redis.Redis.from_url(
                self.url,
                decode_responses=True,
                socket_timeout=1,
                socket_connect_timeout=1,
                retry_on_timeout=True,
            )
        return self._client

    def check_rate_limit(self, key: str, *, limit: int, window_seconds: int) -> Optional[bool]:
        """Return None when Redis is disabled/unavailable so callers can fall back."""
        if not self.enabled:
            return None
        try:
            client = self.client
            if client is None:
                return None
            redis_key = f"rate_limit:{key}"
            current = int(client.incr(redis_key))
            if current == 1:
                client.expire(redis_key, window_seconds)
            return current <= int(limit)
        except Exception as exc:
            logger.warning("Redis rate limit backend unavailable, falling back to DB: %s", exc)
            return None


redis_service = RedisService()
