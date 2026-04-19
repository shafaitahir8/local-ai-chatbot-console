"""
Non-blocking startup warmup orchestration for first-message latency reduction.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text

from ..models.database import AsyncSessionLocal, Bot, SessionLocal
from .bot_service import bot_service
from .health_service import health_service
from .metrics_service import metrics_service
from .supervisor_service import supervisor_service


logger = logging.getLogger(__name__)


class StartupWarmupService:
    """Coordinate background startup hydration without blocking request acceptance."""

    def __init__(self) -> None:
        self.lease_interval_seconds = max(2.0, float(os.getenv("STARTUP_WARMUP_LEASE_INTERVAL_SECONDS", "5")))
        self.keepalive_interval_seconds = max(
            60.0,
            float(os.getenv("LOCAL_LLM_KEEPALIVE_INTERVAL_SECONDS", "420")),
        )
        self.provider_prime_concurrency = max(1, int(os.getenv("STARTUP_PROVIDER_PRIME_CONCURRENCY", "4")))
        self.index_hydration_concurrency = max(1, int(os.getenv("STARTUP_INDEX_HYDRATION_CONCURRENCY", "2")))
        self.local_warmup_concurrency = max(1, int(os.getenv("STARTUP_LOCAL_WARMUP_CONCURRENCY", "1")))
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._warmup_task: Optional[asyncio.Task] = None
        self._pulse_task: Optional[asyncio.Task] = None
        self._lease_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._state_lock = threading.Lock()
        self._state: Dict[str, Any] = {
            "phase": "idle",
            "hot_state": "cold",
            "hot_ready": False,
            "detail": "warmup idle",
            "started_at": None,
            "completed_at": None,
            "last_pulse_at": None,
            "database_ready": False,
            "errors": [],
            "provider_clients_ready": [],
            "local_models_hot": [],
            "hydrated_bot_ids": [],
        }

    def _provider_key(self, provider_type: str, model: Optional[str]) -> str:
        clean_model = (model or "default").strip() or "default"
        return f"{provider_type}:{clean_model}"

    def _record_error(self, detail: str) -> None:
        with self._state_lock:
            errors = list(self._state.get("errors") or [])
            if detail not in errors:
                errors.append(detail)
            self._state["errors"] = errors[-12:]

    def _update_state(self, **values: Any) -> None:
        with self._state_lock:
            self._state.update(values)
            provider_clients_ready = sorted(set(self._state.get("provider_clients_ready") or []))
            local_models_hot = sorted(set(self._state.get("local_models_hot") or []))
            hydrated_bot_ids = sorted(set(self._state.get("hydrated_bot_ids") or []))
            self._state["provider_clients_ready"] = provider_clients_ready
            self._state["local_models_hot"] = local_models_hot
            self._state["hydrated_bot_ids"] = hydrated_bot_ids
            hot_ready = bool(
                self._state.get("database_ready")
                and (self._state.get("phase") in {"completed", "steady"})
                and not self._state.get("errors")
            )
            self._state["hot_ready"] = hot_ready
            if hot_ready:
                self._state["hot_state"] = "hot"
            elif self._state.get("phase") in {"warming", "scheduled"}:
                self._state["hot_state"] = "warming"
            elif self._state.get("errors"):
                self._state["hot_state"] = "degraded"
            elif self._state.get("database_ready"):
                self._state["hot_state"] = "warm"
            else:
                self._state["hot_state"] = "cold"

    def snapshot(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "phase": self._state.get("phase"),
                "hot_state": self._state.get("hot_state"),
                "hot_ready": bool(self._state.get("hot_ready")),
                "detail": self._state.get("detail"),
                "started_at": self._state.get("started_at"),
                "completed_at": self._state.get("completed_at"),
                "last_pulse_at": self._state.get("last_pulse_at"),
                "database_ready": bool(self._state.get("database_ready")),
                "provider_clients_ready": list(self._state.get("provider_clients_ready") or []),
                "local_models_hot": list(self._state.get("local_models_hot") or []),
                "hydrated_bot_ids": list(self._state.get("hydrated_bot_ids") or []),
                "errors": list(self._state.get("errors") or []),
                "worker_id": self._worker_id,
            }

    def get_bot_hot_status(self, bot: Bot) -> Dict[str, Any]:
        snapshot = self.snapshot()
        provider_key = self._provider_key(bot.provider_type, bot.model)
        index_status = bot_service.embedding_service.get_index_status(bot.id)
        index_required = index_status.get("state") == "active"
        index_hydrated = bool(
            not index_required
            or bot.id in set(snapshot.get("hydrated_bot_ids") or [])
            or bot_service.embedding_service.is_bundle_hydrated(bot.id)
        )
        provider_hot = bool(
            provider_key in set(snapshot.get("provider_clients_ready") or [])
            or provider_key in set(snapshot.get("local_models_hot") or [])
        )
        if bot.provider_type == "local":
            provider_hot = provider_key in set(snapshot.get("local_models_hot") or [])

        hot_ready = bool(snapshot.get("database_ready") and provider_hot and index_hydrated)
        if hot_ready:
            hot_state = "hot"
        elif snapshot.get("phase") in {"warming", "scheduled"}:
            hot_state = "warming"
        elif snapshot.get("errors"):
            hot_state = "degraded"
        else:
            hot_state = "cold"

        detail_parts = []
        detail_parts.append("provider hot" if provider_hot else "provider warming")
        if index_required:
            detail_parts.append("index hydrated" if index_hydrated else "index hydrating")
        else:
            detail_parts.append("no active index")

        return {
            "hot_state": hot_state,
            "hot_ready": hot_ready,
            "provider_ready": provider_hot,
            "index_hydrated": index_hydrated,
            "detail": ", ".join(detail_parts),
        }

    async def start(self) -> None:
        if self._warmup_task and not self._warmup_task.done():
            return
        self._stop_event = asyncio.Event()
        started_at = datetime.utcnow().isoformat() + "Z"
        self._update_state(
            phase="scheduled",
            detail="startup warmup scheduled",
            started_at=started_at,
            completed_at=None,
            errors=[],
        )
        self._warmup_task = asyncio.create_task(self._run_warmup(), name="startup-warmup")
        self._pulse_task = asyncio.create_task(self._run_keepalive_loop(), name="startup-warmup-pulse")
        self._lease_task = asyncio.create_task(self._run_lease_loop(), name="startup-warmup-lease")

    async def shutdown(self) -> None:
        self._stop_event.set()
        for task in (self._warmup_task, self._pulse_task, self._lease_task):
            if task:
                task.cancel()
        for task in (self._warmup_task, self._pulse_task, self._lease_task):
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._update_state(
            phase="stopped",
            detail="warmup stopped",
            hot_ready=False,
            database_ready=False,
        )

        db = SessionLocal()
        try:
            supervisor_service.upsert_component_lease(
                db,
                component_name="warmup",
                unit_type="warmup",
                lease_owner=self._worker_id,
                state="stopped",
                metadata=self.snapshot(),
            )
        finally:
            db.close()
        health_service.set_component(
            "warmup",
            "degraded",
            critical=False,
            detail="warmup stopped",
            metadata=self.snapshot(),
        )

    async def _list_active_bots(self) -> List[Bot]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Bot).filter(Bot.is_active == True)  # noqa: E712
            )
            return list(result.scalars().all())

    async def _prime_database(self) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        self._update_state(database_ready=True, detail="database pool primed")

    async def _prime_provider_clients(self, bots: List[Bot]) -> None:
        semaphore = asyncio.Semaphore(self.provider_prime_concurrency)

        async def _prime(bot: Bot) -> None:
            provider_key = self._provider_key(bot.provider_type, bot.model)
            if provider_key in set(self.snapshot().get("provider_clients_ready") or []):
                return
            async with semaphore:
                try:
                    provider = bot_service._get_provider(bot)
                    await provider.warm_client()
                    snapshot = self.snapshot()
                    provider_clients_ready = list(snapshot.get("provider_clients_ready") or [])
                    provider_clients_ready.append(provider_key)
                    self._update_state(provider_clients_ready=provider_clients_ready)
                except Exception as exc:
                    logger.warning("Startup provider prime failed for %s: %s", provider_key, exc)
                    self._record_error(f"provider prime failed for {provider_key}: {exc}")

        await asyncio.gather(*[_prime(bot) for bot in bots])

    async def _hydrate_indices(self, bots: List[Bot]) -> None:
        semaphore = asyncio.Semaphore(self.index_hydration_concurrency)

        async def _hydrate(bot: Bot) -> None:
            index_status = bot_service.embedding_service.get_index_status(bot.id)
            if index_status.get("state") != "active":
                return
            async with semaphore:
                try:
                    result = await asyncio.to_thread(
                        bot_service.embedding_service.hydrate_index_bundle,
                        bot.id,
                    )
                    if result.get("hydrated"):
                        snapshot = self.snapshot()
                        hydrated_bot_ids = list(snapshot.get("hydrated_bot_ids") or [])
                        hydrated_bot_ids.append(bot.id)
                        self._update_state(hydrated_bot_ids=hydrated_bot_ids)
                except Exception as exc:
                    logger.warning("Index hydration failed for bot %s: %s", bot.id, exc)
                    self._record_error(f"index hydration failed for {bot.id}: {exc}")

        await asyncio.gather(*[_hydrate(bot) for bot in bots])

    async def _warm_local_models(self, bots: List[Bot]) -> None:
        local_bots = [bot for bot in bots if bot.provider_type == "local"]
        if not local_bots:
            return

        semaphore = asyncio.Semaphore(self.local_warmup_concurrency)

        async def _warm(bot: Bot) -> None:
            provider_key = self._provider_key(bot.provider_type, bot.model)
            async with semaphore:
                try:
                    provider = bot_service._get_provider(bot)
                    await provider.warm_model()
                    snapshot = self.snapshot()
                    provider_clients_ready = list(snapshot.get("provider_clients_ready") or [])
                    provider_clients_ready.append(provider_key)
                    local_models_hot = list(snapshot.get("local_models_hot") or [])
                    local_models_hot.append(provider_key)
                    self._update_state(
                        provider_clients_ready=provider_clients_ready,
                        local_models_hot=local_models_hot,
                    )
                except Exception as exc:
                    logger.warning("Local model warmup failed for %s: %s", provider_key, exc)
                    self._record_error(f"local warmup failed for {provider_key}: {exc}")

        await asyncio.gather(*[_warm(bot) for bot in local_bots])

    async def _run_warmup(self) -> None:
        self._update_state(phase="warming", detail="startup warmup in progress")
        try:
            await self._prime_database()
            bots = await self._list_active_bots()
            await asyncio.gather(
                self._prime_provider_clients(bots),
                self._hydrate_indices(bots),
                self._warm_local_models(bots),
            )
            self._update_state(
                phase="completed",
                completed_at=datetime.utcnow().isoformat() + "Z",
                detail="startup warmup completed",
            )
        except Exception as exc:
            logger.exception("Startup warmup failed: %s", exc)
            metrics_service.inc_counter(
                "task_restart_total",
                labels={"task": "startup_warmup", "reason": "failure"},
            )
            self._record_error(str(exc))
            self._update_state(
                phase="completed",
                completed_at=datetime.utcnow().isoformat() + "Z",
                detail=f"startup warmup completed with errors: {exc}",
            )

    async def _run_keepalive_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self.keepalive_interval_seconds)
                bots = await self._list_active_bots()
                local_bots = [bot for bot in bots if bot.provider_type == "local"]
                for bot in local_bots:
                    try:
                        provider = bot_service._get_provider(bot)
                        await provider.warm_model()
                        snapshot = self.snapshot()
                        local_models_hot = list(snapshot.get("local_models_hot") or [])
                        local_models_hot.append(self._provider_key(bot.provider_type, bot.model))
                        self._update_state(
                            phase="steady",
                            last_pulse_at=datetime.utcnow().isoformat() + "Z",
                            local_models_hot=local_models_hot,
                            detail="local keepalive pulse completed",
                        )
                    except Exception as exc:
                        logger.warning("Local keepalive pulse failed for bot %s: %s", bot.id, exc)
                        self._record_error(f"keepalive pulse failed for {bot.id}: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Warmup keepalive loop error: %s", exc)
                self._record_error(f"keepalive loop error: {exc}")

    async def _run_lease_loop(self) -> None:
        while not self._stop_event.is_set():
            snapshot = self.snapshot()
            state = "degraded" if snapshot.get("hot_state") == "degraded" else "running"
            db = SessionLocal()
            try:
                supervisor_service.upsert_component_lease(
                    db,
                    component_name="warmup",
                    unit_type="warmup",
                    lease_owner=self._worker_id,
                    state=state,
                    last_error="; ".join(snapshot.get("errors") or []) or None,
                    metadata=snapshot,
                )
            finally:
                db.close()
            health_service.set_component(
                "warmup",
                "degraded" if state == "degraded" else "healthy",
                critical=False,
                detail=str(snapshot.get("detail") or "startup warmup"),
                metadata=snapshot,
            )
            await asyncio.sleep(self.lease_interval_seconds)


startup_warmup_service = StartupWarmupService()
