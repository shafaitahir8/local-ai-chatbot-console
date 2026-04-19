"""
Role-based runtime entrypoints for supervised background units.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta

from .models.database import ComponentLease, SessionLocal, init_db
from .services.api_gateway_service import api_gateway_service
from .services.embedding_worker_service import embedding_worker_service
from .services.ollama_sentinel_service import ollama_sentinel_service
from .services.scheduler_service import scheduler_service
from .services.scraper_job_service import scraper_job_service
from .services.supervisor_service import supervisor_service
from .services.tunnel_service import tunnel_service


logger = logging.getLogger(__name__)


def _make_worker_id(role: str) -> str:
    return f"{role}-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def _run_service(start_fn, stop_fn) -> None:
    await start_fn()
    stopper = asyncio.Event()
    try:
        await stopper.wait()
    finally:
        await stop_fn()


async def run_gateway_agent() -> None:
    worker_id = _make_worker_id("gateway-agent")
    target_base_url = os.getenv("APP_ORIGIN_URL", "http://127.0.0.1:8000").rstrip("/")
    attempt = 0
    try:
        while True:
            db = SessionLocal()
            try:
                if not api_gateway_service.is_running():
                    api_gateway_service.start(target_base_url=target_base_url)
                status = api_gateway_service.get_status()
                metadata = {
                    **status,
                    "origin_url": api_gateway_service.get_origin_url() or target_base_url,
                }
                state = "running" if status.get("running") else "degraded"
                supervisor_service.upsert_component_lease(
                    db,
                    component_name="gateway-agent",
                    unit_type="gateway-agent",
                    lease_owner=worker_id,
                    state=state,
                    attempt=attempt,
                    last_error=status.get("circuit_breaker", {}).get("last_error"),
                    metadata=metadata,
                )
                attempt = 0
            except Exception as exc:
                attempt += 1
                delay = supervisor_service.full_jitter_backoff(attempt)
                supervisor_service.upsert_component_lease(
                    db,
                    component_name="gateway-agent",
                    unit_type="gateway-agent",
                    lease_owner=worker_id,
                    state="retrying",
                    attempt=attempt,
                    last_error=str(exc),
                    next_retry_at=datetime.utcnow() + timedelta(seconds=delay),
                    metadata={"target_base_url": target_base_url},
                )
                logger.warning("Gateway agent startup failed: %s", exc)
                await asyncio.sleep(delay)
            finally:
                db.close()
            await asyncio.sleep(5)
    finally:
        api_gateway_service.shutdown()
        db = SessionLocal()
        try:
            supervisor_service.upsert_component_lease(
                db,
                component_name="gateway-agent",
                unit_type="gateway-agent",
                lease_owner=worker_id,
                state="stopped",
            )
        finally:
            db.close()


def _resolve_tunnel_origin_url() -> str:
    explicit = os.getenv("TUNNEL_ORIGIN_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")

    db = SessionLocal()
    try:
        gateway_lease = db.query(ComponentLease).filter(ComponentLease.component_name == "gateway-agent").first()
        if gateway_lease and (gateway_lease.metadata_json or {}).get("origin_url"):
            return str(gateway_lease.metadata_json["origin_url"]).rstrip("/")
    finally:
        db.close()

    return os.getenv("APP_ORIGIN_URL", "http://127.0.0.1:8000").rstrip("/")


async def run_tunnel_agent() -> None:
    worker_id = _make_worker_id("tunnel-agent")
    attempt = 0
    try:
        while True:
            origin_url = _resolve_tunnel_origin_url()
            db = SessionLocal()
            try:
                existing_lease = supervisor_service.get_component_lease(db, "tunnel-agent")
                existing_metadata = (existing_lease.metadata_json or {}) if existing_lease else {}
                restart_event = supervisor_service.claim_outbox_event(
                    db,
                    event_type="restart_tunnel",
                    lease_owner=worker_id,
                )
                if restart_event and restart_event.aggregate_id == "tunnel-agent":
                    payload = restart_event.payload_json or {}
                    requested_origin = str(payload.get("origin_url") or "").strip().rstrip("/")
                    await tunnel_service.shutdown()
                    origin_url = requested_origin or origin_url
                    supervisor_service.complete_outbox_event(db, restart_event)
                    supervisor_service.upsert_component_lease(
                        db,
                        component_name="tunnel-agent",
                        unit_type="tunnel-agent",
                        lease_owner=worker_id,
                        state="starting",
                        metadata={
                            "origin_url": origin_url,
                            "restart_requested_by": payload.get("requested_by"),
                            "restart_requested_at": payload.get("requested_at"),
                        },
                    )
                tunnel_ok = await tunnel_service.ensure_started(origin_url=origin_url)
                status = tunnel_service.get_status()
                live_public_base_url = status.get("live_public_base_url")
                last_known_public_base_url = (
                    live_public_base_url
                    or status.get("configured_public_base_url")
                    or existing_metadata.get("last_known_public_base_url")
                    or existing_metadata.get("public_base_url")
                )
                metadata = {
                    **status,
                    "origin_url": origin_url,
                    "live_public_base_url": live_public_base_url,
                    "public_base_url": last_known_public_base_url,
                    "last_known_public_base_url": last_known_public_base_url,
                }
                if live_public_base_url:
                    metadata["public_base_url_observed_at"] = datetime.utcnow().isoformat() + "Z"
                elif existing_metadata.get("public_base_url_observed_at"):
                    metadata["public_base_url_observed_at"] = existing_metadata.get("public_base_url_observed_at")
                state = "running" if tunnel_ok and live_public_base_url else "degraded"
                supervisor_service.upsert_component_lease(
                    db,
                    component_name="tunnel-agent",
                    unit_type="tunnel-agent",
                    lease_owner=worker_id,
                    state=state,
                    attempt=attempt,
                    last_error=status.get("last_error"),
                    metadata=metadata,
                )
                if state == "running":
                    attempt = 0
                else:
                    attempt += 1
            except Exception as exc:
                attempt += 1
                delay = supervisor_service.full_jitter_backoff(attempt)
                try:
                    db.rollback()
                except Exception:
                    pass
                failure_db = SessionLocal()
                try:
                    supervisor_service.upsert_component_lease(
                        failure_db,
                        component_name="tunnel-agent",
                        unit_type="tunnel-agent",
                        lease_owner=worker_id,
                        state="retrying",
                        attempt=attempt,
                        last_error=str(exc),
                        next_retry_at=datetime.utcnow() + timedelta(seconds=delay),
                        metadata={"origin_url": origin_url},
                    )
                except Exception as lease_exc:
                    logger.warning("Tunnel agent lease update failed during retry handling: %s", lease_exc)
                finally:
                    failure_db.close()
                logger.warning("Tunnel agent startup failed: %s", exc)
                await asyncio.sleep(delay)
            finally:
                db.close()
            await asyncio.sleep(5)
    finally:
        await tunnel_service.shutdown()
        db = SessionLocal()
        try:
            supervisor_service.upsert_component_lease(
                db,
                component_name="tunnel-agent",
                unit_type="tunnel-agent",
                lease_owner=worker_id,
                state="stopped",
            )
        finally:
            db.close()


async def run_role(role: str) -> None:
    init_db()
    role = role.strip().lower()

    if role == "scraper-worker":
        await _run_service(scraper_job_service.start, scraper_job_service.shutdown)
        return
    if role == "embedding-worker":
        await _run_service(embedding_worker_service.start, embedding_worker_service.shutdown)
        return
    if role == "scheduler":
        await _run_service(scheduler_service.start, scheduler_service.shutdown)
        return
    if role == "gateway-agent":
        await run_gateway_agent()
        return
    if role == "tunnel-agent":
        await run_tunnel_agent()
        return
    if role == "ollama-sentinel":
        await _run_service(ollama_sentinel_service.start, ollama_sentinel_service.shutdown)
        return

    raise SystemExit(
        f"Unknown role '{role}'. Supported roles: scraper-worker, embedding-worker, scheduler, gateway-agent, tunnel-agent, ollama-sentinel"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a supervised background role")
    parser.add_argument(
        "role",
        nargs="?",
        default=os.getenv("APP_RUNTIME_ROLE", "").strip() or "scheduler",
        help="Role to run",
    )
    args = parser.parse_args()
    asyncio.run(run_role(args.role))


if __name__ == "__main__":
    main()
