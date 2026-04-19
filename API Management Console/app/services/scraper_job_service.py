"""
Durable scraper job worker backed by the database.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..models.database import ScraperJob, SessionLocal
from .health_service import health_service
from .logging_utils import log_event
from .scraper_service import ScraperConfig, ScraperService
from .scrape_trace_service import scrape_trace_service
from .standalone_model_service import standalone_model_service
from .supervisor_service import supervisor_service


logger = logging.getLogger(__name__)


class ScraperJobService:
    """Poll and execute scraper jobs stored in the database."""

    def __init__(self) -> None:
        self.poll_interval_seconds = float(os.getenv("SCRAPER_JOB_POLL_SECONDS", "2"))
        self.lease_seconds = int(os.getenv("SCRAPER_JOB_LEASE_SECONDS", "30"))
        self.max_concurrency = max(1, int(os.getenv("SCRAPER_JOB_MAX_CONCURRENCY", "1")))
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._active_jobs: set[str] = set()
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def _job_pages_path(self, job_id: str, bot_id: Optional[str] = None) -> str:
        return str(standalone_model_service.scraper_job_pages_path(job_id, bot_id))

    def _job_active_path(self, job_id: str, bot_id: Optional[str] = None) -> str:
        return str(standalone_model_service.scraper_active_job_path(bot_id=bot_id, job_id=job_id))

    def _portable_path(self, path_value: Optional[str]) -> Optional[str]:
        if not path_value:
            return None
        path_obj = Path(str(path_value))
        if not path_obj.is_absolute():
            return str(path_obj)
        try:
            return str(path_obj.relative_to(standalone_model_service.project_root))
        except ValueError:
            return str(path_obj)

    def _find_revalidation_pages_path(self, db, job: ScraperJob) -> Optional[str]:
        prior_jobs = db.query(ScraperJob).filter(
            ScraperJob.bot_id == job.bot_id,
            ScraperJob.id != job.id,
            ScraperJob.url == job.url,
            ScraperJob.status.in_(["review", "completed"]),
        ).order_by(
            ScraperJob.completed_at.desc(),
            ScraperJob.created_at.desc(),
        ).limit(10).all()
        for prior_job in prior_jobs:
            extracted = dict(prior_job.extracted_data or {})
            pages_path = extracted.get("persist_pages_path")
            if pages_path:
                path_obj = Path(str(pages_path))
                if not path_obj.is_absolute():
                    path_obj = standalone_model_service.project_root / path_obj
                if path_obj.exists():
                    return str(path_obj)
        return None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._recover_stale_jobs()
        db = SessionLocal()
        try:
            supervisor_service.upsert_component_lease(
                db,
                component_name="scraper-worker",
                unit_type="scraper-worker",
                lease_owner=self._worker_id,
                state="running",
                metadata={"max_concurrency": self.max_concurrency},
            )
        finally:
            db.close()
        health_service.set_component(
            "scraper_worker",
            "healthy",
            critical=False,
            detail="scraper worker polling",
            metadata={"worker_id": self._worker_id},
        )
        self._task = asyncio.create_task(self._run_loop(), name="scraper-job-worker")

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
                component_name="scraper-worker",
                unit_type="scraper-worker",
                lease_owner=self._worker_id,
                state="stopped",
            )
        finally:
            db.close()
        health_service.set_component(
            "scraper_worker",
            "degraded",
            critical=False,
            detail="scraper worker stopped",
            metadata={"worker_id": self._worker_id},
        )

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                lease_db = SessionLocal()
                try:
                    supervisor_service.upsert_component_lease(
                        lease_db,
                        component_name="scraper-worker",
                        unit_type="scraper-worker",
                        lease_owner=self._worker_id,
                        state="running",
                        metadata={"active_jobs": len(self._active_jobs)},
                    )
                finally:
                    lease_db.close()
                while len(self._active_jobs) < self.max_concurrency:
                    claimed_job_id = self._claim_next_job()
                    if not claimed_job_id:
                        break
                    self._active_jobs.add(claimed_job_id)
                    asyncio.create_task(self._run_job(claimed_job_id))
            except Exception as exc:
                logger.exception("Scraper worker loop failed: %s", exc)
                log_event(
                    logger,
                    logging.ERROR,
                    "scraper_worker_loop_failed",
                    component="scraper-worker",
                    dependency="database",
                    error=str(exc),
                    worker_id=self._worker_id,
                )
                health_service.set_component(
                    "scraper_worker",
                    "degraded",
                    critical=False,
                    detail=str(exc),
                    metadata={"worker_id": self._worker_id},
                )
            await asyncio.sleep(self.poll_interval_seconds)

    def _recover_stale_jobs(self) -> None:
        cutoff = datetime.utcnow() - timedelta(seconds=self.lease_seconds)
        db = SessionLocal()
        try:
            stale_jobs = db.query(ScraperJob).filter(
                ScraperJob.status == "running",
                (
                    (ScraperJob.heartbeat_at == None)  # noqa: E711
                    | (ScraperJob.heartbeat_at < cutoff)
                ),
            ).all()
            for job in stale_jobs:
                job.status = "pending"
                job.error_message = "Recovered after worker lease expiry"
                job.lease_owner = None
                task = supervisor_service.ensure_task_run(
                    db,
                    task_type="scraper_job",
                    task_key=job.id,
                    payload_json={"job_id": job.id, "url": job.url, "depth": job.depth},
                    state="pending",
                )
                supervisor_service.fail_task(
                    db,
                    task,
                    "worker lease expired; requeued",
                    retryable=True,
                )
            if stale_jobs:
                db.commit()
        finally:
            db.close()

    def _claim_next_job(self) -> Optional[str]:
        self._recover_stale_jobs()
        db = SessionLocal()
        try:
            job = db.query(ScraperJob).filter(
                ScraperJob.status == "pending"
            ).order_by(ScraperJob.created_at.asc()).first()
            if not job:
                return None

            now = datetime.utcnow()
            job.status = "running"
            job.started_at = job.started_at or now
            job.heartbeat_at = now
            job.lease_owner = self._worker_id
            job.attempt_count = int(job.attempt_count or 0) + 1
            job.error_message = None
            task = supervisor_service.ensure_task_run(
                db,
                task_type="scraper_job",
                task_key=job.id,
                payload_json=None,
                checkpoint_json={},
                state="pending",
            )
            if not task.payload_json:
                task.payload_json = {"job_id": job.id, "url": job.url, "depth": job.depth}
            supervisor_service.heartbeat_task(
                db,
                task,
                state="running",
                lease_owner=self._worker_id,
                attempt=job.attempt_count,
            )
            db.commit()
            return job.id
        finally:
            db.close()

    async def _run_job(self, job_id: str) -> None:
        db = SessionLocal()
        try:
            job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
            if not job:
                return
            task = supervisor_service.ensure_task_run(
                db,
                task_type="scraper_job",
                task_key=job_id,
                payload_json=None,
                state="running",
            )
            task_payload = dict(task.payload_json or {"job_id": job.id, "url": job.url, "depth": job.depth})

            focus_keywords = [
                item.strip()
                for item in str(task_payload.get("focus") or "").split(",")
                if item and item.strip()
            ]
            scraper = ScraperService(
                ScraperConfig(
                    max_depth=int(task_payload.get("depth") or job.depth or 2),
                    max_pages=int(task_payload.get("limit") or 50),
                    focus_keywords=focus_keywords,
                    sitemap_only=bool(task_payload.get("sitemap_only", False)),
                )
            )
            pages_path = self._job_pages_path(job_id, job.bot_id)
            active_job_path = self._job_active_path(job_id, job.bot_id)
            revalidate_pages_path = self._find_revalidation_pages_path(db, job)

            async def _progress_callback(progress: dict) -> None:
                progress_db = SessionLocal()
                try:
                    progress_job = progress_db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
                    progress_task = supervisor_service.ensure_task_run(
                        progress_db,
                        task_type="scraper_job",
                        task_key=job_id,
                        payload_json=None,
                        state="running",
                    )
                    if not progress_job:
                        return
                    progress_job.heartbeat_at = datetime.utcnow()
                    progress_job.pages_scraped = int(progress.get("pages_scraped", progress_job.pages_scraped or 0))
                    checkpoint = progress.get("checkpoint", {})
                    checkpoint["persist_pages_path"] = self._portable_path(pages_path)
                    checkpoint["active_job_path"] = self._portable_path(active_job_path)
                    if revalidate_pages_path:
                        checkpoint["revalidate_pages_path"] = self._portable_path(revalidate_pages_path)
                    supervisor_service.heartbeat_task(
                        progress_db,
                        progress_task,
                        state="running",
                        lease_owner=self._worker_id,
                        checkpoint_json=checkpoint,
                        attempt=progress_job.attempt_count,
                    )
                    progress_db.commit()
                finally:
                    progress_db.close()

            result = await scraper.scrape_website(
                job.url,
                progress_callback=_progress_callback,
                checkpoint=task.checkpoint_json or {},
                persist_pages_path=pages_path,
                revalidate_pages_path=revalidate_pages_path,
                bot_id=job.bot_id,
                job_id=job.id,
            )
            persisted_pages = scraper.load_persisted_pages(pages_path)
            trace_payload = None
            if job.bot_id:
                trace_payload = scrape_trace_service.persist_scrape_job(
                    job.bot_id,
                    job_id=job.id,
                    url=job.url,
                    depth=job.depth,
                    success=not any(page.get("error") for page in persisted_pages),
                    scraped_at=result.get("scraped_at"),
                    pages=persisted_pages,
                    statistics=result.get("statistics", {}),
                )
            page_range_by_index = {
                int(page_meta.get("page_index", 0)): dict(page_meta)
                for page_meta in (trace_payload.get("pages", []) if trace_payload else [])
            }
            stored_result = {
                "start_url": result.get("start_url"),
                "statistics": result.get("statistics", {}),
                "limit": int(task_payload.get("limit") or 50),
                "focus": str(task_payload.get("focus") or "").strip(),
                "sitemap_only": bool(task_payload.get("sitemap_only", False)),
                "scraped_at": result.get("scraped_at"),
                "persist_pages_path": self._portable_path(pages_path),
                "active_job_path": self._portable_path(active_job_path),
                "revalidate_pages_path": self._portable_path(revalidate_pages_path),
                "trace_job_path": trace_payload.get("job_dir_relative") if trace_payload else None,
                "trace_source_file_path": trace_payload.get("full_source_text") if trace_payload else None,
                "trace_source_html_path": trace_payload.get("full_source_html") if trace_payload else None,
                "trace_chunks_dir": trace_payload.get("chunks_dir") if trace_payload else None,
                "trace_pages": trace_payload.get("pages") if trace_payload else [],
                "pages": [
                    {
                        "url": page.get("url", ""),
                        "title": page.get("title", ""),
                        "word_count": page.get("word_count", 0),
                        "content_changed": page.get("content_changed", True),
                        "revalidation_status": page.get("revalidation_status"),
                        "priority_tier": page.get("priority_tier"),
                        "priority_score": page.get("priority_score"),
                        "discovered_depth": page.get("discovered_depth"),
                    }
                    for page in persisted_pages
                ],
            }

            if task_payload.get("auto_index_bot") and job.bot_id:
                from .bot_service import bot_service

                changed_only = bool(revalidate_pages_path) and bool(task_payload.get("append", True))
                documents = []
                for page_index, page in enumerate(persisted_pages):
                    if not page.get("content"):
                        continue
                    if changed_only and page.get("content_changed") is False:
                        continue
                    page_range = page_range_by_index.get(page_index, {})
                    documents.append(
                        {
                            "text": page["content"],
                            "source": page["url"],
                            "metadata": {
                                "title": page.get("title", ""),
                                "url": page.get("url", ""),
                                "word_count": page.get("word_count", 0),
                                "scraped_at": result.get("scraped_at"),
                                "etag": page.get("etag"),
                                "last_modified": page.get("last_modified"),
                                "content_hash": page.get("content_hash"),
                                "revalidation_status": page.get("revalidation_status"),
                                "source_file_path": trace_payload.get("full_source_text") if trace_payload else None,
                                "raw_html_path": trace_payload.get("full_source_html") if trace_payload else None,
                                "trace_job_path": trace_payload.get("job_dir_relative") if trace_payload else None,
                                "trace_chunks_dir": trace_payload.get("chunks_dir") if trace_payload else None,
                                "source_line_start": page_range.get("content_line_start", 1),
                                "source_line_end": page_range.get("content_line_end", 1),
                                "_raw_text": page.get("raw_text") or page.get("content") or "",
                            },
                        }
                    )
                index_stats = await bot_service.upload_knowledge(
                    db,
                    job.bot_id,
                    documents,
                    bool(task_payload.get("append", True)),
                )
                job.chunks_created = int(index_stats.get("chunks_created", 0))
                job.status = "completed"
                job.extracted_data = {
                    **stored_result,
                    "indexing": index_stats,
                    "auto_index_bot": True,
                }
            else:
                job.status = "review"
                job.extracted_data = stored_result
            job.pages_scraped = result.get("statistics", {}).get("total_pages", 0)
            job.completed_at = datetime.utcnow()
            job.heartbeat_at = datetime.utcnow()
            job.lease_owner = None
            supervisor_service.complete_task(db, task, state="completed")
            db.commit()
            log_event(
                logger,
                logging.INFO,
                "scraper_job_completed",
                component="scraper-worker",
                task_id=job_id,
                attempt=job.attempt_count,
                bot_id=job.bot_id,
                worker_id=self._worker_id,
            )
            health_service.set_component(
                "scraper_worker",
                "healthy",
                critical=False,
                detail="scraper job completed",
                metadata={"job_id": job_id, "worker_id": self._worker_id},
            )
        except Exception as exc:
            logger.exception("Scraper job %s failed: %s", job_id, exc)
            job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
            if job:
                job.status = "pending"
                job.error_message = str(exc)
                job.completed_at = None
                job.heartbeat_at = datetime.utcnow()
                job.lease_owner = None
                task = supervisor_service.ensure_task_run(
                    db,
                    task_type="scraper_job",
                    task_key=job_id,
                    payload_json=None,
                    state="running",
                )
                supervisor_service.fail_task(db, task, str(exc), retryable=True)
                db.commit()
                log_event(
                    logger,
                    logging.ERROR,
                    "scraper_job_failed",
                    component="scraper-worker",
                    task_id=job_id,
                    attempt=job.attempt_count,
                    bot_id=job.bot_id,
                    dependency="http",
                    error=str(exc),
                    worker_id=self._worker_id,
                )
            health_service.set_component(
                "scraper_worker",
                "degraded",
                critical=False,
                detail=str(exc),
                metadata={"job_id": job_id, "worker_id": self._worker_id},
            )
        finally:
            db.close()
            self._active_jobs.discard(job_id)


scraper_job_service = ScraperJobService()
