"""
Persisted runtime secret management.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path


logger = logging.getLogger(__name__)


class RuntimeSecretService:
    """Load secrets from env first, otherwise persist a generated fallback."""

    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.secrets_dir = root / "data" / "runtime_secrets"
        self.secrets_dir.mkdir(parents=True, exist_ok=True)

    def _is_production_mode(self) -> bool:
        environment = (
            os.getenv("APP_ENV")
            or os.getenv("ENVIRONMENT")
            or os.getenv("ENV")
            or ""
        ).strip().lower()
        debug_enabled = (os.getenv("DEBUG") or "").strip().lower() in {"1", "true", "yes"}
        return environment in {"prod", "production"} or (environment not in {"", "dev", "development", "local", "test"} and not debug_enabled)

    def _secret_path(self, secret_name: str) -> Path:
        safe_name = "".join(ch for ch in secret_name if ch.isalnum() or ch in {"_", "-"}).strip("._-")
        safe_name = safe_name or "runtime_secret"
        return self.secrets_dir / f"{safe_name}.txt"

    def get_or_create_secret(self, env_name: str, secret_name: str) -> str:
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value

        if self._is_production_mode():
            raise RuntimeError(
                f"{env_name} is required when APP_ENV indicates production mode; "
                "runtime secret autogeneration is disabled."
            )

        path = self._secret_path(secret_name)
        if path.exists():
            persisted = path.read_text(encoding="utf-8").strip()
            if persisted:
                return persisted

        generated = secrets.token_urlsafe(48)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(generated, encoding="utf-8")
        os.replace(temp_path, path)
        logger.warning(
            "%s was not set; generated a persisted runtime secret at %s",
            env_name,
            path,
        )
        return generated


runtime_secret_service = RuntimeSecretService()
