"""
Base LLM Provider - Abstract interface for all LLM providers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional, AsyncIterator, Awaitable, Callable, TypeVar, Any

from ..bulkhead import get_bulkhead
from ..circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from ..metrics_service import metrics_service


T = TypeVar("T")


@dataclass
class ToolCall:
    """Represents a provider-emitted tool call."""
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: Optional[str] = None


@dataclass
class ChatMessage:
    """Represents a chat message."""
    role: str  # "system", "user", "assistant"
    content: str
    tool_name: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)


@dataclass
class ChatResponse:
    """Response from LLM provider."""
    content: str
    tokens_prompt: int
    tokens_completion: int
    tokens_total: int
    model: str
    finish_reason: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)


@dataclass
class EmbeddingResponse:
    """Response from embedding provider."""
    embeddings: List[List[float]]
    tokens_used: int
    model: str


class BaseLLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    All providers must implement these methods.
    """
    
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        provider_name = self.__class__.__name__.lower()
        self.chat_breaker = CircuitBreaker(name=f"{provider_name}.chat")
        self.embedding_breaker = CircuitBreaker(name=f"{provider_name}.embeddings")
        self.chat_bulkhead = get_bulkhead(f"{provider_name}.chat")
        self.embedding_bulkhead = get_bulkhead(f"{provider_name}.embeddings")
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass
    
    @property
    @abstractmethod
    def default_model(self) -> str:
        """Return the default model for this provider."""
        pass
    
    @property
    @abstractmethod
    def supported_models(self) -> List[str]:
        """Return list of supported models."""
        pass
    
    @abstractmethod
    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> ChatResponse:
        """
        Send a chat completion request.
        
        Args:
            messages: List of chat messages
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response
            **kwargs: Additional provider-specific parameters
            
        Returns:
            ChatResponse with content and usage info
        """
        pass
    
    @abstractmethod
    async def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Stream a chat completion response.
        
        Args:
            messages: List of chat messages
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response
            **kwargs: Additional provider-specific parameters
            
        Yields:
            Content chunks as they arrive
        """
        pass
    
    @abstractmethod
    async def get_embeddings(
        self,
        texts: List[str],
        **kwargs
    ) -> EmbeddingResponse:
        """
        Get embeddings for texts.
        
        Args:
            texts: List of texts to embed
            **kwargs: Additional provider-specific parameters
            
        Returns:
            EmbeddingResponse with embeddings
        """
        pass
    
    @abstractmethod
    def validate_config(self) -> bool:
        """
        Validate the provider configuration.
        
        Returns:
            True if configuration is valid
        """
        pass
    
    def format_messages(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """Convert ChatMessage objects to dict format."""
        formatted: List[Dict[str, Any]] = []
        for msg in messages:
            payload: Dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_name:
                payload["tool_name"] = msg.tool_name
            if msg.tool_calls:
                payload["tool_calls"] = [
                    {
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        },
                        **({"id": call.call_id} if call.call_id else {}),
                    }
                    for call in msg.tool_calls
                ]
            formatted.append(payload)
        return formatted
    
    def prepare_system_prompt(self, system_prompt: str, context: Optional[str] = None) -> str:
        """Prepare system prompt with optional RAG context."""
        if context:
            return f"{system_prompt}\n\nRelevant context:\n{context}"
        return system_prompt

    async def warm_client(self) -> bool:
        """Best-effort client pre-initialization hook for startup warmup."""
        return True

    async def warm_model(self) -> bool:
        """Best-effort model warmup hook for providers that need it."""
        return await self.warm_client()

    async def run_with_breaker(
        self,
        breaker: CircuitBreaker,
        bulkhead,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """Execute an async operation behind a circuit breaker."""
        try:
            breaker.before_request()
        except CircuitBreakerOpenError:
            metrics_service.set_gauge(
                "provider_circuit_open",
                1,
                labels={"dependency": self.provider_name, "breaker": breaker.name},
            )
            raise
        async with bulkhead:
            try:
                result = await operation()
            except Exception as exc:
                breaker.record_failure(exc)
                if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
                    metrics_service.inc_counter(
                        "provider_timeout_total",
                        labels={"dependency": self.provider_name},
                    )
                metrics_service.set_gauge(
                    "provider_circuit_open",
                    1 if breaker.snapshot().get("state") == "open" else 0,
                    labels={"dependency": self.provider_name, "breaker": breaker.name},
                )
                raise
        breaker.record_success()
        metrics_service.set_gauge(
            "provider_circuit_open",
            1 if breaker.snapshot().get("state") == "open" else 0,
            labels={"dependency": self.provider_name, "breaker": breaker.name},
        )
        return result
