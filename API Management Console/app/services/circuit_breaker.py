"""
Simple circuit breaker for external dependency protection.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a breaker is open and requests are rejected."""

    def __init__(self, name: str, retry_after: float) -> None:
        self.name = name
        self.retry_after = max(0.0, float(retry_after))
        super().__init__(f"Circuit breaker '{name}' is open; retry after {self.retry_after:.2f}s")


@dataclass
class _BreakerState:
    state: str = "closed"
    failure_count: int = 0
    opened_at: Optional[float] = None
    last_error: Optional[str] = None
    half_open_attempts: int = 0
    half_open_successes: int = 0


class CircuitBreaker:
    """Thread-safe circuit breaker with a minimal half-open probe state."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: Optional[int] = None,
        recovery_timeout: Optional[float] = None,
        half_open_max_calls: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = int(
            failure_threshold or os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5")
        )
        self.failure_rate_window = int(os.getenv("CIRCUIT_BREAKER_FAILURE_RATE_WINDOW", "20"))
        self.failure_rate_threshold = float(os.getenv("CIRCUIT_BREAKER_FAILURE_RATE_THRESHOLD", "0.5"))
        self.recovery_timeout = float(
            recovery_timeout or os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS", "30")
        )
        self.half_open_max_calls = max(1, int(half_open_max_calls))
        self.half_open_success_threshold = int(os.getenv("CIRCUIT_BREAKER_HALF_OPEN_SUCCESS_THRESHOLD", "3"))
        self._state = _BreakerState()
        self._history: Deque[bool] = deque(maxlen=max(1, self.failure_rate_window))
        self._lock = threading.Lock()

    def before_request(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._state.state == "open":
                if self._state.opened_at is None:
                    self._state.opened_at = now
                elapsed = now - self._state.opened_at
                if elapsed >= self.recovery_timeout:
                    self._state.state = "half_open"
                    self._state.half_open_attempts = 0
                    self._state.half_open_successes = 0
                else:
                    raise CircuitBreakerOpenError(self.name, self.recovery_timeout - elapsed)

            if self._state.state == "half_open":
                if self._state.half_open_attempts >= self.half_open_max_calls:
                    retry_after = self.recovery_timeout
                    raise CircuitBreakerOpenError(self.name, retry_after)
                self._state.half_open_attempts += 1

    def record_success(self) -> None:
        with self._lock:
            self._history.append(True)
            if self._state.state == "half_open":
                self._state.half_open_successes += 1
                if self._state.half_open_successes >= self.half_open_success_threshold:
                    self._state = _BreakerState(state="closed")
                return
            self._state.failure_count = 0
            self._state.last_error = None
            self._state.state = "closed"

    def record_failure(self, error: Exception | str) -> None:
        with self._lock:
            self._history.append(False)
            self._state.failure_count += 1
            self._state.last_error = str(error)
            should_open = self._state.state == "half_open" or self._state.failure_count >= self.failure_threshold
            if len(self._history) >= self.failure_rate_window:
                failures = sum(1 for outcome in self._history if outcome is False)
                failure_rate = failures / max(1, len(self._history))
                if failure_rate >= self.failure_rate_threshold:
                    should_open = True
            if should_open:
                self._state.state = "open"
                self._state.opened_at = time.monotonic()
                self._state.half_open_attempts = 0
                self._state.half_open_successes = 0

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            retry_after = 0.0
            if self._state.state == "open" and self._state.opened_at is not None:
                retry_after = max(0.0, self.recovery_timeout - (time.monotonic() - self._state.opened_at))
            failures = sum(1 for outcome in self._history if outcome is False)
            total = len(self._history)
            return {
                "name": self.name,
                "state": self._state.state,
                "failure_count": self._state.failure_count,
                "recent_failure_rate": round((failures / total) if total else 0.0, 3),
                "recent_window_size": total,
                "last_error": self._state.last_error,
                "retry_after_seconds": round(retry_after, 2),
            }
