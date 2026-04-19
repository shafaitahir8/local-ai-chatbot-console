"""
Optional Ollama sentinel that keeps local-model readiness outside chat requests.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import Optional

from ..models.database import SessionLocal
from .health_service import health_service
from .llm_providers.local_provider import LocalProvider
from .supervisor_service import supervisor_service


logger = logging.getLogger(__name__)


class OllamaSentinelService:
    """Periodically verify or start Ollama in a dedicated supervised unit."""

    def __init__(self) -> None:
        self.poll_interval_seconds = float(os.getenv("OLLAMA_SENTINEL_POLL_SECONDS", "15"))
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        db = SessionLocal()
        try:
            supervisor_service.upsert_component_lease(
                db,
                component_name="ollama-sentinel",
                unit_type="ollama-sentinel",
                lease_owner=self._worker_id,
                state="running",
            )
        finally:
            db.close()
        self._task = asyncio.create_task(self._run_loop(), name="ollama-sentinel")

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        db = SessionLocal()
        try:
            supervisor_service.upsert_component_lease(
                db,
                component_name="ollama-sentinel",
                unit_type="ollama-sentinel",
                lease_owner=self._worker_id,
                state="stopped",
            )
        finally:
            db.close()

    async def _run_loop(self) -> None:
        provider = LocalProvider(model=os.getenv("OLLAMA_SENTINEL_MODEL", "qwen2.5:1.5b"))
        while not self._stop_event.is_set():
            state = "running"
            detail = "ollama reachable"
            metadata = {"worker_id": self._worker_id, "base_url": provider.base_url}
            try:
                ready = await provider._ensure_ollama_running()
                if not ready:
                    state = "degraded"
                    detail = "ollama unavailable"
            except Exception as exc:
                logger.warning("Ollama sentinel probe failed: %s", exc)
                state = "degraded"
                detail = str(exc)

            db = SessionLocal()
            try:
                supervisor_service.upsert_component_lease(
                    db,
                    component_name="ollama-sentinel",
                    unit_type="ollama-sentinel",
                    lease_owner=self._worker_id,
                    state=state,
                    last_error=None if state == "running" else detail,
                    metadata=metadata,
                )
            finally:
                db.close()

            health_service.set_component(
                "ollama_sentinel",
                "healthy" if state == "running" else "degraded",
                critical=False,
                detail=detail,
                metadata=metadata,
            )
            await asyncio.sleep(self.poll_interval_seconds)


ollama_sentinel_service = OllamaSentinelService()
