"""
Idempotent chat request coordination.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from ..models.database import ChatRequestRecord
from .supervisor_service import supervisor_service


logger = logging.getLogger(__name__)


class ChatRequestService:
    """Persist chat request lifecycle for retry-safe request handling."""

    def _normalize_payload(self, *, bot_id: str, session_id: Optional[str], message: str) -> dict:
        return {
            "bot_id": bot_id,
            "session_id": session_id or "",
            "message": message,
        }

    def build_idempotency_key(
        self,
        *,
        bot_id: str,
        session_id: Optional[str],
        message: str,
        explicit_key: Optional[str],
    ) -> str:
        if explicit_key and explicit_key.strip():
            return explicit_key.strip()
        payload = self._normalize_payload(bot_id=bot_id, session_id=session_id, message=message)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"chat-{digest}"

    def prepare(
        self,
        db: Session,
        *,
        bot_id: str,
        session_id: Optional[str],
        message: str,
        idempotency_key: str,
    ) -> ChatRequestRecord:
        payload = self._normalize_payload(bot_id=bot_id, session_id=session_id, message=message)
        request_hash = supervisor_service._hash_payload(payload)
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
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                record = db.query(ChatRequestRecord).filter(
                    ChatRequestRecord.idempotency_key == idempotency_key
                ).first()
                if not record:
                    raise
                if record.request_hash != request_hash:
                    raise ValueError("Idempotency key already exists for a different request payload")
            return record
        if record.request_hash != request_hash:
            raise ValueError("Idempotency key already exists for a different request payload")
        return record

    def mark_running(self, db: Session, record: ChatRequestRecord, lease_owner: str) -> ChatRequestRecord:
        record.state = "running"
        record.lease_owner = lease_owner
        record.heartbeat_at = datetime.utcnow()
        record.attempt = int(record.attempt or 0) + 1
        record.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(record)
        return record

    def mark_completed(
        self,
        db: Session,
        record: ChatRequestRecord,
        *,
        response_payload: dict,
        degraded_flags: list[str],
    ) -> ChatRequestRecord:
        now = datetime.utcnow()
        record.state = "completed"
        record.response_payload = response_payload
        record.degraded_flags = degraded_flags
        record.completed_at = now
        record.updated_at = now
        record.heartbeat_at = now
        record.lease_owner = None
        db.commit()
        db.refresh(record)
        return record

    def mark_failed(self, db: Session, record: ChatRequestRecord, error: str) -> ChatRequestRecord:
        now = datetime.utcnow()
        record.state = "failed"
        record.last_error = error
        record.updated_at = now
        record.heartbeat_at = now
        record.lease_owner = None
        db.commit()
        db.refresh(record)
        return record

    async def prepare_async(
        self,
        db: AsyncSession,
        *,
        bot_id: str,
        session_id: Optional[str],
        message: str,
        idempotency_key: str,
    ) -> ChatRequestRecord:
        payload = self._normalize_payload(bot_id=bot_id, session_id=session_id, message=message)
        request_hash = supervisor_service._hash_payload(payload)
        result = await db.execute(
            select(ChatRequestRecord).filter(
                ChatRequestRecord.idempotency_key == idempotency_key
            ).limit(1)
        )
        record = result.scalar_one_or_none()
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
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                result = await db.execute(
                    select(ChatRequestRecord).filter(
                        ChatRequestRecord.idempotency_key == idempotency_key
                    ).limit(1)
                )
                record = result.scalar_one_or_none()
                if not record:
                    raise
                if record.request_hash != request_hash:
                    raise ValueError("Idempotency key already exists for a different request payload")
            return record
        if record.request_hash != request_hash:
            raise ValueError("Idempotency key already exists for a different request payload")
        return record

    async def mark_running_async(self, db: AsyncSession, record: ChatRequestRecord, lease_owner: str) -> ChatRequestRecord:
        record.state = "running"
        record.lease_owner = lease_owner
        record.heartbeat_at = datetime.utcnow()
        record.attempt = int(record.attempt or 0) + 1
        record.updated_at = datetime.utcnow()
        await db.flush()
        return record

    async def mark_completed_async(
        self,
        db: AsyncSession,
        record: ChatRequestRecord,
        *,
        response_payload: dict,
        degraded_flags: list[str],
    ) -> ChatRequestRecord:
        now = datetime.utcnow()
        record.state = "completed"
        record.response_payload = response_payload
        record.degraded_flags = degraded_flags
        record.completed_at = now
        record.updated_at = now
        record.heartbeat_at = now
        record.lease_owner = None
        await db.flush()
        return record

    async def mark_failed_async(self, db: AsyncSession, record: ChatRequestRecord, error: str) -> ChatRequestRecord:
        now = datetime.utcnow()
        record.state = "failed"
        record.last_error = error
        record.updated_at = now
        record.heartbeat_at = now
        record.lease_owner = None
        await db.flush()
        return record


chat_request_service = ChatRequestService()
