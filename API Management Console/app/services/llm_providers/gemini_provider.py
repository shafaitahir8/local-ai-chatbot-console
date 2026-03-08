"""
Google Gemini LLM Provider implementation (Generative Language API).
"""

import os
from typing import List, Optional, AsyncIterator, Dict, Any
import httpx

from .base_provider import BaseLLMProvider, ChatMessage, ChatResponse, EmbeddingResponse


class GeminiProvider(BaseLLMProvider):
    """
    Gemini provider using the Google Generative Language REST API.
    """

    API_BASE = "https://generativelanguage.googleapis.com/v1beta"

    SUPPORTED_MODELS = [
        "gemini-2.5-flash",
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]

    EMBEDDING_MODEL = "text-embedding-004"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        super().__init__(api_key, model)
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or "gemini-2.5-flash"
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def default_model(self) -> str:
        return "gemini-2.5-flash"

    @property
    def supported_models(self) -> List[str]:
        return self.SUPPORTED_MODELS

    def _headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json"}

    def _build_payload(
        self,
        messages: List[ChatMessage],
        temperature: float,
        max_tokens: int,
    ) -> Dict[str, Any]:
        system_parts: List[Dict[str, str]] = []
        contents: List[Dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                system_parts.append({"text": msg.content})
                continue

            role = "user" if msg.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.content}]})

        if not contents:
            contents = [{"role": "user", "parts": [{"text": ""}]}]

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": max(0.0, min(2.0, float(temperature))),
                "maxOutputTokens": max(1, int(max_tokens)),
            },
        }

        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        return payload

    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> ChatResponse:
        try:
            if not self.api_key:
                raise RuntimeError("Gemini API key is required")

            payload = self._build_payload(messages, temperature, max_tokens)
            url = f"{self.API_BASE}/models/{self.model}:generateContent"

            response = await self.client.post(
                url,
                params={"key": self.api_key},
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            candidates = data.get("candidates", [])
            content = ""
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                content = "".join(part.get("text", "") for part in parts if isinstance(part, dict))

            usage = data.get("usageMetadata", {})
            prompt_tokens = int(usage.get("promptTokenCount", 0) or 0)
            completion_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
            total_tokens = int(usage.get("totalTokenCount", prompt_tokens + completion_tokens) or 0)

            finish_reason = candidates[0].get("finishReason") if candidates else None

            return ChatResponse(
                content=content,
                tokens_prompt=prompt_tokens,
                tokens_completion=completion_tokens,
                tokens_total=total_tokens,
                model=self.model,
                finish_reason=finish_reason,
            )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise RuntimeError(f"Gemini API error: {exc.response.status_code} - {detail}")
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"Gemini API error: {str(exc)}")

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> AsyncIterator[str]:
        # Simple compatibility stream: emit the full response as one chunk.
        response = await self.chat(messages, temperature=temperature, max_tokens=max_tokens, **kwargs)
        if response.content:
            yield response.content

    async def get_embeddings(
        self,
        texts: List[str],
        **kwargs
    ) -> EmbeddingResponse:
        try:
            if not self.api_key:
                raise RuntimeError("Gemini API key is required")

            embeddings: List[List[float]] = []
            tokens_used = 0

            for text in texts:
                payload = {
                    "model": f"models/{self.EMBEDDING_MODEL}",
                    "content": {"parts": [{"text": text}]},
                }
                url = f"{self.API_BASE}/models/{self.EMBEDDING_MODEL}:embedContent"
                response = await self.client.post(
                    url,
                    params={"key": self.api_key},
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                vector = data.get("embedding", {}).get("values", [])
                embeddings.append(vector)

                usage = data.get("usageMetadata", {})
                tokens_used += int(usage.get("promptTokenCount", 0) or 0)

            return EmbeddingResponse(
                embeddings=embeddings,
                tokens_used=tokens_used,
                model=self.EMBEDDING_MODEL,
            )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise RuntimeError(f"Gemini embedding error: {exc.response.status_code} - {detail}")
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"Gemini embedding error: {str(exc)}")

    def validate_config(self) -> bool:
        if not self.api_key:
            return False
        return self.model in self.SUPPORTED_MODELS

    async def test_connection(self) -> bool:
        try:
            await self.chat([ChatMessage(role="user", content="Hi")], max_tokens=8)
            return True
        except Exception:
            return False

    def estimate_cost(self, tokens_prompt: int, tokens_completion: int) -> float:
        # Keep as zero to avoid inaccurate hardcoded pricing.
        return 0.0

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
