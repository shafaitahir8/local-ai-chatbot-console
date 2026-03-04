"""
Base LLM Provider - Abstract interface for all LLM providers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional, AsyncIterator


@dataclass
class ChatMessage:
    """Represents a chat message."""
    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class ChatResponse:
    """Response from LLM provider."""
    content: str
    tokens_prompt: int
    tokens_completion: int
    tokens_total: int
    model: str
    finish_reason: Optional[str] = None


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
    
    def format_messages(self, messages: List[ChatMessage]) -> List[Dict[str, str]]:
        """Convert ChatMessage objects to dict format."""
        return [{"role": msg.role, "content": msg.content} for msg in messages]
    
    def prepare_system_prompt(self, system_prompt: str, context: Optional[str] = None) -> str:
        """Prepare system prompt with optional RAG context."""
        if context:
            return f"{system_prompt}\n\nRelevant context:\n{context}"
        return system_prompt
