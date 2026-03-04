"""
OpenAI LLM Provider implementation.
"""

import os
from typing import List, Optional, AsyncIterator
import asyncio
from openai import AsyncOpenAI
from .base_provider import BaseLLMProvider, ChatMessage, ChatResponse, EmbeddingResponse


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider for GPT models."""
    
    SUPPORTED_MODELS = [
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
        "o1",
        "o1-mini",
        "o1-pro",
        "o3-mini",
    ]
    
    EMBEDDING_MODELS = [
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-ada-002",
    ]
    
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        super().__init__(api_key, model)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or "gpt-4.1-mini"
        self.embedding_model = "text-embedding-3-small"
        self._client: Optional[AsyncOpenAI] = None
    
    @property
    def client(self) -> AsyncOpenAI:
        """Lazy initialization of OpenAI client."""
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client
    
    @property
    def provider_name(self) -> str:
        return "openai"
    
    @property
    def default_model(self) -> str:
        return "gpt-4.1-mini"
    
    @property
    def supported_models(self) -> List[str]:
        return self.SUPPORTED_MODELS
    
    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> ChatResponse:
        """Send chat completion request to OpenAI."""
        try:
            formatted_messages = self.format_messages(messages)
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=formatted_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            
            choice = response.choices[0]
            usage = response.usage
            
            return ChatResponse(
                content=choice.message.content or "",
                tokens_prompt=usage.prompt_tokens if usage else 0,
                tokens_completion=usage.completion_tokens if usage else 0,
                tokens_total=usage.total_tokens if usage else 0,
                model=response.model,
                finish_reason=choice.finish_reason
            )
        except Exception as e:
            raise RuntimeError(f"OpenAI API error: {str(e)}")
    
    async def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream chat completion from OpenAI."""
        try:
            formatted_messages = self.format_messages(messages)
            
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=formatted_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                **kwargs
            )
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            raise RuntimeError(f"OpenAI streaming error: {str(e)}")
    
    async def get_embeddings(
        self,
        texts: List[str],
        **kwargs
    ) -> EmbeddingResponse:
        """Get embeddings from OpenAI."""
        try:
            model = kwargs.get("embedding_model", self.embedding_model)
            
            response = await self.client.embeddings.create(
                model=model,
                input=texts
            )
            
            embeddings = [item.embedding for item in response.data]
            
            return EmbeddingResponse(
                embeddings=embeddings,
                tokens_used=response.usage.total_tokens,
                model=response.model
            )
        except Exception as e:
            raise RuntimeError(f"OpenAI embedding error: {str(e)}")
    
    def validate_config(self) -> bool:
        """Validate OpenAI configuration."""
        if not self.api_key:
            return False
        if self.model not in self.SUPPORTED_MODELS:
            return False
        return True
    
    async def test_connection(self) -> bool:
        """Test the OpenAI connection."""
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False
    
    def estimate_cost(self, tokens_prompt: int, tokens_completion: int) -> float:
        """Estimate cost based on token usage."""
        # Pricing per 1M tokens (as of late 2024 / early 2025 pricing estimates)
        pricing = {
            "gpt-4.1": {"input": 2.00, "output": 8.00},
            "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
            "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4-turbo": {"input": 10.00, "output": 30.00},
            "gpt-4": {"input": 30.00, "output": 60.00},
            "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
            "o1": {"input": 15.00, "output": 60.00},
            "o1-mini": {"input": 3.00, "output": 12.00},
            "o1-pro": {"input": 150.00, "output": 600.00},
            "o3-mini": {"input": 1.10, "output": 4.40},
        }
        
        model_pricing = pricing.get(self.model, {"input": 1.00, "output": 2.00})
        
        input_cost = (tokens_prompt / 1_000_000) * model_pricing["input"]
        output_cost = (tokens_completion / 1_000_000) * model_pricing["output"]
        
        return round(input_cost + output_cost, 6)
