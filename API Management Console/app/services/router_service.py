"""
Deterministic cost-aware routing policy for chat requests.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple


@dataclass
class RoutingDecision:
    task_class: str
    provider_type: str
    model: Optional[str]
    allow_downshift_fallback: bool
    reason: str


class RouterService:
    """Choose local, balanced, or premium providers from a deterministic policy."""

    def classify_task(self, message: str, retrieval_confidence: float) -> Tuple[str, bool]:
        clean = (message or "").strip()
        lower = clean.lower()
        word_count = len(clean.split())
        code_like = bool(
            "```" in clean
            or re.search(
                r"\b(def |class |function |select |insert |update |from |where |try:|except |stack trace|traceback|json|yaml|sql|python|javascript|typescript|bash)\b",
                clean,
                flags=re.IGNORECASE,
            )
        )
        multi_step = clean.count("?") > 1 or any(
            marker in lower
            for marker in [
                "step by step",
                "compare",
                "analyze",
                "design",
                "refactor",
                "optimize",
                "architecture",
                "why does",
                "how can",
            ]
        )
        faq_like = word_count <= 18 and not code_like and not multi_step
        rag_min_score = float(os.getenv("CHAT_RAG_MIN_SCORE", "0.18"))
        rag_high_confidence_score = float(os.getenv("CHAT_RAG_HIGH_CONFIDENCE_SCORE", "0.72"))

        if code_like or word_count >= 110 or multi_step or (retrieval_confidence and retrieval_confidence < rag_min_score):
            return "premium", code_like
        if faq_like and retrieval_confidence >= rag_high_confidence_score:
            return "cheap", code_like
        return "balanced", code_like

    def provider_candidates(self, bot: Any, task_class: str) -> List[Tuple[str, Optional[str]]]:
        candidates: List[Tuple[str, Optional[str]]] = []
        if task_class == "cheap":
            candidates.extend([
                ("local", os.getenv("ROUTER_LOCAL_MODEL", "qwen2.5:1.5b")),
                ("gemini", os.getenv("ROUTER_GEMINI_MODEL", "gemini-2.5-flash")),
                ("openai", os.getenv("ROUTER_OPENAI_BALANCED_MODEL", "gpt-4.1-mini")),
            ])
        elif task_class == "premium":
            candidates.extend([
                ("claude", os.getenv("ROUTER_CLAUDE_PREMIUM_MODEL", "claude-sonnet-4-20250514")),
                ("openai", os.getenv("ROUTER_OPENAI_PREMIUM_MODEL", "gpt-4.1")),
            ])
        else:
            candidates.extend([
                ("gemini", os.getenv("ROUTER_GEMINI_MODEL", "gemini-2.5-flash")),
                ("openai", os.getenv("ROUTER_OPENAI_BALANCED_MODEL", "gpt-4.1-mini")),
                ("local", os.getenv("ROUTER_LOCAL_MODEL", "qwen2.5:1.5b")),
            ])

        configured = (str(getattr(bot, "provider_type", "local") or "local"), getattr(bot, "model", None))
        if configured not in candidates:
            candidates.append(configured)

        deduped: List[Tuple[str, Optional[str]]] = []
        for candidate in candidates:
            if candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def select_route(self, bot: Any, message: str, retrieval_confidence: float) -> RoutingDecision:
        task_class, code_like = self.classify_task(message, retrieval_confidence)
        provider_candidates = self.provider_candidates(bot, task_class)
        chosen_provider = provider_candidates[0] if provider_candidates else (getattr(bot, "provider_type", "local"), getattr(bot, "model", None))
        allow_downshift = task_class != "premium" and not code_like
        reason = f"{task_class}_route confidence={retrieval_confidence:.2f}"
        return RoutingDecision(
            task_class=task_class,
            provider_type=chosen_provider[0],
            model=chosen_provider[1],
            allow_downshift_fallback=allow_downshift,
            reason=reason,
        )

    def ordered_candidates(self, bot: Any, routing_decision: RoutingDecision) -> List[Tuple[str, Optional[str]]]:
        candidates = self.provider_candidates(bot, routing_decision.task_class)
        preferred = (routing_decision.provider_type, routing_decision.model)
        if preferred in candidates:
            return [preferred] + [candidate for candidate in candidates if candidate != preferred]
        return [preferred] + candidates


router_service = RouterService()
