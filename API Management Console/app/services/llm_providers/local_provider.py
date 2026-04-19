"""
Local LLM Provider implementation for Ollama and compatible servers.
"""

import os
import subprocess
import asyncio
import logging
import threading
import time
from typing import List, Optional, AsyncIterator
from concurrent.futures import ThreadPoolExecutor
import httpx
from .base_provider import BaseLLMProvider, ChatMessage, ChatResponse, EmbeddingResponse, ToolCall

logger = logging.getLogger(__name__)

# Module-level singleton for sentence-transformers model (lazy loaded)
_sentence_model = None
_sentence_model_lock = asyncio.Lock()
_thread_executor = ThreadPoolExecutor(max_workers=2)
_model_download_lock = threading.Lock()
_model_download_processes: dict[str, subprocess.Popen] = {}
_ollama_probe_cache_lock = threading.Lock()
_ollama_probe_cache: dict[str, dict] = {}


class LocalProvider(BaseLLMProvider):
    """
    Local LLM provider for Ollama and compatible OpenAI-style API servers.
    
    Supports:
    - Ollama (default, port 11434)
    - llama.cpp server
    - LM Studio
    - text-generation-webui
    - vLLM
    - LocalAI
    
    Low VRAM Models (~6GB or less):
    - qwen2.5:1.5b - Qwen 2.5 1.5B (~1GB VRAM)
    - llama3.2:1b - Llama 3.2 1B (~1GB VRAM)
    - llama3.2:3b - Llama 3.2 3B (~2GB VRAM)
    - phi3:mini - Phi-3 Mini (~2.2GB VRAM)
    - gemma2:2b - Gemma 2 2B (~1.6GB VRAM)
    
    Install models with: ollama pull <model_name>
    Example: ollama pull qwen2.5:1.5b
    """
    
    # Default to Ollama's port
    DEFAULT_BASE_URL = "http://localhost:11434"
    
    # Low VRAM models recommended for consumer hardware (confirmed available on Ollama)
    LOW_VRAM_MODELS = [
        "qwen2.5:1.5b",      # ~1GB disk, great for general tasks
        "llama3.2:1b",       # ~1.3GB disk, ultra lightweight
        "llama3.2:3b",       # ~2GB disk, good balance
        "phi3:mini",         # ~2.2GB disk, strong reasoning
        "gemma2:2b",         # ~1.6GB disk, Google's efficient model
    ]
    
    SUPPORTED_MODELS = LOW_VRAM_MODELS + [
        "local",             # Generic local model
        "llama3.1:8b",
        "mistral:7b",
        "mixtral:8x7b",
        "codellama:7b",
        "deepseek-coder:6.7b",
        "custom",            # For any custom model
    ]
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        super().__init__(api_key, model)
        self.base_url = base_url or os.getenv("LOCAL_LLM_URL", self.DEFAULT_BASE_URL)
        self.model = model or "qwen2.5:1.5b"  # Default to low-VRAM model
        total_timeout = float(os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", "120"))
        connect_timeout = float(os.getenv("LOCAL_LLM_CONNECT_TIMEOUT_SECONDS", "5"))
        self.request_timeout = httpx.Timeout(timeout=total_timeout, connect=connect_timeout)
        self.autostart_timeout_seconds = int(os.getenv("LOCAL_LLM_AUTOSTART_TIMEOUT_SECONDS", "10"))
        self.auto_pull_missing_models = os.getenv("LOCAL_LLM_AUTO_PULL_MISSING", "true").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        self.control_plane_cache_ttl_seconds = max(
            1.0,
            float(os.getenv("LOCAL_LLM_CONTROL_PLANE_CACHE_TTL_SECONDS", "15")),
        )
        self._client: Optional[httpx.AsyncClient] = None
        self._is_ollama = "11434" in self.base_url or "ollama" in self.base_url.lower()
    
    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy initialization of HTTP client."""
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.request_timeout,
                limits=httpx.Limits(
                    max_connections=int(os.getenv("HTTPX_MAX_CONNECTIONS", "32")),
                    max_keepalive_connections=int(os.getenv("HTTPX_MAX_KEEPALIVE_CONNECTIONS", "16")),
                    keepalive_expiry=float(os.getenv("HTTPX_KEEPALIVE_EXPIRY_SECONDS", "30")),
                ),
            )
        return self._client
    
    @property
    def provider_name(self) -> str:
        return "local"
    
    @property
    def default_model(self) -> str:
        return "local"
    
    @property
    def supported_models(self) -> List[str]:
        return self.SUPPORTED_MODELS

    def _probe_cache_key(self, suffix: str) -> str:
        return f"{self.base_url}:{suffix}"

    def _get_probe_cache(self, suffix: str):
        cache_key = self._probe_cache_key(suffix)
        now = time.monotonic()
        with _ollama_probe_cache_lock:
            cached = _ollama_probe_cache.get(cache_key)
            if cached is None:
                return None
            if now - float(cached.get("stored_at", 0.0)) > self.control_plane_cache_ttl_seconds:
                _ollama_probe_cache.pop(cache_key, None)
                return None
            return cached.get("value")

    def _set_probe_cache(self, suffix: str, value) -> None:
        cache_key = self._probe_cache_key(suffix)
        with _ollama_probe_cache_lock:
            _ollama_probe_cache[cache_key] = {
                "value": value,
                "stored_at": time.monotonic(),
            }

    async def warm_client(self) -> bool:
        """Prime the shared HTTP client and ensure Ollama is reachable."""
        _ = self.client
        if self._is_ollama:
            return await self._ensure_ollama_running()
        return True

    async def warm_model(self) -> bool:
        """Silently load the configured local model into memory before the first user request."""
        ready = await self.warm_client()
        if not ready:
            return False

        if self._is_ollama:
            model_available = await self._ensure_model_available(self.model)
            if not model_available:
                if self.auto_pull_missing_models:
                    if self._is_model_download_in_progress(self.model):
                        raise RuntimeError(f"MODEL_DOWNLOAD_IN_PROGRESS::{self.model}")
                    if self._start_model_download(self.model):
                        raise RuntimeError(f"MODEL_DOWNLOAD_STARTED::{self.model}")
                raise RuntimeError(
                    f"Model '{self.model}' is not installed. "
                    f"Please install it with: ollama pull {self.model}"
                )

            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are a silent warmup request. Respond with OK."},
                    {"role": "user", "content": "ping"},
                ],
                "stream": False,
                "keep_alive": os.getenv("LOCAL_LLM_KEEP_ALIVE", "15m"),
                "options": {
                    "temperature": 0,
                    "num_predict": max(1, int(os.getenv("LOCAL_LLM_WARMUP_MAX_TOKENS", "1"))),
                },
            }
            response = await self.client.post("/api/chat", json=payload)
            response.raise_for_status()
            self._set_probe_cache("ready", True)
            self._set_probe_cache(f"model:{self.model}", True)
            if os.getenv("LOCAL_LLM_WARMUP_EMBEDDINGS", "true").strip().lower() not in {"0", "false", "no"}:
                await self.warm_embeddings()
            return True

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "temperature": 0,
            "max_tokens": max(1, int(os.getenv("LOCAL_LLM_WARMUP_MAX_TOKENS", "1"))),
            "stream": False,
        }
        response = await self.client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        if os.getenv("LOCAL_LLM_WARMUP_EMBEDDINGS", "true").strip().lower() not in {"0", "false", "no"}:
            await self.warm_embeddings()
        return True

    async def _ensure_sentence_embedding_model(self):
        global _sentence_model
        from sentence_transformers import SentenceTransformer

        async with _sentence_model_lock:
            if _sentence_model is None:
                logger.info("Loading sentence-transformers model (first time, may take a moment)...")
                loop = asyncio.get_event_loop()
                _sentence_model = await loop.run_in_executor(
                    _thread_executor,
                    lambda: SentenceTransformer('all-MiniLM-L6-v2')
                )
                logger.info("Sentence-transformers model loaded successfully")
        return _sentence_model

    async def warm_embeddings(self) -> bool:
        """Prime the embedding path so the first RAG query does not cold-load the fallback model."""
        try:
            payload = {
                "input": ["warmup"],
                "model": "default",
            }
            response = await self.client.post("/v1/embeddings", json=payload)
            response.raise_for_status()
            return True
        except Exception:
            await self._ensure_sentence_embedding_model()
            return True

    async def _fetch_ollama_tags(self) -> Optional[list[str]]:
        cached_models = self._get_probe_cache("tags")
        if cached_models is not None:
            return list(cached_models)
        response = await self.client.get("/api/tags")
        response.raise_for_status()
        data = response.json()
        models = [model.get("name") for model in data.get("models", []) if model.get("name")]
        self._set_probe_cache("tags", models)
        self._set_probe_cache("ready", True)
        return models

    async def _ensure_ollama_running(self) -> bool:
        """
        Ensure Ollama is running. Starts it if necessary.
        Returns True if Ollama is running/started successfully.
        """
        if not self._is_ollama:
            return True  # Not using Ollama, skip

        cached_ready = self._get_probe_cache("ready")
        if cached_ready is True:
            return True
        
        # Check if Ollama is already running
        try:
            await self._fetch_ollama_tags()
            logger.info("Ollama is already running")
            return True
        except Exception:
            pass  # Ollama not responding, try to start it
        
        # Try to start Ollama
        logger.info("Ollama not running, attempting to start...")
        try:
            # Start Ollama serve in background
            if os.name == 'nt':  # Windows
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:  # Unix/Linux/Mac
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
            
            # Wait for Ollama to start, but fail fast enough to avoid request pileups.
            for i in range(self.autostart_timeout_seconds):
                await asyncio.sleep(1)
                try:
                    await self._fetch_ollama_tags()
                    logger.info(f"Ollama started successfully after {i+1} seconds")
                    return True
                except Exception:
                    continue
            
            logger.error("Ollama failed to start within %s seconds", self.autostart_timeout_seconds)
            return False
            
        except FileNotFoundError:
            logger.error("Ollama executable not found. Please install Ollama from https://ollama.ai")
            return False
        except Exception as e:
            logger.error(f"Failed to start Ollama: {e}")
            return False
    
    async def _ensure_model_available(self, model_name: str) -> bool:
        """
        Check if a model is available locally. If not, attempt to pull it.
        Returns True if model is available.
        """
        if not self._is_ollama:
            return True

        cached_available = self._get_probe_cache(f"model:{model_name}")
        if cached_available is not None:
            return bool(cached_available)
        
        try:
            available_models = await self._fetch_ollama_tags()
            if available_models is None:
                return False

            # Check exact match or base name match (e.g., "qwen2.5:1.5b" matches "qwen2.5:1.5b")
            if model_name in available_models:
                self._set_probe_cache(f"model:{model_name}", True)
                return True

            # Check if base name matches (handle version tags)
            base_name = model_name.split(":")[0]
            for m in available_models:
                if m.startswith(base_name):
                    logger.info(f"Model {model_name} not found, but {m} is available")
                    self._set_probe_cache(f"model:{model_name}", True)
                    return True

            logger.warning(f"Model '{model_name}' not found locally. Available: {available_models}")
            logger.info(f"To install: ollama pull {model_name}")
            self._set_probe_cache(f"model:{model_name}", False)
            return False
        except Exception as e:
            logger.error(f"Failed to check model availability: {e}")
            return False

    def _is_model_download_in_progress(self, model_name: str) -> bool:
        """Check whether a background ollama pull is already running for a model."""
        with _model_download_lock:
            process = _model_download_processes.get(model_name)
            if not process:
                return False
            if process.poll() is None:
                return True
            _model_download_processes.pop(model_name, None)
            return False

    def _start_model_download(self, model_name: str) -> bool:
        """Start ollama pull in background for a missing model."""
        if self._is_model_download_in_progress(model_name):
            return False

        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        try:
            process = subprocess.Popen(["ollama", "pull", model_name], **popen_kwargs)
            with _model_download_lock:
                _model_download_processes[model_name] = process
            logger.warning(
                "Model '%s' is missing. Started background download via `ollama pull %s` (pid=%s).",
                model_name,
                model_name,
                process.pid,
            )
            return True
        except Exception as e:
            logger.error("Failed to start background download for model '%s': %s", model_name, e)
            return False
    
    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> ChatResponse:
        """Send chat completion request to local server."""
        try:
            # Ensure Ollama is running (auto-start if needed)
            if self._is_ollama:
                ollama_ready = await self._ensure_ollama_running()
                if not ollama_ready:
                    raise RuntimeError(
                        "Ollama is not running and could not be started automatically. "
                        "Please start it manually with: ollama serve"
                    )
                
                # Check if model is available
                model_available = await self._ensure_model_available(self.model)
                if not model_available:
                    if self.auto_pull_missing_models:
                        if self._is_model_download_in_progress(self.model):
                            raise RuntimeError(f"MODEL_DOWNLOAD_IN_PROGRESS::{self.model}")
                        if self._start_model_download(self.model):
                            raise RuntimeError(f"MODEL_DOWNLOAD_STARTED::{self.model}")
                    raise RuntimeError(
                        f"Model '{self.model}' is not installed. "
                        f"Please install it with: ollama pull {self.model}"
                    )
            
            formatted_messages = self.format_messages(messages)
            
            async def _call() -> ChatResponse:
                if self._is_ollama:
                    return await self._chat_ollama(formatted_messages, temperature, max_tokens, **kwargs)
                return await self._chat_openai_compat(formatted_messages, temperature, max_tokens, **kwargs)

            return await self.run_with_breaker(self.chat_breaker, self.chat_bulkhead, _call)
                
        except httpx.HTTPStatusError as e:
            error_text = e.response.text
            if "not found" in error_text.lower():
                raise RuntimeError(
                    f"Model '{self.model}' not found. Install with: ollama pull {self.model}"
                )
            raise RuntimeError(f"Local LLM API error: {e.response.status_code} - {error_text}")
        except httpx.RequestError as e:
            raise RuntimeError(
                f"Local LLM connection error: {str(e)}. "
                "Ollama may not be running. Attempting to start automatically..."
            )
        except Exception as e:
            if "RuntimeError" in str(type(e).__name__):
                raise  # Re-raise RuntimeError as-is
            raise RuntimeError(f"Local LLM error: {str(e)}")
    
    async def _chat_ollama(
        self,
        messages: List[dict],
        temperature: float,
        max_tokens: int,
        **kwargs
    ) -> ChatResponse:
        """Chat using Ollama's native API."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
        
        response = await self.client.post("/api/chat", json=payload)
        response.raise_for_status()
        
        data = response.json()
        
        return ChatResponse(
            content=data.get("message", {}).get("content", ""),
            tokens_prompt=data.get("prompt_eval_count", 0),
            tokens_completion=data.get("eval_count", 0),
            tokens_total=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            model=data.get("model", self.model),
            finish_reason=data.get("done_reason", "stop"),
            tool_calls=self._parse_tool_calls(data.get("message", {}).get("tool_calls") or []),
        )
    
    async def _chat_openai_compat(
        self,
        messages: List[dict],
        temperature: float,
        max_tokens: int,
        **kwargs
    ) -> ChatResponse:
        """Chat using OpenAI-compatible endpoint."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        payload.update(kwargs)
        
        response = await self.client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        
        data = response.json()
        choice = data["choices"][0]
        usage = data.get("usage", {})
        
        return ChatResponse(
            content=choice["message"].get("content", ""),
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_completion=usage.get("completion_tokens", 0),
            tokens_total=usage.get("total_tokens", 0),
            model=data.get("model", self.model),
            finish_reason=choice.get("finish_reason"),
            tool_calls=self._parse_tool_calls(choice.get("message", {}).get("tool_calls") or []),
        )
    
    async def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream chat completion from local server."""
        try:
            formatted_messages = self.format_messages(messages)

            if self._is_ollama:
                ollama_ready = await self._ensure_ollama_running()
                if not ollama_ready:
                    raise RuntimeError(
                        "Ollama is not running and could not be started automatically. "
                        "Please start it manually with: ollama serve"
                    )
                model_available = await self._ensure_model_available(self.model)
                if not model_available:
                    raise RuntimeError(
                        f"Model '{self.model}' is not installed. "
                        f"Please install it with: ollama pull {self.model}"
                    )
            
            if self._is_ollama:
                # Ollama streaming
                payload = {
                    "model": self.model,
                    "messages": formatted_messages,
                    "stream": True,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    }
                }
                if kwargs.get("tools"):
                    payload["tools"] = kwargs["tools"]
                
                async with self.client.stream("POST", "/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line:
                            try:
                                import json
                                chunk = json.loads(line)
                                if chunk.get("message", {}).get("content"):
                                    yield chunk["message"]["content"]
                                if chunk.get("done"):
                                    break
                            except (json.JSONDecodeError, KeyError):
                                continue
            else:
                # OpenAI-compatible streaming
                payload = {
                    "model": self.model,
                    "messages": formatted_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                }
                payload.update(kwargs)
                
                async with self.client.stream("POST", "/v1/chat/completions", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                import json
                                chunk = json.loads(data)
                                if chunk["choices"] and chunk["choices"][0]["delta"].get("content"):
                                    yield chunk["choices"][0]["delta"]["content"]
                            except (json.JSONDecodeError, KeyError):
                                continue
        except Exception as e:
            raise RuntimeError(f"Local LLM streaming error: {str(e)}")
    
    async def get_embeddings(
        self,
        texts: List[str],
        **kwargs
    ) -> EmbeddingResponse:
        """Get embeddings from local server."""
        try:
            async def _call_remote() -> EmbeddingResponse:
                payload = {
                    "input": texts,
                    "model": kwargs.get("embedding_model", "default")
                }

                response = await self.client.post(
                    "/v1/embeddings",
                    json=payload
                )
                response.raise_for_status()

                data = response.json()
                embeddings = [item["embedding"] for item in data["data"]]

                return EmbeddingResponse(
                    embeddings=embeddings,
                    tokens_used=data.get("usage", {}).get("total_tokens", 0),
                    model=data.get("model", "local")
                )

            return await self.run_with_breaker(self.embedding_breaker, self.embedding_bulkhead, _call_remote)
        except Exception as e:
            # Fallback: use sentence-transformers locally if available
            try:
                global _sentence_model
                await self._ensure_sentence_embedding_model()

                # Run encoding in thread executor to avoid blocking async
                loop = asyncio.get_event_loop()
                embeddings = await loop.run_in_executor(
                    _thread_executor,
                    lambda: _sentence_model.encode(texts).tolist()
                )
                self.embedding_breaker.record_success()
                return EmbeddingResponse(
                    embeddings=embeddings,
                    tokens_used=sum(len(t.split()) for t in texts),
                    model="all-MiniLM-L6-v2"
                )
            except ImportError:
                self.embedding_breaker.record_failure(e)
                raise RuntimeError(f"Local embedding error: {str(e)}. Install sentence-transformers as fallback.")
    
    def validate_config(self) -> bool:
        """Validate local LLM configuration."""
        if not self.base_url:
            return False
        return True
    
    async def test_connection(self) -> bool:
        """Test the local LLM connection."""
        try:
            if self._is_ollama:
                # Ollama tags endpoint
                response = await self.client.get("/api/tags")
                return response.status_code == 200
            else:
                response = await self.client.get("/v1/models")
                return response.status_code == 200
        except Exception:
            # Try health endpoint as fallback
            try:
                response = await self.client.get("/health")
                return response.status_code == 200
            except Exception:
                return False
    
    async def get_available_models(self) -> List[str]:
        """Get list of available models from local server."""
        try:
            if self._is_ollama:
                response = await self.client.get("/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    return [m["name"] for m in data.get("models", [])]
            else:
                response = await self.client.get("/v1/models")
                if response.status_code == 200:
                    data = response.json()
                    return [m["id"] for m in data.get("data", [])]
        except Exception:
            pass
        return []
    
    async def pull_model(self, model_name: str) -> bool:
        """Pull/download a model (Ollama only)."""
        if not self._is_ollama:
            return False
        try:
            response = await self.client.post(
                "/api/pull",
                json={"name": model_name},
                timeout=600.0  # Models can take a while to download
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def estimate_cost(self, tokens_prompt: int, tokens_completion: int) -> float:
        """Local models have no API cost."""
        return 0.0
    
    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _parse_tool_calls(self, raw_calls: List[dict]) -> List[ToolCall]:
        parsed: List[ToolCall] = []
        for call in raw_calls or []:
            function = dict(call.get("function") or {})
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    import json
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            parsed.append(
                ToolCall(
                    name=name,
                    arguments=arguments,
                    call_id=str(call.get("id") or "").strip() or None,
                )
            )
        return parsed
