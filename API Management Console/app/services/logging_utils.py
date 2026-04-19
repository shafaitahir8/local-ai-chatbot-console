"""
Structured JSON logging helpers.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any


SENSITIVE_QUERY_RE = re.compile(r"([?&](?:token|auth|authorization|key|api_key)=)([^&\\s]+)", re.IGNORECASE)
SENSITIVE_BEARER_RE = re.compile(r"(authorization\\s*[:=]\\s*bearer\\s+)([^\\s\",]+)", re.IGNORECASE)
SENSITIVE_HEADER_RE = re.compile(r"(x-tunnel-token\\s*[:=]\\s*)([^\\s\",]+)", re.IGNORECASE)


def redact_sensitive_text(value: str) -> str:
    text = str(value)
    text = SENSITIVE_QUERY_RE.sub(r"\\1[REDACTED]", text)
    text = SENSITIVE_BEARER_RE.sub(r"\\1[REDACTED]", text)
    text = SENSITIVE_HEADER_RE.sub(r"\\1[REDACTED]", text)
    return text


def _redact_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_fields(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_fields(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


class JsonFormatter(logging.Formatter):
    """Render log records as JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive_text(record.getMessage()),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(_redact_fields(fields))
        if record.exc_info:
            payload["exception"] = redact_sensitive_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=True)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Emit a structured log event with JSON-serializable fields."""
    logger.log(level, message, extra={"fields": fields})
