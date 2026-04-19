"""
Bot Service - Core bot management operations.
"""

import asyncio
import copy
import hashlib
import os
import json
import random
import shutil
import uuid
import re
import logging
import threading
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import (
    Bot,
    ChatRequestRecord,
    ConversationMemory,
    IndexManifest,
    KnowledgeChunk,
    OutboxEvent,
    SessionLocal,
    ScraperJob,
    SemanticCacheEntry,
    TaskRun,
)
from .encryption_service import encryption_service
from .embedding_service import EmbeddingService
from .logging_utils import log_event
from .metrics_service import metrics_service
from .router_service import RoutingDecision, router_service
from .scraper_service import ScraperService
from .scrape_trace_service import scrape_trace_service
from .semantic_cache_service import semantic_cache_service
from .supervisor_service import supervisor_service
from .standalone_model_service import standalone_model_service
from .template_service import template_service
from .circuit_breaker import CircuitBreakerOpenError
from .llm_providers import OpenAIProvider, LocalProvider, ClaudeProvider, GeminiProvider
from .llm_providers.base_provider import BaseLLMProvider, ChatMessage
from .tool_service import (
    build_quick_chip_ai_system_hint,
    build_provider_tool_specs,
    get_startup_quick_chips,
    merge_quick_chip_sources,
    normalize_quick_chip_settings,
    normalize_tool_definitions,
    normalize_tool_settings,
    process_tool_call,
    scan_quick_chip_keywords,
)

logger = logging.getLogger(__name__)


class ProviderTemporarilyUnavailableError(RuntimeError):
    def __init__(self, message: str, retry_after: float = 5.0) -> None:
        self.retry_after = max(0.0, float(retry_after))
        super().__init__(message)


class BotService:
    """
    Core service for managing chatbots.
    """
    
    def __init__(self):
        self.embedding_service = EmbeddingService()
        self._provider_cache: Dict[str, Dict[str, Any]] = {}
        self.chat_retry_attempts = max(0, int(os.getenv("CHAT_RETRY_ATTEMPTS", "2")))
        self.chat_retry_backoff_seconds = max(0.05, float(os.getenv("CHAT_RETRY_BACKOFF_SECONDS", "0.25")))
        self.chat_retry_backoff_cap_seconds = max(
            self.chat_retry_backoff_seconds,
            float(os.getenv("CHAT_RETRY_BACKOFF_CAP_SECONDS", "4")),
        )
        self.chat_max_memory_messages = max(0, int(os.getenv("CHAT_MEMORY_WINDOW", "10")))
        self.chat_memory_turns = max(1, int(os.getenv("CHAT_MEMORY_TURNS", "2")))
        self.chat_system_prompt_token_budget = max(
            120,
            int(os.getenv("CHAT_SYSTEM_PROMPT_TOKEN_BUDGET", "600")),
        )
        self.chat_memory_token_budget = max(80, int(os.getenv("CHAT_MEMORY_TOKEN_BUDGET", "400")))
        self.chat_memory_summary_token_budget = max(
            80,
            int(os.getenv("CHAT_MEMORY_SUMMARY_TOKEN_BUDGET", "180")),
        )
        self.chat_recent_message_token_budget = max(
            32,
            int(os.getenv("CHAT_RECENT_MESSAGE_TOKEN_BUDGET", "90")),
        )
        self.chat_rag_token_budget = max(120, int(os.getenv("CHAT_RAG_TOKEN_BUDGET", "800")))
        self.chat_rag_min_score = float(os.getenv("CHAT_RAG_MIN_SCORE", "0.18"))
        self.chat_rag_high_confidence_score = float(os.getenv("CHAT_RAG_HIGH_CONFIDENCE_SCORE", "0.72"))
        self.chat_rag_snippet_token_budget = max(
            32,
            int(os.getenv("CHAT_RAG_SNIPPET_TOKEN_BUDGET", "120")),
        )
        self.chat_rag_min_snippet_tokens = max(
            8,
            int(os.getenv("CHAT_RAG_MIN_SNIPPET_TOKENS", "12")),
        )
        self.semantic_cache_max_query_tokens = max(
            1,
            int(os.getenv("SEMANTIC_CACHE_MAX_QUERY_TOKENS", "96")),
        )
        self._session_file_lock = threading.Lock()
        self._session_memory_lock = threading.Lock()
        self._startup_welcome_lock = threading.Lock()
        self._startup_welcome_cache: Dict[str, Dict[str, str]] = {}
        self._bot_cache_lock = threading.Lock()
        self._bot_cache: Dict[str, Dict[str, Any]] = {}
        self._bot_cache_ttl_seconds = max(30.0, float(os.getenv("BOT_CACHE_TTL_SECONDS", "300")))
        self._provider_cache_ttl_seconds = max(30.0, float(os.getenv("PROVIDER_CACHE_TTL_SECONDS", "300")))
        self._session_memory_cache: Dict[str, Dict[str, Any]] = {}
        self._session_memory_cache_max = max(64, int(os.getenv("SESSION_MEMORY_CACHE_MAX", "512")))
        self._session_tool_usage_lock = threading.Lock()
        self._session_tool_usage: Dict[str, int] = {}

    def _slug(self, value: Optional[str]) -> str:
        """Create a filesystem-safe slug."""
        if not value:
            return "default"
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-").lower()
        return slug or "default"

    def get_bot_model_files_dir(self, bot: Bot) -> str:
        """Get model-specific files directory for a bot."""
        path = standalone_model_service.model_files_dir(bot.id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def ensure_bot_storage(self, bot: Bot) -> None:
        """Ensure all storage directories exist for a bot."""
        standalone_model_service.ensure_bot_layout(bot.id)
        self.embedding_service.get_documents_dir(bot.id)
        self.embedding_service.get_embeddings_dir(bot.id)
        self.get_bot_sessions_dir(bot.id)
        self.get_bot_model_files_dir(bot)
        standalone_model_service.write_bot_config(bot)

    def get_bot_sessions_dir(self, bot_id: str) -> Path:
        """Get the sessions directory for a bot."""
        sessions_dir = standalone_model_service.transcripts_dir(bot_id)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        return sessions_dir

    def create_session_id(self) -> str:
        """Create a server-issued unique session identifier."""
        return str(uuid.uuid4())

    def _session_file_name(self, session_id: str) -> str:
        """Create a stable and filesystem-safe filename for a session."""
        safe_session = self._slug(session_id)[:80]
        digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:12]
        return f"{safe_session}_{digest}.json"

    def get_session_file_path(self, bot_id: str, session_id: str) -> Path:
        """Get the JSON transcript path for a session."""
        return self.get_bot_sessions_dir(bot_id) / self._session_file_name(session_id)

    def _session_memory_cache_key(self, bot_id: str, session_id: str) -> str:
        return f"{bot_id}:{session_id}"

    def _get_cached_session_memory(self, bot_id: str, session_id: str) -> Optional[List[Dict[str, Any]]]:
        cache_key = self._session_memory_cache_key(bot_id, session_id)
        with self._session_memory_lock:
            cached = self._session_memory_cache.get(cache_key)
            if cached is None:
                return None
            cached["last_accessed"] = time.monotonic()
            return list(cached.get("messages") or [])

    def _set_cached_session_memory(
        self,
        bot_id: str,
        session_id: str,
        messages: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        cache_key = self._session_memory_cache_key(bot_id, session_id)
        normalized = [dict(message) for message in (messages or [])]
        with self._session_memory_lock:
            self._session_memory_cache[cache_key] = {
                "messages": normalized,
                "last_accessed": time.monotonic(),
            }
            if len(self._session_memory_cache) > self._session_memory_cache_max:
                oldest_key = min(
                    self._session_memory_cache.items(),
                    key=lambda item: item[1].get("last_accessed", 0.0),
                )[0]
                if oldest_key != cache_key:
                    self._session_memory_cache.pop(oldest_key, None)
        return [dict(message) for message in normalized]

    def _track_background_task(self, task: asyncio.Task) -> None:
        pass

    def _handle_background_task_result(self, task: asyncio.Task) -> None:
        return

    async def _persist_memory_async(
        self,
        *,
        bot_id: str,
        session_id: str,
        messages: List[Dict[str, Any]],
    ) -> None:
        await asyncio.to_thread(
            standalone_model_service.persist_memory_messages,
            bot_id,
            session_id,
            list(messages or []),
        )

    async def _append_session_transcript_async(self, **kwargs) -> None:
        await asyncio.to_thread(self._append_session_transcript, **kwargs)

    def set_startup_welcome_message(self, bot_id: str, message: str) -> None:
        """Store latest startup-generated welcome message for quick UI retrieval."""
        clean_message = (message or "").strip()
        if not clean_message:
            return

        with self._startup_welcome_lock:
            self._startup_welcome_cache[bot_id] = {
                "message": clean_message,
                "generated_at": datetime.utcnow().isoformat() + "Z",
            }

    def get_startup_welcome_message(self, bot_id: str) -> Dict[str, Any]:
        """Get startup-generated welcome message metadata for a bot."""
        with self._startup_welcome_lock:
            payload = dict(self._startup_welcome_cache.get(bot_id) or {})

        message = (payload.get("message") or "").strip()
        return {
            "ready": bool(message),
            "message": message or None,
            "generated_at": payload.get("generated_at"),
        }

    def _get_cached_bot(self, bot_id: str) -> Optional[Bot]:
        now = time.monotonic()
        with self._bot_cache_lock:
            cached = self._bot_cache.get(bot_id)
            if not cached:
                return None
            if now - float(cached.get("stored_at", 0.0)) > self._bot_cache_ttl_seconds:
                self._bot_cache.pop(bot_id, None)
                return None
            cached["last_accessed"] = now
            return copy.copy(cached["bot"])

    def _set_cached_bot(self, bot: Bot) -> Bot:
        with self._bot_cache_lock:
            self._bot_cache[bot.id] = {
                "bot": copy.copy(bot),
                "stored_at": time.monotonic(),
                "last_accessed": time.monotonic(),
            }
        return copy.copy(bot)

    def _invalidate_bot_cache(self, bot_id: str) -> None:
        with self._bot_cache_lock:
            self._bot_cache.pop(bot_id, None)

    def _invalidate_provider_cache(self, bot_id: str) -> None:
        stale_keys = [key for key in self._provider_cache.keys() if str(key).startswith(f"{bot_id}_")]
        for key in stale_keys:
            self._provider_cache.pop(key, None)

    def _tool_usage_cache_key(self, bot_id: str, session_id: str) -> str:
        return f"{bot_id}:{session_id}"

    def _get_session_tool_usage(self, bot_id: str, session_id: str) -> int:
        with self._session_tool_usage_lock:
            return int(self._session_tool_usage.get(self._tool_usage_cache_key(bot_id, session_id), 0))

    def _increment_session_tool_usage(self, bot_id: str, session_id: str, amount: int = 1) -> int:
        cache_key = self._tool_usage_cache_key(bot_id, session_id)
        with self._session_tool_usage_lock:
            self._session_tool_usage[cache_key] = int(self._session_tool_usage.get(cache_key, 0)) + max(0, int(amount))
            return self._session_tool_usage[cache_key]

    def _normalize_bot_tool_state(self, bot: Bot) -> bool:
        normalized_tool_settings = normalize_tool_settings(
            bot.tool_settings_json,
            fallback_enabled=bool(bot.tools_enabled),
        )
        normalized_quick_chip_settings = normalize_quick_chip_settings(bot.quick_chips_json)
        normalized_allowed_tools = normalize_tool_definitions(bot.allowed_tools_json)

        changed = False
        if normalized_allowed_tools != list(bot.allowed_tools_json or []):
            bot.allowed_tools_json = normalized_allowed_tools
            changed = True
        if normalized_tool_settings != dict(bot.tool_settings_json or {}):
            bot.tool_settings_json = normalized_tool_settings
            changed = True
        if normalized_quick_chip_settings != dict(bot.quick_chips_json or {}):
            bot.quick_chips_json = normalized_quick_chip_settings
            changed = True
        if bool(bot.tools_enabled) != bool(normalized_tool_settings.get("enabled")):
            bot.tools_enabled = bool(normalized_tool_settings.get("enabled"))
            changed = True
        return changed

    def _estimate_tokens(self, text: Optional[str]) -> int:
        clean = re.sub(r"\s+", " ", (text or "").strip())
        if not clean:
            return 0
        word_estimate = int(len(clean.split()) * 1.3)
        char_estimate = max(1, len(clean) // 4)
        return max(word_estimate, char_estimate)

    def _trim_text_to_token_budget(self, text: Optional[str], budget_tokens: int) -> str:
        clean = re.sub(r"\s+", " ", (text or "").strip())
        if budget_tokens <= 0 or not clean:
            return ""
        if self._estimate_tokens(clean) <= budget_tokens:
            return clean

        words = clean.split()
        if not words:
            return ""

        trimmed: List[str] = []
        for word in words:
            candidate = " ".join(trimmed + [word]).strip()
            if self._estimate_tokens(candidate) > budget_tokens:
                break
            trimmed.append(word)

        if not trimmed:
            approx_chars = max(12, budget_tokens * 4)
            return clean[:approx_chars].rstrip(" ,.;:") + "..."

        shortened = " ".join(trimmed).rstrip(" ,.;:")
        if shortened != clean:
            shortened += "..."
        return shortened

    def _split_sentences(self, text: Optional[str]) -> List[str]:
        clean = (text or "").strip()
        if not clean:
            return []
        pieces = re.split(r"(?<=[.!?])\s+|\n{2,}", clean)
        sentences = [re.sub(r"\s+", " ", piece).strip() for piece in pieces if piece and piece.strip()]
        return sentences or [re.sub(r"\s+", " ", clean).strip()]

    def _is_low_value_assistant_message(self, text: Optional[str]) -> bool:
        lower = re.sub(r"\s+", " ", (text or "").strip()).lower()
        if not lower:
            return True
        weak_patterns = [
            "hello! how can i assist you today",
            "how can i assist you today",
            "how can i help you today",
            "i'm sorry, but i'm not sure what you're asking",
            "could you please provide more information or context",
            "could you please provide more information",
            "i'm not sure what you're asking",
            "please provide more context",
        ]
        if any(pattern in lower for pattern in weak_patterns):
            return True
        if len(lower.split()) <= 8 and lower in {
            "hello",
            "hi",
            "hello!",
            "hi!",
            "how can i help?",
            "how can i help you?",
        }:
            return True
        return False

    def _is_low_value_user_message(self, text: Optional[str]) -> bool:
        lower = re.sub(r"\s+", " ", (text or "").strip()).lower()
        if not lower:
            return True
        if lower in {"hi", "hello", "hey", "sup", "yo", "good morning", "good afternoon"}:
            return True
        return False

    def _is_capability_query(self, text: Optional[str]) -> bool:
        lower = re.sub(r"\s+", " ", (text or "").strip()).lower()
        if not lower:
            return False
        patterns = [
            r"\bwhat can you do\b",
            r"\bwhat do you do\b",
            r"\bwhat do you know\b",
            r"\bhow can you help\b",
            r"\bwhat can you help me with\b",
            r"\bwhat services\b",
            r"\bwhat are your services\b",
            r"\btell me about\b",
            r"\btell me about .* services\b",
            r"\bwhat can you tell me about\b",
            r"\bwhat can you tell me about .* services\b",
            r"\bwhat does .* do\b",
            r"\bwhat services does .* offer\b",
            r"\babout .* services\b",
            r"\bwho are you\b",
        ]
        return any(re.search(pattern, lower) for pattern in patterns)

    def _is_service_overview_query(self, text: Optional[str]) -> bool:
        lower = re.sub(r"\s+", " ", (text or "").strip()).lower()
        if not lower:
            return False
        if "service" in lower or "services" in lower:
            if any(
                phrase in lower
                for phrase in (
                    "tell me about",
                    "what can you tell me about",
                    "what services",
                    "what does",
                    "what do you know about",
                    "offer",
                )
            ):
                return True
        return bool(re.search(r"\bwhat does [a-z0-9 .,&'-]+ do\b", lower))

    def _sanitize_summary_content(self, summary: Optional[str]) -> Optional[str]:
        clean_summary = re.sub(r"^Conversation summary:\s*", "", (summary or "").strip(), flags=re.IGNORECASE)
        if not clean_summary:
            return None
        fragments = [fragment.strip() for fragment in clean_summary.split("|") if fragment and fragment.strip()]
        retained: List[str] = []
        for fragment in fragments:
            normalized = re.sub(r"^(User|Assistant):\s*", "", fragment, flags=re.IGNORECASE).strip()
            if self._is_low_value_assistant_message(normalized) or self._is_low_value_user_message(normalized):
                continue
            retained.append(fragment)
        if not retained:
            return None
        return " | ".join(retained)

    def _query_terms(self, query: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9_]{3,}", (query or "").lower())
            if token not in {"that", "this", "with", "from", "your", "have", "what", "when", "where"}
        }

    def _source_priority_score(
        self,
        raw_source: Optional[str],
        *,
        query: str = "",
        capability_mode: bool = False,
    ) -> float:
        normalized = (raw_source or "").strip().lower().replace("\\", "/")
        if not normalized:
            return 0.0

        score = 0.0
        if "example.com" in normalized:
            score -= 8.0

        high_priority_markers = (
            "/about",
            "/services",
            "/service",
            "/products",
            "/solutions",
            "/case-studies",
            "/pricing",
            "/our-work",
            "/cro",
            "/seo",
            "/content-strategy",
            "/hubspot-email-marketing",
            "/hubspot-integration",
        )
        medium_priority_markers = (
            "/contact",
            "/team",
            "/blog/",
        )
        low_priority_markers = (
            "/tag/",
            "/category/",
            "/page/",
            "/author/",
            "/privacy-policy",
            "/terms",
            "/cookie",
        )

        if any(marker in normalized for marker in low_priority_markers):
            score -= 4.0
        if normalized.rstrip("/").endswith("/blog"):
            score -= 2.5
        if any(marker in normalized for marker in medium_priority_markers):
            score += 0.75
        if any(marker in normalized for marker in high_priority_markers):
            score += 3.0

        if capability_mode:
            if any(marker in normalized for marker in high_priority_markers):
                score += 2.0
            if "/blog" in normalized or "/author/" in normalized:
                score -= 2.0

        query_terms = self._query_terms(query)
        if query_terms:
            score += sum(0.3 for term in query_terms if term in normalized)
        return score

    def _prioritize_search_results(
        self,
        query: str,
        search_results: List[Tuple[str, float, Dict[str, Any]]],
        *,
        capability_mode: bool = False,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        if not search_results:
            return []
        return sorted(
            search_results,
            key=lambda item: (
                float(item[1]) + self._source_priority_score(
                    (item[2] or {}).get("source"),
                    query=query,
                    capability_mode=capability_mode,
                ),
                float(item[1]),
            ),
            reverse=True,
        )

    def _is_summary_message(self, message: Dict[str, Any]) -> bool:
        content = str(message.get("content") or "")
        return bool(message.get("summary")) or content.startswith("Conversation summary:")

    def _summarize_history_entry(self, message: Dict[str, Any]) -> str:
        role = str(message.get("role") or "assistant").capitalize()
        content = self._trim_text_to_token_budget(str(message.get("content") or ""), 24)
        if not content:
            return ""
        return f"{role}: {content}"

    def _build_rolling_summary(
        self,
        existing_summary: Optional[str],
        older_messages: List[Dict[str, Any]],
    ) -> Optional[str]:
        fragments: List[str] = []
        clean_existing = self._sanitize_summary_content(existing_summary)
        if clean_existing:
            fragments.append(clean_existing)
        for message in older_messages:
            summary_line = self._summarize_history_entry(message)
            if summary_line:
                fragments.append(summary_line)
        if not fragments:
            return None
        body = " | ".join(fragments)
        body = self._trim_text_to_token_budget(body, self.chat_memory_summary_token_budget)
        if not body:
            return None
        return f"Conversation summary: {body}"

    def _compact_memory_messages(self, messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        existing_summary: Optional[str] = None
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            content = re.sub(r"\s+", " ", str(message.get("content") or "")).strip()
            if not content:
                continue
            if self._is_summary_message(message):
                if existing_summary:
                    existing_summary = f"{existing_summary} | {content}"
                else:
                    existing_summary = content
                continue
            if role not in {"user", "assistant"}:
                continue
            if role == "assistant" and self._is_low_value_assistant_message(content):
                continue
            if role == "user" and self._is_low_value_user_message(content):
                continue
            normalized.append({"role": role, "content": content})

        keep_count = max(0, self.chat_memory_turns * 2)
        recent = normalized[-keep_count:] if keep_count else []
        older = normalized[:-keep_count] if keep_count else normalized
        summary_message = self._build_rolling_summary(existing_summary, older)

        compacted: List[Dict[str, Any]] = []
        if summary_message:
            compacted.append({"role": "system", "content": summary_message, "summary": True})
        compacted.extend(recent)
        return compacted

    def _build_memory_prompt_messages(self, memory_history: List[Dict[str, Any]]) -> List[ChatMessage]:
        compacted = self._compact_memory_messages(memory_history)
        summary_content: Optional[str] = None
        recent_messages: List[Dict[str, Any]] = []
        for message in compacted:
            if self._is_summary_message(message):
                summary_content = str(message.get("content") or "")
                continue
            recent_messages.append(message)

        prompt_messages: List[ChatMessage] = []
        keep_count = max(0, self.chat_memory_turns * 2)
        recent_messages = recent_messages[-keep_count:] if keep_count else []
        reserved_recent_budget = min(
            self.chat_memory_token_budget,
            max(self.chat_recent_message_token_budget * len(recent_messages), self.chat_memory_token_budget // 2),
        )
        recent_budget_used = 0
        trimmed_recent: List[ChatMessage] = []
        for message in recent_messages:
            remaining_recent_budget = max(0, reserved_recent_budget - recent_budget_used)
            per_message_budget = min(self.chat_recent_message_token_budget, remaining_recent_budget)
            if per_message_budget <= 0:
                break
            trimmed_content = self._trim_text_to_token_budget(message.get("content"), per_message_budget)
            if not trimmed_content:
                continue
            recent_budget_used += self._estimate_tokens(trimmed_content)
            trimmed_recent.append(ChatMessage(role=message["role"], content=trimmed_content))

        summary_budget = max(0, self.chat_memory_token_budget - recent_budget_used)
        if summary_content and summary_budget > 0:
            trimmed_summary = self._trim_text_to_token_budget(summary_content, summary_budget)
            if trimmed_summary:
                prompt_messages.append(ChatMessage(role="system", content=trimmed_summary))

        prompt_messages.extend(trimmed_recent)
        return prompt_messages

    def _score_retrieval_span(self, span: str, query_terms: set[str], chunk_score: float, query: str) -> float:
        lowered_span = span.lower()
        overlap = sum(1 for term in query_terms if term in lowered_span)
        overlap_score = overlap / max(1, len(query_terms))
        phrase_bonus = 0.2 if query and query.lower() in lowered_span else 0.0
        return float(chunk_score) + overlap_score + phrase_bonus

    def _extract_retrieval_context(
        self,
        query: str,
        search_results: List[Tuple[str, float, Dict[str, Any]]],
    ) -> Tuple[str, float]:
        query_terms = self._query_terms(query)
        best_score = max((float(score) for _, score, _ in search_results), default=0.0)
        if not search_results:
            return "", best_score

        snippets: List[str] = []
        budget_remaining = self.chat_rag_token_budget

        for text, score, metadata in search_results:
            if score < self.chat_rag_min_score:
                continue
            spans = self._split_sentences(text)
            scored_spans = sorted(
                (
                    (self._score_retrieval_span(span, query_terms, float(score), query), span)
                    for span in spans
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            selected_spans: List[str] = []
            per_chunk_budget = min(self.chat_rag_snippet_token_budget * 2, budget_remaining)
            per_chunk_remaining = per_chunk_budget
            for _, span in scored_spans:
                trimmed_span = self._trim_text_to_token_budget(span, min(self.chat_rag_snippet_token_budget, per_chunk_remaining))
                span_tokens = self._estimate_tokens(trimmed_span)
                if span_tokens < self.chat_rag_min_snippet_tokens:
                    continue
                if span_tokens > per_chunk_remaining or span_tokens > budget_remaining:
                    continue
                selected_spans.append(trimmed_span)
                per_chunk_remaining -= span_tokens
                budget_remaining -= span_tokens
                if len(selected_spans) >= 2 or budget_remaining <= self.chat_rag_min_snippet_tokens:
                    break
            if not selected_spans:
                continue
            source = metadata.get("source", "unknown")
            snippets.append(
                f"[Source: {source} | score={float(score):.2f}]\n- " + "\n- ".join(selected_spans)
            )
            if budget_remaining <= self.chat_rag_min_snippet_tokens:
                break

        return "\n\n".join(snippets), best_score

    def _classify_task(self, message: str, retrieval_confidence: float) -> Tuple[str, bool]:
        return router_service.classify_task(message, retrieval_confidence)

    def _build_capability_lookup_query(self, bot: Bot) -> str:
        prompt = re.sub(r"\s+", " ", (bot.system_prompt or "").strip()).lower()
        prompt_terms: List[str] = []
        for token in re.findall(r"[a-z0-9]{4,}", prompt):
            if token in {
                "helpful",
                "assistant",
                "please",
                "customer",
                "support",
                "within",
                "scope",
                "their",
                "with",
                "that",
                "this",
                "from",
                "your",
                "hubbase",
            }:
                continue
            if token not in prompt_terms:
                prompt_terms.append(token)
            if len(prompt_terms) >= 8:
                break
        seed_terms = " ".join(prompt_terms[:8])
        return (
            f"{bot.name} services capabilities expertise offerings solutions pricing "
            f"about integrations email marketing content strategy seo cro {seed_terms}"
        ).strip()

    def _build_capability_system_hint(self, bot: Bot) -> str:
        prompt_seed = self._trim_text_to_token_budget(bot.system_prompt or "", 120)
        return (
            "If the user asks what you can do, what services you offer, or what you know, "
            "answer directly with concrete capabilities and service areas. "
            "Do not say the request lacks context. "
            f"Known business profile: {prompt_seed or bot.name}."
        )

    def _company_unknown_fallback(self) -> str:
        return (
            "I don't have the specific details on that right now. "
            "I recommend checking our General Contact Page to speak with a specialist."
        )

    def _build_company_voice_system_hint(self, bot: Bot) -> str:
        company_name = (bot.name or "the company").strip()
        return (
            f"You are the Official AI Concierge for {company_name}. "
            "You are an integrated part of the team, not a third-party scraper, researcher, or analyst. "
            "Speak with calm authority in a professional, helpful tone. "
            "Always answer like a chatbot speaking directly to the user, not like a drafted email or letter. "
            "Do not use greetings like 'Dear ...', sign-offs like 'Best regards', or placeholders like '[User's Name]' or '[Your Full Name]' unless the user explicitly asks you to draft a message template. "
            "For contact, meeting, booking, or escalation requests, prefer 1-3 short chat-ready sentences that directly share the next step. "
            "Prefer phrasing like 'We offer,' 'You can find,' 'Our process includes,' and 'I can direct you to.' "
            "When structure helps, use clean markdown formatting: numbered steps, bullet lists, bold labels, and fenced code blocks. "
            "Do not say 'I found,' 'Based on the scrape,' 'According to the data,' 'The website says,' "
            "'knowledge base,' 'raw file,' 'chunk,' or similar source-discovery language in your main response. "
            "Treat references as optional follow-up material, not proof of how you know the answer. "
            "If a detail is unavailable, say exactly: "
            f"'{self._company_unknown_fallback()}' "
            "When sharing links, format them as markdown like [Link Text](URL)."
        )

    def _apply_company_voice_guardrails(
        self,
        bot: Bot,
        response_text: str,
        *,
        user_message: str = "",
        retrieval_context: str = "",
    ) -> str:
        text = (response_text or "").strip()
        if not text:
            return self._company_unknown_fallback()

        lower = text.lower()
        unknown_markers = [
            "i don't have that information in my knowledge base",
            "the scrape failed",
            "based on the scrape",
            "i'm not sure what you mean",
            "i do not have the specific information",
        ]
        if any(marker in lower for marker in unknown_markers):
            return self._company_unknown_fallback()

        substitutions = [
            (r"(?i)\bbased on the current knowledge base,\s*", ""),
            (r"(?i)\bbased on the provided context,\s*", ""),
            (r"(?i)\bbased on the scrape,\s*", ""),
            (r"(?i)\baccording to the data,\s*", ""),
            (r"(?i)\baccording to the website,\s*", ""),
            (r"(?i)\bthe website says(?: that)?\s*", ""),
            (r"(?i)\bi found that\s*", ""),
            (r"(?i)\bi found\s*", ""),
        ]
        for pattern, replacement in substitutions:
            text = re.sub(pattern, replacement, text).strip()

        if not text:
            return self._company_unknown_fallback()

        text = self._enforce_verified_contact_details(
            text,
            user_message=user_message,
            retrieval_context=retrieval_context,
        )
        if not text:
            return self._company_unknown_fallback()

        if self._looks_like_formal_email_response(text):
            text = self._rewrite_formal_contact_response(text)

        return text[0].upper() + text[1:] if text else self._company_unknown_fallback()

    def _extract_emails_from_text(self, text: str) -> List[str]:
        return re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text or "", flags=re.IGNORECASE)

    def _enforce_verified_contact_details(
        self,
        text: str,
        *,
        user_message: str = "",
        retrieval_context: str = "",
    ) -> str:
        response_emails = self._extract_emails_from_text(text)
        if not response_emails:
            return text

        allowed_emails: List[str] = []
        for email in self._extract_emails_from_text(f"{user_message}\n{retrieval_context}"):
            if email.lower() not in {item.lower() for item in allowed_emails}:
                allowed_emails.append(email)

        if not allowed_emails:
            return "I don’t have a verified contact email to share here yet. Use the Talk with us option below and I’ll point you to the right person."

        replacement_email = allowed_emails[0]
        return re.sub(
            r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
            replacement_email,
            text,
            flags=re.IGNORECASE,
        )

    def _looks_like_formal_email_response(self, text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        formal_markers = [
            "dear ",
            "best regards",
            "kind regards",
            "sincerely",
            "[user's name]",
            "[your full name]",
            "i hope this message finds you well",
        ]
        if any(marker in lowered for marker in formal_markers):
            return True
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if len(lines) >= 4 and lines[0].lower().startswith("dear "):
            return True
        return False

    def _rewrite_formal_contact_response(self, text: str) -> str:
        emails = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text or "", flags=re.IGNORECASE)
        unique_emails: List[str] = []
        for email in emails:
            normalized = email.strip()
            if normalized and normalized.lower() not in {item.lower() for item in unique_emails}:
                unique_emails.append(normalized)

        if unique_emails:
            if len(unique_emails) == 1:
                return f"You can contact us at {unique_emails[0]} to set up a meeting."
            return "You can contact us at " + ", ".join(unique_emails) + " to set up a meeting."

        cleaned_lines: List[str] = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if lowered.startswith("dear "):
                continue
            if lowered in {"best regards,", "best regards", "kind regards,", "kind regards", "sincerely,", "sincerely"}:
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                continue
            cleaned_lines.append(stripped)

        cleaned_text = " ".join(cleaned_lines)
        cleaned_text = re.sub(r"\[[^\]]+\]", "", cleaned_text).strip()
        cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
        sentences = re.split(r"(?<=[.!?])\s+", cleaned_text)
        concise = " ".join(sentence.strip() for sentence in sentences[:2] if sentence.strip())
        return concise or self._company_unknown_fallback()

    def _extract_capability_labels(self, retrieval_context: str) -> List[str]:
        lower_context = (retrieval_context or "").lower()
        service_map = [
            ("hubspot integration", "HubSpot integrations"),
            ("integration", "custom integrations"),
            ("workflow", "workflow automation"),
            ("slack", "Slack to HubSpot integrations"),
            ("seo", "SEO improvements"),
            ("cro", "conversion rate optimization"),
            ("content strategy", "content strategy"),
            ("email marketing", "HubSpot email marketing"),
            ("sequences", "email sequences and workflows"),
            ("automation", "marketing automation"),
            ("hubspot cms", "HubSpot CMS development"),
            ("hubspot developer", "HubSpot development support"),
            ("api", "custom API work"),
        ]
        capabilities: List[str] = []
        for needle, label in service_map:
            if needle in lower_context and label not in capabilities:
                capabilities.append(label)

        if capabilities:
            return capabilities

        snippet_lines = [
            re.sub(r"^\-\s*", "", line).strip()
            for line in (retrieval_context or "").splitlines()
            if line.strip().startswith("-")
        ]
        return [self._trim_text_to_token_budget(line, 18) for line in snippet_lines[:3] if line.strip()]

    def _has_repetitive_response_pattern(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", (text or "").strip()).lower()
        if not normalized:
            return False
        repeated_phrases = [
            phrase
            for phrase in re.findall(r"[a-z0-9][a-z0-9 '&/-]{12,}", normalized)
            if len(phrase.split()) >= 2
        ]
        counts: Dict[str, int] = {}
        for phrase in repeated_phrases:
            counts[phrase] = counts.get(phrase, 0) + 1
        return any(count >= 3 for count in counts.values())

    def _is_low_value_capability_response(
        self,
        text: Optional[str],
        retrieval_context: str = "",
        user_message: str = "",
    ) -> bool:
        lower = re.sub(r"\s+", " ", (text or "").strip()).lower()
        if not lower:
            return True
        weak_markers = [
            "i don't have any knowledge",
            "i do not have any knowledge",
            "i don't have any specific information",
            "i do not have any specific information",
            "i'm not sure what you're asking about",
            "i am not sure what you are asking about",
            "i'm sorry, but i'm not able to assist with that",
            "i am sorry, but i am not able to assist with that",
            "i'm sorry, but i can't assist with that",
            "i am sorry, but i can't assist with that",
            "i'm sorry, but i cannot assist with that",
            "i am sorry, but i cannot assist with that",
            "i can't assist with that",
            "i cannot assist with that",
            "i'm unable to assist with that",
            "i am unable to assist with that",
            "i'm sorry",
            "please provide more details",
            "please provide more information",
            "i'm here to help with any questions",
            "i'm here to help you with any questions",
            "i can help you with any questions",
            "i can provide information about our services",
            "our products and services",
            "general assistance",
        ]
        if any(marker in lower for marker in weak_markers):
            return True
        if self._has_repetitive_response_pattern(lower):
            return True

        capabilities = self._extract_capability_labels(retrieval_context)
        if not capabilities:
            return False

        capability_terms = {
            token
            for capability in capabilities
            for token in re.findall(r"[a-z0-9]{4,}", capability.lower())
            if token not in {"hubspot", "support"}
        }
        response_terms = {
            token
            for token in re.findall(r"[a-z0-9]{4,}", lower)
            if token not in {"about", "that", "with", "from", "your", "help"}
        }

        if capability_terms and capability_terms.isdisjoint(response_terms):
            about_query = bool(re.search(r"\bwhat do you know about\b|\btell me about\b", (user_message or "").lower()))
            generic_response = len(response_terms) <= 10 or "assist" in response_terms or "information" in response_terms
            if about_query or generic_response:
                return True

        return False

    def _build_capability_response(self, bot: Bot, retrieval_context: str, user_message: str) -> str:
        capabilities = self._extract_capability_labels(retrieval_context)
        service_query = self._is_service_overview_query(user_message)

        if re.search(r"\bwhat do you know about\b|\btell me about\b", (user_message or "").lower()):
            intro = f"{bot.name} focuses on HubSpot-related implementation and growth work."
        elif service_query:
            intro = f"{bot.name} offers HubSpot-focused services."
        else:
            intro = f"{bot.name} can help with practical HubSpot-focused work."

        if capabilities:
            if len(capabilities) == 1:
                detail = capabilities[0]
            elif len(capabilities) == 2:
                detail = f"{capabilities[0]} and {capabilities[1]}"
            else:
                detail = ", ".join(capabilities[:-1]) + f", and {capabilities[-1]}"
            return (
                f"{intro} We offer support across {detail}. "
                "If you want, I can explain one of those areas in more detail or help you scope the next step."
            )
        return (
            f"{intro} We can help with services, delivery scope, "
            "integrations, optimization work, and HubSpot-related implementation."
        )

    def _source_label(self, raw_source: Optional[str]) -> str:
        source = (raw_source or "").strip()
        if not source:
            return "Knowledge Base"
        normalized = source.replace("\\", "/").strip()
        if re.match(r"^https?://", normalized, flags=re.IGNORECASE):
            try:
                from urllib.parse import urlparse

                parsed = urlparse(normalized)
                host = (parsed.netloc or "").replace("www.", "")
                path = (parsed.path or "").strip("/")
                if path:
                    return f"{host}/{path}"[:64]
                if host:
                    return host[:64]
            except Exception:
                pass
        parts = [part for part in normalized.split("/") if part]
        if parts:
            return parts[-1][:64]
        return source[:64]

    def _infer_context_scope(self, text: str, source: str) -> str:
        lowered = f"{text}\n{source}".lower()
        if any(term in lowered for term in ["hubl", "hubspot module", "module html", "module field"]):
            return "HubL"
        if any(term in lowered for term in ["module css", "responsive css", "stylesheet", " css "]):
            return "Module CSS"
        if any(term in lowered for term in ["theme json", "fields.json", "theme settings", " theme "]):
            return "Theme JSON"
        if any(term in lowered for term in ["crm", "object mapping", "property mapping", "pipeline"]):
            return "CRM Object Mapping"
        if any(term in lowered for term in ["hubspot cms", "cms", "template", "hubspot developer"]):
            return "HubSpot CMS"
        return "HubSpot KB"

    def _summarize_retrieval_metadata(
        self,
        search_results: List[Tuple[str, float, Dict[str, Any]]],
    ) -> Tuple[Optional[str], List[str], List[Dict[str, Any]]]:
        if not search_results:
            return None, [], []

        citations: List[Dict[str, Any]] = []
        scope_counts: Dict[str, int] = {}
        source_labels: List[str] = []
        seen_sources: set[str] = set()

        for text, score, metadata in search_results[:4]:
            raw_source = str((metadata or {}).get("source") or "Knowledge Base")
            label = self._source_label(raw_source)
            if label and label not in seen_sources:
                seen_sources.add(label)
                source_labels.append(label)

            scope = self._infer_context_scope(text or "", raw_source)
            scope_counts[scope] = scope_counts.get(scope, 0) + 1
            citations.append(
                {
                    "label": label,
                    "scope": scope,
                    "snippet": self._trim_text_to_token_budget(re.sub(r"\s+", " ", (text or "").strip()), 36),
                    "score": round(float(score), 3),
                }
            )

        dominant_scope = max(scope_counts.items(), key=lambda item: item[1])[0] if scope_counts else None
        return dominant_scope, source_labels[:4], citations

    def _provider_candidates(self, bot: Bot, task_class: str) -> List[Tuple[str, Optional[str]]]:
        return router_service.provider_candidates(bot, task_class)

    def _resolve_rag_provider(self, bot: Bot, configured_provider: Optional[BaseLLMProvider]) -> Optional[BaseLLMProvider]:
        if configured_provider is not None:
            return configured_provider

        local_model = os.getenv("ROUTER_LOCAL_MODEL", "qwen2.5:1.5b")
        candidate_order: List[Tuple[str, Optional[str]]] = [
            ("local", local_model),
            (bot.provider_type, bot.model),
            ("gemini", os.getenv("ROUTER_GEMINI_MODEL", "gemini-2.5-flash")),
            ("openai", os.getenv("ROUTER_OPENAI_BALANCED_MODEL", "gpt-4.1-mini")),
            ("claude", os.getenv("ROUTER_CLAUDE_PREMIUM_MODEL", "claude-sonnet-4-20250514")),
        ]
        seen: set[Tuple[str, Optional[str]]] = set()
        for provider_type, model_name in candidate_order:
            key = (provider_type, model_name)
            if key in seen:
                continue
            seen.add(key)
            try:
                return self._get_provider(bot, provider_type=provider_type, model=model_name)
            except Exception:
                continue
        return None

    def _select_routing_decision(
        self,
        bot: Bot,
        message: str,
        retrieval_confidence: float,
    ) -> RoutingDecision:
        return router_service.select_route(bot, message, retrieval_confidence)

    def _is_deterministic_prompt_failure(self, error_message: str) -> bool:
        text = (error_message or "").lower()
        deterministic_markers = [
            "maximum context length",
            "context length",
            "prompt too long",
            "too many tokens",
            "request too large",
            "invalid request",
            "invalid message",
            "token limit",
            "413",
        ]
        return any(marker in text for marker in deterministic_markers)

    def _is_temporarily_unavailable_error(self, error_message: str) -> bool:
        text = (error_message or "").lower()
        unavailable_markers = [
            "timeout",
            "timed out",
            "connection error",
            "temporarily unavailable",
            "service unavailable",
            "resource exhausted",
            "breaker",
            "429",
            "502",
            "503",
        ]
        return any(marker in text for marker in unavailable_markers)

    def _is_provider_configuration_error(self, error_message: str) -> bool:
        text = (error_message or "").lower()
        config_markers = [
            "api key is required",
            "provider is not configured",
            "not configured for routed execution",
            "missing api key",
            "api_key client option must be set",
            "openai_api_key environment variable",
            "authentication credentials were not provided",
        ]
        return any(marker in text for marker in config_markers)

    def _create_provider(
        self,
        provider_type: str,
        *,
        api_key: Optional[str],
        model: Optional[str],
    ) -> BaseLLMProvider:
        if provider_type == "openai":
            return OpenAIProvider(api_key=api_key, model=model)
        if provider_type == "local":
            return LocalProvider(api_key=api_key, model=model)
        if provider_type == "claude":
            return ClaudeProvider(api_key=api_key, model=model)
        if provider_type == "gemini":
            return GeminiProvider(api_key=api_key, model=model)
        raise ValueError(f"Unknown provider type: {provider_type}")

    def _get_provider(
        self,
        bot: Bot,
        *,
        provider_type: Optional[str] = None,
        model: Optional[str] = None,
    ) -> BaseLLMProvider:
        """
        Get or create LLM provider for a bot.
        
        Args:
            bot: Bot object
            
        Returns:
            LLM provider instance
        """
        resolved_provider_type = provider_type or bot.provider_type
        resolved_model = model or (bot.model if resolved_provider_type == bot.provider_type else None)
        cache_key = f"{bot.id}_{resolved_provider_type}_{resolved_model or 'default'}"
        now = time.monotonic()
        cached_entry = self._provider_cache.get(cache_key)
        if cached_entry and (now - float(cached_entry.get("stored_at", 0.0)) <= self._provider_cache_ttl_seconds):
            cached_entry["last_accessed"] = now
            return cached_entry["provider"]

        if cached_entry is None or (now - float(cached_entry.get("stored_at", 0.0)) > self._provider_cache_ttl_seconds):
            # Decrypt API key if present
            api_key = None
            if bot.api_key_encrypted and resolved_provider_type == bot.provider_type:
                try:
                    api_key = encryption_service.decrypt(bot.api_key_encrypted)
                except Exception:
                    api_key = bot.api_key_encrypted  # Maybe not encrypted

            provider_instance = self._create_provider(
                resolved_provider_type,
                api_key=api_key,
                model=resolved_model,
            )
            if not provider_instance.validate_config():
                raise ValueError(
                    f"Provider '{resolved_provider_type}' is not configured for routed execution"
                )
            self._provider_cache[cache_key] = provider_instance
        provider_instance = self._provider_cache[cache_key]
        self._provider_cache[cache_key] = {
            "provider": provider_instance,
            "stored_at": now,
            "last_accessed": now,
        }
        return self._provider_cache[cache_key]["provider"]

    async def get_bot_async(self, db: AsyncSession, bot_id: str) -> Optional[Bot]:
        cached_bot = self._get_cached_bot(bot_id)
        if cached_bot is not None:
            return cached_bot
        result = await db.execute(select(Bot).filter(Bot.id == bot_id))
        bot = result.scalar_one_or_none()
        if not bot:
            return None
        if self._normalize_bot_tool_state(bot):
            await db.commit()
            await db.refresh(bot)
        db.expunge(bot)
        return self._set_cached_bot(bot)

    async def _load_memory_messages(
        self,
        db: Session | AsyncSession,
        bot_id: str,
        session_id: str,
    ) -> List[Dict[str, Any]]:
        local_messages = standalone_model_service.load_memory_messages(bot_id, session_id)
        if local_messages:
            return local_messages
        if isinstance(db, AsyncSession):
            result = await db.execute(
                select(ConversationMemory).filter(
                    ConversationMemory.bot_id == bot_id,
                    ConversationMemory.session_id == session_id,
                )
            )
            memory = result.scalar_one_or_none()
        else:
            memory = db.query(ConversationMemory).filter(
                ConversationMemory.bot_id == bot_id,
                ConversationMemory.session_id == session_id,
            ).first()
        db_messages = list(memory.messages or []) if memory and memory.messages else []
        if db_messages:
            standalone_model_service.persist_memory_messages(bot_id, session_id, db_messages)
        return db_messages

    async def _enqueue_outbox_event(
        self,
        db: Session | AsyncSession,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload_json: dict[str, Any],
        dedupe_key: Optional[str] = None,
    ):
        if isinstance(db, AsyncSession):
            return await supervisor_service.enqueue_outbox_async(
                db,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload_json=payload_json,
                dedupe_key=dedupe_key,
            )
        return supervisor_service.enqueue_outbox(
            db,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload_json=payload_json,
            dedupe_key=dedupe_key,
        )

    def _is_retryable_error(self, error_message: str) -> bool:
        """Return True for transient failures that should be retried."""
        text = (error_message or "").lower()
        retry_markers = [
            "timeout",
            "timed out",
            "rate limit",
            "429",
            "503",
            "502",
            "500",
            "temporarily unavailable",
            "connection error",
            "network error",
            "resource exhausted",
        ]
        return any(marker in text for marker in retry_markers)

    async def _chat_with_retries(
        self,
        provider: BaseLLMProvider,
        messages: List[ChatMessage],
        temperature: float,
        max_tokens: int,
        retry_attempts: Optional[int] = None,
        **provider_kwargs,
    ):
        """Call provider.chat with retry/backoff on transient failures."""
        max_retries = self.chat_retry_attempts if retry_attempts is None else max(0, int(retry_attempts))
        attempts = max_retries + 1
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                response = await provider.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **provider_kwargs,
                )
                return response, attempt - 1
            except Exception as exc:
                last_error = exc
                if attempt >= attempts or not self._is_retryable_error(str(exc)):
                    raise
                retry_index = attempt - 1
                backoff_ceiling = min(
                    self.chat_retry_backoff_cap_seconds,
                    self.chat_retry_backoff_seconds * (2 ** retry_index),
                )
                delay = random.uniform(0.0, backoff_ceiling)
                logger.warning(
                    "Transient provider failure on attempt %s/%s, retrying in %.2fs: %s",
                    attempt,
                    attempts,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error
        raise RuntimeError("Provider chat failed without a specific error")

    async def _prime_provider_for_request(
        self,
        provider: Optional[BaseLLMProvider],
        *,
        bot_id: str,
        session_id: str,
    ) -> None:
        """Start provider-side client/model preparation ahead of the actual generation call."""
        if provider is None:
            return
        try:
            await provider.warm_client()
            if provider.provider_name == "local":
                await provider.warm_model()
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "provider_prewarm_failed",
                component="chat",
                bot_id=bot_id,
                session_id=session_id,
                dependency=provider.provider_name,
                error=str(exc),
            )

    async def _lookup_semantic_cache(
        self,
        db: Session | AsyncSession,
        *,
        bot_id: str,
        provider: BaseLLMProvider,
        model_name: str,
        manifest_version: str,
        base_system_prompt: str,
        query: str,
        memory_history: List[Dict[str, Any]],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], float]:
        if not isinstance(db, AsyncSession) or not semantic_cache_service.enabled or memory_history:
            return None, None, 0.0

        cache_query = self._trim_text_to_token_budget(query, self.semantic_cache_max_query_tokens)
        query_embedding = await self.embedding_service.get_query_embedding(cache_query, provider)
        cached_payload, cache_hit_type, cache_similarity = await semantic_cache_service.get_cached_response(
            db,
            bot_id=bot_id,
            provider=provider.provider_name,
            model=model_name,
            manifest_version=manifest_version,
            system_prompt=base_system_prompt,
            query=cache_query,
            query_embedding=query_embedding,
        )
        return cached_payload, cache_hit_type, float(cache_similarity or 0.0)

    async def _execute_chat_with_routing(
        self,
        *,
        bot: Bot,
        routing_decision: RoutingDecision,
        messages: List[ChatMessage],
        fallback_messages: List[ChatMessage],
        temperature: float,
        max_tokens: int,
        retry_attempts: Optional[int],
        provider_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, int, BaseLLMProvider, bool]:
        ordered_candidates = router_service.ordered_candidates(bot, routing_decision)
        provider_kwargs = dict(provider_kwargs or {})

        last_error: Optional[Exception] = None
        fallback_used = False

        for index, (provider_type, model_name) in enumerate(ordered_candidates):
            try:
                provider = self._get_provider(bot, provider_type=provider_type, model=model_name)
            except Exception as provider_error:
                last_error = provider_error
                continue

            breaker_state = provider.chat_breaker.snapshot()
            if breaker_state.get("state") == "open":
                last_error = CircuitBreakerOpenError(
                    str(breaker_state.get("name") or provider.chat_breaker.name),
                    float(breaker_state.get("retry_after_seconds") or 30.0),
                )
                if not routing_decision.allow_downshift_fallback or index >= len(ordered_candidates) - 1:
                    raise ProviderTemporarilyUnavailableError(
                        f"{provider.provider_name} temporarily unavailable",
                        retry_after=float(breaker_state.get("retry_after_seconds") or 30.0),
                    )
                fallback_used = True
                continue

            try:
                response, retry_count = await self._chat_with_retries(
                    provider=provider,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    retry_attempts=retry_attempts,
                    **provider_kwargs,
                )
                return response, retry_count, provider, fallback_used or index > 0
            except Exception as exc:
                last_error = exc
                if self._is_provider_configuration_error(str(exc)) and index < len(ordered_candidates) - 1:
                    fallback_used = True
                    continue
                if self._is_deterministic_prompt_failure(str(exc)):
                    response, retry_count = await self._chat_with_retries(
                        provider=provider,
                        messages=fallback_messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        retry_attempts=retry_attempts,
                        **provider_kwargs,
                    )
                    return response, retry_count, provider, True

                if self._is_temporarily_unavailable_error(str(exc)):
                    if routing_decision.allow_downshift_fallback and index < len(ordered_candidates) - 1:
                        fallback_used = True
                        continue
                    raise ProviderTemporarilyUnavailableError(
                        str(exc),
                        retry_after=float(provider.chat_breaker.snapshot().get("retry_after_seconds") or 5.0),
                    ) from exc

                raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("No routed providers were available for this request")

    def _append_session_transcript(
        self,
        bot: Bot,
        session_id: str,
        user_message: str,
        assistant_response: str,
        *,
        tokens_used: int,
        latency_ms: int,
        retries_used: int,
        fallback_used: bool,
        rag_used: bool,
    ) -> None:
        """Persist per-session transcript in the bot-local memory DB and JSON export."""
        standalone_model_service.append_transcript(
            bot.id,
            session_id=session_id,
            user_message=user_message,
            assistant_response=assistant_response,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            retries_used=retries_used,
            fallback_used=fallback_used,
            rag_used=rag_used,
            provider_type=getattr(bot, "provider_type", None),
            model=getattr(bot, "model", None),
        )
        session_path = self.get_session_file_path(bot.id, session_id)
        timestamp = datetime.utcnow().isoformat() + "Z"

        with self._session_file_lock:
            if session_path.exists():
                try:
                    with open(session_path, "r", encoding="utf-8") as fh:
                        session_payload = json.load(fh)
                except Exception:
                    session_payload = {}
            else:
                session_payload = {}

            session_payload.setdefault("bot_id", bot.id)
            session_payload.setdefault("bot_name", bot.name)
            session_payload.setdefault("session_id", session_id)
            session_payload.setdefault("provider_type", bot.provider_type)
            session_payload.setdefault("model", bot.model)
            session_payload.setdefault("created_at", timestamp)
            session_payload["updated_at"] = timestamp
            session_payload.setdefault("messages", [])

            session_payload["messages"].append({
                "role": "user",
                "content": user_message,
                "timestamp": timestamp,
            })
            session_payload["messages"].append({
                "role": "assistant",
                "content": assistant_response,
                "timestamp": timestamp,
                "tokens_used": int(tokens_used or 0),
                "latency_ms": int(latency_ms or 0),
                "retries_used": int(retries_used or 0),
                "fallback_used": bool(fallback_used),
                "rag_used": bool(rag_used),
            })
            session_payload["message_count"] = len(session_payload["messages"])

            with open(session_path, "w", encoding="utf-8") as fh:
                json.dump(session_payload, fh, ensure_ascii=False, indent=2)
    
    def create_bot(
        self,
        db: Session,
        name: str,
        template_type: str,
        provider_type: str,
        api_key: Optional[str] = None,
        **kwargs
    ) -> Bot:
        """
        Create a new bot.
        
        Args:
            db: Database session
            name: Bot name
            template_type: Template to use
            provider_type: LLM provider type
            api_key: Optional API key
            **kwargs: Additional bot configuration
            
        Returns:
            Created Bot object
        """
        # Get template defaults
        template = template_service.get_template(template_type)
        defaults = template_service.get_default_config_for_template(template_type) if template else {}
        
        # Encrypt API key if provided
        encrypted_key = None
        if api_key:
            encrypted_key = encryption_service.encrypt(api_key)
        
        system_prompt = kwargs.get('system_prompt', defaults.get('system_prompt', ''))
        if system_prompt:
            try:
                system_prompt = system_prompt.format(
                    company_name=(kwargs.get("company_name") or name),
                    bot_name=name,
                )
            except Exception:
                pass

        # Create bot
        bot = Bot(
            id=str(uuid.uuid4()),
            name=name,
            template_type=template_type,
            provider_type=provider_type,
            system_prompt=system_prompt,
            temperature=kwargs.get('temperature', defaults.get('temperature', 0.7)),
            max_tokens=kwargs.get('max_tokens', defaults.get('max_tokens', 2000)),
            model=kwargs.get('model', defaults.get('model')),
            api_key_encrypted=encrypted_key,
            memory_enabled=kwargs.get('memory_enabled', defaults.get('memory_enabled', True)),
            analytics_enabled=kwargs.get('analytics_enabled', True),
            is_active=kwargs.get('is_active', True),
            tools_enabled=bool(
                kwargs.get("tools_enabled", kwargs.get("tool_settings", {}).get("enabled", False))
            ),
            allowed_tools_json=normalize_tool_definitions(kwargs.get('allowed_tools')),
            tool_settings_json=normalize_tool_settings(
                kwargs.get("tool_settings"),
                fallback_enabled=bool(kwargs.get("tools_enabled", False)),
            ),
            quick_chips_json=normalize_quick_chip_settings(kwargs.get("quick_chip_settings")),
            message_limit=kwargs.get('message_limit'),
            expiration_date=kwargs.get('expiration_date'),
            accent_color=kwargs.get('accent_color', '#007bff'),
            welcome_message=kwargs.get('welcome_message', 'Hello! How can I help you today?'),
            description=kwargs.get('description')
        )
        self._normalize_bot_tool_state(bot)
        
        db.add(bot)
        db.commit()
        db.refresh(bot)
        self._invalidate_bot_cache(bot.id)
        
        # Create bot data directories
        self.ensure_bot_storage(bot)
        standalone_model_service.write_bot_config(bot)
        
        return bot
    
    def get_bot(self, db: Session, bot_id: str) -> Optional[Bot]:
        """Get a bot by ID."""
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if bot and self._normalize_bot_tool_state(bot):
            db.commit()
            db.refresh(bot)
        return bot
    
    def list_bots(
        self,
        db: Session,
        skip: int = 0,
        limit: int = 100,
        active_only: bool = False
    ) -> Tuple[List[Bot], int]:
        """List all bots with pagination."""
        query = db.query(Bot)
        
        if active_only:
            query = query.filter(Bot.is_active == True)
        
        total = query.count()
        bots = query.order_by(Bot.created_at.desc()).offset(skip).limit(limit).all()
        changed = False
        for bot in bots:
            changed = self._normalize_bot_tool_state(bot) or changed
        if changed:
            db.commit()
            for bot in bots:
                db.refresh(bot)
        
        return bots, total
    
    def update_bot(
        self,
        db: Session,
        bot_id: str,
        **kwargs
    ) -> Optional[Bot]:
        """
        Update bot configuration.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            **kwargs: Fields to update
            
        Returns:
            Updated Bot or None
        """
        bot = self.get_bot(db, bot_id)
        if not bot:
            return None

        provider_reconfigured = False
        
        # Handle API key encryption
        if 'api_key' in kwargs:
            api_key = kwargs.pop('api_key')
            if api_key:
                bot.api_key_encrypted = encryption_service.encrypt(api_key)
            else:
                bot.api_key_encrypted = None
            provider_reconfigured = True
        
        # Update fields
        nullable_fields = {"message_limit", "expiration_date", "description", "model", "system_prompt", "welcome_message"}
        for key, value in kwargs.items():
            target_key = key
            target_value = value
            if key == "allowed_tools":
                target_key = "allowed_tools_json"
                target_value = normalize_tool_definitions(value)
            elif key == "tool_settings":
                target_key = "tool_settings_json"
                target_value = normalize_tool_settings(value, fallback_enabled=bool(bot.tools_enabled))
            elif key == "quick_chip_settings":
                target_key = "quick_chips_json"
                target_value = normalize_quick_chip_settings(value)
            if hasattr(bot, target_key) and (target_value is not None or target_key in nullable_fields):
                setattr(bot, target_key, target_value)
                if target_key in {"model", "provider_type"}:
                    provider_reconfigured = True
        if "tools_enabled" in kwargs and "tool_settings" not in kwargs:
            bot.tool_settings_json = normalize_tool_settings(
                bot.tool_settings_json,
                fallback_enabled=bool(kwargs.get("tools_enabled")),
            )

        self._normalize_bot_tool_state(bot)
        
        bot.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(bot)
        self._invalidate_bot_cache(bot.id)

        if provider_reconfigured:
            self._invalidate_provider_cache(bot.id)

        self.ensure_bot_storage(bot)
        standalone_model_service.write_bot_config(bot)
        
        return bot
    
    def delete_bot(self, db: Session, bot_id: str) -> bool:
        """
        Delete a bot and all its data.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            
        Returns:
            True if deleted
        """
        bot = self.get_bot(db, bot_id)
        if not bot:
            return False

        scraper_jobs = db.query(ScraperJob).filter(ScraperJob.bot_id == bot_id).all()
        scraper_job_ids = [job.id for job in scraper_jobs]

        if scraper_job_ids:
            db.query(TaskRun).filter(
                TaskRun.task_type == "scraper_job",
                TaskRun.task_key.in_(scraper_job_ids),
            ).delete(synchronize_session=False)
            db.query(OutboxEvent).filter(
                OutboxEvent.aggregate_type == "scraper_job",
                OutboxEvent.aggregate_id.in_(scraper_job_ids),
            ).delete(synchronize_session=False)
            db.query(ScraperJob).filter(ScraperJob.id.in_(scraper_job_ids)).delete(synchronize_session=False)

        db.query(ChatRequestRecord).filter(ChatRequestRecord.bot_id == bot_id).delete(synchronize_session=False)
        db.query(IndexManifest).filter(IndexManifest.bot_id == bot_id).delete(synchronize_session=False)
        db.query(ConversationMemory).filter(ConversationMemory.bot_id == bot_id).delete(synchronize_session=False)
        db.query(TaskRun).filter(
            TaskRun.task_type.in_(["embedding_rebuild", "bot_warmup"]),
            TaskRun.task_key == bot_id,
        ).delete(synchronize_session=False)
        db.query(OutboxEvent).filter(
            OutboxEvent.aggregate_type == "bot",
            OutboxEvent.aggregate_id == bot_id,
        ).delete(synchronize_session=False)
        
        # Delete from database (cascades to messages, analytics, etc.)
        db.delete(bot)
        db.commit()
        
        standalone_model_service.delete_bot_folder(bot_id)
        
        # Clear provider cache
        self._invalidate_provider_cache(bot_id)
        self._invalidate_bot_cache(bot_id)
        
        return True
    
    def clone_bot(
        self,
        db: Session,
        source_bot_id: str,
        new_name: str,
        copy_knowledge_base: bool = True,
        reset_analytics: bool = True
    ) -> Optional[Bot]:
        """
        Clone an existing bot.
        
        Args:
            db: Database session
            source_bot_id: Source bot identifier
            new_name: Name for the cloned bot
            copy_knowledge_base: Whether to copy knowledge base
            reset_analytics: Whether to reset analytics
            
        Returns:
            Cloned Bot or None
        """
        source = self.get_bot(db, source_bot_id)
        if not source:
            return None
        
        # Create new bot with same config
        new_bot = Bot(
            id=str(uuid.uuid4()),
            name=new_name,
            description=source.description,
            template_type=source.template_type,
            provider_type=source.provider_type,
            system_prompt=source.system_prompt,
            temperature=source.temperature,
            max_tokens=source.max_tokens,
            model=source.model,
            api_key_encrypted=source.api_key_encrypted,
            memory_enabled=source.memory_enabled,
            analytics_enabled=source.analytics_enabled,
            is_active=False,  # Start inactive
            tools_enabled=source.tools_enabled,
            allowed_tools_json=normalize_tool_definitions(list(source.allowed_tools_json or [])),
            tool_settings_json=normalize_tool_settings(
                source.tool_settings_json,
                fallback_enabled=bool(source.tools_enabled),
            ),
            quick_chips_json=normalize_quick_chip_settings(source.quick_chips_json),
            message_limit=source.message_limit,
            expiration_date=None,  # Reset expiration
            accent_color=source.accent_color,
            welcome_message=source.welcome_message,
            messages_used=0  # Reset counter
        )
        self._normalize_bot_tool_state(new_bot)
        
        db.add(new_bot)
        db.commit()
        db.refresh(new_bot)
        self._invalidate_bot_cache(new_bot.id)
        
        self.ensure_bot_storage(new_bot)

        # Copy knowledge base if requested
        if copy_knowledge_base:
            for source_dir, target_dir in (
                (
                    standalone_model_service.documents_dir(source_bot_id),
                    standalone_model_service.documents_dir(new_bot.id),
                ),
                (
                    standalone_model_service.model_files_dir(source_bot_id),
                    standalone_model_service.model_files_dir(new_bot.id),
                ),
                (
                    standalone_model_service.vector_store_dir(source_bot_id),
                    standalone_model_service.vector_store_dir(new_bot.id),
                ),
            ):
                if source_dir.exists():
                    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)

            # Copy knowledge chunks
            chunks = db.query(KnowledgeChunk).filter(
                KnowledgeChunk.bot_id == source_bot_id,
                KnowledgeChunk.deleted_at == None,  # noqa: E711
            ).all()
            
            for chunk in chunks:
                new_chunk = KnowledgeChunk(
                    bot_id=new_bot.id,
                    content=chunk.content,
                    source=chunk.source,
                    chunk_index=chunk.chunk_index,
                    word_count=chunk.word_count,
                    chunk_metadata=chunk.chunk_metadata
                )
                db.add(new_chunk)
            
            db.commit()
            if chunks:
                self._schedule_index_rebuild(
                    db,
                    new_bot.id,
                    reason="clone_bot",
                )
        else:
            standalone_model_service.ensure_bot_layout(new_bot.id)

        standalone_model_service.write_bot_config(new_bot)
        
        return new_bot
    
    async def chat(
        self,
        db: Session | AsyncSession,
        bot_id: str,
        message: str,
        session_id: Optional[str] = None,
        *,
        include_rag: bool = True,
        include_memory: bool = True,
        persist_memory: bool = True,
        persist_session_file: bool = True,
        retry_attempts: Optional[int] = None,
        allow_admin_tools: bool = False,
        execute_mutating_tools: bool = False,
    ) -> Dict[str, Any]:
        """
        Process a chat message.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            message: User message
            session_id: Session identifier
            
        Returns:
            Chat response dictionary
        """
        start_time = time.perf_counter()

        if isinstance(db, AsyncSession):
            bot = await self.get_bot_async(db, bot_id)
        else:
            bot = self.get_bot(db, bot_id)
        if not bot:
            raise ValueError("Bot not found")
        
        if not bot.is_available():
            return {
                'response': "This chatbot is currently inactive.",
                'session_id': session_id or self.create_session_id(),
                'tokens_used': 0,
                'latency_ms': 0,
                'retry_count': 0,
                'fallback_used': False,
                'status_message': "bot_unavailable"
            }
        
        # Get or create session
        session_id = (session_id or "").strip() or self.create_session_id()
        tool_settings = normalize_tool_settings(
            bot.tool_settings_json,
            fallback_enabled=bool(bot.tools_enabled),
        )
        quick_chip_settings = normalize_quick_chip_settings(bot.quick_chips_json)
        allowed_tool_definitions = normalize_tool_definitions(bot.allowed_tools_json)
        surface_scope = "admin" if allow_admin_tools else "public"
        keyword_quick_chips = scan_quick_chip_keywords(
            message,
            quick_chip_settings,
            allowed_tool_definitions,
            surface_scope=surface_scope,
        )
        
        configured_provider: Optional[BaseLLMProvider] = None
        try:
            configured_provider = self._get_provider(bot)
        except Exception as provider_error:
            log_event(
                logger,
                logging.WARNING,
                "configured_provider_initialization_failed",
                component="chat",
                bot_id=bot_id,
                session_id=session_id,
                dependency=bot.provider_type,
                error=str(provider_error),
            )
        retrieval_provider = self._resolve_rag_provider(bot, configured_provider)
        provider_warmup_task: Optional[asyncio.Task] = None
        if configured_provider is not None:
            provider_warmup_task = asyncio.create_task(
                self._prime_provider_for_request(
                    configured_provider,
                    bot_id=bot_id,
                    session_id=session_id,
                )
            )

        system_prompt_sections = [
            bot.system_prompt or "You are a helpful AI assistant.",
            self._build_company_voice_system_hint(bot),
        ]
        if bool(tool_settings.get("enabled")) and bool(quick_chip_settings.get("allow_ai_suggestions", True)) and any(
            tool.get("id") == "ui.suggest_chips" for tool in allowed_tool_definitions
        ):
            ai_chip_hint = build_quick_chip_ai_system_hint(quick_chip_settings)
            if ai_chip_hint:
                system_prompt_sections.append(ai_chip_hint)

        base_system_prompt = self._trim_text_to_token_budget(
            "\n\n".join(section for section in system_prompt_sections if section),
            self.chat_system_prompt_token_budget,
        )
        system_prompt = base_system_prompt
        capability_query = self._is_capability_query(message)
        rag_used = False
        fallback_used = False
        retry_count = 0
        degraded_components: List[str] = []
        index_status = self.embedding_service.get_index_status(bot_id)
        manifest_version = str(index_status.get("version") or "missing")
        memory_history: List[Dict[str, Any]] = []
        cache_hit_type: Optional[str] = None
        retrieval_context = ""
        retrieval_confidence = 0.0
        selected_search_results: List[Tuple[str, float, Dict[str, Any]]] = []

        def _mark_degraded(component: str) -> None:
            if component not in degraded_components:
                degraded_components.append(component)

        # Load and compact conversation memory before cache lookup so reuse stays session-safe.
        effective_include_memory = include_memory and bot.memory_enabled and not capability_query
        if effective_include_memory:
            try:
                cached_history = self._get_cached_session_memory(bot_id, session_id)
                if cached_history is None:
                    cached_history = self._set_cached_session_memory(
                        bot_id,
                        session_id,
                        await self._load_memory_messages(db, bot_id, session_id),
                    )
                compacted_history = self._compact_memory_messages(cached_history)
                memory_history = self._set_cached_session_memory(bot_id, session_id, compacted_history)
            except Exception as memory_error:
                _mark_degraded("memory")
                metrics_service.inc_counter(
                    "db_write_fail_total",
                    labels={"operation": "memory_load"},
                )
                log_event(
                    logger,
                    logging.WARNING,
                    "memory_load_failed",
                    component="memory",
                    bot_id=bot_id,
                    session_id=session_id,
                    dependency="database",
                    degraded_components=degraded_components,
                    error=str(memory_error),
                )

        # Get relevant context from knowledge base
        if include_rag:
            if index_status.get("state") not in {"active", "missing"}:
                _mark_degraded("rag")
                metrics_service.inc_counter("rag_bypass_total", labels={"bot_id": bot_id})
                logger.warning(
                    "RAG bypassed for bot %s session %s because index state is %s",
                    bot_id,
                    session_id,
                    index_status.get("state"),
                )
            elif retrieval_provider is None:
                _mark_degraded("rag")
                metrics_service.inc_counter("rag_bypass_total", labels={"bot_id": bot_id})
            else:
                try:
                    if provider_warmup_task is not None:
                        search_results, warmup_result = await asyncio.gather(
                            self.embedding_service.search(
                                message,
                                bot_id,
                                retrieval_provider,
                                top_k=3,
                            ),
                            provider_warmup_task,
                            return_exceptions=True,
                        )
                        if isinstance(search_results, Exception):
                            raise search_results
                        if isinstance(warmup_result, Exception):
                            log_event(
                                logger,
                                logging.WARNING,
                                "provider_prewarm_parallel_failed",
                                component="chat",
                                bot_id=bot_id,
                                session_id=session_id,
                                dependency=retrieval_provider.provider_name,
                                error=str(warmup_result),
                            )
                    else:
                        search_results = await self.embedding_service.search(
                            message, bot_id, retrieval_provider, top_k=3
                        )
                    if search_results:
                        search_results = self._prioritize_search_results(
                            message,
                            search_results,
                            capability_mode=capability_query,
                        )
                        selected_search_results = list(search_results)
                        retrieval_context, retrieval_confidence = self._extract_retrieval_context(message, search_results)
                    if capability_query:
                        capability_results = await self.embedding_service.search(
                            self._build_capability_lookup_query(bot),
                            bot_id,
                            retrieval_provider,
                            top_k=4,
                        )
                        if capability_results:
                            capability_results = self._prioritize_search_results(
                                message,
                                capability_results,
                                capability_mode=True,
                            )
                            capability_context, capability_confidence = self._extract_retrieval_context(
                                self._build_capability_lookup_query(bot),
                                capability_results,
                            )
                            if capability_context and capability_confidence >= retrieval_confidence:
                                selected_search_results = list(capability_results)
                                retrieval_context = capability_context
                                retrieval_confidence = capability_confidence
                    if retrieval_context:
                        rag_used = True
                        system_prompt = f"{system_prompt}\n\nInternal reference notes:\n{retrieval_context}"
                except Exception as rag_error:
                    _mark_degraded("rag")
                    metrics_service.inc_counter("rag_bypass_total", labels={"bot_id": bot_id})
                    log_event(
                        logger,
                        logging.WARNING,
                        "rag_search_failed",
                        component="rag",
                        bot_id=bot_id,
                        session_id=session_id,
                        dependency=retrieval_provider.provider_name if retrieval_provider else bot.provider_type,
                        circuit_state=retrieval_provider.embedding_breaker.snapshot().get("state") if retrieval_provider else None,
                        degraded_components=degraded_components,
                        error=str(rag_error),
                    )

        if capability_query:
            system_prompt = f"{system_prompt}\n\n{self._build_capability_system_hint(bot)}"

        routing_decision = self._select_routing_decision(bot, message, retrieval_confidence)
        route_candidates = router_service.ordered_candidates(bot, routing_decision)

        cache_provider: Optional[BaseLLMProvider] = None
        cache_model_name: Optional[str] = None
        for provider_type, model_name in route_candidates:
            try:
                candidate_provider = self._get_provider(bot, provider_type=provider_type, model=model_name)
            except Exception:
                continue
            cache_provider = candidate_provider
            cache_model_name = str(model_name or candidate_provider.model or candidate_provider.provider_name)
            break

        if cache_provider is not None:
            try:
                cached_payload, cache_hit_type, cache_similarity = await self._lookup_semantic_cache(
                    db,
                    bot_id=bot_id,
                    provider=cache_provider,
                    model_name=str(cache_model_name or cache_provider.model or cache_provider.provider_name),
                    manifest_version=manifest_version,
                    base_system_prompt=base_system_prompt,
                    query=message,
                    memory_history=memory_history,
                )
                if cached_payload:
                    latency_ms = int((time.perf_counter() - start_time) * 1000)
                    response_text = self._apply_company_voice_guardrails(
                        bot,
                        str(cached_payload.get("response") or ""),
                        user_message=message,
                    )
                    if persist_memory and bot.memory_enabled:
                        cached_messages = list(self._get_cached_session_memory(bot_id, session_id) or memory_history or [])
                        cached_messages.append({"role": "user", "content": message})
                        cached_messages.append({"role": "assistant", "content": response_text})
                        cached_messages = self._set_cached_session_memory(
                            bot_id,
                            session_id,
                            self._compact_memory_messages(cached_messages),
                        )
                        await self._persist_memory_async(
                            bot_id=bot_id,
                            session_id=session_id,
                            messages=cached_messages,
                        )
                        await self._enqueue_outbox_event(
                            db,
                            event_type="conversation_memory_persist",
                            aggregate_type="conversation_memory",
                            aggregate_id=f"{bot_id}:{session_id}",
                            payload_json={
                                "bot_id": bot_id,
                                "session_id": session_id,
                                "messages": cached_messages,
                            },
                            dedupe_key=f"conversation_memory_persist:{bot_id}:{session_id}:{hashlib.sha1(json.dumps(cached_messages, sort_keys=True).encode('utf-8')).hexdigest()[:12]}",
                        )
                    if persist_session_file:
                        await self._append_session_transcript_async(
                            bot=bot,
                            session_id=session_id,
                            user_message=message,
                            assistant_response=response_text,
                            tokens_used=0,
                            latency_ms=latency_ms,
                            retries_used=0,
                            fallback_used=False,
                            rag_used=rag_used,
                        )
                        await self._enqueue_outbox_event(
                            db,
                            event_type="session_transcript_persist",
                            aggregate_type="session_transcript",
                            aggregate_id=f"{bot_id}:{session_id}",
                            payload_json={
                                "bot": {
                                    "id": bot.id,
                                    "name": bot.name,
                                    "provider_type": cache_provider.provider_name,
                                    "model": str(cache_model_name or cache_provider.model or cache_provider.provider_name),
                                },
                                "session_id": session_id,
                                "user_message": message,
                                "assistant_response": response_text,
                                "tokens_used": 0,
                                "latency_ms": latency_ms,
                                "retries_used": 0,
                                "fallback_used": False,
                                "rag_used": rag_used,
                            },
                            dedupe_key=f"session_transcript_persist:{bot_id}:{session_id}:{hashlib.sha1((message + response_text).encode('utf-8')).hexdigest()[:12]}",
                        )
                    return {
                        'response': response_text,
                        'session_id': session_id,
                        'tokens_used': 0,
                        'tokens_prompt': 0,
                        'tokens_completion': 0,
                        'latency_ms': latency_ms,
                        'model': f"{cache_provider.provider_name}:semantic-cache",
                        'retry_count': 0,
                        'fallback_used': False,
                        'status_message': f"semantic_cache_{cache_hit_type}_hit",
                        'degraded_components': [],
                        'data_freshness': "fresh",
                        'cache_similarity': cache_similarity,
                        'context_scope': cached_payload.get("context_scope"),
                        'context_sources': list(cached_payload.get("context_sources") or []),
                        'citations': list(cached_payload.get("citations") or []),
                        'quick_chips': merge_quick_chip_sources(
                            keyword_quick_chips,
                            list(cached_payload.get("quick_chips") or []),
                            allowed_tool_definitions=allowed_tool_definitions,
                            surface_scope=surface_scope,
                        ),
                        'tool_events': list(cached_payload.get("tool_events") or []),
                        'pending_tool_actions': list(cached_payload.get("pending_tool_actions") or []),
                    }
            except Exception as cache_error:
                log_event(
                    logger,
                    logging.WARNING,
                    "semantic_cache_lookup_failed",
                    component="semantic-cache",
                    bot_id=bot_id,
                    session_id=session_id,
                    dependency="database",
                    error=str(cache_error),
                )

        # Build messages
        messages = [ChatMessage(role="system", content=system_prompt)]

        if provider_warmup_task is not None and not provider_warmup_task.done():
            try:
                await provider_warmup_task
            except Exception as warmup_error:
                log_event(
                    logger,
                    logging.WARNING,
                    "provider_prewarm_deferred_failed",
                    component="chat",
                    bot_id=bot_id,
                    session_id=session_id,
                    dependency=configured_provider.provider_name if configured_provider else bot.provider_type,
                    error=str(warmup_error),
                )

        # Add compressed conversation memory if enabled.
        if effective_include_memory and memory_history:
            try:
                messages.extend(self._build_memory_prompt_messages(memory_history))
            except Exception as memory_error:
                _mark_degraded("memory")
                metrics_service.inc_counter(
                    "db_write_fail_total",
                    labels={"operation": "memory_load"},
                )
                log_event(
                    logger,
                    logging.WARNING,
                    "memory_load_failed",
                    component="memory",
                    bot_id=bot_id,
                    session_id=session_id,
                    dependency="database",
                    degraded_components=degraded_components,
                    error=str(memory_error),
                )
        
        # Add user message
        messages.append(ChatMessage(role="user", content=message))

        fallback_messages = [
            ChatMessage(role="system", content=base_system_prompt),
            ChatMessage(role="user", content=message),
        ]

        execution_provider: Optional[BaseLLMProvider] = None
        quick_chips: List[Dict[str, Any]] = list(keyword_quick_chips)
        tool_events: List[Dict[str, Any]] = []
        pending_tool_actions: List[Dict[str, Any]] = []
        provider_tool_specs: List[Dict[str, Any]] = []
        if bool(tool_settings.get("enabled")):
            provider_tool_specs = build_provider_tool_specs(
                allowed_tool_definitions,
                include_admin=allow_admin_tools,
                allow_ai_suggestions=bool(quick_chip_settings.get("allow_ai_suggestions", True)),
            )
        provider_kwargs: Dict[str, Any] = {}
        if provider_tool_specs:
            provider_kwargs["tools"] = provider_tool_specs

        # Get response with retries and routing-aware fallback.
        try:
            response, retry_count, execution_provider, fallback_used = await self._execute_chat_with_routing(
                bot=bot,
                routing_decision=routing_decision,
                messages=messages,
                fallback_messages=fallback_messages,
                temperature=bot.temperature,
                max_tokens=bot.max_tokens,
                retry_attempts=retry_attempts,
                provider_kwargs=provider_kwargs,
            )
        except ProviderTemporarilyUnavailableError as primary_error:
            _mark_degraded("provider")
            log_event(
                logger,
                logging.WARNING,
                "provider_temporarily_unavailable",
                component="chat",
                bot_id=bot_id,
                session_id=session_id,
                dependency=routing_decision.provider_type,
                circuit_state=execution_provider.chat_breaker.snapshot().get("state") if execution_provider else None,
                degraded_components=degraded_components,
                error=str(primary_error),
            )
            raise
        except Exception as primary_error:
            _mark_degraded("provider")
            log_event(
                logger,
                logging.WARNING,
                "primary_chat_pipeline_failed",
                component="chat",
                bot_id=bot_id,
                session_id=session_id,
                dependency=routing_decision.provider_type,
                circuit_state=execution_provider.chat_breaker.snapshot().get("state") if execution_provider else None,
                degraded_components=degraded_components,
                error=str(primary_error),
            )
            raise

        if response.tool_calls and execution_provider is not None:
            max_tool_calls_per_session = int(tool_settings.get("max_tool_calls_per_session") or 6)
            assistant_tool_message = ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=list(response.tool_calls),
            )
            tool_result_messages: List[ChatMessage] = []
            for tool_call in response.tool_calls:
                current_session_tool_usage = self._get_session_tool_usage(bot_id, session_id)
                if current_session_tool_usage >= max_tool_calls_per_session:
                    tool_events.append(
                        {
                            "tool_id": tool_call.name,
                            "status": "limit_reached",
                            "mutating": False,
                        }
                    )
                    tool_result_messages.append(
                        ChatMessage(
                            role="tool",
                            content=json.dumps(
                                {
                                    "status": "tool_limit_reached",
                                    "message": "The maximum tool calls for this session has been reached.",
                                },
                                ensure_ascii=False,
                            ),
                            tool_name=tool_call.name,
                        )
                    )
                    continue
                processed = await process_tool_call(
                    db=db,
                    bot_id=bot_id,
                    tool_definitions=allowed_tool_definitions,
                    tool_id=tool_call.name,
                    arguments=tool_call.arguments,
                    allow_admin=allow_admin_tools,
                    execute_mutations=execute_mutating_tools,
                )
                self._increment_session_tool_usage(bot_id, session_id)
                if processed.get("quick_chips"):
                    quick_chips.extend(list(processed["quick_chips"]))
                if processed.get("tool_event"):
                    tool_events.append(dict(processed["tool_event"]))
                if processed.get("pending_tool_action"):
                    pending_tool_actions.append(dict(processed["pending_tool_action"]))
                tool_result_messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(processed.get("result_for_model") or {"status": "ok"}, ensure_ascii=False),
                        tool_name=tool_call.name,
                    )
                )
            if tool_result_messages:
                try:
                    follow_up_response, extra_retries = await self._chat_with_retries(
                        provider=execution_provider,
                        messages=messages + [assistant_tool_message] + tool_result_messages,
                        temperature=bot.temperature,
                        max_tokens=bot.max_tokens,
                        retry_attempts=retry_attempts,
                        **provider_kwargs,
                    )
                    if follow_up_response.content.strip():
                        response = follow_up_response
                    retry_count += extra_retries
                except Exception as tool_followup_error:
                    log_event(
                        logger,
                        logging.WARNING,
                        "tool_followup_generation_failed",
                        component="chat",
                        bot_id=bot_id,
                        session_id=session_id,
                        dependency=execution_provider.provider_name,
                        error=str(tool_followup_error),
                    )
            if pending_tool_actions and not response.content.strip():
                response.content = "I have an admin-only tool action ready for approval."

        if capability_query and retrieval_context and self._is_low_value_capability_response(
            response.content,
            retrieval_context,
            message,
        ):
            response.content = self._build_capability_response(bot, retrieval_context, message)

        response.content = self._apply_company_voice_guardrails(
            bot,
            response.content,
            user_message=message,
            retrieval_context=retrieval_context,
        )

        context_scope, context_sources, citations = self._summarize_retrieval_metadata(selected_search_results)

        latency_ms = int((time.perf_counter() - start_time) * 1000)
        
        # Update memory if enabled
        if persist_memory and bot.memory_enabled:
            try:
                cached_messages = list(self._get_cached_session_memory(bot_id, session_id) or memory_history or [])
                cached_messages.append({"role": "user", "content": message})
                cached_messages.append({"role": "assistant", "content": response.content})
                cached_messages = self._set_cached_session_memory(
                    bot_id,
                    session_id,
                    self._compact_memory_messages(cached_messages),
                )
                await self._persist_memory_async(
                    bot_id=bot_id,
                    session_id=session_id,
                    messages=cached_messages,
                )
                await self._enqueue_outbox_event(
                    db,
                    event_type="conversation_memory_persist",
                    aggregate_type="conversation_memory",
                    aggregate_id=f"{bot_id}:{session_id}",
                    payload_json={
                        "bot_id": bot_id,
                        "session_id": session_id,
                        "messages": cached_messages,
                    },
                    dedupe_key=f"conversation_memory_persist:{bot_id}:{session_id}:{hashlib.sha1(json.dumps(cached_messages, sort_keys=True).encode('utf-8')).hexdigest()[:12]}",
                )
            except Exception as memory_persist_error:
                _mark_degraded("memory")
                metrics_service.inc_counter(
                    "db_write_fail_total",
                    labels={"operation": "memory_persist"},
                )
                log_event(
                    logger,
                    logging.WARNING,
                    "memory_persist_failed",
                    component="memory",
                    bot_id=bot_id,
                    session_id=session_id,
                    dependency="database",
                    degraded_components=degraded_components,
                    error=str(memory_persist_error),
                )

        if persist_session_file:
            try:
                await self._append_session_transcript_async(
                    bot=bot,
                    session_id=session_id,
                    user_message=message,
                    assistant_response=response.content,
                    tokens_used=response.tokens_total,
                    latency_ms=latency_ms,
                    retries_used=retry_count,
                    fallback_used=fallback_used,
                    rag_used=rag_used,
                )
                await self._enqueue_outbox_event(
                    db,
                    event_type="session_transcript_persist",
                    aggregate_type="session_transcript",
                    aggregate_id=f"{bot_id}:{session_id}",
                    payload_json={
                        "bot": {
                            "id": bot.id,
                            "name": bot.name,
                            "provider_type": execution_provider.provider_name if execution_provider else bot.provider_type,
                            "model": response.model,
                        },
                        "session_id": session_id,
                        "user_message": message,
                        "assistant_response": response.content,
                        "tokens_used": response.tokens_total,
                        "latency_ms": latency_ms,
                        "retries_used": retry_count,
                        "fallback_used": fallback_used,
                        "rag_used": rag_used,
                    },
                    dedupe_key=f"session_transcript_persist:{bot_id}:{session_id}:{hashlib.sha1((message + response.content).encode('utf-8')).hexdigest()[:12]}",
                )
            except Exception as session_error:
                _mark_degraded("session_transcript")
                metrics_service.inc_counter(
                    "db_write_fail_total",
                    labels={"operation": "session_transcript"},
                )
                log_event(
                    logger,
                    logging.WARNING,
                    "session_transcript_persist_failed",
                    component="session_transcript",
                    bot_id=bot_id,
                    session_id=session_id,
                    degraded_components=degraded_components,
                    error=str(session_error),
                )

        quick_chips = merge_quick_chip_sources(
            quick_chips,
            allowed_tool_definitions=allowed_tool_definitions,
            surface_scope=surface_scope,
        )

        if isinstance(db, AsyncSession):
            try:
                semantic_provider = execution_provider or cache_provider or configured_provider
                if semantic_provider is None:
                    raise RuntimeError("No provider available for semantic cache fill")
                semantic_model_name = str(
                    (execution_provider.model if execution_provider else None)
                    or (cache_provider.model if cache_provider else None)
                    or semantic_provider.model
                    or semantic_provider.provider_name
                )
                cache_query = self._trim_text_to_token_budget(message, self.semantic_cache_max_query_tokens)
                query_embedding = await self.embedding_service.get_query_embedding(cache_query, semantic_provider)
                await self._enqueue_outbox_event(
                    db,
                    event_type="semantic_cache_fill",
                    aggregate_type="semantic_cache",
                    aggregate_id=f"{bot_id}:{semantic_provider.provider_name}:{manifest_version}",
                    payload_json={
                        "bot_id": bot_id,
                        "provider": semantic_provider.provider_name,
                        "model": semantic_model_name,
                        "manifest_version": manifest_version,
                        "system_prompt": base_system_prompt,
                        "query": cache_query,
                        "query_embedding": query_embedding,
                        "response_payload": {
                            "response": response.content,
                            "status_message": "completed_with_fallback" if fallback_used else f"completed_via_{routing_decision.task_class}_route",
                            "quick_chips": quick_chips,
                            "tool_events": tool_events,
                            "pending_tool_actions": pending_tool_actions,
                        },
                    },
                    dedupe_key=f"semantic_cache_fill:{bot_id}:{semantic_provider.provider_name}:{manifest_version}:{semantic_cache_service.hash_text(semantic_cache_service.normalize_query(cache_query))}",
                )
            except Exception as semantic_cache_error:
                log_event(
                    logger,
                    logging.WARNING,
                    "semantic_cache_fill_enqueue_failed",
                    component="semantic-cache",
                    bot_id=bot_id,
                    session_id=session_id,
                    dependency="database",
                    error=str(semantic_cache_error),
                )

        return {
            'response': response.content,
            'session_id': session_id,
            'tokens_used': response.tokens_total,
            'tokens_prompt': response.tokens_prompt,
            'tokens_completion': response.tokens_completion,
            'latency_ms': latency_ms,
            'model': response.model,
            'retry_count': retry_count,
            'fallback_used': fallback_used,
            'status_message': "completed_with_fallback" if fallback_used else f"completed_via_{routing_decision.task_class}_route",
            'degraded_components': degraded_components,
            'data_freshness': "stale" if degraded_components else "fresh",
            'context_scope': context_scope,
            'context_sources': context_sources,
            'citations': citations,
            'quick_chips': quick_chips,
            'tool_events': tool_events,
            'pending_tool_actions': pending_tool_actions,
        }

    async def warmup_bot(
        self,
        db: Session,
        bot_id: str,
        prompt: str = "Hi, send a welcome message",
    ) -> Dict[str, Any]:
        """Warm up a bot model so the first real user prompt is faster."""
        bot = self.get_bot(db, bot_id)
        if not bot:
            raise ValueError("Bot not found")
        if not bot.is_available():
            return {"status": "skipped", "reason": "bot_unavailable"}
        if bot.provider_type != "local":
            return {"status": "skipped", "reason": "provider_not_local"}

        result = await self.chat(
            db=db,
            bot_id=bot_id,
            message=prompt,
            session_id=f"warmup-{self.create_session_id()}",
            include_rag=False,
            include_memory=False,
            persist_memory=False,
            persist_session_file=False,
            retry_attempts=self.chat_retry_attempts,
        )
        response_text = (result.get("response") or "").strip()
        if response_text:
            self.set_startup_welcome_message(bot_id, response_text)

        return {
            "status": "ready",
            "bot_id": bot_id,
            "latency_ms": result.get("latency_ms", 0),
            "model": result.get("model"),
            "response_preview": response_text[:160],
        }
    
    async def upload_knowledge(
        self,
        db: Session,
        bot_id: str,
        documents: List[Dict[str, str]],
        append: bool = False
    ) -> Dict[str, Any]:
        """
        Upload knowledge base documents.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            documents: List of {"text": ..., "source": ...} dicts
            append: Whether to append or replace
            
        Returns:
            Statistics about indexing
        """
        bot = self.get_bot(db, bot_id)
        if not bot:
            raise ValueError("Bot not found")

        if not append:
            db.query(KnowledgeChunk).filter(
                KnowledgeChunk.bot_id == bot_id,
                KnowledgeChunk.deleted_at == None,  # noqa: E711
            ).update(
                {"deleted_at": datetime.utcnow()},
                synchronize_session=False,
            )
            db.commit()

        chunks_created = 0
        for document in documents:
            text = (document.get("text") or "").strip()
            if not text:
                continue
            source = document.get("source", "unknown")
            source_metadata = dict(document.get("metadata", {}) or {})
            raw_text = str(
                source_metadata.pop("_raw_text", "")
                or source_metadata.pop("raw_text", "")
                or text
            )
            for chunk_index, chunk_text in enumerate(self.embedding_service.chunk_text(text)):
                existing = db.query(KnowledgeChunk).filter(
                    KnowledgeChunk.bot_id == bot_id,
                    KnowledgeChunk.content == chunk_text,
                    KnowledgeChunk.source == source,
                    KnowledgeChunk.deleted_at == None,  # noqa: E711
                ).first()
                if existing:
                    continue
                chunk_metadata = self._build_chunk_trace_metadata(
                    raw_text=raw_text,
                    source=source,
                    source_metadata=source_metadata,
                    chunk_text=chunk_text,
                )
                chunk_record = KnowledgeChunk(
                    bot_id=bot_id,
                    content=chunk_text,
                    source=source,
                    chunk_index=chunk_index,
                    word_count=len(chunk_text.split()),
                    chunk_metadata=chunk_metadata,
                )
                db.add(chunk_record)
                db.flush()
                chunk_file_path = scrape_trace_service.write_chunk_trace(
                    bot_id,
                    chunk_id=chunk_record.id,
                    chunk_text=chunk_text,
                    source_metadata=chunk_metadata,
                )
                if chunk_file_path:
                    chunk_record.chunk_metadata = {
                        **chunk_metadata,
                        "chunk_file_path": chunk_file_path,
                    }
                chunks_created += 1

        db.commit()
        outbox_event = self._schedule_index_rebuild(
            db,
            bot_id,
            reason="knowledge_upload_append" if append else "knowledge_replace",
        )

        return {
            "chunks_created": chunks_created,
            "documents_processed": len(documents),
            "index_build_scheduled": True,
            "outbox_event_id": outbox_event.id,
        }

    def _build_chunk_trace_metadata(
        self,
        *,
        raw_text: str,
        source: str,
        source_metadata: Dict[str, Any],
        chunk_text: str,
    ) -> Dict[str, Any]:
        chunk_metadata = dict(source_metadata or {})
        page_line_start = int(chunk_metadata.get("source_line_start") or 1)
        source_range = scrape_trace_service.estimate_chunk_range(
            raw_text=raw_text,
            chunk_text=chunk_text,
            page_line_start=page_line_start,
        )
        chunk_metadata.update(
            {
                "title": chunk_metadata.get("title") or "",
                "url": chunk_metadata.get("url") or source,
                "source_file_path": chunk_metadata.get("source_file_path"),
                "raw_html_path": chunk_metadata.get("raw_html_path"),
                "trace_job_path": chunk_metadata.get("trace_job_path"),
                "trace_chunks_dir": chunk_metadata.get("trace_chunks_dir"),
                "line_range": [source_range.line_start, source_range.line_end],
                "paragraph_range": [source_range.paragraph_start, source_range.paragraph_end],
            }
        )
        return chunk_metadata
    
    async def rebuild_embeddings(
        self,
        db: Session,
        bot_id: str
    ) -> Dict[str, Any]:
        """
        Rebuild embeddings for a bot's knowledge base.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            
        Returns:
            Statistics
        """
        bot = self.get_bot(db, bot_id)
        if not bot:
            raise ValueError("Bot not found")

        outbox_event = self._schedule_index_rebuild(
            db,
            bot.id,
            reason="manual_rebuild",
        )
        return {
            "chunks_processed": 0,
            "index_build_scheduled": True,
            "outbox_event_id": outbox_event.id,
        }

    async def _reindex_knowledge_from_db(
        self,
        db: Session,
        bot: Bot,
        *,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Rebuild the file-based embeddings state from durable DB chunks."""
        
        # Get all chunks from database
        chunks = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.bot_id == bot.id,
            KnowledgeChunk.deleted_at == None,  # noqa: E711
        ).all()
        
        if not chunks:
            self.embedding_service.delete_index(bot.id)
            return {'chunks_processed': 0, 'version': None}
        
        # Prepare pre-chunked metadata rows
        chunk_rows = [
            {
                'text': chunk.content,
                'source': chunk.source,
                'chunk_index': chunk.chunk_index,
                'word_count': chunk.word_count,
                'metadata': chunk.chunk_metadata or {}
            }
            for chunk in chunks
        ]
        
        provider = self._get_provider(bot)
        stats = await self.embedding_service.index_chunks(
            bot.id,
            chunk_rows,
            provider,
            version=version,
        )
        
        return stats

    def _schedule_index_rebuild(
        self,
        db: Session,
        bot_id: str,
        *,
        reason: str,
    ):
        """Mark the current manifest stale and enqueue a durable rebuild event."""
        self.embedding_service.set_manifest_state(
            bot_id,
            state="stale",
            reason=reason,
        )
        now = datetime.utcnow()
        db.query(IndexManifest).filter(
            IndexManifest.bot_id == bot_id,
            IndexManifest.state.in_(["active", "building"]),
        ).update(
            {
                "state": "stale",
                "reason": reason,
                "updated_at": now,
            },
            synchronize_session=False,
        )
        db.commit()
        return supervisor_service.enqueue_outbox(
            db,
            event_type="rebuild_embeddings",
            aggregate_type="bot",
            aggregate_id=bot_id,
            payload_json={
                "bot_id": bot_id,
                "reason": reason,
            },
            dedupe_key=f"rebuild_embeddings:{bot_id}",
        )
    
    def get_knowledge_stats(
        self,
        db: Session,
        bot_id: str
    ) -> Dict[str, Any]:
        """Get knowledge base statistics for a bot."""
        chunk_count = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.bot_id == bot_id,
            KnowledgeChunk.deleted_at == None,  # noqa: E711
        ).count()
        
        index_stats = self.embedding_service.get_index_stats(bot_id)
        
        return {
            'db_chunks': chunk_count,
            **index_stats
        }
    
    def get_knowledge_chunks(
        self,
        db: Session,
        bot_id: str,
        page: int = 1,
        per_page: int = 20,
        search: str = None
    ) -> Dict[str, Any]:
        """
        Get paginated knowledge chunks for a bot.
        
        Args:
            db: Database session
            bot_id: Bot ID
            page: Page number (1-indexed)
            per_page: Items per page
            search: Optional search term to filter chunks
        
        Returns:
            Dictionary with chunks, pagination info, and stats
        """
        query = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.bot_id == bot_id,
            KnowledgeChunk.deleted_at == None,  # noqa: E711
        )
        
        # Apply search filter if provided
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                KnowledgeChunk.content.ilike(search_term) |
                KnowledgeChunk.source.ilike(search_term)
            )
        
        # Get total count
        total_count = query.count()
        
        # Apply pagination
        offset = (page - 1) * per_page
        chunks = query.order_by(KnowledgeChunk.created_at.desc()).offset(offset).limit(per_page).all()
        
        return {
            'chunks': [
                {
                    'id': chunk.id,
                    'content': chunk.content[:500] + ('...' if len(chunk.content) > 500 else ''),
                    'full_content': chunk.content,
                    'source': chunk.source,
                    'metadata': chunk.chunk_metadata,
                    'source_file_path': (chunk.chunk_metadata or {}).get("source_file_path"),
                    'raw_html_path': (chunk.chunk_metadata or {}).get("raw_html_path"),
                    'line_range': (chunk.chunk_metadata or {}).get("line_range"),
                    'paragraph_range': (chunk.chunk_metadata or {}).get("paragraph_range"),
                    'created_at': chunk.created_at.isoformat() if chunk.created_at else None,
                    'word_count': chunk.word_count
                }
                for chunk in chunks
            ],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_items': total_count,
                'total_pages': (total_count + per_page - 1) // per_page if per_page > 0 else 0
            }
        }
    
    async def delete_knowledge_chunk(
        self,
        db: Session,
        bot_id: str,
        chunk_id: str
    ) -> bool:
        """Delete a specific knowledge chunk."""
        chunk = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.id == chunk_id,
            KnowledgeChunk.bot_id == bot_id,
            KnowledgeChunk.deleted_at == None,  # noqa: E711
        ).first()
        
        if not chunk:
            return False
        
        chunk_metadata = dict(chunk.chunk_metadata or {})
        chunk.deleted_at = datetime.utcnow()
        db.commit()
        scrape_trace_service.prune_trace_artifacts(
            db,
            bot_id=bot_id,
            chunk_metadata=chunk_metadata,
        )
        self._schedule_index_rebuild(
            db,
            bot_id,
            reason="knowledge_chunk_deleted",
        )
        return True

    async def update_knowledge_chunk(
        self,
        db: Session,
        bot_id: str,
        chunk_id: str,
        content: str,
    ) -> Optional[Dict[str, Any]]:
        """Update the cleaned content for a specific knowledge chunk."""
        chunk = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.id == chunk_id,
            KnowledgeChunk.bot_id == bot_id,
            KnowledgeChunk.deleted_at == None,  # noqa: E711
        ).first()

        if not chunk:
            return None

        clean_content = re.sub(r"\s+", " ", (content or "").strip())
        if not clean_content:
            raise ValueError("Updated chunk content cannot be empty")

        chunk_metadata = dict(chunk.chunk_metadata or {})
        source_file_path = str(chunk_metadata.get("source_file_path") or "").strip()
        raw_text = ""
        if source_file_path:
            try:
                raw_text = scrape_trace_service.read_source_file(bot_id, source_file_path)
            except FileNotFoundError:
                raw_text = ""

        rebuilt_metadata = self._build_chunk_trace_metadata(
            raw_text=raw_text or clean_content,
            source=chunk.source or "manual_upload",
            source_metadata=chunk_metadata,
            chunk_text=clean_content,
        )
        chunk_file_path = scrape_trace_service.write_chunk_trace(
            bot_id,
            chunk_id=chunk.id,
            chunk_text=clean_content,
            source_metadata=rebuilt_metadata,
        )
        if chunk_file_path:
            rebuilt_metadata["chunk_file_path"] = chunk_file_path

        chunk.content = clean_content
        chunk.word_count = len(clean_content.split())
        chunk.chunk_metadata = rebuilt_metadata
        db.commit()
        db.refresh(chunk)

        self._schedule_index_rebuild(
            db,
            bot_id,
            reason="knowledge_chunk_updated",
        )

        return {
            "id": chunk.id,
            "content": chunk.content[:500] + ("..." if len(chunk.content) > 500 else ""),
            "full_content": chunk.content,
            "source": chunk.source,
            "metadata": chunk.chunk_metadata,
            "source_file_path": (chunk.chunk_metadata or {}).get("source_file_path"),
            "raw_html_path": (chunk.chunk_metadata or {}).get("raw_html_path"),
            "line_range": (chunk.chunk_metadata or {}).get("line_range"),
            "paragraph_range": (chunk.chunk_metadata or {}).get("paragraph_range"),
            "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
            "word_count": chunk.word_count,
        }

    def get_knowledge_chunk_source(
        self,
        db: Session,
        bot_id: str,
        chunk_id: str,
    ) -> Optional[Dict[str, Any]]:
        chunk = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.id == chunk_id,
            KnowledgeChunk.bot_id == bot_id,
            KnowledgeChunk.deleted_at == None,  # noqa: E711
        ).first()
        if not chunk:
            return None
        metadata = dict(chunk.chunk_metadata or {})
        source_file_path = str(metadata.get("source_file_path") or "").strip()
        raw_text = None
        raw_html = None
        if source_file_path:
            try:
                raw_text = scrape_trace_service.read_source_file(bot_id, source_file_path)
            except FileNotFoundError:
                raw_text = None
        raw_html_path = str(metadata.get("raw_html_path") or "").strip()
        if raw_html_path:
            try:
                raw_html = scrape_trace_service.read_source_file(bot_id, raw_html_path)
            except FileNotFoundError:
                raw_html = None
        if raw_text is None and raw_html is None and chunk.source and str(chunk.source).startswith(("http://", "https://")):
            scraper_loader = ScraperService()
            related_jobs = (
                db.query(ScraperJob)
                .filter(
                    ScraperJob.bot_id == bot_id,
                    ScraperJob.extracted_data != None,  # noqa: E711
                )
                .order_by(ScraperJob.completed_at.desc(), ScraperJob.created_at.desc())
                .all()
            )
            for job in related_jobs:
                extracted_data = dict(job.extracted_data or {})
                persist_pages_path = extracted_data.get("persist_pages_path")
                if not persist_pages_path:
                    continue
                try:
                    pages = scraper_loader.load_persisted_pages(str(persist_pages_path))
                except Exception:
                    continue
                matching_page = next((page for page in pages if str(page.get("url") or "").rstrip("/") == str(chunk.source).rstrip("/")), None)
                if not matching_page:
                    continue
                raw_text = str(matching_page.get("raw_text") or matching_page.get("content") or "").strip() or None
                raw_html = str(matching_page.get("raw_html") or "").strip() or None
                if not source_file_path:
                    source_file_path = str(persist_pages_path)
                metadata = {
                    **metadata,
                    "url": matching_page.get("url") or metadata.get("url") or chunk.source,
                    "title": matching_page.get("title") or metadata.get("title"),
                    "source_recovered_from": "persisted_scraper_job",
                }
                break
        return {
            "chunk_id": chunk.id,
            "cleaned_text": chunk.content,
            "raw_text": raw_text,
            "raw_html": raw_html,
            "source_file_path": source_file_path or None,
            "metadata": metadata,
        }

    def prune_orphaned_scrape_artifacts(
        self,
        db: Session,
        *,
        bot_id: Optional[str] = None,
    ) -> Dict[str, int]:
        bot_ids = [bot_id] if bot_id else [row[0] for row in db.query(Bot.id).all()]
        stats = {"orphan_scrape_dirs_archived": 0}
        for current_bot_id in bot_ids:
            pruned = scrape_trace_service.prune_orphaned_bot_scrapes(db, bot_id=current_bot_id)
            stats["orphan_scrape_dirs_archived"] += int(pruned.get("orphan_scrape_dirs_archived", 0))
        return stats


# Singleton instance
bot_service = BotService()
