"""
Database configuration and models for Local AI Chatbot Deployment Console.
"""

from datetime import datetime
from typing import Optional
import uuid
import asyncio
from sqlalchemy import (
    create_engine, Column, String, Text, Integer, Float, Boolean,
    DateTime, ForeignKey, JSON, UniqueConstraint, event, inspect, text
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.exc import OperationalError
from contextlib import contextmanager
import os
from pathlib import Path

# Database setup
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "data" / "chatbot_console.db"
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_SQLITE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"
DATABASE_URL = (os.getenv("DATABASE_URL") or DEFAULT_SQLITE_URL).strip()
IS_SQLITE = DATABASE_URL.startswith("sqlite")
ASYNC_DATABASE_URL = ""


def _to_async_database_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def _to_sync_database_url(url: str) -> str:
    if url.startswith("sqlite+aiosqlite:///"):
        return url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    return url


ASYNC_DATABASE_URL = _to_async_database_url(DATABASE_URL)
SYNC_DATABASE_URL = _to_sync_database_url(DATABASE_URL)

engine_kwargs = {"pool_pre_ping": True}
if IS_SQLITE:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update({
        "pool_size": int(os.getenv("DB_POOL_SIZE", "20")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
        "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "5")),
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "1800")),
    })

engine = create_engine(SYNC_DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
async_engine_kwargs = {"pool_pre_ping": True}
if IS_SQLITE:
    async_engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    async_engine_kwargs.update({
        "pool_size": int(os.getenv("DB_POOL_SIZE", "20")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
        "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "5")),
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "1800")),
    })

async_engine = create_async_engine(ASYNC_DATABASE_URL, **async_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)
Base = declarative_base()


@event.listens_for(engine, "connect")
def _configure_sqlite(connection, _):
    """Apply pragmatic SQLite hardening for concurrent local workloads."""
    if not IS_SQLITE:
        return
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
    finally:
        cursor.close()


@event.listens_for(async_engine.sync_engine, "connect")
def _configure_async_sqlite(connection, _):
    """Apply SQLite pragmas for the async engine as well."""
    if not IS_SQLITE:
        return
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
    finally:
        cursor.close()


def generate_uuid():
    return str(uuid.uuid4())


class Bot(Base):
    """Chatbot model with all configuration options."""
    __tablename__ = "bots"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    template_type = Column(String(50), nullable=False)  # openai, local, claude
    provider_type = Column(String(50), nullable=False)
    
    # Configuration
    system_prompt = Column(Text, nullable=True)
    temperature = Column(Float, default=0.7)
    max_tokens = Column(Integer, default=2000)
    model = Column(String(100), nullable=True)
    
    # Encrypted API key (stored encrypted)
    api_key_encrypted = Column(Text, nullable=True)
    
    # Feature flags
    memory_enabled = Column(Boolean, default=True)
    analytics_enabled = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    tools_enabled = Column(Boolean, default=False)
    allowed_tools_json = Column(JSON, nullable=True)
    tool_settings_json = Column(JSON, nullable=True)
    quick_chips_json = Column(JSON, nullable=True)
    
    # Limits
    message_limit = Column(Integer, nullable=True)  # None = unlimited
    messages_used = Column(Integer, default=0)
    
    # Tracked metrics
    token_usage = Column(Integer, default=0)
    avg_latency = Column(Float, default=0)
    
    # Expiration
    expiration_date = Column(DateTime, nullable=True)
    
    # UI Customization
    accent_color = Column(String(7), default="#007bff")
    welcome_message = Column(Text, default="Hello! How can I help you today?")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    messages = relationship("Message", back_populates="bot", cascade="all, delete-orphan")
    knowledge_chunks = relationship("KnowledgeChunk", back_populates="bot", cascade="all, delete-orphan")
    analytics = relationship("Analytics", back_populates="bot", cascade="all, delete-orphan")
    analytics_sessions = relationship("AnalyticsSession", cascade="all, delete-orphan")
    
    def is_available(self) -> bool:
        """Check if bot is available for chat."""
        if not self.is_active:
            return False
        if self.expiration_date and datetime.utcnow() > self.expiration_date:
            return False
        if self.message_limit and self.messages_used >= self.message_limit:
            return False
        return True
    
    def to_dict(self) -> dict:
        """Convert bot to dictionary (without sensitive data)."""
        from ..services.tool_service import (
            normalize_tool_definitions,
            normalize_tool_settings,
            normalize_quick_chip_settings,
        )

        normalized_tool_settings = normalize_tool_settings(
            self.tool_settings_json,
            fallback_enabled=bool(self.tools_enabled),
        )
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "template_type": self.template_type,
            "provider_type": self.provider_type,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "model": self.model,
            "memory_enabled": self.memory_enabled,
            "analytics_enabled": self.analytics_enabled,
            "is_active": self.is_active,
            "tools_enabled": bool(normalized_tool_settings.get("enabled")),
            "allowed_tools": normalize_tool_definitions(self.allowed_tools_json),
            "tool_settings": normalized_tool_settings,
            "quick_chip_settings": normalize_quick_chip_settings(self.quick_chips_json),
            "message_limit": self.message_limit,
            "messages_used": self.messages_used,
            "token_usage": self.token_usage or 0,
            "avg_latency": self.avg_latency or 0,
            "expiration_date": self.expiration_date.isoformat() if self.expiration_date else None,
            "accent_color": self.accent_color,
            "welcome_message": self.welcome_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_available": self.is_available()
        }


class Message(Base):
    """Chat message log."""
    __tablename__ = "messages"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("bots.id"), nullable=False)
    session_id = Column(String(36), nullable=False)
    
    user_message = Column(Text, nullable=False)
    assistant_response = Column(Text, nullable=False)
    
    # Metrics
    tokens_prompt = Column(Integer, default=0)
    tokens_completion = Column(Integer, default=0)
    tokens_total = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    
    # Metadata
    user_ip = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    bot = relationship("Bot", back_populates="messages")
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "session_id": self.session_id,
            "user_message": self.user_message,
            "assistant_response": self.assistant_response,
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "tokens_total": self.tokens_total,
            "latency_ms": self.latency_ms,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class KnowledgeChunk(Base):
    """Knowledge base chunks for RAG."""
    __tablename__ = "knowledge_chunks"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("bots.id"), nullable=False)
    
    content = Column(Text, nullable=False)
    source = Column(String(500), nullable=True)  # File name or URL
    chunk_index = Column(Integer, default=0)
    word_count = Column(Integer, default=0)
    
    # Chunk metadata (renamed from 'metadata' - reserved by SQLAlchemy)
    chunk_metadata = Column(JSON, nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    bot = relationship("Bot", back_populates="knowledge_chunks")
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "content": self.content,
            "source": self.source,
            "chunk_index": self.chunk_index,
            "word_count": self.word_count,
            "chunk_metadata": self.chunk_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class Analytics(Base):
    """Daily analytics aggregation."""
    __tablename__ = "analytics"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("bots.id"), nullable=False)
    date = Column(DateTime, nullable=False)
    
    # Metrics
    total_messages = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    avg_latency_ms = Column(Float, default=0)
    unique_sessions = Column(Integer, default=0)
    
    # Cost estimate (based on token pricing)
    estimated_cost = Column(Float, default=0)
    
    # Relationships
    bot = relationship("Bot", back_populates="analytics")
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "date": self.date.isoformat() if self.date else None,
            "total_messages": self.total_messages,
            "total_tokens": self.total_tokens,
            "avg_latency_ms": self.avg_latency_ms,
            "unique_sessions": self.unique_sessions,
            "estimated_cost": self.estimated_cost
        }


class AnalyticsSession(Base):
    """Daily unique session guard for analytics aggregation."""
    __tablename__ = "analytics_sessions"
    __table_args__ = (
        UniqueConstraint("bot_id", "date", "session_id", name="uq_analytics_daily_session"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("bots.id"), nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)
    session_id = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationMemory(Base):
    """Session-based conversation memory."""
    __tablename__ = "conversation_memory"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("bots.id"), nullable=False)
    session_id = Column(String(36), nullable=False)
    
    messages = Column(JSON, default=list)  # List of {role, content} dicts
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminUser(Base):
    """Admin user for dashboard access."""
    __tablename__ = "admin_users"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)


class ScraperJob(Base):
    """Website scraper job tracking."""
    __tablename__ = "scraper_jobs"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("bots.id"), nullable=True)
    
    url = Column(String(2000), nullable=False)
    status = Column(String(20), default="pending")  # pending, running, completed, failed, review
    depth = Column(Integer, default=2)
    
    pages_scraped = Column(Integer, default=0)
    chunks_created = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    
    # Consent tracking
    consent_confirmed = Column(Boolean, default=False)
    
    # Extracted data for review
    extracted_data = Column(JSON, nullable=True)
    started_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    lease_owner = Column(String(128), nullable=True)
    attempt_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class RateLimitEvent(Base):
    """Durable rate limit events for public APIs."""
    __tablename__ = "rate_limit_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    key = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ComponentLease(Base):
    """Supervisor lease heartbeat for runtime units."""
    __tablename__ = "component_leases"

    component_name = Column(String(100), primary_key=True)
    unit_type = Column(String(50), nullable=False)
    lease_owner = Column(String(128), nullable=False)
    state = Column(String(20), default="starting")
    attempt = Column(Integer, default=0)
    generation = Column(Integer, default=1)
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, default=datetime.utcnow, index=True)
    metadata_json = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TaskRun(Base):
    """Durable worker task state with checkpoints and lease heartbeats."""
    __tablename__ = "task_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_type = Column(String(50), nullable=False, index=True)
    task_key = Column(String(255), nullable=False, index=True)
    state = Column(String(20), default="pending", index=True)
    lease_owner = Column(String(128), nullable=True)
    attempt = Column(Integer, default=0)
    generation = Column(Integer, default=1)
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True, index=True)
    payload_json = Column(JSON, nullable=True)
    checkpoint_json = Column(JSON, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatRequestRecord(Base):
    """Idempotent public chat request state."""
    __tablename__ = "chat_requests"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    idempotency_key = Column(String(255), nullable=False, unique=True, index=True)
    bot_id = Column(String(36), ForeignKey("bots.id"), nullable=False, index=True)
    session_id = Column(String(255), nullable=True)
    request_hash = Column(String(128), nullable=False)
    state = Column(String(20), default="accepted", index=True)
    response_payload = Column(JSON, nullable=True)
    degraded_flags = Column(JSON, nullable=True)
    attempt = Column(Integer, default=0)
    generation = Column(Integer, default=1)
    lease_owner = Column(String(128), nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class OutboxEvent(Base):
    """Durable outbox for deferred worker-side actions."""
    __tablename__ = "outbox_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_type = Column(String(50), nullable=False, index=True)
    aggregate_type = Column(String(50), nullable=False, index=True)
    aggregate_id = Column(String(255), nullable=False, index=True)
    state = Column(String(20), default="pending", index=True)
    payload_json = Column(JSON, nullable=True)
    dedupe_key = Column(String(128), nullable=True, index=True)
    attempt = Column(Integer, default=0)
    generation = Column(Integer, default=1)
    lease_owner = Column(String(128), nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    available_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class IndexManifest(Base):
    """Versioned embedding manifest for active/stale build selection."""
    __tablename__ = "index_manifests"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("bots.id"), nullable=False, index=True)
    version = Column(String(64), nullable=False, index=True)
    state = Column(String(20), default="building", index=True)
    build_path = Column(String(500), nullable=False)
    reason = Column(String(100), nullable=True)
    chunk_count = Column(Integer, default=0)
    generation = Column(Integer, default=1)
    last_error = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)


class SemanticCacheEntry(Base):
    """Persistent semantic response cache scoped to bot/provider/model/index version."""
    __tablename__ = "semantic_cache_entries"
    __table_args__ = (
        UniqueConstraint(
            "bot_id",
            "provider",
            "model",
            "manifest_version",
            "system_prompt_hash",
            "normalized_query_hash",
            name="uq_semantic_cache_exact_key",
        ),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("bots.id"), nullable=False, index=True)
    provider = Column(String(50), nullable=False, index=True)
    model = Column(String(120), nullable=False, index=True)
    manifest_version = Column(String(64), nullable=False, index=True)
    system_prompt_hash = Column(String(128), nullable=False, index=True)
    normalized_query_hash = Column(String(128), nullable=False, index=True)
    normalized_query = Column(Text, nullable=False)
    query_embedding = Column(JSON, nullable=True)
    response_payload = Column(JSON, nullable=False)
    similarity_threshold = Column(Float, default=0.96)
    hit_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Database initialization
def init_db():
    """Initialize database tables."""
    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError as exc:
        if "already exists" not in str(exc).lower():
            raise
    _run_lightweight_migrations()


def _run_lightweight_migrations():
    """Add new columns for existing SQLite deployments without Alembic."""
    inspector = inspect(engine)
    if "scraper_jobs" in inspector.get_table_names():
        existing_columns = {column["name"] for column in inspector.get_columns("scraper_jobs")}
        migrations = {
            "started_at": "ALTER TABLE scraper_jobs ADD COLUMN started_at DATETIME",
            "heartbeat_at": "ALTER TABLE scraper_jobs ADD COLUMN heartbeat_at DATETIME",
            "lease_owner": "ALTER TABLE scraper_jobs ADD COLUMN lease_owner VARCHAR(128)",
            "attempt_count": "ALTER TABLE scraper_jobs ADD COLUMN attempt_count INTEGER DEFAULT 0",
        }
        with engine.begin() as connection:
            for column_name, ddl in migrations.items():
                if column_name not in existing_columns:
                    connection.execute(text(ddl))
    if "knowledge_chunks" in inspector.get_table_names():
        existing_columns = {column["name"] for column in inspector.get_columns("knowledge_chunks")}
        if "deleted_at" not in existing_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE knowledge_chunks ADD COLUMN deleted_at DATETIME"))
    if "outbox_events" in inspector.get_table_names():
        existing_columns = {column["name"] for column in inspector.get_columns("outbox_events")}
        if "dedupe_key" not in existing_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE outbox_events ADD COLUMN dedupe_key VARCHAR(128)"))
    if "bots" in inspector.get_table_names():
        existing_columns = {column["name"] for column in inspector.get_columns("bots")}
        with engine.begin() as connection:
            if "tools_enabled" not in existing_columns:
                connection.execute(text("ALTER TABLE bots ADD COLUMN tools_enabled BOOLEAN DEFAULT 0"))
            if "allowed_tools_json" not in existing_columns:
                connection.execute(text("ALTER TABLE bots ADD COLUMN allowed_tools_json JSON"))
            if "tool_settings_json" not in existing_columns:
                connection.execute(text("ALTER TABLE bots ADD COLUMN tool_settings_json JSON"))
            if "quick_chips_json" not in existing_columns:
                connection.execute(text("ALTER TABLE bots ADD COLUMN quick_chips_json JSON"))


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def init_db_async():
    """Initialize database tables through the async engine."""
    try:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    except OperationalError as exc:
        if "already exists" not in str(exc).lower():
            raise
    await asyncio.to_thread(_run_lightweight_migrations)


async def get_async_db():
    """Get an async database session for request-serving paths."""
    db = AsyncSessionLocal()
    try:
        yield db
    finally:
        await db.close()


@contextmanager
def get_db_context():
    """Context manager for database sessions."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Initialize on import
init_db()
