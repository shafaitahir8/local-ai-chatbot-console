"""
Scheduler service for lease auditing and startup warmup outside the API process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Optional

from ..models.database import Bot, ConversationMemory, OutboxEvent, SessionLocal, TaskRun
from .analytics_service import analytics_service
from .bot_service import bot_service
from .health_service import health_service
from .semantic_cache_service import semantic_cache_service
from .standalone_model_service import standalone_model_service
from .supervisor_service import supervisor_service


logger = logging.getLogger(__name__)


class SchedulerService:
    """Run stale-work recovery and optional bot warmup outside request serving."""

    def __init__(self) -> None:
        self.poll_interval_seconds = float(os.getenv("SCHEDULER_POLL_SECONDS", "10"))
        self.lease_seconds = int(os.getenv("SCHEDULER_LEASE_SECONDS", "30"))
        self.warmup_enabled = os.getenv("SCHEDULER_ENABLE_WARMUP", "true").strip().lower() not in {"0", "false", "no"}
        self.warmup_concurrency = max(1, int(os.getenv("BOT_WARMUP_CONCURRENCY", "4")))
        self.outbox_event_types = (
            "analytics_write",
            "conversation_memory_persist",
            "session_transcript_persist",
            "semantic_cache_fill",
            "scrape_revalidate",
        )
        self.max_outbox_events_per_poll = max(1, int(os.getenv("SCHEDULER_OUTBOX_EVENTS_PER_POLL", "25")))
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._warmup_complete = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        db = SessionLocal()
        try:
            supervisor_service.upsert_component_lease(
                db,
                component_name="scheduler",
                unit_type="scheduler",
                lease_owner=self._worker_id,
                state="running",
                metadata={"warmup_enabled": self.warmup_enabled},
            )
        finally:
            db.close()
        health_service.set_component(
            "scheduler",
            "healthy",
            critical=False,
            detail="scheduler polling",
            metadata={"worker_id": self._worker_id},
        )
        self._task = asyncio.create_task(self._run_loop(), name="scheduler")

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
                component_name="scheduler",
                unit_type="scheduler",
                lease_owner=self._worker_id,
                state="stopped",
            )
        finally:
            db.close()
        health_service.set_component(
            "scheduler",
            "degraded",
            critical=False,
            detail="scheduler stopped",
            metadata={"worker_id": self._worker_id},
        )

    def _recover_stale_work(self) -> None:
        cutoff = datetime.utcnow() - timedelta(seconds=self.lease_seconds)
        db = SessionLocal()
        try:
            stale_tasks = db.query(TaskRun).filter(
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
                    "scheduler detected stale task heartbeat",
                    retryable=True,
                )

            stale_events = db.query(OutboxEvent).filter(
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
                    "scheduler detected stale outbox heartbeat",
                    retryable=True,
                )
        finally:
            db.close()

    async def _warmup_bot(self, bot_id: str, bot_name: str) -> None:
        db = SessionLocal()
        try:
            task = supervisor_service.ensure_task_run(
                db,
                task_type="bot_warmup",
                task_key=bot_id,
                payload_json={"bot_id": bot_id, "bot_name": bot_name},
                checkpoint_json={},
                state="pending",
            )
            supervisor_service.heartbeat_task(
                db,
                task,
                state="running",
                lease_owner=self._worker_id,
                checkpoint_json={"bot_name": bot_name},
                attempt=task.attempt,
            )
            result = await bot_service.warmup_bot(
                db,
                bot_id,
                prompt="Hi, send a welcome message",
            )
            if result.get("status") == "ready":
                supervisor_service.complete_task(db, task, state="completed")
                health_service.set_component(
                    "warmup",
                    "healthy",
                    critical=False,
                    detail="warmup completed",
                    metadata={"bot_id": bot_id, "bot_name": bot_name},
                )
            else:
                supervisor_service.complete_task(db, task, state="skipped")
                health_service.set_component(
                    "warmup",
                    "degraded",
                    critical=False,
                    detail=str(result.get("reason")),
                    metadata={"bot_id": bot_id, "bot_name": bot_name},
                )
        except Exception as exc:
            logger.warning("Warmup failed for bot %s (%s): %s", bot_name, bot_id, exc)
            db.rollback()
            task = db.query(TaskRun).filter(
                TaskRun.task_type == "bot_warmup",
                TaskRun.task_key == bot_id,
            ).order_by(TaskRun.created_at.desc()).first()
            if task:
                supervisor_service.fail_task(db, task, str(exc), retryable=True)
            health_service.set_component(
                "warmup",
                "degraded",
                critical=False,
                detail=str(exc),
                metadata={"bot_id": bot_id, "bot_name": bot_name},
            )
        finally:
            db.close()

    async def _warmup_local_bots(self) -> None:
        db = SessionLocal()
        try:
            warmup_all = os.getenv("BOT_WARMUP_ALL_PROVIDERS", "false").strip().lower() in {"1", "true", "yes"}
            warmup_query = db.query(Bot).filter(Bot.is_active == True)  # noqa: E712
            if not warmup_all:
                warmup_query = warmup_query.filter(Bot.provider_type == "local")
            bots_to_warm = [(bot.id, bot.name) for bot in warmup_query.all()]
        finally:
            db.close()

        if not bots_to_warm:
            health_service.set_component(
                "warmup",
                "healthy",
                critical=False,
                detail="no eligible bots",
            )
            return

        semaphore = asyncio.Semaphore(self.warmup_concurrency)

        async def _run(bot_id: str, bot_name: str) -> None:
            async with semaphore:
                await self._warmup_bot(bot_id, bot_name)

        await asyncio.gather(*[_run(bot_id, bot_name) for bot_id, bot_name in bots_to_warm])

    def _process_auxiliary_outbox(self) -> None:
        processed = 0
        while processed < self.max_outbox_events_per_poll:
            claimed_event = None
            db = SessionLocal()
            try:
                for event_type in self.outbox_event_types:
                    claimed_event = supervisor_service.claim_outbox_event(
                        db,
                        event_type=event_type,
                        lease_owner=self._worker_id,
                    )
                    if claimed_event:
                        break
                if not claimed_event:
                    return

                payload = dict(claimed_event.payload_json or {})
                if claimed_event.event_type == "analytics_write":
                    analytics_service.log_message_sync(db=db, **payload)
                elif claimed_event.event_type == "conversation_memory_persist":
                    bot_id = str(payload.get("bot_id") or "")
                    session_id = str(payload.get("session_id") or "")
                    messages = list(payload.get("messages") or [])
                    memory = db.query(ConversationMemory).filter(
                        ConversationMemory.bot_id == bot_id,
                        ConversationMemory.session_id == session_id,
                    ).first()
                    if not memory:
                        memory = ConversationMemory(
                            bot_id=bot_id,
                            session_id=session_id,
                            messages=[],
                        )
                        db.add(memory)
                    memory.messages = messages
                    memory.updated_at = datetime.utcnow()
                    db.commit()
                    if bot_id and session_id:
                        standalone_model_service.persist_memory_messages(bot_id, session_id, messages)
                elif claimed_event.event_type == "session_transcript_persist":
                    # Bot-local transcript persistence now happens inline on chat completion
                    # so portable folder storage is available even when the scheduler is offline.
                    db.commit()
                elif claimed_event.event_type == "semantic_cache_fill":
                    semantic_cache_service.upsert_entry(
                        db,
                        bot_id=str(payload.get("bot_id") or ""),
                        provider=str(payload.get("provider") or ""),
                        model=str(payload.get("model") or ""),
                        manifest_version=str(payload.get("manifest_version") or "missing"),
                        system_prompt=str(payload.get("system_prompt") or ""),
                        query=str(payload.get("query") or ""),
                        query_embedding=list(payload.get("query_embedding") or []),
                        response_payload=dict(payload.get("response_payload") or {}),
                    )
                elif claimed_event.event_type == "scrape_revalidate":
                    db.commit()
                supervisor_service.complete_outbox_event(db, claimed_event)
                processed += 1
            except Exception as exc:
                logger.warning("Scheduler outbox event handling failed: %s", exc)
                if claimed_event is not None:
                    supervisor_service.fail_outbox_event(db, claimed_event, str(exc), retryable=True)
            finally:
                db.close()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._recover_stale_work()
                self._process_auxiliary_outbox()
                db = SessionLocal()
                try:
                    supervisor_service.upsert_component_lease(
                        db,
                        component_name="scheduler",
                        unit_type="scheduler",
                        lease_owner=self._worker_id,
                        state="running",
                        metadata={"warmup_complete": self._warmup_complete},
                    )
                finally:
                    db.close()
                if self.warmup_enabled and not self._warmup_complete:
                    await self._warmup_local_bots()
                    self._warmup_complete = True
            except Exception as exc:
                logger.exception("Scheduler loop failed: %s", exc)
                health_service.set_component(
                    "scheduler",
                    "degraded",
                    critical=False,
                    detail=str(exc),
                    metadata={"worker_id": self._worker_id},
                )
            await asyncio.sleep(self.poll_interval_seconds)


scheduler_service = SchedulerService()
