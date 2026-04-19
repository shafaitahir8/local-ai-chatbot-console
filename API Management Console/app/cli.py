"""
Arctic administrative CLI.

This CLI is the primary headless operations surface for bot management, scraping,
database maintenance, and runtime diagnostics. It intentionally reuses the same
service and database layer as the FastAPI application for parity.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Iterator, Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table
from sqlalchemy import func, inspect, or_
from sqlalchemy.orm import Session

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from .models.database import (
    Base,
    Bot,
    ChatRequestRecord,
    ComponentLease,
    ConversationMemory,
    DATABASE_URL,
    IS_SQLITE,
    IndexManifest,
    KnowledgeChunk,
    Message,
    OutboxEvent,
    ScraperJob,
    SessionLocal,
    TaskRun,
    engine,
    init_db,
)
from .models.schemas import BotCreate
from .services.analytics_service import analytics_service
from .services.bot_service import bot_service
from .services.chat_request_service import chat_request_service
from .services.embedding_service import embedding_service
from .services.encryption_service import encryption_service
from .services.llm_providers import ClaudeProvider, GeminiProvider, LocalProvider, OpenAIProvider
from .services.llm_providers.base_provider import (
    BaseLLMProvider,
    ChatMessage,
    ChatResponse as ProviderChatResponse,
    EmbeddingResponse,
)
from .services.metrics_service import metrics_service
from .services.redis_service import redis_service
from .services.runtime_secret_service import runtime_secret_service
from .services.safe_mode_service import safe_mode_service
from .services.scraper_job_service import scraper_job_service
from .services.scraper_service import ScraperConfig, ScraperService
from .services.standalone_model_service import standalone_model_service
from .services.supervisor_service import supervisor_service
from .services.system_inspection_service import (
    build_supervisor_health_report,
    lease_is_stale,
    refresh_observability_metrics,
)
from .services.template_service import template_service
from .services.tunnel_service import tunnel_service


console = Console()
error_console = Console(stderr=True)

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help=(
        "Arctic headless administration CLI.\n\n"
        "Client-safe commands:\n"
        "  arctic bot list\n"
        "  arctic bot chat\n"
        "  arctic scraper status\n"
        "  arctic scraper test-parse\n"
        "  arctic sys health\n"
        "  arctic sys view-metrics\n\n"
        "Operator-only commands:\n"
        "  arctic bot create\n"
        "  arctic bot delete\n"
        "  arctic bot update-config\n"
        "  arctic scraper run\n"
        "  arctic scraper clear-queue\n"
        "  arctic db *\n"
        "  arctic sys restart-tunnel"
    ),
)
bot_app = typer.Typer(
    no_args_is_help=True,
    help="Bot management. Client-safe: list, chat. Operator-only: create, delete, update-config.",
)
scraper_app = typer.Typer(
    no_args_is_help=True,
    help="Scraper tools. Client-safe: status, test-parse. Operator-only: run, clear-queue.",
)
db_app = typer.Typer(no_args_is_help=True, help="Operator-only database maintenance commands.")
sys_app = typer.Typer(
    no_args_is_help=True,
    help="Runtime tools. Client-safe: health, view-metrics. Operator-only: restart-tunnel.",
)

app.add_typer(bot_app, name="bot")
app.add_typer(scraper_app, name="scraper")
app.add_typer(db_app, name="db")
app.add_typer(sys_app, name="sys")

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


class MockProvider(BaseLLMProvider):
    """Deterministic provider for credit-free CLI testing."""

    def __init__(self, provider_name: str = "mock", model: Optional[str] = None):
        self._provider_name = provider_name
        super().__init__(api_key="mock", model=model or f"{provider_name}-mock-model")

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str:
        return self.model or f"{self._provider_name}-mock-model"

    @property
    def supported_models(self) -> list[str]:
        return [self.default_model]

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs,
    ) -> ProviderChatResponse:
        last_user = next((msg.content for msg in reversed(messages) if msg.role == "user"), "")
        context_messages = [msg.content for msg in messages if msg.role == "system"]
        context_hint = " with mocked context" if any("Relevant context:" in msg for msg in context_messages) else ""
        content = f"[mock:{self._provider_name}] Echo{context_hint}: {last_user}"
        prompt_tokens = max(1, sum(len(msg.content.split()) for msg in messages))
        completion_tokens = max(1, len(content.split()))
        return ProviderChatResponse(
            content=content,
            tokens_prompt=prompt_tokens,
            tokens_completion=completion_tokens,
            tokens_total=prompt_tokens + completion_tokens,
            model=self.default_model,
            finish_reason="stop",
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs,
    ):
        response = await self.chat(messages, temperature=temperature, max_tokens=max_tokens, **kwargs)
        yield response.content

    async def get_embeddings(self, texts: list[str], **kwargs) -> EmbeddingResponse:
        return EmbeddingResponse(
            embeddings=[[0.0, 0.0, 0.0, 0.0] for _ in texts],
            tokens_used=0,
            model=f"{self._provider_name}-mock-embeddings",
        )

    def validate_config(self) -> bool:
        return True


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return str(value)


def _emit(payload: dict[str, Any], *, json_output: bool, renderable=None) -> None:
    if json_output:
        typer.echo(json.dumps(payload, default=_json_default, ensure_ascii=True))
        return
    if renderable is not None:
        console.print(renderable)
    elif payload:
        console.print(payload)


def _fail(message: str, *, json_output: bool, exit_code: int = 1, **details: Any) -> None:
    payload = {"status": "error", "message": message, **details}
    if json_output:
        typer.echo(json.dumps(payload, default=_json_default, ensure_ascii=True))
    else:
        error_console.print(f"[red]{message}[/red]")
        if details:
            error_console.print(details)
    raise typer.Exit(exit_code)


def _encryption_status_label() -> str:
    env_secret = bool((os.getenv("ENCRYPTION_SECRET") or "").strip())
    persisted_secret = runtime_secret_service._secret_path("encryption_secret").exists()
    return "enabled" if env_secret or persisted_secret else "missing"


def _hubspot_readiness_label(report: dict[str, Any]) -> str:
    gateway_status = report.get("components", {}).get("gateway-agent", {}).get("status")
    public_base_url = report.get("public_base_url")
    if gateway_status == "healthy" and public_base_url:
        return "ready"
    if gateway_status == "healthy":
        return "local-only"
    return "warming"


def _health_banner(report: dict[str, Any]) -> Panel:
    hot_state = str(report.get("hot_state") or "cold").upper()
    encryption_status = _encryption_status_label().upper()
    public_url = report.get("public_base_url") or "http://localhost:8000"
    hubspot_ready = _hubspot_readiness_label(report).upper()
    summary = (
        f"[bold]Thermal[/bold]: {hot_state}\n"
        f"[bold]Encryption[/bold]: {encryption_status}\n"
        f"[bold]Public URL[/bold]: {public_url}\n"
        f"[bold]HubSpot Readiness[/bold]: {hubspot_ready}\n"
        f"[bold]Safe Modes[/bold]: {', '.join(report.get('safe_modes', [])) or '-'}"
    )
    border_style = "green" if report.get("status") == "healthy" else "yellow" if report.get("status") == "degraded" else "red"
    return Panel(summary, title=f"System {str(report.get('status') or 'unknown').upper()}", border_style=border_style)


def _bot_source_summary(db: Session, bot_id: str, limit: int = 5) -> list[str]:
    rows = (
        db.query(KnowledgeChunk.source, func.count(KnowledgeChunk.id).label("count"))
        .filter(KnowledgeChunk.bot_id == bot_id)
        .group_by(KnowledgeChunk.source)
        .order_by(func.count(KnowledgeChunk.id).desc())
        .limit(limit)
        .all()
    )
    return [f"{(source or 'manual_upload')} ({count})" for source, count in rows]


@contextmanager
def _db_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _parse_cli_value(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"none", "null"}:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_set_pairs(pairs: Optional[list[str]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"Expected key=value syntax, got '{pair}'")
        key, raw_value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid empty key in '{pair}'")
        values[key] = _parse_cli_value(raw_value)
    return values


def _parse_optional_datetime(raw: Optional[str]) -> Optional[datetime]:
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    normalized = candidate.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _provider_class(provider_name: str):
    provider_name = provider_name.strip().lower()
    provider_map = {
        "openai": OpenAIProvider,
        "local": LocalProvider,
        "claude": ClaudeProvider,
        "gemini": GeminiProvider,
    }
    if provider_name not in provider_map:
        raise ValueError(f"Unsupported provider '{provider_name}'")
    return provider_map[provider_name]


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_bot_identity(payload: dict[str, Any], api_key: Optional[str]) -> dict[str, Any]:
    normalized = dict(payload)
    expiration = normalized.get("expiration_date")
    if isinstance(expiration, datetime):
        normalized["expiration_date"] = expiration.isoformat()
    normalized["api_key"] = api_key or ""
    return normalized


def _existing_bot_matches(bot: Bot, *, payload: dict[str, Any], api_key: Optional[str]) -> bool:
    existing_api_key = ""
    if bot.api_key_encrypted:
        try:
            existing_api_key = encryption_service.decrypt(bot.api_key_encrypted)
        except Exception:
            existing_api_key = bot.api_key_encrypted
    existing = {
        "name": bot.name,
        "description": bot.description,
        "template_type": bot.template_type,
        "provider_type": bot.provider_type,
        "system_prompt": bot.system_prompt,
        "temperature": bot.temperature,
        "max_tokens": bot.max_tokens,
        "model": bot.model,
        "memory_enabled": bot.memory_enabled,
        "analytics_enabled": bot.analytics_enabled,
        "is_active": bot.is_active,
        "message_limit": bot.message_limit,
        "expiration_date": bot.expiration_date.isoformat() if bot.expiration_date else None,
        "accent_color": bot.accent_color,
        "welcome_message": bot.welcome_message,
        "api_key": existing_api_key,
    }
    expected = _normalize_bot_identity(payload, api_key)
    for key, value in expected.items():
        if key == "expiration_date" and isinstance(value, datetime):
            value = value.isoformat()
        if existing.get(key) != value:
            return False
    return True


def _format_bot_table(bots: list[Bot]) -> Table:
    table = Table(title="Bots")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Active")
    table.add_column("Messages", justify="right")
    table.add_column("Knowledge", justify="right")
    table.add_column("Updated", no_wrap=True)
    with _db_session() as db:
        chunk_counts = {
            bot_id: count
            for bot_id, count in db.query(
                KnowledgeChunk.bot_id,
                func.count(KnowledgeChunk.id),
            ).filter(KnowledgeChunk.deleted_at.is_(None)).group_by(KnowledgeChunk.bot_id).all()
        }
    for bot in bots:
        table.add_row(
            bot.id,
            bot.name,
            bot.provider_type,
            bot.model or "-",
            "yes" if bot.is_active else "no",
            str(bot.messages_used or 0),
            str(chunk_counts.get(bot.id, 0)),
            bot.updated_at.isoformat(timespec="seconds") if bot.updated_at else "-",
        )
    return table


def _serialize_scraper_job(job: ScraperJob, task: Optional[TaskRun] = None) -> dict[str, Any]:
    extracted = dict(job.extracted_data or {})
    payload_json = dict(task.payload_json or {}) if task else {}
    checkpoint_json = dict(task.checkpoint_json or {}) if task else {}
    payload = {
        "id": job.id,
        "bot_id": job.bot_id,
        "url": job.url,
        "status": job.status,
        "depth": job.depth,
        "limit": payload_json.get("limit"),
        "focus": payload_json.get("focus"),
        "sitemap_only": bool(payload_json.get("sitemap_only", False)),
        "pages_scraped": job.pages_scraped,
        "chunks_created": job.chunks_created,
        "error_message": job.error_message,
        "consent_confirmed": job.consent_confirmed,
        "attempt_count": job.attempt_count,
        "lease_owner": job.lease_owner,
        "heartbeat_at": job.heartbeat_at,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "summary": extracted.get("statistics"),
        "telemetry": checkpoint_json,
    }
    if task:
        payload["task_run"] = {
            "id": task.id,
            "state": task.state,
            "attempt": task.attempt,
            "heartbeat_at": task.heartbeat_at,
            "checkpoint_json": checkpoint_json,
            "payload_json": payload_json,
            "last_error": task.last_error,
        }
    return payload


def _mock_scrape_result(url: str, depth: int) -> dict[str, Any]:
    now = _utcnow_naive().isoformat() + "Z"
    pages = [
        {
            "url": url,
            "title": "Mock Landing Page",
            "content": f"Mock scraped content for {url}. This sample page is generated by the Arctic CLI for testing.",
            "word_count": 15,
            "links": [],
            "error": None,
            "priority_tier": "T1",
            "priority_score": 5.0,
            "discovered_depth": 0,
        }
    ]
    return {
        "start_url": url,
        "scraped_at": now,
        "pages": pages,
        "statistics": {
            "total_pages": len(pages),
            "total_words": sum(page["word_count"] for page in pages),
            "max_depth": depth,
            "pages_found": len(pages),
            "priority_counts": {"T1": 1, "T2": 0, "T3": 0},
        },
    }


def _bot_payload_from_args(
    *,
    name: str,
    template: str,
    provider: Optional[str],
    api_key: Optional[str],
    description: Optional[str],
    system_prompt: Optional[str],
    temperature: Optional[float],
    max_tokens: Optional[int],
    model: Optional[str],
    memory_enabled: bool,
    analytics_enabled: bool,
    is_active: bool,
    message_limit: Optional[int],
    expiration_date: Optional[str],
    accent_color: Optional[str],
    welcome_message: Optional[str],
    set_values: Optional[list[str]],
) -> dict[str, Any]:
    overrides = _parse_set_pairs(set_values)
    payload: dict[str, Any] = {
        "name": name,
        "template_type": template,
        "provider_type": provider or template_service.get_default_config_for_template(template).get("provider_type", "openai"),
        "api_key": api_key,
        "description": description,
        "system_prompt": system_prompt,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "model": model,
        "memory_enabled": memory_enabled,
        "analytics_enabled": analytics_enabled,
        "is_active": is_active,
        "message_limit": message_limit,
        "expiration_date": _parse_optional_datetime(expiration_date),
        "accent_color": accent_color,
        "welcome_message": welcome_message,
    }
    payload.update(overrides)
    return {key: value for key, value in payload.items() if value is not None}


@contextmanager
def _bot_runtime_overrides(
    *,
    bot_id: str,
    provider_override: Optional[str],
    model_override: Optional[str],
    mock_mode: bool,
) -> Iterator[None]:
    original_get_provider = bot_service._get_provider
    original_search = embedding_service.search
    provider_cache: dict[str, BaseLLMProvider] = {}

    def _patched_get_provider(
        self,
        runtime_bot: Bot,
        *,
        provider_type: Optional[str] = None,
        model: Optional[str] = None,
    ):
        effective_provider_type = provider_override or provider_type
        effective_model = model_override or model
        if runtime_bot.id != bot_id or (effective_provider_type is None and effective_model is None and not mock_mode):
            return original_get_provider(runtime_bot, provider_type=provider_type, model=model)
        provider_name = (effective_provider_type or runtime_bot.provider_type).strip().lower()
        resolved_model = effective_model or runtime_bot.model
        cache_key = f"{runtime_bot.id}:{provider_name}:{resolved_model}:{mock_mode}"
        provider = provider_cache.get(cache_key)
        if provider is None:
            api_key = None
            if runtime_bot.api_key_encrypted:
                try:
                    api_key = encryption_service.decrypt(runtime_bot.api_key_encrypted)
                except Exception:
                    api_key = runtime_bot.api_key_encrypted
            if mock_mode:
                provider = MockProvider(provider_name=provider_name, model=resolved_model)
            else:
                provider_cls = _provider_class(provider_name)
                provider = provider_cls(api_key=api_key, model=resolved_model)
            provider_cache[cache_key] = provider
        return provider

    async def _mock_search(query: str, runtime_bot_id: str, provider: BaseLLMProvider, top_k: int = 3):
        db = SessionLocal()
        try:
            chunks = (
                db.query(KnowledgeChunk)
                .filter(
                    KnowledgeChunk.bot_id == runtime_bot_id,
                    KnowledgeChunk.deleted_at.is_(None),
                )
                .order_by(KnowledgeChunk.created_at.desc())
                .limit(top_k)
                .all()
            )
            return [
                (
                    chunk.content,
                    1.0,
                    {
                        "source": chunk.source,
                        "chunk_id": chunk.id,
                        "mock": True,
                    },
                )
                for chunk in chunks
            ]
        finally:
            db.close()

    bot_service._get_provider = _patched_get_provider.__get__(bot_service, bot_service.__class__)
    if mock_mode:
        embedding_service.search = _mock_search
    try:
        yield
    finally:
        bot_service._get_provider = original_get_provider
        embedding_service.search = original_search


async def _execute_chat_turn(
    *,
    db: Session,
    bot: Bot,
    message: str,
    session_id: Optional[str],
    include_rag: bool,
    include_memory: bool,
    persist_memory: bool,
    explicit_idempotency_key: Optional[str],
) -> dict[str, Any]:
    provider = None
    try:
        provider = bot_service._get_provider(bot)
    except Exception:
        provider = None
    active_modes = safe_mode_service.get_active_modes(db, bot_id=bot.id, provider=provider)
    if "PROVIDER_OPEN_CIRCUIT" in active_modes:
        breaker_state = provider.chat_breaker.snapshot() if provider is not None else {}
        raise RuntimeError(
            f"Provider circuit is open for {(provider.provider_name if provider is not None else bot.provider_type)}; retry after {breaker_state.get('retry_after_seconds', 30)}s"
        )

    request_record = chat_request_service.prepare(
        db,
        bot_id=bot.id,
        session_id=session_id,
        message=message,
        idempotency_key=chat_request_service.build_idempotency_key(
            bot_id=bot.id,
            session_id=session_id,
            message=message,
            explicit_key=explicit_idempotency_key,
        ),
    )
    if request_record.state == "completed" and request_record.response_payload:
        return {
            **request_record.response_payload,
            "idempotency_key": request_record.idempotency_key,
            "idempotent_hit": True,
        }
    if request_record.state == "running":
        raise RuntimeError("An identical chat request is already in progress")
    chat_request_service.mark_running(
        db,
        request_record,
        lease_owner=f"cli-{socket.gethostname()}-{os.getpid()}",
    )

    try:
        result = await bot_service.chat(
            db=db,
            bot_id=bot.id,
            message=message,
            session_id=session_id,
            include_rag=include_rag and "RAG_BYPASS_EXPLICIT" not in active_modes,
            include_memory=include_memory,
            persist_memory=persist_memory and "DB_READ_ONLY" not in active_modes,
        )
        degraded = list(result.get("degraded_components", []))
        if "RAG_BYPASS_EXPLICIT" in active_modes and "rag" not in degraded:
            degraded.append("rag")
        if "DB_READ_ONLY" in active_modes and "memory" not in degraded:
            degraded.append("memory")
        result["degraded_components"] = degraded
        result["data_freshness"] = "stale" if degraded else result.get("data_freshness", "fresh")
        result["safe_modes"] = safe_mode_service.serialize_modes(active_modes)

        if bot.analytics_enabled and "DB_READ_ONLY" not in active_modes:
            try:
                await analytics_service.log_message(
                    db,
                    bot_id=bot.id,
                    session_id=result["session_id"],
                    user_message=message,
                    assistant_response=result["response"],
                    tokens_prompt=result.get("tokens_prompt", 0),
                    tokens_completion=result.get("tokens_completion", 0),
                    latency_ms=result["latency_ms"],
                    user_ip="127.0.0.1",
                    user_agent="arctic-cli",
                )
            except Exception as analytics_error:
                db.rollback()
                metrics_service.inc_counter(
                    "db_write_fail_total",
                    labels={"operation": "analytics_write"},
                )
                degraded = list(result.get("degraded_components", []))
                if "analytics" not in degraded:
                    degraded.append("analytics")
                result["degraded_components"] = degraded
                result["data_freshness"] = "stale"
                result["status_message"] = "completed_with_degraded_analytics"
                result["analytics_error"] = str(analytics_error)
        elif bot.analytics_enabled and "DB_READ_ONLY" in active_modes:
            degraded = list(result.get("degraded_components", []))
            if "analytics" not in degraded:
                degraded.append("analytics")
            result["degraded_components"] = degraded
            result["data_freshness"] = "stale"

        response_payload = {
            "response": result["response"],
            "session_id": result["session_id"],
            "tokens_used": result["tokens_used"],
            "latency_ms": result["latency_ms"],
            "retry_count": result.get("retry_count", 0),
            "fallback_used": result.get("fallback_used", False),
            "status_message": result.get("status_message", "completed"),
            "degraded_components": result.get("degraded_components", []),
            "data_freshness": result.get("data_freshness", "fresh"),
            "safe_modes": result.get("safe_modes", []),
        }
        chat_request_service.mark_completed(
            db,
            request_record,
            response_payload=response_payload,
            degraded_flags=response_payload["degraded_components"],
        )
        return {
            **response_payload,
            "idempotency_key": request_record.idempotency_key,
            "idempotent_hit": False,
        }
    except Exception as exc:
        db.rollback()
        chat_request_service.mark_failed(db, request_record, str(exc))
        raise


@bot_app.command("create")
def bot_create(
    name: str,
    template: Annotated[str, typer.Option("--template", help="Template id such as openai_template.")] = "openai_template",
    provider: Annotated[Optional[str], typer.Option("--provider", help="Provider override.")] = None,
    api_key: Annotated[Optional[str], typer.Option("--api-key", help="API key to encrypt and store.")] = None,
    description: Annotated[Optional[str], typer.Option("--description")] = None,
    system_prompt: Annotated[Optional[str], typer.Option("--system-prompt")] = None,
    temperature: Annotated[Optional[float], typer.Option("--temperature")] = None,
    max_tokens: Annotated[Optional[int], typer.Option("--max-tokens")] = None,
    model: Annotated[Optional[str], typer.Option("--model")] = None,
    memory_enabled: Annotated[bool, typer.Option("--memory/--no-memory")] = True,
    analytics_enabled: Annotated[bool, typer.Option("--analytics/--no-analytics")] = True,
    active: Annotated[bool, typer.Option("--active/--inactive")] = True,
    message_limit: Annotated[Optional[int], typer.Option("--message-limit")] = None,
    expiration_date: Annotated[Optional[str], typer.Option("--expiration-date", help="ISO-8601 UTC timestamp.")] = None,
    accent_color: Annotated[Optional[str], typer.Option("--accent-color")] = None,
    welcome_message: Annotated[Optional[str], typer.Option("--welcome-message")] = None,
    set_values: Annotated[Optional[list[str]], typer.Option("--set", help="Additional key=value overrides.")] = None,
    allow_duplicate: Annotated[bool, typer.Option("--allow-duplicate", help="Force creation even when a matching bot exists.")] = False,
    json_output: JsonOption = False,
) -> None:
    try:
        payload = _bot_payload_from_args(
            name=name,
            template=template,
            provider=provider,
            api_key=api_key,
            description=description,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            memory_enabled=memory_enabled,
            analytics_enabled=analytics_enabled,
            is_active=active,
            message_limit=message_limit,
            expiration_date=expiration_date,
            accent_color=accent_color,
            welcome_message=welcome_message,
            set_values=set_values,
        )
        validated = BotCreate.model_validate(payload)
    except Exception as exc:
        _fail(f"Invalid bot configuration: {exc}", json_output=json_output)
        return

    with _db_session() as db:
        try:
            safe_mode_service.assert_writes_allowed(db)
        except Exception as exc:
            _fail(str(exc), json_output=json_output)
        existing = None
        if not allow_duplicate:
            candidates = db.query(Bot).filter(Bot.name == validated.name).all()
            for candidate in candidates:
                if _existing_bot_matches(candidate, payload=payload, api_key=api_key):
                    existing = candidate
                    break
        if existing:
            response = {
                "status": "ok",
                "created": False,
                "idempotent_hit": True,
                "bot": existing.to_dict(),
            }
            renderable = Panel(
                f"Reused existing bot [bold]{existing.name}[/bold]\nID: {existing.id}",
                title="Bot Create",
                border_style="yellow",
            )
            _emit(response, json_output=json_output, renderable=renderable)
            return
        try:
            bot = bot_service.create_bot(
                db,
                name=validated.name,
                template_type=validated.template_type,
                provider_type=validated.provider_type.value,
                api_key=validated.api_key,
                description=validated.description,
                system_prompt=validated.system_prompt,
                temperature=validated.temperature,
                max_tokens=validated.max_tokens,
                model=validated.model,
                memory_enabled=validated.memory_enabled,
                analytics_enabled=validated.analytics_enabled,
                is_active=validated.is_active,
                message_limit=validated.message_limit,
                expiration_date=validated.expiration_date,
                accent_color=validated.accent_color,
                welcome_message=validated.welcome_message,
            )
        except Exception as exc:
            _fail(f"Bot creation failed: {exc}", json_output=json_output)
            return

    renderable = Panel(
        f"[bold]{bot.name}[/bold]\nID: {bot.id}\nProvider: {bot.provider_type}\nModel: {bot.model or '-'}",
        title="Bot Created",
        border_style="green",
    )
    _emit(
        {"status": "ok", "created": True, "idempotent_hit": False, "bot": bot.to_dict()},
        json_output=json_output,
        renderable=renderable,
    )


@bot_app.command("list")
def bot_list(
    active_only: Annotated[bool, typer.Option("--active-only")] = False,
    limit: Annotated[int, typer.Option("--limit")] = 100,
    offset: Annotated[int, typer.Option("--offset")] = 0,
    json_output: JsonOption = False,
) -> None:
    with _db_session() as db:
        bots, total = bot_service.list_bots(db, skip=offset, limit=limit, active_only=active_only)
        payload = {
            "status": "ok",
            "total": total,
            "count": len(bots),
            "bots": [bot.to_dict() for bot in bots],
        }
    _emit(payload, json_output=json_output, renderable=_format_bot_table(bots))


@bot_app.command("delete")
def bot_delete(
    bot_id: str,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation.")] = False,
    json_output: JsonOption = False,
) -> None:
    if not yes and not Confirm.ask(f"Delete bot {bot_id} and all related data?"):
        _emit({"status": "cancelled", "bot_id": bot_id}, json_output=json_output, renderable=Panel("Cancelled", title="Bot Delete"))
        raise typer.Exit(0)

    with _db_session() as db:
        try:
            safe_mode_service.assert_writes_allowed(db)
        except Exception as exc:
            _fail(str(exc), json_output=json_output)
        bot = bot_service.get_bot(db, bot_id)
        if not bot:
            _fail(f"Bot {bot_id} not found", json_output=json_output, exit_code=2)
        bot_name = bot.name
        deleted = bot_service.delete_bot(db, bot_id)
        if not deleted:
            _fail(f"Bot {bot_id} could not be deleted", json_output=json_output)

    _emit(
        {"status": "ok", "deleted": True, "bot_id": bot_id, "name": bot_name},
        json_output=json_output,
        renderable=Panel(f"Deleted [bold]{bot_name}[/bold]\nID: {bot_id}", title="Bot Delete", border_style="green"),
    )


@bot_app.command("update-config")
def bot_update_config(
    bot_id: str,
    set_values: Annotated[list[str], typer.Option("--set", help="Field updates as key=value.")] = [],
    api_key: Annotated[Optional[str], typer.Option("--api-key")] = None,
    clear_api_key: Annotated[bool, typer.Option("--clear-api-key")] = False,
    json_output: JsonOption = False,
) -> None:
    try:
        update_data = _parse_set_pairs(set_values)
    except Exception as exc:
        _fail(str(exc), json_output=json_output)
        return
    if api_key is not None and clear_api_key:
        _fail("Choose either --api-key or --clear-api-key, not both", json_output=json_output)
    if api_key is not None:
        update_data["api_key"] = api_key
    if clear_api_key:
        update_data["api_key"] = None
    if "expiration_date" in update_data:
        update_data["expiration_date"] = _parse_optional_datetime(str(update_data["expiration_date"]) if update_data["expiration_date"] is not None else None)
    with _db_session() as db:
        try:
            safe_mode_service.assert_writes_allowed(db)
        except Exception as exc:
            _fail(str(exc), json_output=json_output)
        bot = bot_service.update_bot(db, bot_id, **update_data)
        if not bot:
            _fail(f"Bot {bot_id} not found", json_output=json_output, exit_code=2)

    _emit(
        {"status": "ok", "updated": True, "bot": bot.to_dict()},
        json_output=json_output,
        renderable=Panel(f"Updated [bold]{bot.name}[/bold]\nID: {bot.id}", title="Bot Updated", border_style="green"),
    )


@bot_app.command("chat")
def bot_chat(
    bot_id: str,
    message: Annotated[Optional[str], typer.Option("--message", help="Send one message and exit.")] = None,
    session_id: Annotated[Optional[str], typer.Option("--session-id", help="Reuse a specific session id.")] = None,
    no_rag: Annotated[bool, typer.Option("--no-rag", help="Disable RAG retrieval for this test session.")] = False,
    no_memory: Annotated[bool, typer.Option("--no-memory", help="Disable conversation memory load/persist.")] = False,
    provider: Annotated[Optional[str], typer.Option("--provider", help="Provider override for live or mock testing.")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="Model override for this session.")] = None,
    idempotency_key: Annotated[Optional[str], typer.Option("--idempotency-key", help="Explicit idempotency key for one-shot runs.")] = None,
    mock_mode: Annotated[bool, typer.Option("--mock", help="Simulate provider responses without external API usage.")] = False,
    json_output: JsonOption = False,
) -> None:
    with _db_session() as db:
        bot = bot_service.get_bot(db, bot_id)
        if not bot:
            _fail(f"Bot {bot_id} not found", json_output=json_output, exit_code=2)
        if not bot.is_available():
            _fail(f"Bot {bot.name} is unavailable", json_output=json_output, exit_code=3)
        resolved_session_id = (session_id or "").strip()
        if not resolved_session_id and idempotency_key:
            resolved_session_id = f"cli-{idempotency_key.strip()}"
        active_session_id = resolved_session_id or bot_service.create_session_id()
        current_sources = _bot_source_summary(db, bot.id)
        resolved_provider = provider or bot.provider_type
        resolved_model = model or bot.model or "-"

        def _run_turn(user_message: str, turn_number: int) -> dict[str, Any]:
            explicit_key = idempotency_key
            if explicit_key and message is None:
                explicit_key = f"{explicit_key}-turn-{turn_number}"
            with _bot_runtime_overrides(
                bot_id=bot.id,
                provider_override=provider,
                model_override=model,
                mock_mode=mock_mode,
            ):
                return asyncio.run(
                    _execute_chat_turn(
                        db=db,
                        bot=bot,
                        message=user_message,
                        session_id=active_session_id,
                        include_rag=not no_rag,
                        include_memory=not no_memory,
                        persist_memory=not no_memory,
                        explicit_idempotency_key=explicit_key,
                    )
                )

        def _render_result(result: dict[str, Any]) -> None:
            if json_output:
                _emit({"status": "ok", "bot_id": bot.id, **result}, json_output=True)
                return
            footer_parts = [
                f"session={result['session_id']}",
                f"latency={result['latency_ms']}ms",
                f"tokens={result['tokens_used']}",
                f"freshness={result.get('data_freshness', 'fresh')}",
            ]
            if result.get("degraded_components"):
                footer_parts.append(f"degraded={','.join(result['degraded_components'])}")
            if result.get("safe_modes"):
                footer_parts.append(f"safe_modes={','.join(result['safe_modes'])}")
            if result.get("idempotent_hit"):
                footer_parts.append("idempotent_hit=true")
            console.print(
                Panel(
                    result["response"],
                    title=f"{bot.name} [{bot.id}]",
                    subtitle=" | ".join(footer_parts),
                    border_style="cyan",
                )
            )
            citations = list(result.get("citations") or [])
            if citations:
                reference_lines = []
                for citation in citations:
                    label = str(citation.get("label") or "Reference")
                    scope = str(citation.get("scope") or "").strip()
                    snippet = str(citation.get("snippet") or "").strip()
                    line = f"- {label}"
                    if scope:
                        line += f" [{scope}]"
                    if snippet:
                        line += f"\n  {snippet}"
                    reference_lines.append(line)
                console.print(
                    Panel(
                        "\n".join(reference_lines),
                        title="References",
                        border_style="bright_black",
                    )
                )

        def _render_chat_startup() -> None:
            source_text = "\n".join(f"- {source}" for source in current_sources) if current_sources else "- No indexed sources yet"
            command_text = "\n".join([
                "/help    show quick commands",
                "/sources list current knowledge sources",
                "/status  show current chat session settings",
                "/exit    leave the chat",
            ])
            console.print(
                Panel(
                    f"[bold]{bot.name}[/bold]\n"
                    f"session_id={active_session_id}\n"
                    f"provider={resolved_provider}\n"
                    f"model={resolved_model}\n"
                    f"rag={'off' if no_rag or mock_mode else 'on'}\n"
                    f"memory={'off' if no_memory else 'on'}\n"
                    f"mock={'on' if mock_mode else 'off'}\n\n"
                    f"[bold]Quick Commands[/bold]\n{command_text}\n\n"
                    f"[bold]Current Sources[/bold]\n{source_text}",
                    title="Bot Chat",
                    border_style="blue",
                )
            )

        def _render_sources() -> None:
            console.print(
                Panel(
                    "\n".join(current_sources) if current_sources else "No indexed sources available for this bot yet.",
                    title="Current Sources",
                    border_style="cyan",
                )
            )

        def _render_status() -> None:
            console.print(
                Panel(
                    f"bot={bot.name}\n"
                    f"session_id={active_session_id}\n"
                    f"provider={resolved_provider}\n"
                    f"model={resolved_model}\n"
                    f"rag={'off' if no_rag or mock_mode else 'on'}\n"
                    f"memory={'off' if no_memory else 'on'}\n"
                    f"mock={'on' if mock_mode else 'off'}",
                    title="Session Status",
                    border_style="green",
                )
            )

        if message is not None:
            try:
                result = _run_turn(message, 1)
            except Exception as exc:
                _fail(f"Chat failed: {exc}", json_output=json_output)
                return
            _render_result(result)
            return

        if not json_output:
            _render_chat_startup()

        turn = 0
        while True:
            try:
                user_message = Prompt.ask("[bold green]you[/bold green]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nSession closed.")
                break
            if not user_message:
                continue
            if user_message.lower() in {"/help", "help"}:
                _render_chat_startup()
                continue
            if user_message.lower() == "/sources":
                _render_sources()
                continue
            if user_message.lower() == "/status":
                _render_status()
                continue
            if user_message.lower() in {"/exit", "exit", "quit", "/quit"}:
                break
            turn += 1
            try:
                result = _run_turn(user_message, turn)
            except Exception as exc:
                _fail(f"Chat failed: {exc}", json_output=json_output)
                return
            _render_result(result)


async def _wait_for_scraper_job(job_id: str, *, start_local_worker: bool) -> dict[str, Any]:
    local_worker_started = False
    if start_local_worker:
        await scraper_job_service.start()
        local_worker_started = True

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Waiting for scraper job", total=50)
            while True:
                with _db_session() as db:
                    job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
                    task = (
                        db.query(TaskRun)
                        .filter(TaskRun.task_type == "scraper_job", TaskRun.task_key == job_id)
                        .order_by(TaskRun.created_at.desc())
                        .first()
                    )
                    if not job:
                        raise RuntimeError(f"Scraper job {job_id} no longer exists")
                    progress.update(
                        task_id,
                        description=f"Scraper {job.status} {job.url}",
                        completed=min(job.pages_scraped or 0, 50),
                        total=max(1, max(job.pages_scraped or 0, 1, 50)),
                    )
                    if job.status in {"completed", "failed", "review"}:
                        return _serialize_scraper_job(job, task)
                await asyncio.sleep(1)
    finally:
        if local_worker_started:
            await scraper_job_service.shutdown()


async def _run_test_parse_async(
    *,
    url: str,
    depth: int,
    max_pages: int,
    focus: str,
    sitemap_only: bool,
    timeout: float,
    rate_limit_delay: float,
    mock_mode: bool,
) -> dict[str, Any]:
    if mock_mode:
        return _mock_scrape_result(url, depth)

    service = ScraperService(
        ScraperConfig(
            max_depth=depth,
            max_pages=max_pages,
            timeout=timeout,
            rate_limit_delay=rate_limit_delay,
            focus_keywords=[item.strip() for item in focus.split(",") if item.strip()],
            sitemap_only=sitemap_only,
        )
    )
    latest = {"pages_scraped": 0, "pages_found": 0, "current_url": url}

    async def _progress_callback(update: dict[str, Any]) -> None:
        latest.update(update)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Parsing website", total=max_pages)
        runner = asyncio.create_task(service.scrape_website(url, progress_callback=_progress_callback))
        while not runner.done():
            progress.update(
                task_id,
                description=f"Parsing {latest.get('current_url') or url}",
                completed=min(int(latest.get("pages_scraped", 0)), max_pages),
            )
            await asyncio.sleep(0.25)
        result = await runner
        progress.update(
            task_id,
            completed=min(int(result.get("statistics", {}).get("total_pages", 0)), max_pages),
            description="Parsing completed",
        )
        return result


def _find_duplicate_scraper_job(
    db: Session,
    *,
    url: str,
    depth: int,
    limit: int,
    focus: str,
    sitemap_only: bool,
    bot_id: Optional[str],
) -> Optional[ScraperJob]:
    candidates = (
        db.query(ScraperJob)
        .filter(
            ScraperJob.url == url,
            ScraperJob.depth == depth,
            ScraperJob.bot_id == bot_id,
            ScraperJob.status.in_(["pending", "running"]),
        )
        .order_by(ScraperJob.created_at.desc())
        .all()
    )
    for candidate in candidates:
        task = (
            db.query(TaskRun)
            .filter(TaskRun.task_type == "scraper_job", TaskRun.task_key == candidate.id)
            .order_by(TaskRun.created_at.desc())
            .first()
        )
        payload = dict(task.payload_json or {}) if task else {}
        if int(payload.get("limit") or 0) != int(limit):
            continue
        if str(payload.get("focus") or "").strip().lower() != focus.strip().lower():
            continue
        if bool(payload.get("sitemap_only", False)) != bool(sitemap_only):
            continue
        return candidate
    return None


@scraper_app.command("run")
def scraper_run(
    url: str,
    bot_id: Annotated[Optional[str], typer.Option("--bot-id", help="Attach scraped content to a bot.")] = None,
    depth: Annotated[int, typer.Option("--depth")] = 2,
    limit: Annotated[int, typer.Option("--limit")] = 20,
    focus: Annotated[str, typer.Option("--focus", help="Comma-separated keywords to prioritize.")] = "",
    sitemap_only: Annotated[bool, typer.Option("--sitemap-only", help="Only read sitemap.xml for discovery.")] = False,
    append: Annotated[bool, typer.Option("--append/--replace")] = True,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Wait for job completion or review readiness.")] = False,
    mock_mode: Annotated[bool, typer.Option("--mock", help="Create a synthetic scrape result without network access.")] = False,
    json_output: JsonOption = False,
) -> None:
    with _db_session() as db:
        try:
            if mock_mode:
                safe_mode_service.assert_writes_allowed(db)
            else:
                safe_mode_service.assert_scraper_allowed(db)
        except Exception as exc:
            _fail(str(exc), json_output=json_output)
        if bot_id and not bot_service.get_bot(db, bot_id):
            _fail(f"Bot {bot_id} not found", json_output=json_output, exit_code=2)

        duplicate = _find_duplicate_scraper_job(
            db,
            url=url,
            depth=depth,
            limit=limit,
            focus=focus,
            sitemap_only=sitemap_only,
            bot_id=bot_id,
        )
        if duplicate:
            task = (
                db.query(TaskRun)
                .filter(TaskRun.task_type == "scraper_job", TaskRun.task_key == duplicate.id)
                .order_by(TaskRun.created_at.desc())
                .first()
            )
            payload = {
                "status": "ok",
                "created": False,
                "idempotent_hit": True,
                "job": _serialize_scraper_job(duplicate, task),
            }
            _emit(
                payload,
                json_output=json_output,
                renderable=Panel(
                    f"Reused scraper job {duplicate.id}\n{duplicate.url}",
                    title="Scraper Run",
                    border_style="yellow",
                ),
            )
            return

        job = ScraperJob(
            bot_id=bot_id,
            url=url,
            depth=depth,
            consent_confirmed=True,
            status="pending",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        task = supervisor_service.ensure_task_run(
            db,
            task_type="scraper_job",
            task_key=job.id,
            payload_json={
                "job_id": job.id,
                "url": job.url,
                "depth": job.depth,
                "limit": limit,
                "focus": focus.strip(),
                "sitemap_only": sitemap_only,
                "bot_id": bot_id,
                "append": append,
                "auto_index_bot": bool(bot_id),
                "source": "cli",
            },
            checkpoint_json={},
            state="pending",
        )

        if mock_mode:
            result = _mock_scrape_result(url, depth)
            result["statistics"]["priority_counts"] = {"T1": 1, "T2": 0, "T3": 0}
            result["statistics"]["pages_found"] = result["statistics"]["total_pages"]
            result["limit"] = limit
            result["focus"] = focus.strip()
            result["sitemap_only"] = sitemap_only
            job.extracted_data = result
            job.pages_scraped = result["statistics"]["total_pages"]
            job.chunks_created = 0
            job.completed_at = _utcnow_naive()
            if bot_id:
                documents = ScraperService().prepare_for_indexing(result)
                index_stats = asyncio.run(bot_service.upload_knowledge(db, bot_id, documents, append))
                job.chunks_created = int(index_stats.get("chunks_created", 0))
                job.status = "completed"
                job.extracted_data = {
                    **result,
                    "indexing": index_stats,
                    "auto_index_bot": True,
                    "mock": True,
                }
            else:
                job.status = "review"
            supervisor_service.complete_task(db, task, state="completed")
            db.commit()
        job_payload = _serialize_scraper_job(job, task)

    if wait and not mock_mode:
        start_local_worker = False
        with _db_session() as db:
            lease = db.query(ComponentLease).filter(ComponentLease.component_name == "scraper-worker").first()
            start_local_worker = lease is None or lease.state != "running" or lease_is_stale(lease.heartbeat_at)
        job_payload = asyncio.run(_wait_for_scraper_job(job.id, start_local_worker=start_local_worker))

    render_text = (
        f"Job ID: {job_payload['id']}\n"
        f"Status: {job_payload['status']}\n"
        f"URL: {job_payload['url']}\n"
        f"Bot: {job_payload.get('bot_id') or '-'}\n"
        f"Limit: {job_payload.get('limit') or limit}\n"
        f"Focus: {job_payload.get('focus') or '-'}\n"
        f"Sitemap only: {'yes' if job_payload.get('sitemap_only') else 'no'}"
    )
    summary = job_payload.get("summary") or {}
    if summary:
        priority_counts = summary.get("priority_counts", {})
        render_text += (
            f"\nScraped {summary.get('total_pages', 0)}/{job_payload.get('limit') or limit} pages"
            f"\n{priority_counts.get('T1', 0)} T1 pages, {priority_counts.get('T2', 0)} T2 pages"
            f"\nTotal words: {summary.get('total_words', 0):,}"
        )
    _emit(
        {"status": "ok", "created": True, "idempotent_hit": False, "job": job_payload},
        json_output=json_output,
        renderable=Panel(render_text, title="Scraper Run", border_style="green"),
    )


@scraper_app.command("status")
def scraper_status(job_id: str, json_output: JsonOption = False) -> None:
    with _db_session() as db:
        job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
        if not job:
            _fail(f"Scraper job {job_id} not found", json_output=json_output, exit_code=2)
        task = (
            db.query(TaskRun)
            .filter(TaskRun.task_type == "scraper_job", TaskRun.task_key == job_id)
            .order_by(TaskRun.created_at.desc())
            .first()
        )
        payload = {"status": "ok", "job": _serialize_scraper_job(job, task)}

    if json_output:
        _emit(payload, json_output=True)
        return

    table = Table(title=f"Scraper Job {job_id}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    for key, value in payload["job"].items():
        if key in {"task_run", "summary", "telemetry"}:
            continue
        table.add_row(key, json.dumps(value, default=_json_default) if isinstance(value, (dict, list)) else str(value))
    if payload["job"].get("task_run"):
        task_info = payload["job"]["task_run"]
        table.add_row("task_state", str(task_info.get("state")))
        table.add_row("task_attempt", str(task_info.get("attempt")))
        table.add_row("task_last_error", str(task_info.get("last_error")))
        table.add_row("checkpoint", json.dumps(task_info.get("checkpoint_json", {}), default=_json_default))
    console.print(table)
    summary = payload["job"].get("summary") or {}
    if summary:
        priority_counts = summary.get("priority_counts", {})
        console.print(
            Panel(
                f"Scraped {summary.get('total_pages', 0)}/{payload['job'].get('limit') or summary.get('total_pages', 0)} pages. "
                f"{priority_counts.get('T1', 0)} T1 pages, {priority_counts.get('T2', 0)} T2 pages. "
                f"Total words: {summary.get('total_words', 0):,}.",
                title="Summary",
                border_style="green",
            )
        )
    telemetry = payload["job"].get("telemetry") or {}
    if telemetry:
        console.print(
            Panel(
                json.dumps(
                    {
                        "pages_found": telemetry.get("pages_found"),
                        "pages_scraped": telemetry.get("pages_saved") or telemetry.get("pages_scraped"),
                        "current_url": telemetry.get("last_url") or telemetry.get("current_url"),
                        "status": payload["job"].get("status"),
                    },
                    indent=2,
                    default=_json_default,
                ),
                title="Live Telemetry",
                border_style="blue",
            )
        )


@scraper_app.command("clear-queue")
def scraper_clear_queue(
    include_running: Annotated[bool, typer.Option("--include-running", help="Also clear running jobs.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation.")] = False,
    json_output: JsonOption = False,
) -> None:
    if not yes and not Confirm.ask("Clear queued scraper jobs?"):
        _emit({"status": "cancelled"}, json_output=json_output, renderable=Panel("Cancelled", title="Scraper Queue"))
        raise typer.Exit(0)

    with _db_session() as db:
        try:
            safe_mode_service.assert_writes_allowed(db)
        except Exception as exc:
            _fail(str(exc), json_output=json_output)
        statuses = ["pending"]
        if include_running:
            statuses.append("running")
        jobs = db.query(ScraperJob).filter(ScraperJob.status.in_(statuses)).all()
        job_ids = [job.id for job in jobs]
        deleted_jobs = len(job_ids)
        deleted_tasks = (
            db.query(TaskRun)
            .filter(TaskRun.task_type == "scraper_job", TaskRun.task_key.in_(job_ids))
            .delete(synchronize_session=False)
            if job_ids
            else 0
        )
        if job_ids:
            db.query(ScraperJob).filter(ScraperJob.id.in_(job_ids)).delete(synchronize_session=False)
        db.commit()

    _emit(
        {"status": "ok", "deleted_jobs": deleted_jobs, "deleted_task_runs": deleted_tasks},
        json_output=json_output,
        renderable=Panel(
            f"Deleted jobs: {deleted_jobs}\nDeleted task runs: {deleted_tasks}",
            title="Scraper Queue Cleared",
            border_style="green",
        ),
    )


@scraper_app.command("test-parse")
def scraper_test_parse(
    url: str,
    depth: Annotated[int, typer.Option("--depth")] = 1,
    max_pages: Annotated[int, typer.Option("--max-pages")] = 10,
    focus: Annotated[str, typer.Option("--focus", help="Comma-separated keywords to prioritize.")] = "",
    sitemap_only: Annotated[bool, typer.Option("--sitemap-only", help="Only read sitemap.xml for discovery.")] = False,
    timeout: Annotated[float, typer.Option("--timeout")] = 15.0,
    rate_limit_delay: Annotated[float, typer.Option("--rate-limit-delay")] = 0.25,
    mock_mode: Annotated[bool, typer.Option("--mock", help="Use a synthetic parse result.")] = False,
    json_output: JsonOption = False,
) -> None:
    try:
        result = asyncio.run(
            _run_test_parse_async(
                url=url,
                depth=depth,
                max_pages=max_pages,
                focus=focus,
                sitemap_only=sitemap_only,
                timeout=timeout,
                rate_limit_delay=rate_limit_delay,
                mock_mode=mock_mode,
            )
        )
    except Exception as exc:
        _fail(f"Parse failed: {exc}", json_output=json_output)
        return

    payload = {"status": "ok", "result": result}
    if json_output:
        _emit(payload, json_output=True)
        return

    pages = result.get("pages", [])
    summary = Table(title="Parse Preview")
    summary.add_column("URL", style="cyan")
    summary.add_column("Title", style="bold")
    summary.add_column("Tier", justify="center")
    summary.add_column("Words", justify="right")
    summary.add_column("Preview")
    for page in pages[:10]:
        summary.add_row(
            page.get("url", ""),
            page.get("title", ""),
            str(page.get("priority_tier", "-")),
            str(page.get("word_count", 0)),
            (page.get("content", "")[:120] + "...") if len(page.get("content", "")) > 120 else page.get("content", ""),
        )
    console.print(summary)
    stats = result.get("statistics", {})
    priority_counts = stats.get("priority_counts", {})
    console.print(
        Panel(
            f"Scraped {stats.get('total_pages', 0)}/{max_pages} pages\n"
            f"{priority_counts.get('T1', 0)} T1 pages, {priority_counts.get('T2', 0)} T2 pages\n"
            f"Total words: {stats.get('total_words', 0):,}",
            title="Summary",
            border_style="green",
        )
    )
    console.print(
        Panel(
            json.dumps(stats, indent=2, default=_json_default),
            title="Statistics",
            border_style="blue",
        )
    )


@db_app.command("migrate")
def db_migrate(json_output: JsonOption = False) -> None:
    init_db()
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    payload = {
        "status": "ok",
        "database_url": DATABASE_URL,
        "backend": "sqlite" if IS_SQLITE else "postgres",
        "table_count": len(tables),
        "tables": sorted(tables),
    }
    _emit(
        payload,
        json_output=json_output,
        renderable=Panel(
            f"Backend: {'sqlite' if IS_SQLITE else 'postgres'}\nTables: {len(tables)}",
            title="DB Migrate",
            border_style="green",
        ),
    )


@db_app.command("reset-hard")
def db_reset_hard(
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation prompt.")] = False,
    json_output: JsonOption = False,
) -> None:
    if not yes and not Confirm.ask("Drop and recreate all database tables?"):
        _emit({"status": "cancelled"}, json_output=json_output, renderable=Panel("Cancelled", title="DB Reset"))
        raise typer.Exit(0)

    data_root = Path(__file__).resolve().parents[1] / "data"
    removed_paths: list[str] = []
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    init_db()

    for directory in [data_root / "bots", data_root / "model_files"]:
        if directory.exists():
            shutil.rmtree(directory)
            removed_paths.append(str(directory))
    payload = {
        "status": "ok",
        "database_url": DATABASE_URL,
        "removed_paths": removed_paths,
    }
    _emit(
        payload,
        json_output=json_output,
        renderable=Panel(
            f"Reset complete\nRemoved: {len(removed_paths)} data directories",
            title="DB Reset",
            border_style="red",
        ),
    )


@db_app.command("query-logs")
def db_query_logs(
    bot_id: Annotated[Optional[str], typer.Option("--bot-id")] = None,
    session_id: Annotated[Optional[str], typer.Option("--session-id")] = None,
    search: Annotated[Optional[str], typer.Option("--search", help="Substring filter on user/assistant content.")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 25,
    json_output: JsonOption = False,
) -> None:
    with _db_session() as db:
        query = db.query(Message).order_by(Message.created_at.desc())
        if bot_id:
            query = query.filter(Message.bot_id == bot_id)
        if session_id:
            query = query.filter(Message.session_id == session_id)
        if search:
            pattern = f"%{search}%"
            query = query.filter(or_(Message.user_message.ilike(pattern), Message.assistant_response.ilike(pattern)))
        rows = query.limit(limit).all()
        payload = {"status": "ok", "count": len(rows), "messages": [row.to_dict() for row in rows]}

    if json_output:
        _emit(payload, json_output=True)
        return

    table = Table(title="Message Logs")
    table.add_column("Time", no_wrap=True)
    table.add_column("Bot", style="cyan", no_wrap=True)
    table.add_column("Session", no_wrap=True)
    table.add_column("User")
    table.add_column("Assistant")
    table.add_column("Tokens", justify="right")
    for row in rows:
        table.add_row(
            row.created_at.isoformat(timespec="seconds") if row.created_at else "-",
            row.bot_id,
            row.session_id,
            (row.user_message[:80] + "...") if len(row.user_message) > 80 else row.user_message,
            (row.assistant_response[:80] + "...") if len(row.assistant_response) > 80 else row.assistant_response,
            str(row.tokens_total or 0),
        )
    console.print(table)


@db_app.command("prune-orphans")
def db_prune_orphans(
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation.")] = False,
    json_output: JsonOption = False,
) -> None:
    if not yes and not Confirm.ask("Prune orphaned DB rows and bot data directories?"):
        _emit({"status": "cancelled"}, json_output=json_output, renderable=Panel("Cancelled", title="Prune Orphans"))
        raise typer.Exit(0)

    with _db_session() as db:
        try:
            safe_mode_service.assert_writes_allowed(db)
        except Exception as exc:
            _fail(str(exc), json_output=json_output)
        bot_ids = {bot_id for (bot_id,) in db.query(Bot.id).all()}
        scraper_job_ids = {job_id for (job_id,) in db.query(ScraperJob.id).all()}
        stats = {
            "chat_requests": 0,
            "index_manifests": 0,
            "conversation_memory": 0,
            "messages": 0,
            "knowledge_chunks": 0,
            "task_runs": 0,
            "outbox_events": 0,
            "orphan_bot_dirs": 0,
            "orphan_scrape_dirs_archived": 0,
        }

        if bot_ids:
            stats["chat_requests"] = (
                db.query(ChatRequestRecord)
                .filter(ChatRequestRecord.bot_id.notin_(bot_ids))
                .delete(synchronize_session=False)
            )
            stats["index_manifests"] = (
                db.query(IndexManifest)
                .filter(IndexManifest.bot_id.notin_(bot_ids))
                .delete(synchronize_session=False)
            )
            stats["conversation_memory"] = (
                db.query(ConversationMemory)
                .filter(ConversationMemory.bot_id.notin_(bot_ids))
                .delete(synchronize_session=False)
            )
            stats["messages"] = (
                db.query(Message)
                .filter(Message.bot_id.notin_(bot_ids))
                .delete(synchronize_session=False)
            )
            stats["knowledge_chunks"] = (
                db.query(KnowledgeChunk)
                .filter(KnowledgeChunk.bot_id.notin_(bot_ids))
                .delete(synchronize_session=False)
            )
            stats["task_runs"] = (
                db.query(TaskRun)
                .filter(
                    or_(
                        (TaskRun.task_type.in_(["embedding_rebuild", "bot_warmup"]) & TaskRun.task_key.notin_(bot_ids)),
                        (TaskRun.task_type == "scraper_job") & TaskRun.task_key.notin_(scraper_job_ids or {""}),
                    )
                )
                .delete(synchronize_session=False)
            )
            stats["outbox_events"] = (
                db.query(OutboxEvent)
                .filter(
                    or_(
                        (OutboxEvent.aggregate_type == "bot") & OutboxEvent.aggregate_id.notin_(bot_ids),
                        (OutboxEvent.aggregate_type == "scraper_job") & OutboxEvent.aggregate_id.notin_(scraper_job_ids or {""}),
                    )
                )
                .delete(synchronize_session=False)
            )
        else:
            stats["chat_requests"] = db.query(ChatRequestRecord).delete(synchronize_session=False)
            stats["index_manifests"] = db.query(IndexManifest).delete(synchronize_session=False)
            stats["conversation_memory"] = db.query(ConversationMemory).delete(synchronize_session=False)
            stats["messages"] = db.query(Message).delete(synchronize_session=False)
            stats["knowledge_chunks"] = db.query(KnowledgeChunk).delete(synchronize_session=False)
            stats["task_runs"] = db.query(TaskRun).delete(synchronize_session=False)
            stats["outbox_events"] = db.query(OutboxEvent).delete(synchronize_session=False)
        db.commit()
        prune_stats = bot_service.prune_orphaned_scrape_artifacts(db)
        stats["orphan_scrape_dirs_archived"] = int(prune_stats.get("orphan_scrape_dirs_archived", 0))

    models_root = standalone_model_service.models_root
    if models_root.exists():
        for child in models_root.iterdir():
            if not child.is_dir() or child.name == "_shared":
                continue
            if child.name not in bot_ids:
                shutil.rmtree(child)
                stats["orphan_bot_dirs"] += 1

    _emit(
        {"status": "ok", "pruned": stats},
        json_output=json_output,
        renderable=Panel(
            "\n".join(f"{key}: {value}" for key, value in stats.items()),
            title="Prune Orphans",
            border_style="green",
        ),
    )


def _check_redis_dependency() -> dict[str, Any]:
    payload = {
        "configured": redis_service.enabled,
        "available": False,
        "detail": "disabled",
    }
    if not payload["configured"]:
        return payload
    try:
        client = redis_service.client
        if client is None:
            payload["detail"] = "client unavailable"
            return payload
        payload["available"] = bool(client.ping())
        payload["detail"] = "ping ok" if payload["available"] else "ping failed"
    except Exception as exc:
        payload["detail"] = str(exc)
    return payload


@sys_app.command("health")
def sys_health(json_output: JsonOption = False) -> None:
    refresh_observability_metrics()
    report = build_supervisor_health_report()
    report["dependencies"] = {
        "database": {
            "backend": "sqlite" if IS_SQLITE else "postgres",
            "url": DATABASE_URL,
        },
        "redis": _check_redis_dependency(),
        "templates": {
            "count": len(template_service.list_templates()),
        },
    }
    report["summary"] = {
        "hot_state": report.get("hot_state"),
        "encryption": _encryption_status_label(),
        "public_url": report.get("public_base_url") or "http://localhost:8000",
        "hubspot_readiness": _hubspot_readiness_label(report),
    }
    if json_output:
        _emit(report, json_output=True)
        return

    console.print(_health_banner(report))
    table = Table(title="System Health")
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    table.add_column("Critical")
    table.add_column("Detail")
    table.add_column("Updated", no_wrap=True)
    for name, component in sorted(report.get("components", {}).items()):
        table.add_row(
            name,
            component.get("status", "unknown"),
            "yes" if component.get("critical") else "no",
            str(component.get("detail") or ""),
            str(component.get("updated_at") or "-"),
        )
    console.print(table)
    console.print(
        Panel(
            f"Overall: {report.get('status')}\n"
            f"Safe modes: {', '.join(report.get('safe_modes', [])) or '-'}\n"
            f"Public base URL: {report.get('public_base_url') or '-'}\n"
            f"Redis: {report['dependencies']['redis']['detail']}",
            title="Dependency Summary",
            border_style="blue",
        )
    )


@sys_app.command("restart-tunnel")
def sys_restart_tunnel(
    origin_url: Annotated[Optional[str], typer.Option("--origin-url", help="Override the app origin URL for the tunnel.")] = None,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Wait for healthy tunnel lease after restart request.")] = True,
    json_output: JsonOption = False,
) -> None:
    request_time = _utcnow_naive()
    restart_payload = {
        "requested_by": "arctic-cli",
        "requested_at": request_time.isoformat() + "Z",
    }
    if origin_url:
        restart_payload["origin_url"] = origin_url.rstrip("/")

    with _db_session() as db:
        event = supervisor_service.enqueue_outbox(
            db,
            event_type="restart_tunnel",
            aggregate_type="component",
            aggregate_id="tunnel-agent",
            payload_json=restart_payload,
            dedupe_key=f"restart_tunnel:{restart_payload.get('origin_url', 'default')}",
        )
        lease = db.query(ComponentLease).filter(ComponentLease.component_name == "tunnel-agent").first()
        active_agent = lease is not None and lease.state == "running" and not lease_is_stale(lease.heartbeat_at)

    if not active_agent:
        async def _local_restart() -> dict[str, Any]:
            await tunnel_service.shutdown()
            resolved_origin = origin_url.rstrip("/") if origin_url else os.getenv("APP_ORIGIN_URL", "http://127.0.0.1:8000").rstrip("/")
            ok = await tunnel_service.ensure_started(origin_url=resolved_origin)
            status = tunnel_service.get_status()
            with _db_session() as db:
                current_event = db.query(OutboxEvent).filter(OutboxEvent.id == event.id).first()
                if current_event:
                    supervisor_service.complete_outbox_event(db, current_event)
                supervisor_service.upsert_component_lease(
                    db,
                    component_name="tunnel-agent",
                    unit_type="tunnel-agent",
                    lease_owner=f"cli-{socket.gethostname()}-{os.getpid()}",
                    state="running" if ok else "degraded",
                    metadata={**status, "origin_url": resolved_origin, "source": "cli-fallback"},
                )
            return status
        try:
            status = asyncio.run(_local_restart())
        except Exception as exc:
            _fail(f"Tunnel restart failed: {exc}", json_output=json_output)
            return
        _emit(
            {"status": "ok", "mode": "cli-fallback", "event_id": event.id, "tunnel": status},
            json_output=json_output,
            renderable=Panel(
                f"Tunnel restarted locally\nURL: {status.get('public_base_url') or '-'}",
                title="Tunnel Restart",
                border_style="green",
            ),
        )
        return

    final_lease = None
    if wait:
        for _attempt in range(30):
            with _db_session() as db:
                final_lease = db.query(ComponentLease).filter(ComponentLease.component_name == "tunnel-agent").first()
                current_event = db.query(OutboxEvent).filter(OutboxEvent.id == event.id).first()
                if current_event and current_event.state == "completed" and final_lease and final_lease.state == "running":
                    break
            time.sleep(1)

    payload = {
        "status": "ok",
        "mode": "supervised",
        "event_id": event.id,
        "lease": {
            "state": final_lease.state if final_lease else None,
            "heartbeat_at": final_lease.heartbeat_at if final_lease else None,
            "metadata": final_lease.metadata_json if final_lease else {},
        },
    }
    renderable = Panel(
        f"Restart requested for tunnel-agent\nEvent: {event.id}",
        title="Tunnel Restart",
        border_style="green",
    )
    _emit(payload, json_output=json_output, renderable=renderable)


@sys_app.command("view-metrics")
def sys_view_metrics(
    raw: Annotated[bool, typer.Option("--raw", help="Render Prometheus text instead of a table.")] = False,
    json_output: JsonOption = False,
) -> None:
    refresh_observability_metrics()
    snapshot = metrics_service.snapshot()
    if raw:
        text = metrics_service.render_prometheus()
        if json_output:
            _emit({"status": "ok", "metrics": text}, json_output=True)
        else:
            console.print(text)
        return

    if json_output:
        _emit({"status": "ok", "metrics": snapshot}, json_output=True)
        return

    table = Table(title="Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Type")
    table.add_column("Labels")
    table.add_column("Value", justify="right")

    for collection_name in ["counters", "gauges"]:
        collection = snapshot.get(collection_name, {})
        for metric_name, samples in sorted(collection.items()):
            metric_type = snapshot["definitions"].get(metric_name, {}).get("type", collection_name[:-1])
            for sample in samples:
                table.add_row(
                    metric_name,
                    metric_type,
                    json.dumps(sample.get("labels", {}), ensure_ascii=True),
                    str(sample.get("value")),
                )
    console.print(table)


@app.callback()
def main_callback() -> None:
    """Initialize DB-backed CLI context."""
    init_db()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
