"""
Async bulkhead concurrency limits for external dependencies.
"""

from __future__ import annotations

import asyncio
import os
from typing import Dict


class Bulkhead:
    """Small async semaphore wrapper with introspection."""

    def __init__(self, name: str, limit: int) -> None:
        self.name = name
        self.limit = max(1, int(limit))
        self._semaphore = asyncio.Semaphore(self.limit)

    async def __aenter__(self):
        await self._semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._semaphore.release()
        return False

    def snapshot(self) -> dict:
        available = getattr(self._semaphore, "_value", None)
        in_use = None if available is None else self.limit - int(available)
        return {
            "name": self.name,
            "limit": self.limit,
            "in_use": in_use,
        }


_bulkheads: Dict[str, Bulkhead] = {}


def _default_limit(name: str) -> int:
    defaults = {
        "localprovider.chat": int(os.getenv("BULKHEAD_LOCAL_CHAT", "2")),
        "openaiprovider.chat": int(os.getenv("BULKHEAD_OPENAI_CHAT", "16")),
        "geminiprovider.chat": int(os.getenv("BULKHEAD_GEMINI_CHAT", "8")),
        "claudeprovider.chat": int(os.getenv("BULKHEAD_CLAUDE_CHAT", "8")),
        "localprovider.embeddings": int(os.getenv("BULKHEAD_LOCAL_EMBEDDINGS", "2")),
        "openaiprovider.embeddings": int(os.getenv("BULKHEAD_OPENAI_EMBEDDINGS", "8")),
        "geminiprovider.embeddings": int(os.getenv("BULKHEAD_GEMINI_EMBEDDINGS", "4")),
        "claudeprovider.embeddings": int(os.getenv("BULKHEAD_CLAUDE_EMBEDDINGS", "4")),
    }
    return defaults.get(name, int(os.getenv("BULKHEAD_DEFAULT_LIMIT", "8")))


def get_bulkhead(name: str) -> Bulkhead:
    if name not in _bulkheads:
        _bulkheads[name] = Bulkhead(name=name, limit=_default_limit(name))
    return _bulkheads[name]
