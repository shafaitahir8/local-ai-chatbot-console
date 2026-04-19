"""
Claude LLM Provider implementation (Anthropic API).
"""

import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, AsyncIterator
from .base_provider import BaseLLMProvider, ChatMessage, ChatResponse, EmbeddingResponse

_sentence_model = None
_sentence_model_lock = asyncio.Lock()
_thread_executor = ThreadPoolExecutor(max_workers=2)


class ClaudeProvider(BaseLLMProvider):
    """
    Anthropic Claude API provider.
    Future-ready implementation for Claude models.
    """
    
    SUPPORTED_MODELS = [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
        "claude-3-sonnet-20240229",
        "claude-3-haiku-20240307",
    ]
    
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        super().__init__(api_key, model)
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or "claude-sonnet-4-20250514"
        self.request_timeout = float(os.getenv("CLAUDE_TIMEOUT_SECONDS", "45"))
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
                self._client = AsyncAnthropic(
                    api_key=self.api_key,
                    timeout=self.request_timeout,
                    max_retries=0,
                )
            except ImportError:
                raise ImportError(
                    "anthropic package is required for Claude provider. "
                    "Install with: pip install anthropic"
                )
        return self._client
    
    @property
    def provider_name(self) -> str:
        return "claude"
    
    @property
    def default_model(self) -> str:
        return "claude-sonnet-4-20250514"
    
    @property
    def supported_models(self) -> List[str]:
        return self.SUPPORTED_MODELS

    async def warm_client(self) -> bool:
        """Prime the shared Anthropic client object during startup."""
        _ = self.client
        return True
    
    def _convert_messages(self, messages: List[ChatMessage]) -> tuple:
        """
        Convert messages to Anthropic format.
        Returns (system_prompt, messages_list)
        """
        system_prompt = None
        converted = []
        
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            else:
                converted.append({
                    "role": msg.role,
                    "content": msg.content
                })
        
        return system_prompt, converted
    
    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> ChatResponse:
        """Send chat completion request to Claude."""
        try:
            system_prompt, converted_messages = self._convert_messages(messages)

            async def _call() -> ChatResponse:
                params = {
                    "model": self.model,
                    "messages": converted_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }

                if system_prompt:
                    params["system"] = system_prompt

                response = await self.client.messages.create(**params)

                content = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        content += block.text

                return ChatResponse(
                    content=content,
                    tokens_prompt=response.usage.input_tokens,
                    tokens_completion=response.usage.output_tokens,
                    tokens_total=response.usage.input_tokens + response.usage.output_tokens,
                    model=response.model,
                    finish_reason=response.stop_reason
                )

            return await self.run_with_breaker(self.chat_breaker, self.chat_bulkhead, _call)
        except Exception as e:
            raise RuntimeError(f"Claude API error: {str(e)}")
    
    async def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream chat completion from Claude."""
        try:
            system_prompt, converted_messages = self._convert_messages(messages)
            
            params = {
                "model": self.model,
                "messages": converted_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            
            if system_prompt:
                params["system"] = system_prompt
            
            async with self.client.messages.stream(**params) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            raise RuntimeError(f"Claude streaming error: {str(e)}")
    
    async def get_embeddings(
        self,
        texts: List[str],
        **kwargs
    ) -> EmbeddingResponse:
        """
        Claude doesn't have a native embedding API.
        Fall back to OpenAI or local embeddings.
        """
        self.embedding_breaker.before_request()
        # Try OpenAI embeddings as fallback
        try:
            from .openai_provider import OpenAIProvider
            openai_key = os.getenv("OPENAI_API_KEY")
            if openai_key:
                provider = OpenAIProvider(api_key=openai_key)
                return await self.run_with_breaker(
                    self.embedding_breaker,
                    self.embedding_bulkhead,
                    lambda: provider.get_embeddings(texts, **kwargs),
                )
        except Exception:
            pass
        
        # Try local sentence-transformers
        try:
            global _sentence_model
            from sentence_transformers import SentenceTransformer
            async with _sentence_model_lock:
                if _sentence_model is None:
                    loop = asyncio.get_event_loop()
                    _sentence_model = await loop.run_in_executor(
                        _thread_executor,
                        lambda: SentenceTransformer('all-MiniLM-L6-v2')
                    )
            loop = asyncio.get_event_loop()
            embeddings = await loop.run_in_executor(
                _thread_executor,
                lambda: _sentence_model.encode(texts).tolist()
            )
            response = EmbeddingResponse(
                embeddings=embeddings,
                tokens_used=sum(len(t.split()) for t in texts),
                model="all-MiniLM-L6-v2"
            )
            self.embedding_breaker.record_success()
            return response
        except ImportError:
            self.embedding_breaker.record_failure("embeddings_unavailable")
            raise RuntimeError(
                "Claude does not provide embeddings. "
                "Please configure OpenAI API key or install sentence-transformers."
            )
    
    def validate_config(self) -> bool:
        """Validate Claude configuration."""
        if not self.api_key:
            return False
        if self.model not in self.SUPPORTED_MODELS:
            return False
        return True
    
    async def test_connection(self) -> bool:
        """Test the Claude connection."""
        try:
            # Send a minimal test message
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}]
            )
            return True
        except Exception:
            return False
    
    def estimate_cost(self, tokens_prompt: int, tokens_completion: int) -> float:
        """Estimate cost based on token usage."""
        # Pricing per 1M tokens
        pricing = {
            "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
            "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
            "claude-3-7-sonnet-20250219": {"input": 3.00, "output": 15.00},
            "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
            "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
            "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
            "claude-3-sonnet-20240229": {"input": 3.00, "output": 15.00},
            "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
        }
        
        model_pricing = pricing.get(self.model, {"input": 3.00, "output": 15.00})
        
        input_cost = (tokens_prompt / 1_000_000) * model_pricing["input"]
        output_cost = (tokens_completion / 1_000_000) * model_pricing["output"]
        
        return round(input_cost + output_cost, 6)
