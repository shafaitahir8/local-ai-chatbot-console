"""
Supervisor persistence helpers for leases, tasks, outbox events, and retries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..models.database import (
    ChatRequestRecord,
    ComponentLease,
    OutboxEvent,
    TaskRun,
)
from .metrics_service import metrics_service


logger = logging.getLogger(__name__)


class SupervisorService:
    """Database-backed supervisor utilities."""

    def __init__(self) -> None:
        self.base_retry_seconds = 2.0
        self.max_retry_seconds = 300.0

    def full_jitter_backoff(self, attempt: int) -> float:
        upper_bound = min(self.max_retry_seconds, (2 ** max(0, int(attempt))) * self.base_retry_seconds)
        return random.uniform(0.0, upper_bound)

    def _hash_payload(self, payload: Any) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _commit_with_retry(self, db: Session, retries: int = 3) -> None:
        for attempt in range(retries):
            try:
                db.commit()
                return
            except OperationalError as exc:
                db.rollback()
                message = str(exc).lower()
                if "database is locked" not in message or attempt >= retries - 1:
                    raise
                time.sleep(min(1.0, 0.15 * (attempt + 1)))

    def upsert_component_lease(
        self,
        db: Session,
        *,
        component_name: str,
        unit_type: str,
        lease_owner: str,
        state: str,
        attempt: int = 0,
        generation: Optional[int] = None,
        last_error: Optional[str] = None,
        next_retry_at: Optional[datetime] = None,
        metadata: Optional[dict] = None,
    ) -> ComponentLease:
        lease = db.query(ComponentLease).filter(ComponentLease.component_name == component_name).first()
        if not lease:
            lease = ComponentLease(
                component_name=component_name,
                unit_type=unit_type,
                lease_owner=lease_owner,
                generation=generation or 1,
            )
            db.add(lease)
        lease.unit_type = unit_type
        lease.lease_owner = lease_owner
        lease.state = state
        lease.attempt = int(attempt or 0)
        if generation is not None:
            lease.generation = int(generation)
        lease.last_error = last_error
        lease.next_retry_at = next_retry_at
        lease.metadata_json = metadata or {}
        lease.heartbeat_at = datetime.utcnow()
        lease.updated_at = datetime.utcnow()
        self._commit_with_retry(db)
        db.refresh(lease)
        return lease

    def get_component_lease(self, db: Session, component_name: str) -> Optional[ComponentLease]:
        return db.query(ComponentLease).filter(ComponentLease.component_name == component_name).first()

    def ensure_task_run(
        self,
        db: Session,
        *,
        task_type: str,
        task_key: str,
        payload_json: Optional[dict] = None,
        checkpoint_json: Optional[dict] = None,
        state: str = "pending",
    ) -> TaskRun:
        task = db.query(TaskRun).filter(
            TaskRun.task_type == task_type,
            TaskRun.task_key == task_key,
        ).order_by(TaskRun.created_at.desc()).first()
        if not task:
            task = TaskRun(
                task_type=task_type,
                task_key=task_key,
                payload_json=payload_json,
                checkpoint_json=checkpoint_json,
                state=state,
            )
            db.add(task)
        else:
            task.payload_json = payload_json if payload_json is not None else task.payload_json
            task.checkpoint_json = checkpoint_json if checkpoint_json is not None else task.checkpoint_json
            task.state = state or task.state
        task.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(task)
        return task

    def heartbeat_task(
        self,
        db: Session,
        task: TaskRun,
        *,
        state: Optional[str] = None,
        lease_owner: Optional[str] = None,
        checkpoint_json: Optional[dict] = None,
        attempt: Optional[int] = None,
        generation: Optional[int] = None,
    ) -> TaskRun:
        if state:
            task.state = state
        if lease_owner is not None:
            task.lease_owner = lease_owner
        if checkpoint_json is not None:
            task.checkpoint_json = checkpoint_json
        if attempt is not None:
            task.attempt = int(attempt)
        if generation is not None:
            task.generation = int(generation)
        now = datetime.utcnow()
        task.heartbeat_at = now
        task.updated_at = now
        if task.state == "running" and task.started_at is None:
            task.started_at = now
        db.commit()
        db.refresh(task)
        return task

    def complete_task(self, db: Session, task: TaskRun, *, state: str = "completed") -> TaskRun:
        now = datetime.utcnow()
        task.state = state
        task.completed_at = now
        task.heartbeat_at = now
        task.updated_at = now
        task.lease_owner = None
        db.commit()
        db.refresh(task)
        return task

    def fail_task(self, db: Session, task: TaskRun, error: str, *, retryable: bool = True) -> TaskRun:
        now = datetime.utcnow()
        task.last_error = error
        task.attempt = int(task.attempt or 0) + 1
        task.heartbeat_at = now
        task.updated_at = now
        task.lease_owner = None
        if retryable:
            delay = self.full_jitter_backoff(task.attempt)
            task.state = "pending"
            task.next_retry_at = now + timedelta(seconds=delay)
            metrics_service.inc_counter(
                "task_restart_total",
                labels={"task_type": task.task_type},
            )
        else:
            task.state = "failed"
            task.completed_at = now
        db.commit()
        db.refresh(task)
        return task

    def enqueue_outbox(
        self,
        db: Session,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload_json: Optional[dict] = None,
        dedupe_key: Optional[str] = None,
    ) -> OutboxEvent:
        payload_json = payload_json or {}
        dedupe_source = dedupe_key or self._hash_payload(
            {
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "payload": payload_json,
            }
        )
        event = db.query(OutboxEvent).filter(
            OutboxEvent.event_type == event_type,
            OutboxEvent.aggregate_type == aggregate_type,
            OutboxEvent.aggregate_id == aggregate_id,
            OutboxEvent.state.in_(["pending", "running"]),
            OutboxEvent.dedupe_key == dedupe_source,
        ).first()
        if event:
            return event
        event = OutboxEvent(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload_json=payload_json,
            dedupe_key=dedupe_source,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event

    async def enqueue_outbox_async(
        self,
        db: AsyncSession,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload_json: Optional[dict] = None,
        dedupe_key: Optional[str] = None,
    ) -> OutboxEvent:
        payload_json = payload_json or {}
        dedupe_source = dedupe_key or self._hash_payload(
            {
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "payload": payload_json,
            }
        )
        result = await db.execute(
            select(OutboxEvent).filter(
                OutboxEvent.event_type == event_type,
                OutboxEvent.aggregate_type == aggregate_type,
                OutboxEvent.aggregate_id == aggregate_id,
                OutboxEvent.state.in_(["pending", "running"]),
                OutboxEvent.dedupe_key == dedupe_source,
            ).limit(1)
        )
        event = result.scalar_one_or_none()
        if event:
            return event
        event = OutboxEvent(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload_json=payload_json,
            dedupe_key=dedupe_source,
        )
        db.add(event)
        await db.flush()
        return event

    def claim_outbox_event(
        self,
        db: Session,
        *,
        event_type: str,
        lease_owner: str,
    ) -> Optional[OutboxEvent]:
        now = datetime.utcnow()
        event = db.query(OutboxEvent).filter(
            OutboxEvent.event_type == event_type,
            OutboxEvent.state == "pending",
            ((OutboxEvent.next_retry_at == None) | (OutboxEvent.next_retry_at <= now)),  # noqa: E711
        ).order_by(OutboxEvent.available_at.asc(), OutboxEvent.created_at.asc()).first()
        if not event:
            return None
        event.state = "running"
        event.lease_owner = lease_owner
        event.heartbeat_at = now
        event.attempt = int(event.attempt or 0) + 1
        db.commit()
        db.refresh(event)
        return event

    def heartbeat_outbox_event(self, db: Session, event: OutboxEvent, payload_json: Optional[dict] = None) -> OutboxEvent:
        event.heartbeat_at = datetime.utcnow()
        event.updated_at = datetime.utcnow()
        if payload_json is not None:
            event.payload_json = payload_json
        db.commit()
        db.refresh(event)
        return event

    def complete_outbox_event(self, db: Session, event: OutboxEvent) -> OutboxEvent:
        now = datetime.utcnow()
        event.state = "completed"
        event.completed_at = now
        event.updated_at = now
        event.heartbeat_at = now
        event.lease_owner = None
        db.commit()
        db.refresh(event)
        return event

    def fail_outbox_event(self, db: Session, event: OutboxEvent, error: str, *, retryable: bool = True) -> OutboxEvent:
        now = datetime.utcnow()
        event.last_error = error
        event.updated_at = now
        event.heartbeat_at = now
        event.lease_owner = None
        if retryable:
            event.state = "pending"
            event.next_retry_at = now + timedelta(seconds=self.full_jitter_backoff(event.attempt))
        else:
            event.state = "failed"
            event.completed_at = now
        db.commit()
        db.refresh(event)
        return event

    def upsert_chat_request(
        self,
        db: Session,
        *,
        idempotency_key: str,
        bot_id: str,
        session_id: Optional[str],
        request_payload: dict,
    ) -> ChatRequestRecord:
        request_hash = self._hash_payload(request_payload)
        record = db.query(ChatRequestRecord).filter(
            ChatRequestRecord.idempotency_key == idempotency_key
        ).first()
        if not record:
            record = ChatRequestRecord(
                idempotency_key=idempotency_key,
                bot_id=bot_id,
                session_id=session_id,
                request_hash=request_hash,
                response_payload=None,
                degraded_flags=[],
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record
        if record.request_hash != request_hash:
            raise ValueError("Idempotency key already exists for a different request payload")
        return record


supervisor_service = SupervisorService()
