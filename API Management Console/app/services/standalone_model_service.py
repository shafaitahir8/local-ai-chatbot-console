"""
Standalone per-bot model folder layout and portability helpers.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional


class StandaloneModelService:
    """Manage the portable models/<bot_id>/ storage contract."""

    STORAGE_VERSION = 1

    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[2]
        self.models_root = self.project_root / "models"
        self.legacy_data_root = self.project_root / "data"
        self.legacy_bots_root = self.legacy_data_root / "bots"
        self.legacy_model_files_root = self.legacy_data_root / "model_files"
        self.models_root.mkdir(parents=True, exist_ok=True)

    @property
    def manifest_path(self) -> Path:
        return self.models_root / "manifest.json"

    def bot_root(self, bot_id: str) -> Path:
        return self.models_root / str(bot_id)

    def config_path(self, bot_id: str) -> Path:
        return self.bot_root(bot_id) / "config.json"

    def vector_store_dir(self, bot_id: str) -> Path:
        return self.bot_root(bot_id) / "vector_store"

    def assets_dir(self, bot_id: str) -> Path:
        return self.bot_root(bot_id) / "assets"

    def memory_db_path(self, bot_id: str) -> Path:
        return self.bot_root(bot_id) / "memory.db"

    def scrapes_dir(self, bot_id: str) -> Path:
        return self.bot_root(bot_id) / "scrapes"

    def transcripts_dir(self, bot_id: str) -> Path:
        return self.assets_dir(bot_id) / "transcripts"

    def documents_dir(self, bot_id: str) -> Path:
        return self.assets_dir(bot_id) / "documents"

    def model_files_dir(self, bot_id: str) -> Path:
        return self.assets_dir(bot_id) / "model_files"

    def scraper_jobs_dir(self, bot_id: str) -> Path:
        return self.assets_dir(bot_id) / "scraper_jobs"

    def shared_runtime_dir(self) -> Path:
        return self.models_root / "_shared"

    def shared_scraper_jobs_dir(self) -> Path:
        return self.shared_runtime_dir() / "scraper_jobs"

    def scraper_job_dir(self, job_id: str, bot_id: Optional[str] = None) -> Path:
        if bot_id:
            base_dir = self.scraper_jobs_dir(bot_id)
        else:
            base_dir = self.shared_scraper_jobs_dir()
        target = base_dir / str(job_id)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def scraper_job_pages_path(self, job_id: str, bot_id: Optional[str] = None) -> Path:
        return self.scraper_job_dir(job_id, bot_id) / "pages.jsonl.gz"

    def scraper_active_job_path(self, bot_id: Optional[str] = None, job_id: Optional[str] = None) -> Path:
        if bot_id:
            base_dir = self.scrapes_dir(bot_id)
            base_dir.mkdir(parents=True, exist_ok=True)
            if job_id:
                return base_dir / f"active_job_{job_id}.json"
            return base_dir / "active_job.json"
        shared_dir = self.shared_scraper_jobs_dir()
        shared_dir.mkdir(parents=True, exist_ok=True)
        if job_id:
            return shared_dir / f"active_job_{job_id}.json"
        return shared_dir / "active_job.json"

    def ensure_bot_layout(self, bot_id: str) -> Path:
        root = self.bot_root(bot_id)
        root.mkdir(parents=True, exist_ok=True)
        self.vector_store_dir(bot_id).mkdir(parents=True, exist_ok=True)
        self.assets_dir(bot_id).mkdir(parents=True, exist_ok=True)
        self.scrapes_dir(bot_id).mkdir(parents=True, exist_ok=True)
        self.transcripts_dir(bot_id).mkdir(parents=True, exist_ok=True)
        self.documents_dir(bot_id).mkdir(parents=True, exist_ok=True)
        self.model_files_dir(bot_id).mkdir(parents=True, exist_ok=True)
        self.scraper_jobs_dir(bot_id).mkdir(parents=True, exist_ok=True)
        self.shared_scraper_jobs_dir().mkdir(parents=True, exist_ok=True)
        self._initialize_memory_db(bot_id)
        return root

    def _initialize_memory_db(self, bot_id: str) -> None:
        self.ensure_bot_layout_shallow(bot_id)
        with self.memory_connection(bot_id) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_memory (
                    session_id TEXT PRIMARY KEY,
                    messages_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    assistant_response TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    retries_used INTEGER NOT NULL DEFAULT 0,
                    fallback_used INTEGER NOT NULL DEFAULT 0,
                    rag_used INTEGER NOT NULL DEFAULT 0,
                    provider_type TEXT,
                    model TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def ensure_bot_layout_shallow(self, bot_id: str) -> None:
        self.bot_root(bot_id).mkdir(parents=True, exist_ok=True)
        self.vector_store_dir(bot_id).mkdir(parents=True, exist_ok=True)
        self.assets_dir(bot_id).mkdir(parents=True, exist_ok=True)
        self.scrapes_dir(bot_id).mkdir(parents=True, exist_ok=True)

    @contextmanager
    def memory_connection(self, bot_id: str) -> Iterator[sqlite3.Connection]:
        db_path = self.memory_db_path(bot_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        try:
            yield connection
        finally:
            connection.close()

    def load_memory_messages(self, bot_id: str, session_id: str) -> list[dict[str, Any]]:
        self._initialize_memory_db(bot_id)
        with self.memory_connection(bot_id) as connection:
            row = connection.execute(
                "SELECT messages_json FROM conversation_memory WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return []
        try:
            payload = json.loads(row[0] or "[]")
        except json.JSONDecodeError:
            return []
        return list(payload) if isinstance(payload, list) else []

    def persist_memory_messages(self, bot_id: str, session_id: str, messages: list[dict[str, Any]]) -> None:
        self._initialize_memory_db(bot_id)
        now = datetime.utcnow().isoformat() + "Z"
        payload = json.dumps(list(messages or []), ensure_ascii=False)
        with self.memory_connection(bot_id) as connection:
            connection.execute(
                """
                INSERT INTO conversation_memory (session_id, messages_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    messages_json = excluded.messages_json,
                    updated_at = excluded.updated_at
                """,
                (session_id, payload, now, now),
            )
            connection.commit()

    def append_transcript(
        self,
        bot_id: str,
        *,
        session_id: str,
        user_message: str,
        assistant_response: str,
        tokens_used: int,
        latency_ms: int,
        retries_used: int,
        fallback_used: bool,
        rag_used: bool,
        provider_type: Optional[str],
        model: Optional[str],
    ) -> None:
        self._initialize_memory_db(bot_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self.memory_connection(bot_id) as connection:
            connection.execute(
                """
                INSERT INTO chat_transcripts (
                    session_id,
                    user_message,
                    assistant_response,
                    tokens_used,
                    latency_ms,
                    retries_used,
                    fallback_used,
                    rag_used,
                    provider_type,
                    model,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_message,
                    assistant_response,
                    int(tokens_used or 0),
                    int(latency_ms or 0),
                    int(retries_used or 0),
                    1 if fallback_used else 0,
                    1 if rag_used else 0,
                    provider_type,
                    model,
                    now,
                ),
            )
            connection.commit()

    def write_bot_config(self, bot: Any) -> Path:
        self.ensure_bot_layout(bot.id)
        payload = {
            "storage_version": self.STORAGE_VERSION,
            "bot": {
                "id": str(bot.id),
                "name": bot.name,
                "description": bot.description,
                "template_type": bot.template_type,
                "provider_type": bot.provider_type,
                "system_prompt": bot.system_prompt,
                "temperature": bot.temperature,
                "max_tokens": bot.max_tokens,
                "model": bot.model,
                "api_key_encrypted": getattr(bot, "api_key_encrypted", None),
                "memory_enabled": bot.memory_enabled,
                "analytics_enabled": bot.analytics_enabled,
                "is_active": bot.is_active,
                "message_limit": bot.message_limit,
                "messages_used": bot.messages_used,
                "token_usage": bot.token_usage,
                "avg_latency": bot.avg_latency,
                "expiration_date": bot.expiration_date.isoformat() if getattr(bot, "expiration_date", None) else None,
                "accent_color": bot.accent_color,
                "welcome_message": bot.welcome_message,
                "created_at": bot.created_at.isoformat() if getattr(bot, "created_at", None) else None,
                "updated_at": bot.updated_at.isoformat() if getattr(bot, "updated_at", None) else None,
            },
            "paths": {
                "vector_store": str(Path("vector_store")),
                "memory_db": str(Path("memory.db")),
                "assets": str(Path("assets")),
            },
            "last_synced_at": datetime.utcnow().isoformat() + "Z",
        }
        config_path = self.config_path(bot.id)
        config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update_manifest_entry(bot.id, payload["bot"])
        return config_path

    def load_bot_config(self, bot_id: str) -> Optional[dict[str, Any]]:
        path = self.config_path(bot_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def iter_bot_configs(self) -> Iterator[tuple[str, dict[str, Any]]]:
        for child in sorted(self.models_root.iterdir()):
            if not child.is_dir():
                continue
            config_path = child / "config.json"
            if not config_path.exists():
                continue
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            bot_payload = payload.get("bot") or {}
            bot_id = str(bot_payload.get("id") or child.name)
            yield bot_id, payload

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {
                "storage_version": self.STORAGE_VERSION,
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "bots": [],
            }
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {
                "storage_version": self.STORAGE_VERSION,
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "bots": [],
            }

    def write_manifest(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["storage_version"] = self.STORAGE_VERSION
        payload["generated_at"] = datetime.utcnow().isoformat() + "Z"
        self.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_manifest_entry(self, bot_id: str, bot_payload: dict[str, Any]) -> None:
        manifest = self.load_manifest()
        entries = [entry for entry in manifest.get("bots", []) if str(entry.get("id")) != str(bot_id)]
        vector_dir = self.vector_store_dir(bot_id)
        entry = {
            "id": str(bot_id),
            "name": bot_payload.get("name"),
            "path": str(bot_id),
            "config_path": f"{bot_id}/config.json",
            "provider_type": bot_payload.get("provider_type"),
            "model": bot_payload.get("model"),
            "is_active": bool(bot_payload.get("is_active", True)),
            "updated_at": bot_payload.get("updated_at") or datetime.utcnow().isoformat() + "Z",
            "storage_version": self.STORAGE_VERSION,
            "vector_store": {
                "path": f"{bot_id}/vector_store",
                "faiss_index": (vector_dir / "faiss.index").exists(),
                "chunks_json": (vector_dir / "chunks.json").exists(),
            },
            "memory_db": {
                "path": f"{bot_id}/memory.db",
                "exists": self.memory_db_path(bot_id).exists(),
            },
        }
        entries.append(entry)
        manifest["bots"] = sorted(entries, key=lambda item: str(item.get("name") or item.get("id")))
        self.write_manifest(manifest)

    def remove_manifest_entry(self, bot_id: str) -> None:
        manifest = self.load_manifest()
        manifest["bots"] = [entry for entry in manifest.get("bots", []) if str(entry.get("id")) != str(bot_id)]
        self.write_manifest(manifest)

    def delete_bot_folder(self, bot_id: str) -> None:
        bot_root = self.bot_root(bot_id)
        if bot_root.exists():
            shutil.rmtree(bot_root)
        self.remove_manifest_entry(bot_id)

    def sync_db_from_folders(self, db: Any) -> int:
        """Create missing DB bot records from portable folders."""
        from ..models.database import Bot

        created = 0
        imported_chunks = 0
        for bot_id, payload in self.iter_bot_configs():
            existing_bot = db.query(Bot).filter(Bot.id == bot_id).first()
            if existing_bot:
                imported_chunks += self._sync_knowledge_chunks_from_folder(db, bot_id)
                continue
            bot_payload = dict(payload.get("bot") or {})
            if not bot_payload:
                continue
            bot = Bot(
                id=bot_id,
                name=bot_payload.get("name") or bot_id,
                description=bot_payload.get("description"),
                template_type=bot_payload.get("template_type") or "local_template",
                provider_type=bot_payload.get("provider_type") or "local",
                system_prompt=bot_payload.get("system_prompt"),
                temperature=float(bot_payload.get("temperature") or 0.7),
                max_tokens=int(bot_payload.get("max_tokens") or 2000),
                model=bot_payload.get("model"),
                api_key_encrypted=bot_payload.get("api_key_encrypted"),
                memory_enabled=bool(bot_payload.get("memory_enabled", True)),
                analytics_enabled=bool(bot_payload.get("analytics_enabled", True)),
                is_active=bool(bot_payload.get("is_active", True)),
                message_limit=bot_payload.get("message_limit"),
                messages_used=int(bot_payload.get("messages_used") or 0),
                token_usage=int(bot_payload.get("token_usage") or 0),
                avg_latency=float(bot_payload.get("avg_latency") or 0),
                expiration_date=self._parse_datetime(bot_payload.get("expiration_date")),
                accent_color=bot_payload.get("accent_color") or "#007bff",
                welcome_message=bot_payload.get("welcome_message") or "Hello! How can I help you today?",
                created_at=self._parse_datetime(bot_payload.get("created_at")) or datetime.utcnow(),
                updated_at=self._parse_datetime(bot_payload.get("updated_at")) or datetime.utcnow(),
            )
            db.add(bot)
            db.flush()
            imported_chunks += self._sync_knowledge_chunks_from_folder(db, bot_id)
            created += 1
        if created or imported_chunks:
            db.commit()
        return created

    def ensure_folders_for_existing_bots(self, bots: Iterable[Any]) -> None:
        for bot in bots:
            self.write_bot_config(bot)

    def migrate_legacy_bot_storage(self, bot_id: str) -> dict[str, Any]:
        """Move old scattered storage into models/<bot_id>/."""
        root = self.ensure_bot_layout(bot_id)
        migrated: dict[str, Any] = {
            "bot_id": bot_id,
            "bot_root": str(root),
            "moved": [],
        }

        legacy_bot_root = self.legacy_bots_root / bot_id
        if legacy_bot_root.exists():
            for child_name, target_dir in {
                "documents": self.documents_dir(bot_id),
                "sessions": self.transcripts_dir(bot_id),
                "embeddings": self.vector_store_dir(bot_id),
            }.items():
                child = legacy_bot_root / child_name
                if not child.exists():
                    continue
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.copytree(child, target_dir)
                migrated["moved"].append({"from": str(child.relative_to(self.project_root)), "to": str(target_dir.relative_to(self.project_root))})

        if self.legacy_model_files_root.exists():
            for path in self.legacy_model_files_root.rglob(bot_id):
                if not path.is_dir():
                    continue
                target = self.model_files_dir(bot_id)
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(path, target)
                migrated["moved"].append({"from": str(path.relative_to(self.project_root)), "to": str(target.relative_to(self.project_root))})
                break

        return migrated

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    def _sync_knowledge_chunks_from_folder(self, db: Any, bot_id: str) -> int:
        from ..models.database import KnowledgeChunk

        existing_count = db.query(KnowledgeChunk).filter(KnowledgeChunk.bot_id == bot_id).count()
        if existing_count:
            return 0

        chunks_path = self.vector_store_dir(bot_id) / "chunks.json"
        if not chunks_path.exists():
            return 0

        try:
            chunks_payload = json.loads(chunks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return 0

        imported = 0
        for chunk in list(chunks_payload or []):
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            db.add(
                KnowledgeChunk(
                    bot_id=bot_id,
                    content=text,
                    source=chunk.get("source"),
                    chunk_index=int(chunk.get("chunk_index") or imported),
                    word_count=int(chunk.get("word_count") or len(text.split())),
                    chunk_metadata=dict(chunk.get("metadata") or {}),
                )
            )
            imported += 1
        return imported


standalone_model_service = StandaloneModelService()
