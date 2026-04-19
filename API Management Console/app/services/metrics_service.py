"""
Lightweight Prometheus-compatible metrics registry.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Dict, Iterable, Optional, Tuple


LabelKey = Tuple[Tuple[str, str], ...]


class MetricsService:
    """Small in-process metrics collector with Prometheus text rendering."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._definitions: Dict[str, tuple[str, str]] = {
            "heartbeat_age_seconds": ("gauge", "Age of the latest component lease heartbeat in seconds."),
            "task_restart_total": ("counter", "Number of task restarts triggered by supervisor recovery."),
            "provider_timeout_total": ("counter", "Number of provider timeout failures."),
            "provider_circuit_open": ("gauge", "Whether a provider circuit breaker is open."),
            "rag_bypass_total": ("counter", "Number of chat requests that bypassed RAG due to degraded state."),
            "index_version_age": ("gauge", "Age of the active embedding index version in seconds."),
            "scraper_checkpoint_lag": ("gauge", "Age of the latest scraper checkpoint heartbeat in seconds."),
            "db_write_fail_total": ("counter", "Number of database write failures."),
        }
        self._counters: Dict[str, Dict[LabelKey, float]] = defaultdict(dict)
        self._gauges: Dict[str, Dict[LabelKey, float]] = defaultdict(dict)

    def _normalize_labels(self, labels: Optional[dict[str, object]] = None) -> LabelKey:
        normalized = []
        for key, value in sorted((labels or {}).items()):
            normalized.append((str(key), "" if value is None else str(value)))
        return tuple(normalized)

    def inc_counter(self, name: str, value: float = 1.0, *, labels: Optional[dict[str, object]] = None) -> None:
        with self._lock:
            label_key = self._normalize_labels(labels)
            current = self._counters[name].get(label_key, 0.0)
            self._counters[name][label_key] = current + float(value)

    def set_gauge(self, name: str, value: float, *, labels: Optional[dict[str, object]] = None) -> None:
        with self._lock:
            label_key = self._normalize_labels(labels)
            self._gauges[name][label_key] = float(value)

    def _format_labels(self, labels: LabelKey) -> str:
        if not labels:
            return ""
        encoded = []
        for key, value in labels:
            safe = value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
            encoded.append(f'{key}="{safe}"')
        return "{" + ",".join(encoded) + "}"

    def _render_metric_samples(self, name: str, samples: Iterable[tuple[LabelKey, float]]) -> list[str]:
        return [f"{name}{self._format_labels(labels)} {value}" for labels, value in samples]

    def render_prometheus(self, extra_blocks: Optional[list[str]] = None) -> str:
        with self._lock:
            counter_copy = {name: dict(values) for name, values in self._counters.items()}
            gauge_copy = {name: dict(values) for name, values in self._gauges.items()}
            definitions = dict(self._definitions)

        lines: list[str] = []
        rendered_names = sorted(set(definitions) | set(counter_copy) | set(gauge_copy))
        for name in rendered_names:
            metric_type, help_text = definitions.get(name, ("gauge", name))
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {metric_type}")
            if metric_type == "counter":
                lines.extend(self._render_metric_samples(name, counter_copy.get(name, {}).items()))
            else:
                lines.extend(self._render_metric_samples(name, gauge_copy.get(name, {}).items()))
        for block in extra_blocks or []:
            if block:
                lines.append(block.rstrip())
        return "\n".join(lines).strip() + "\n"

    def snapshot(self) -> dict:
        """Return a JSON-serializable snapshot of definitions and current samples."""
        with self._lock:
            return {
                "definitions": {
                    name: {"type": metric_type, "help": help_text}
                    for name, (metric_type, help_text) in self._definitions.items()
                },
                "counters": {
                    name: [
                        {"labels": dict(labels), "value": value}
                        for labels, value in samples.items()
                    ]
                    for name, samples in self._counters.items()
                },
                "gauges": {
                    name: [
                        {"labels": dict(labels), "value": value}
                        for labels, value in samples.items()
                    ]
                    for name, samples in self._gauges.items()
                },
            }


metrics_service = MetricsService()
