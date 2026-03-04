"""
Database configuration and models for Local AI Chatbot Deployment Console.
"""

from datetime import datetime
from typing import Optional
import uuid
from sqlalchemy import (
    create_engine, Column, String, Text, Integer, Float, Boolean,
    DateTime, ForeignKey, JSON, event
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from contextlib import contextmanager
import os

# Database setup
DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'chatbot_console.db')
os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)

DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


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
    
    # Limits
    message_limit = Column(Integer, nullable=True)  # None = unlimited
    messages_used = Column(Integer, default=0)
    
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
            "message_limit": self.message_limit,
            "messages_used": self.messages_used,
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
    
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


# Database initialization
def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
