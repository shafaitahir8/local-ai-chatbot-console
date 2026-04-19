"""
Durable embedding rebuild worker backed by the outbox and supervisor tables.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Optional

from ..models.database import Bot, IndexManifest, OutboxEvent, SessionLocal, TaskRun
from .bot_service import bot_service
from .health_service import health_service
from .logging_utils import log_event
from .supervisor_service import supervisor_service


logger = logging.getLogger(__name__)


class EmbeddingWorkerService:
    """Consume durable embedding rebuild events and publish versioned manifests."""

    def __init__(self) -> None:
        self.poll_interval_seconds = float(os.getenv("EMBEDDING_WORKER_POLL_SECONDS", "2"))
        self.lease_seconds = int(os.getenv("EMBEDDING_WORKER_LEASE_SECONDS", "45"))
        self.max_concurrency = max(1, int(os.getenv("EMBEDDING_WORKER_MAX_CONCURRENCY", "1")))
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._active_events: set[str] = set()
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._recover_stale_events()
        db = SessionLocal()
        try:
            supervisor_service.upsert_component_lease(
                db,
                component_name="embedding-worker",
                unit_type="embedding-worker",
                lease_owner=self._worker_id,
                state="running",
                metadata={"max_concurrency": self.max_concurrency},
            )
        finally:
            db.close()
        health_service.set_component(
            "embedding_worker",
            "healthy",
            critical=False,
            detail="embedding worker polling",
            metadata={"worker_id": self._worker_id},
        )
        self._task = asyncio.create_task(self._run_loop(), name="embedding-worker")

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
                component_name="embedding-worker",
                unit_type="embedding-worker",
                lease_owner=self._worker_id,
                state="stopped",
            )
        finally:
            db.close()
        health_service.set_component(
            "embedding_worker",
            "degraded",
            critical=False,
            detail="embedding worker stopped",
            metadata={"worker_id": self._worker_id},
        )

    def _recover_stale_events(self) -> None:
        cutoff = datetime.utcnow() - timedelta(seconds=self.lease_seconds)
        db = SessionLocal()
        try:
            stale_events = db.query(OutboxEvent).filter(
                OutboxEvent.event_type == "rebuild_embeddings",
                OutboxEvent.state == "running",
                (
                    (OutboxEvent.heartbeat_at == None)  # noqa: E711
                    | (OutboxEvent.heartbeat_at < cutoff)
                ),
            ).all()
            for event in stale_events:
                supervisor_service.fail_outbox_event(
                    db,
                    event,
                    "embedding worker lease expired; requeued",
                    retryable=True,
                )

            stale_tasks = db.query(TaskRun).filter(
                TaskRun.task_type == "embedding_rebuild",
                TaskRun.state == "running",
                (
                    (TaskRun.heartbeat_at == None)  # noqa: E711
                    | (TaskRun.heartbeat_at < cutoff)
                ),
            ).all()
            for task in stale_tasks:
                supervisor_service.fail_task(
                    db,
                    task,
                    "embedding worker lease expired; requeued",
                    retryable=True,
                )
        finally:
            db.close()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                lease_db = SessionLocal()
                try:
                    supervisor_service.upsert_component_lease(
                        lease_db,
                        component_name="embedding-worker",
                        unit_type="embedding-worker",
                        lease_owner=self._worker_id,
                        state="running",
                        metadata={"active_events": len(self._active_events)},
                    )
                finally:
                    lease_db.close()

                while len(self._active_events) < self.max_concurrency:
                    event_id = self._claim_next_event()
                    if not event_id:
                        break
                    self._active_events.add(event_id)
                    asyncio.create_task(self._run_event(event_id))
            except Exception as exc:
                logger.exception("Embedding worker loop failed: %s", exc)
                log_event(
                    logger,
                    logging.ERROR,
                    "embedding_worker_loop_failed",
                    component="embedding-worker",
                    dependency="database",
                    error=str(exc),
                    worker_id=self._worker_id,
                )
                health_service.set_component(
                    "embedding_worker",
                    "degraded",
                    critical=False,
                    detail=str(exc),
                    metadata={"worker_id": self._worker_id},
                )
            await asyncio.sleep(self.poll_interval_seconds)

    def _claim_next_event(self) -> Optional[str]:
        self._recover_stale_events()
        db = SessionLocal()
        try:
            event = supervisor_service.claim_outbox_event(
                db,
                event_type="rebuild_embeddings",
                lease_owner=self._worker_id,
            )
            if not event:
                return None
            task = supervisor_service.ensure_task_run(
                db,
                task_type="embedding_rebuild",
                task_key=event.aggregate_id,
                payload_json=event.payload_json or {},
                checkpoint_json={"event_id": event.id},
                state="pending",
            )
            supervisor_service.heartbeat_task(
                db,
                task,
                state="running",
                lease_owner=self._worker_id,
                checkpoint_json={"event_id": event.id},
                attempt=event.attempt,
            )
            return event.id
        finally:
            db.close()

    async def _heartbeat_inflight(self, event_id: str, bot_id: str, version: str) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while event_id in self._active_events and not self._stop_event.is_set():
            await asyncio.sleep(interval)
            if event_id not in self._active_events:
                return
            db = SessionLocal()
            try:
                event = db.query(OutboxEvent).filter(OutboxEvent.id == event_id).first()
                if not event or event.state != "running":
                    return
                supervisor_service.heartbeat_outbox_event(
                    db,
                    event,
                    payload_json={**(event.payload_json or {}), "version": version},
                )
                task = db.query(TaskRun).filter(
                    TaskRun.task_type == "embedding_rebuild",
                    TaskRun.task_key == bot_id,
                ).order_by(TaskRun.created_at.desc()).first()
                if task:
                    supervisor_service.heartbeat_task(
                        db,
                        task,
                        state="running",
                        lease_owner=self._worker_id,
                        checkpoint_json={"event_id": event_id, "version": version},
                        attempt=event.attempt,
                    )
                supervisor_service.upsert_component_lease(
                    db,
                    component_name="embedding-worker",
                    unit_type="embedding-worker",
                    lease_owner=self._worker_id,
                    state="running",
                    metadata={"active_events": len(self._active_events), "bot_id": bot_id, "version": version},
                )
            finally:
                db.close()

    async def _run_event(self, event_id: str) -> None:
        heartbeat_task: Optional[asyncio.Task] = None
        version: Optional[str] = None
        db = SessionLocal()
        try:
            event = db.query(OutboxEvent).filter(OutboxEvent.id == event_id).first()
            if not event:
                return

            payload = event.payload_json or {}
            bot_id = payload.get("bot_id") or event.aggregate_id
            task = supervisor_service.ensure_task_run(
                db,
                task_type="embedding_rebuild",
                task_key=bot_id,
                payload_json=payload,
                checkpoint_json={"event_id": event.id},
                state="running",
            )
            bot = db.query(Bot).filter(Bot.id == bot_id).first()
            if not bot:
                supervisor_service.fail_task(db, task, f"Bot {bot_id} not found", retryable=False)
                supervisor_service.fail_outbox_event(db, event, f"Bot {bot_id} not found", retryable=False)
                return

            version = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
            event.payload_json = {**payload, "bot_id": bot_id, "version": version}
            db.commit()
            build_dir = bot_service.embedding_service.get_build_dir(bot_id, version)
            manifest = IndexManifest(
                bot_id=bot_id,
                version=version,
                state="building",
                build_path=str(build_dir),
                reason=payload.get("reason"),
                generation=max(1, int(event.attempt or 1)),
                metadata_json={"event_id": event.id, "worker_id": self._worker_id},
            )
            db.add(manifest)
            db.commit()

            supervisor_service.heartbeat_task(
                db,
                task,
                state="running",
                lease_owner=self._worker_id,
                checkpoint_json={"event_id": event.id, "version": version},
                attempt=event.attempt,
            )
            heartbeat_task = asyncio.create_task(self._heartbeat_inflight(event_id, bot_id, version))

            stats = await bot_service._reindex_knowledge_from_db(
                db,
                bot,
                version=version,
            )
            chunk_count = int(stats.get("chunks_created") or stats.get("chunks_processed") or 0)

            db.query(IndexManifest).filter(
                IndexManifest.bot_id == bot_id,
                IndexManifest.version != version,
                IndexManifest.state == "active",
            ).update(
                {
                    "state": "superseded",
                    "updated_at": datetime.utcnow(),
                },
                synchronize_session=False,
            )
            manifest = db.query(IndexManifest).filter(
                IndexManifest.bot_id == bot_id,
                IndexManifest.version == version,
            ).first()
            if manifest:
                manifest.state = "active" if chunk_count > 0 else "empty"
                manifest.chunk_count = chunk_count
                manifest.activated_at = datetime.utcnow()
                manifest.last_error = None
                manifest.metadata_json = {
                    "event_id": event.id,
                    "worker_id": self._worker_id,
                    "stats": stats,
                }
                if chunk_count == 0:
                    manifest.build_path = str(bot_service.embedding_service.get_embeddings_dir(bot_id))

            supervisor_service.complete_task(db, task, state="completed")
            supervisor_service.complete_outbox_event(db, event)
            db.commit()
            log_event(
                logger,
                logging.INFO,
                "embedding_rebuild_completed",
                component="embedding-worker",
                task_id=bot_id,
                attempt=event.attempt,
                bot_id=bot_id,
                worker_id=self._worker_id,
                version=version,
            )
            health_service.set_component(
                "embedding_worker",
                "healthy",
                critical=False,
                detail="embedding rebuild completed",
                metadata={"bot_id": bot_id, "version": version, "worker_id": self._worker_id},
            )
        except Exception as exc:
            logger.exception("Embedding rebuild event %s failed: %s", event_id, exc)
            event = db.query(OutboxEvent).filter(OutboxEvent.id == event_id).first()
            bot_id = (event.payload_json or {}).get("bot_id") if event else None
            task = None
            if bot_id:
                task = db.query(TaskRun).filter(
                    TaskRun.task_type == "embedding_rebuild",
                    TaskRun.task_key == bot_id,
                ).order_by(TaskRun.created_at.desc()).first()
            if task:
                supervisor_service.fail_task(db, task, str(exc), retryable=True)
            if event:
                if version:
                    manifest = db.query(IndexManifest).filter(
                        IndexManifest.bot_id == event.aggregate_id,
                        IndexManifest.version == version,
                    ).first()
                    if manifest:
                        manifest.state = "failed"
                        manifest.last_error = str(exc)
                supervisor_service.fail_outbox_event(db, event, str(exc), retryable=True)
            db.commit()
            log_event(
                logger,
                logging.ERROR,
                "embedding_rebuild_failed",
                component="embedding-worker",
                task_id=bot_id or event_id,
                attempt=(event.attempt if event else None),
                bot_id=bot_id,
                dependency="embeddings",
                error=str(exc),
                worker_id=self._worker_id,
                version=version,
            )
            health_service.set_component(
                "embedding_worker",
                "degraded",
                critical=False,
                detail=str(exc),
                metadata={"event_id": event_id, "worker_id": self._worker_id},
            )
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            db.close()
            self._active_events.discard(event_id)


embedding_worker_service = EmbeddingWorkerService()
