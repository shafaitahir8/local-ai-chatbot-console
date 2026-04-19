"""
Runtime component health registry.
"""

from __future__ import annotations

from datetime import datetime
import threading
from typing import Any, Dict, Optional


class HealthService:
    """In-memory health registry for process-local component truthfulness."""

    def __init__(self) -> None:
        self._components: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def set_component(
        self,
        name: str,
        status: str,
        *,
        critical: bool = False,
        detail: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "status": status,
            "critical": bool(critical),
            "detail": detail,
            "metadata": metadata or {},
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        with self._lock:
            self._components[name] = payload

    def get_report(self) -> Dict[str, Any]:
        with self._lock:
            components = {name: dict(value) for name, value in self._components.items()}

        overall = "healthy"
        for component in components.values():
            status = component.get("status", "unknown")
            critical = bool(component.get("critical"))
            if status == "unhealthy" and critical:
                overall = "unhealthy"
                break
            if status in {"degraded", "unhealthy", "unknown"} and overall != "unhealthy":
                overall = "degraded"

        return {
            "status": overall,
            "components": components,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }


health_service = HealthService()
