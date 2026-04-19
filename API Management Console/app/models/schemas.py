"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, validator
from enum import Enum


class ProviderType(str, Enum):
    OPENAI = "openai"
    LOCAL = "local"
    CLAUDE = "claude"
    GEMINI = "gemini"


DEFAULT_TOOL_PARAMETER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {},
}


class ToolDefinition(BaseModel):
    id: str = Field(..., min_length=1, max_length=120)
    display_name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=2000)
    parameters: Dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_TOOL_PARAMETER_SCHEMA))
    execution_type: Literal["builtin", "webhook", "local_script"] = "builtin"
    scope: Literal["public", "admin_only"] = "admin_only"
    is_mutating: bool = False
    requires_approval: bool = False
    execution_config: Dict[str, Any] = Field(default_factory=dict)


class QuickChipDefinition(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    action_type: Literal["suggested_prompt", "tool_call", "link"] = "suggested_prompt"
    value: str = Field(..., min_length=1, max_length=500)
    style: Literal["primary", "outline", "ghost"] = "outline"
    visibility_mode: Literal["always", "on_start", "keywords", "ai_only"] = "always"
    trigger_keywords: List[str] = Field(default_factory=list)
    ai_instruction: str = Field(default="", max_length=500)
    sentiment_filter: Literal["positive", "negative", "any"] = "any"
    priority: int = Field(default=0, ge=0, le=1000)
    id: Optional[str] = Field(default=None, max_length=120)
    payload: Dict[str, Any] = Field(default_factory=dict)


class ToolSettings(BaseModel):
    enabled: bool = False
    always_require_approval_for_mutating: bool = True
    max_tool_calls_per_session: int = Field(default=6, ge=1, le=50)


class QuickChipSettings(BaseModel):
    initial_chips: List[QuickChipDefinition] = Field(default_factory=list)
    allow_ai_suggestions: bool = True


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
    tools_enabled: bool = False
    allowed_tools: List[ToolDefinition] = Field(default_factory=list)
    tool_settings: ToolSettings = Field(default_factory=ToolSettings)
    quick_chip_settings: QuickChipSettings = Field(default_factory=QuickChipSettings)
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
    tools_enabled: Optional[bool] = None
    allowed_tools: Optional[List[ToolDefinition]] = None
    tool_settings: Optional[ToolSettings] = None
    quick_chip_settings: Optional[QuickChipSettings] = None
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
    tools_enabled: bool = False
    allowed_tools: List[ToolDefinition] = Field(default_factory=list)
    tool_settings: ToolSettings = Field(default_factory=ToolSettings)
    quick_chip_settings: QuickChipSettings = Field(default_factory=QuickChipSettings)
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


class QuickChipResponse(QuickChipDefinition):
    id: str = Field(..., min_length=1, max_length=120)


# ============== Chat Schemas ==============

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    session_id: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=255)

    @validator("message")
    def validate_message_not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Message cannot be empty or whitespace-only")
        return normalized


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tokens_used: int
    latency_ms: int
    retry_count: int = 0
    fallback_used: bool = False
    status_message: Optional[str] = None
    degraded_components: List[str] = Field(default_factory=list)
    data_freshness: str = "fresh"
    safe_modes: List[str] = Field(default_factory=list)
    context_scope: Optional[str] = None
    context_sources: List[str] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    quick_chips: List[QuickChipResponse] = Field(default_factory=list)
    tool_events: List[Dict[str, Any]] = Field(default_factory=list)
    pending_tool_actions: List[Dict[str, Any]] = Field(default_factory=list)


class BotStatusResponse(BaseModel):
    bot_id: str
    name: str
    is_available: bool
    reason: Optional[str] = None
    welcome_message: Optional[str] = None
    startup_welcome_ready: bool = False
    startup_welcome_message: Optional[str] = None
    startup_welcome_generated_at: Optional[str] = None
    hot_state: str = "cold"
    hot_ready: bool = False
    provider_ready: bool = False
    index_hydrated: bool = False
    warmup_detail: Optional[str] = None
    quick_chip_settings: QuickChipSettings = Field(default_factory=QuickChipSettings)


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
    limit: int = Field(default=20, ge=1, le=500)
    focus: Optional[str] = None
    sitemap_only: bool = False
    consent_confirmed: bool = False


class ScraperJobResponse(BaseModel):
    id: str
    bot_id: Optional[str]
    url: str
    status: str
    depth: int
    limit: Optional[int] = None
    focus: Optional[str] = None
    sitemap_only: bool = False
    pages_scraped: int
    chunks_created: int
    error_message: Optional[str]
    consent_confirmed: bool
    telemetry: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, Any]] = None
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
    requires_password_change: bool = False
    token_type: str = "bearer"


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8)


class AdminToolExecutionRequest(BaseModel):
    tool_id: str = Field(..., min_length=1, max_length=120)
    payload: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None


class PublicToolTriggerRequest(BaseModel):
    tool_id: str = Field(..., min_length=1, max_length=120)
    payload: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None

    @validator("tool_id")
    def validate_tool_id_not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("tool_id cannot be empty or whitespace-only")
        return normalized


class ToolTriggerResponse(BaseModel):
    status: str = "ok"
    tool_id: str
    response: str
    session_id: str
    quick_chips: List[QuickChipResponse] = Field(default_factory=list)
    tool_events: List[Dict[str, Any]] = Field(default_factory=list)
    pending_tool_actions: List[Dict[str, Any]] = Field(default_factory=list)
    result: Dict[str, Any] = Field(default_factory=dict)


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
    quick_chip_settings: QuickChipSettings = Field(default_factory=QuickChipSettings)


# ============== Error Schemas ==============

class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
