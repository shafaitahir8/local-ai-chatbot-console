"""
Shared system inspection helpers for health reports and DB-backed metrics refresh.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from ..models.database import ComponentLease, IndexManifest, SessionLocal, TaskRun
from .metrics_service import metrics_service
from .safe_mode_service import safe_mode_service
from .tunnel_service import tunnel_service


def lease_is_stale(heartbeat_at) -> bool:
    """Return True when a component heartbeat has exceeded the staleness budget."""
    stale_after = int(os.getenv("COMPONENT_LEASE_STALE_SECONDS", "15"))
    if not heartbeat_at:
        return True
    return heartbeat_at < datetime.utcnow() - timedelta(seconds=stale_after)


def build_supervisor_health_report() -> dict:
    """Build the supervisor-backed health report used by API and CLI surfaces."""
    from sqlalchemy import text

    db = SessionLocal()
    try:
        components = {}
        db_status = "healthy"
        db_detail = "query ok"
        try:
            db.execute(text("SELECT 1"))
        except Exception as exc:
            db_status = "unhealthy"
            db_detail = str(exc)

        components["database"] = {
            "status": db_status,
            "critical": True,
            "detail": db_detail,
            "metadata": {},
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

        for lease in db.query(ComponentLease).all():
            stale = lease_is_stale(lease.heartbeat_at)
            components[lease.component_name] = {
                "status": "degraded" if stale or lease.state != "running" else "healthy",
                "critical": lease.component_name == "api",
                "detail": "stale lease" if stale else lease.state,
                "metadata": {
                    **(lease.metadata_json or {}),
                    "lease_owner": lease.lease_owner,
                    "unit_type": lease.unit_type,
                    "attempt": lease.attempt,
                    "generation": lease.generation,
                    "last_error": lease.last_error,
                    "next_retry_at": lease.next_retry_at.isoformat() if lease.next_retry_at else None,
                },
                "updated_at": lease.heartbeat_at.isoformat() + "Z" if lease.heartbeat_at else None,
            }

        overall = "healthy"
        for component in components.values():
            status = component.get("status", "unknown")
            critical = bool(component.get("critical"))
            if status == "unhealthy" and critical:
                overall = "unhealthy"
                break
            if status in {"degraded", "unhealthy", "unknown"} and overall != "unhealthy":
                overall = "degraded"

        active_modes = safe_mode_service.serialize_modes(safe_mode_service.get_active_modes(db))
        tunnel_metadata = components.get("tunnel-agent", {}).get("metadata", {})
        gateway_metadata = components.get("gateway-agent", {}).get("metadata", {})
        warmup_metadata = components.get("warmup", {}).get("metadata", {}) or {}

        public_base_url = (
            tunnel_metadata.get("public_base_url")
            or tunnel_metadata.get("last_known_public_base_url")
            or tunnel_service.get_configured_public_base_url()
        )

        return {
            "status": overall,
            "components": components,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "public_base_url": public_base_url,
            "api_gateway": gateway_metadata,
            "safe_modes": active_modes,
            "hot_state": warmup_metadata.get("hot_state", "cold"),
            "hot_ready": bool(warmup_metadata.get("hot_ready")),
            "warmup": warmup_metadata,
        }
    finally:
        db.close()


def refresh_observability_metrics() -> None:
    """Populate the process-local metrics registry from durable supervisor state."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        for lease in db.query(ComponentLease).all():
            age = max(0.0, (now - lease.heartbeat_at).total_seconds()) if lease.heartbeat_at else -1.0
            metrics_service.set_gauge(
                "heartbeat_age_seconds",
                age,
                labels={"component": lease.component_name},
            )

        for manifest in db.query(IndexManifest).filter(IndexManifest.state == "active").all():
            reference_time = manifest.activated_at or manifest.updated_at or manifest.created_at
            age = max(0.0, (now - reference_time).total_seconds()) if reference_time else -1.0
            metrics_service.set_gauge(
                "index_version_age",
                age,
                labels={"bot_id": manifest.bot_id, "version": manifest.version},
            )

        for task in db.query(TaskRun).filter(TaskRun.task_type == "scraper_job").all():
            checkpoint_lag = max(0.0, (now - task.heartbeat_at).total_seconds()) if task.heartbeat_at else -1.0
            metrics_service.set_gauge(
                "scraper_checkpoint_lag",
                checkpoint_lag,
                labels={"task_id": task.task_key, "state": task.state},
            )
    finally:
        db.close()
