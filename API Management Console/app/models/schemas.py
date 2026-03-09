"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator
from enum import Enum


class ProviderType(str, Enum):
    OPENAI = "openai"
    LOCAL = "local"
    CLAUDE = "claude"
    GEMINI = "gemini"


# ============== Bot Schemas ==============

class BotCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    template_type: str
    provider_type: ProviderType
    system_prompt: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=2000, ge=1, le=128000)
    model: Optional[str] = None
    api_key: Optional[str] = None  # Will be encrypted
    memory_enabled: bool = True
    analytics_enabled: bool = True
    is_active: bool = True
    message_limit: Optional[int] = Field(default=None, ge=1)
    expiration_date: Optional[datetime] = None
    accent_color: str = Field(default="#007bff", pattern="^#[0-9A-Fa-f]{6}$")
    welcome_message: str = "Hello! How can I help you today?"


class BotUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=1, le=128000)
    model: Optional[str] = None
    api_key: Optional[str] = None
    memory_enabled: Optional[bool] = None
    analytics_enabled: Optional[bool] = None
    is_active: Optional[bool] = None
    message_limit: Optional[int] = Field(None, ge=1)
    expiration_date: Optional[datetime] = None
    accent_color: Optional[str] = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    welcome_message: Optional[str] = None


class BotResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    template_type: str
    provider_type: str
    system_prompt: Optional[str]
    temperature: float
    max_tokens: int
    model: Optional[str]
    memory_enabled: bool
    analytics_enabled: bool
    is_active: bool
    message_limit: Optional[int]
    messages_used: int
    token_usage: int = 0
    avg_latency: float = 0
    expiration_date: Optional[datetime]
    accent_color: str
    welcome_message: str
    created_at: datetime
    updated_at: datetime
    is_available: bool
    
    class Config:
        from_attributes = True


class BotListResponse(BaseModel):
    bots: List[BotResponse]
    total: int


# ============== Chat Schemas ==============

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tokens_used: int
    latency_ms: int
    retry_count: int = 0
    fallback_used: bool = False
    status_message: Optional[str] = None


class BotStatusResponse(BaseModel):
    bot_id: str
    name: str
    is_available: bool
    reason: Optional[str] = None
    welcome_message: Optional[str] = None
    startup_welcome_ready: bool = False
    startup_welcome_message: Optional[str] = None
    startup_welcome_generated_at: Optional[str] = None


# ============== Knowledge Base Schemas ==============

class KnowledgeUpload(BaseModel):
    content: str
    source: str
    append: bool = False


class KnowledgeChunkResponse(BaseModel):
    id: str
    content: str
    source: Optional[str]
    chunk_index: int
    word_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============== Scraper Schemas ==============

class ScraperRequest(BaseModel):
    url: str = Field(..., pattern="^https?://")
    depth: int = Field(default=2, ge=1, le=3)
    consent_confirmed: bool = False


class ScraperJobResponse(BaseModel):
    id: str
    bot_id: Optional[str]
    url: str
    status: str
    depth: int
    pages_scraped: int
    chunks_created: int
    error_message: Optional[str]
    consent_confirmed: bool
    created_at: datetime
    completed_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class ScraperReviewRequest(BaseModel):
    job_id: str
    approved: bool
    selected_chunks: Optional[List[int]] = None  # Indices of chunks to keep


# ============== Analytics Schemas ==============

class AnalyticsResponse(BaseModel):
    bot_id: str
    period_start: datetime
    period_end: datetime
    total_messages: int
    total_tokens: int
    avg_latency_ms: float
    unique_sessions: int
    estimated_cost: float
    daily_breakdown: List[Dict[str, Any]]
    top_questions: List[Dict[str, Any]]


class MessageLogResponse(BaseModel):
    id: str
    session_id: str
    user_message: str
    assistant_response: str
    tokens_total: int
    latency_ms: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============== Template Schemas ==============

class TemplateResponse(BaseModel):
    template_id: Optional[str] = None
    name: str
    provider: str
    required_fields: List[str]
    optional_fields: List[str]
    default_model: Optional[str]
    default_temperature: float
    default_system_prompt: Optional[str]
    supports_memory: bool
    supports_temperature: bool
    supports_streaming: bool


class TemplateListResponse(BaseModel):
    templates: List[TemplateResponse]


# ============== Auth Schemas ==============

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8)


# ============== Clone Schemas ==============

class CloneRequest(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=255)
    copy_knowledge_base: bool = True
    reset_analytics: bool = True


# ============== Embed Schemas ==============

class EmbedConfigResponse(BaseModel):
    bot_id: str
    bot_name: str
    accent_color: str
    welcome_message: str
    is_available: bool


# ============== Error Schemas ==============

class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
