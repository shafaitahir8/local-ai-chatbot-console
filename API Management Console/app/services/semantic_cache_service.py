"""
Persistent semantic cache for chat responses.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.database import SemanticCacheEntry


class SemanticCacheService:
    """Store and retrieve exact or near-match cached responses."""

    def __init__(self) -> None:
        self.max_candidates = max(5, int(os.getenv("SEMANTIC_CACHE_CANDIDATE_LIMIT", "50")))
        self.similarity_threshold = max(
            0.5,
            min(0.999, float(os.getenv("SEMANTIC_CACHE_SIMILARITY_THRESHOLD", "0.965"))),
        )
        self.enabled = os.getenv("SEMANTIC_CACHE_ENABLED", "true").strip().lower() not in {"0", "false", "no"}

    def normalize_query(self, query: str) -> str:
        return " ".join((query or "").strip().lower().split())

    def hash_text(self, value: str) -> str:
        encoded = (value or "").encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def build_key_fields(
        self,
        *,
        bot_id: str,
        provider: str,
        model: str,
        manifest_version: str,
        system_prompt: str,
        query: str,
    ) -> dict[str, str]:
        normalized_query = self.normalize_query(query)
        return {
            "bot_id": bot_id,
            "provider": provider,
            "model": model,
            "manifest_version": manifest_version or "missing",
            "system_prompt_hash": self.hash_text(system_prompt or ""),
            "normalized_query": normalized_query,
            "normalized_query_hash": self.hash_text(normalized_query),
        }

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return -1.0
        dot = sum(float(a) * float(b) for a, b in zip(left, right))
        left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
        right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
        if left_norm <= 0 or right_norm <= 0:
            return -1.0
        return dot / (left_norm * right_norm)

    async def get_cached_response(
        self,
        db: AsyncSession,
        *,
        bot_id: str,
        provider: str,
        model: str,
        manifest_version: str,
        system_prompt: str,
        query: str,
        query_embedding: Optional[list[float]] = None,
    ) -> tuple[Optional[dict[str, Any]], Optional[str], Optional[float]]:
        if not self.enabled:
            return None, None, None

        key_fields = self.build_key_fields(
            bot_id=bot_id,
            provider=provider,
            model=model,
            manifest_version=manifest_version,
            system_prompt=system_prompt,
            query=query,
        )

        exact_stmt = select(SemanticCacheEntry).filter(
            SemanticCacheEntry.bot_id == key_fields["bot_id"],
            SemanticCacheEntry.provider == key_fields["provider"],
            SemanticCacheEntry.model == key_fields["model"],
            SemanticCacheEntry.manifest_version == key_fields["manifest_version"],
            SemanticCacheEntry.system_prompt_hash == key_fields["system_prompt_hash"],
            SemanticCacheEntry.normalized_query_hash == key_fields["normalized_query_hash"],
        ).limit(1)
        exact_result = await db.execute(exact_stmt)
        exact_entry = exact_result.scalar_one_or_none()
        if exact_entry and exact_entry.response_payload:
            return dict(exact_entry.response_payload), "exact", 1.0

        if not query_embedding:
            return None, None, None

        candidates_stmt = select(SemanticCacheEntry).filter(
            SemanticCacheEntry.bot_id == key_fields["bot_id"],
            SemanticCacheEntry.provider == key_fields["provider"],
            SemanticCacheEntry.model == key_fields["model"],
            SemanticCacheEntry.manifest_version == key_fields["manifest_version"],
            SemanticCacheEntry.system_prompt_hash == key_fields["system_prompt_hash"],
        ).order_by(SemanticCacheEntry.last_used_at.desc()).limit(self.max_candidates)
        candidates_result = await db.execute(candidates_stmt)
        best_payload = None
        best_similarity = self.similarity_threshold
        for candidate in candidates_result.scalars():
            similarity = self._cosine_similarity(query_embedding, list(candidate.query_embedding or []))
            if similarity >= max(self.similarity_threshold, float(candidate.similarity_threshold or 0.0)):
                if similarity >= best_similarity:
                    best_similarity = similarity
                    best_payload = dict(candidate.response_payload or {})
        if best_payload:
            return best_payload, "near", best_similarity
        return None, None, None

    def upsert_entry(
        self,
        db: Session,
        *,
        bot_id: str,
        provider: str,
        model: str,
        manifest_version: str,
        system_prompt: str,
        query: str,
        query_embedding: Optional[list[float]],
        response_payload: dict[str, Any],
    ) -> SemanticCacheEntry:
        key_fields = self.build_key_fields(
            bot_id=bot_id,
            provider=provider,
            model=model,
            manifest_version=manifest_version,
            system_prompt=system_prompt,
            query=query,
        )
        entry = db.query(SemanticCacheEntry).filter(
            SemanticCacheEntry.bot_id == key_fields["bot_id"],
            SemanticCacheEntry.provider == key_fields["provider"],
            SemanticCacheEntry.model == key_fields["model"],
            SemanticCacheEntry.manifest_version == key_fields["manifest_version"],
            SemanticCacheEntry.system_prompt_hash == key_fields["system_prompt_hash"],
            SemanticCacheEntry.normalized_query_hash == key_fields["normalized_query_hash"],
        ).first()
        if not entry:
            entry = SemanticCacheEntry(
                bot_id=key_fields["bot_id"],
                provider=key_fields["provider"],
                model=key_fields["model"],
                manifest_version=key_fields["manifest_version"],
                system_prompt_hash=key_fields["system_prompt_hash"],
                normalized_query_hash=key_fields["normalized_query_hash"],
                normalized_query=key_fields["normalized_query"],
            )
            db.add(entry)

        entry.query_embedding = list(query_embedding or [])
        entry.response_payload = dict(response_payload or {})
        entry.similarity_threshold = self.similarity_threshold
        entry.hit_count = int(entry.hit_count or 0) + 1
        entry.last_used_at = datetime.utcnow()
        db.commit()
        db.refresh(entry)
        return entry


semantic_cache_service = SemanticCacheService()
