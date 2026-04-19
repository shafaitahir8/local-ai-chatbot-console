"""
Safe mode evaluation for degraded subsystem operation.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Iterable, Set

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.database import ComponentLease, OutboxEvent, ScraperJob


class SafeModeService:
    """Compute and enforce runtime safe modes."""

    def __init__(self) -> None:
        self.lease_stale_seconds = int(os.getenv("COMPONENT_LEASE_STALE_SECONDS", "15"))
        self.scraper_backlog_limit = int(os.getenv("SCRAPER_SAFE_MODE_BACKLOG_LIMIT", "10"))

    def _is_stale(self, heartbeat_at) -> bool:
        if not heartbeat_at:
            return True
        return heartbeat_at < datetime.utcnow() - timedelta(seconds=self.lease_stale_seconds)

    def get_active_modes(self, db: Session, *, bot_id: str | None = None, provider=None) -> Set[str]:
        modes: Set[str] = set()

        if os.getenv("FORCE_DB_READ_ONLY", "").strip().lower() in {"1", "true", "yes"}:
            modes.add("DB_READ_ONLY")

        tunnel_lease = db.query(ComponentLease).filter(ComponentLease.component_name == "tunnel-agent").first()
        if not tunnel_lease or tunnel_lease.state != "running" or self._is_stale(tunnel_lease.heartbeat_at):
            modes.add("TUNNEL_OFFLINE_LOCAL_ONLY")

        scraper_lease = db.query(ComponentLease).filter(ComponentLease.component_name == "scraper-worker").first()
        pending_scraper_events = db.query(OutboxEvent).filter(
            OutboxEvent.event_type == "scraper_job",
            OutboxEvent.state == "pending",
        ).count()
        pending_scraper_jobs = db.query(ScraperJob).filter(
            ScraperJob.status == "pending",
        ).count()
        if (
            not scraper_lease
            or scraper_lease.state != "running"
            or self._is_stale(scraper_lease.heartbeat_at)
            or max(pending_scraper_events, pending_scraper_jobs) > self.scraper_backlog_limit
        ):
            modes.add("SCRAPER_PAUSED")

        if bot_id:
            from ..services.embedding_service import embedding_service

            index_status = embedding_service.get_index_status(bot_id)
            if index_status.get("state") not in {"active", "missing"}:
                modes.add("RAG_BYPASS_EXPLICIT")

        if provider is not None:
            breaker_state = provider.chat_breaker.snapshot().get("state")
            if breaker_state == "open":
                modes.add("PROVIDER_OPEN_CIRCUIT")

        return modes

    async def get_active_modes_async(self, db: AsyncSession, *, bot_id: str | None = None, provider=None) -> Set[str]:
        modes: Set[str] = set()

        if os.getenv("FORCE_DB_READ_ONLY", "").strip().lower() in {"1", "true", "yes"}:
            modes.add("DB_READ_ONLY")

        tunnel_lease_result = await db.execute(
            select(ComponentLease).filter(ComponentLease.component_name == "tunnel-agent").limit(1)
        )
        tunnel_lease = tunnel_lease_result.scalar_one_or_none()
        if not tunnel_lease or tunnel_lease.state != "running" or self._is_stale(tunnel_lease.heartbeat_at):
            modes.add("TUNNEL_OFFLINE_LOCAL_ONLY")

        scraper_lease_result = await db.execute(
            select(ComponentLease).filter(ComponentLease.component_name == "scraper-worker").limit(1)
        )
        scraper_lease = scraper_lease_result.scalar_one_or_none()
        pending_scraper_events_result = await db.execute(
            select(func.count()).select_from(OutboxEvent).filter(
                OutboxEvent.event_type == "scraper_job",
                OutboxEvent.state == "pending",
            )
        )
        pending_scraper_jobs_result = await db.execute(
            select(func.count()).select_from(ScraperJob).filter(
                ScraperJob.status == "pending",
            )
        )
        pending_scraper_events = int(pending_scraper_events_result.scalar() or 0)
        pending_scraper_jobs = int(pending_scraper_jobs_result.scalar() or 0)
        if (
            not scraper_lease
            or scraper_lease.state != "running"
            or self._is_stale(scraper_lease.heartbeat_at)
            or max(pending_scraper_events, pending_scraper_jobs) > self.scraper_backlog_limit
        ):
            modes.add("SCRAPER_PAUSED")

        if bot_id:
            from ..services.embedding_service import embedding_service

            index_status = embedding_service.get_index_status(bot_id)
            if index_status.get("state") not in {"active", "missing"}:
                modes.add("RAG_BYPASS_EXPLICIT")

        if provider is not None and provider.chat_breaker.snapshot().get("state") == "open":
            modes.add("PROVIDER_OPEN_CIRCUIT")

        return modes

    def assert_writes_allowed(self, db: Session) -> None:
        if "DB_READ_ONLY" in self.get_active_modes(db):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="DB_READ_ONLY safe mode is active; mutating operations are blocked",
            )

    def assert_scraper_allowed(self, db: Session) -> None:
        active_modes = self.get_active_modes(db)
        if "DB_READ_ONLY" in active_modes:
            self.assert_writes_allowed(db)
        if "SCRAPER_PAUSED" in active_modes:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SCRAPER_PAUSED safe mode is active; scraper jobs are temporarily disabled",
            )

    def serialize_modes(self, modes: Iterable[str]) -> list[str]:
        return sorted(set(modes))


safe_mode_service = SafeModeService()
