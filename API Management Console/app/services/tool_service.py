"""
Structured tool registry, normalization, and execution helpers.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.database import ScraperJob
from ..models.schemas import QuickChipSettings, ToolDefinition, ToolSettings
from .system_inspection_service import build_supervisor_health_report
from .supervisor_service import supervisor_service


LEGACY_TOOL_ALIASES = {
    "quick_chips.suggest": "ui.suggest_chips",
}

MAX_DYNAMIC_QUICK_CHIPS = 4
DEFAULT_LOCAL_SCRIPT_TIMEOUT_SECONDS = 20
VALID_CHIP_VISIBILITY_MODES = {"always", "on_start", "keywords", "ai_only"}
AI_VISIBLE_CHIP_MODES = {"always", "keywords", "ai_only"}
LEGACY_CHIP_VISIBILITY_MAP = {
    "always": "always",
    "on_start_only": "on_start",
    "conditional": "keywords",
}
VALID_CHIP_SENTIMENTS = {"positive", "negative", "any"}
CHIP_INTENT_SYNONYMS = {
    "agent": "human",
    "human": "human",
    "person": "human",
    "representative": "human",
    "sales": "sales",
    "demo": "demo",
    "meeting": "meeting",
    "call": "meeting",
    "consultation": "meeting",
    "book": "meeting",
    "schedule": "meeting",
    "talk": "speak",
    "speak": "speak",
    "chat": "speak",
    "contact": "contact",
    "email": "contact",
}


BUILTIN_TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "ui.suggest_chips": ToolDefinition(
        id="ui.suggest_chips",
        display_name="Quick Chips",
        description="Suggest 1-4 quick reply or action chips for the UI when the user would benefit from next-step options.",
        parameters={
            "type": "object",
            "properties": {
                "chips": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "action_type": {
                                "type": "string",
                                "enum": ["suggested_prompt", "tool_call", "link"],
                            },
                            "value": {"type": "string"},
                            "style": {
                                "type": "string",
                                "enum": ["primary", "outline", "ghost"],
                            },
                            "visibility_mode": {
                                "type": "string",
                                "enum": ["always", "on_start", "keywords", "ai_only"],
                            },
                            "trigger_keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "ai_instruction": {"type": "string"},
                            "sentiment_filter": {
                                "type": "string",
                                "enum": ["positive", "negative", "any"],
                            },
                            "priority": {"type": "integer"},
                            "payload": {"type": "object"},
                        },
                        "required": ["label"],
                    },
                },
                "reason": {"type": "string"},
            },
            "required": ["chips"],
        },
        execution_type="builtin",
        scope="public",
        is_mutating=False,
        requires_approval=False,
    ).model_dump(),
    "system_status.get": ToolDefinition(
        id="system_status.get",
        display_name="System Status",
        description="Read current runtime health, tunnel, gateway, and safe-mode status before answering operational questions.",
        parameters={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["summary", "gateway", "tunnel", "health"],
                }
            },
        },
        execution_type="builtin",
        scope="admin_only",
        is_mutating=False,
        requires_approval=False,
    ).model_dump(),
    "knowledge.scrape.enqueue": ToolDefinition(
        id="knowledge.scrape.enqueue",
        display_name="Queue Website Scrape",
        description="Queue a website scrape for the bot knowledge base only when the user explicitly asks to ingest or refresh website content.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "depth": {"type": "integer"},
                "limit": {"type": "integer"},
                "focus": {"type": "string"},
                "sitemap_only": {"type": "boolean"},
            },
            "required": ["url"],
        },
        execution_type="builtin",
        scope="admin_only",
        is_mutating=True,
        requires_approval=True,
    ).model_dump(),
    "knowledge.rebuild_embeddings": ToolDefinition(
        id="knowledge.rebuild_embeddings",
        display_name="Rebuild Embeddings",
        description="Schedule an embeddings rebuild only when the user explicitly asks to refresh the bot knowledge index.",
        parameters={
            "type": "object",
            "properties": {},
        },
        execution_type="builtin",
        scope="admin_only",
        is_mutating=True,
        requires_approval=True,
    ).model_dump(),
}


def _canonical_tool_id(tool_id: str) -> str:
    normalized = str(tool_id or "").strip()
    return LEGACY_TOOL_ALIASES.get(normalized, normalized)


def get_builtin_tool_definition(tool_id: str) -> Optional[Dict[str, Any]]:
    canonical = _canonical_tool_id(tool_id)
    builtin = BUILTIN_TOOL_DEFINITIONS.get(canonical)
    if not builtin:
        return None
    return copy.deepcopy(builtin)


def normalize_tool_settings(
    raw: Optional[Dict[str, Any]],
    *,
    fallback_enabled: bool = False,
) -> Dict[str, Any]:
    payload = dict(raw or {})
    if "enabled" not in payload:
        payload["enabled"] = bool(fallback_enabled)
    return ToolSettings(**payload).model_dump()


def normalize_quick_chip_definition(
    value: Any,
    *,
    default_visibility_mode: str = "always",
    assign_id: bool = False,
) -> Optional[Dict[str, Any]]:
    is_dict_payload = isinstance(value, dict)
    if isinstance(value, str):
        payload: Dict[str, Any] = {
            "label": value.strip(),
            "action_type": "suggested_prompt",
            "value": value.strip(),
            "visibility_mode": default_visibility_mode,
        }
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        return None

    label = str(payload.get("label") or "").strip()
    if not label:
        return None

    action_type = str(payload.get("action_type") or "").strip()
    resolved_value = str(payload.get("value") or "").strip()

    if not action_type:
        prompt = str(payload.get("prompt") or "").strip()
        tool_id = str(payload.get("tool_id") or "").strip()
        if tool_id:
            action_type = "tool_call"
            resolved_value = _canonical_tool_id(tool_id)
        elif prompt:
            action_type = "suggested_prompt"
            resolved_value = prompt
        else:
            action_type = "suggested_prompt"
            resolved_value = label

    if not resolved_value and not is_dict_payload:
        resolved_value = label
    if action_type == "tool_call":
        resolved_value = _canonical_tool_id(resolved_value)

    raw_visibility = str(
        payload.get("visibility_mode")
        or payload.get("visibility")
        or default_visibility_mode
        or "always"
    ).strip().lower()
    visibility_mode = LEGACY_CHIP_VISIBILITY_MAP.get(raw_visibility, raw_visibility)
    if visibility_mode not in VALID_CHIP_VISIBILITY_MODES:
        visibility_mode = default_visibility_mode

    raw_keywords = list(payload.get("trigger_keywords") or [])
    trigger_keywords: List[str] = []
    seen_keywords: set[str] = set()
    for keyword in raw_keywords:
        next_keyword = str(keyword or "").strip()
        if not next_keyword:
            continue
        lookup = next_keyword.lower()
        if lookup in seen_keywords:
            continue
        seen_keywords.add(lookup)
        trigger_keywords.append(next_keyword[:120])

    ai_instruction = str(payload.get("ai_instruction") or "").strip()
    sentiment_filter = str(payload.get("sentiment_filter") or "any").strip().lower() or "any"
    if sentiment_filter not in VALID_CHIP_SENTIMENTS:
        sentiment_filter = "any"
    try:
        priority = int(payload.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0

    chip_payload = dict(payload.get("payload") or {})
    normalized = {
        "label": label[:120],
        "action_type": action_type,
        "value": resolved_value[:500],
        "style": str(payload.get("style") or "outline").strip() or "outline",
        "visibility_mode": visibility_mode,
        "trigger_keywords": trigger_keywords,
        "ai_instruction": ai_instruction[:500],
        "sentiment_filter": sentiment_filter,
        "priority": max(0, min(1000, priority)),
        "payload": chip_payload,
    }
    chip_id = str(payload.get("id") or "").strip()
    if chip_id:
        normalized["id"] = chip_id[:120]
    elif assign_id:
        digest = hashlib.sha1(
            f"{normalized['label']}|{normalized['action_type']}|{normalized['value']}".encode("utf-8")
        ).hexdigest()[:10]
        normalized["id"] = f"chip-{digest}"
    return normalized


def quick_chip_has_required_fields(chip: Dict[str, Any]) -> bool:
    label = str(chip.get("label") or "").strip()
    action_type = str(chip.get("action_type") or "suggested_prompt").strip()
    value = str(chip.get("value") or "").strip()
    visibility_mode = str(chip.get("visibility_mode") or "always").strip()
    trigger_keywords = [
        str(item or "").strip()
        for item in list(chip.get("trigger_keywords") or [])
        if str(item or "").strip()
    ]
    ai_instruction = str(chip.get("ai_instruction") or "").strip()

    if not label:
        return False
    if action_type not in {"suggested_prompt", "tool_call", "link"}:
        return False
    if not value:
        return False
    if visibility_mode == "keywords" and not trigger_keywords and not ai_instruction:
        return False
    if visibility_mode == "ai_only" and not ai_instruction:
        return False
    return True


def normalize_quick_chip_settings(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = dict(raw or {})
    initial_chips = []
    for chip in list(payload.get("initial_chips") or []):
        normalized = normalize_quick_chip_definition(chip, default_visibility_mode="on_start")
        if normalized:
            initial_chips.append(normalized)
    payload["initial_chips"] = initial_chips
    return QuickChipSettings(**payload).model_dump()


def _normalize_tool_definition(tool: Any) -> Optional[Dict[str, Any]]:
    if isinstance(tool, str):
        return get_builtin_tool_definition(tool)
    if not isinstance(tool, dict):
        return None

    payload = dict(tool)
    canonical_id = _canonical_tool_id(str(payload.get("id") or "").strip())
    if not canonical_id:
        return None
    payload["id"] = canonical_id

    builtin = get_builtin_tool_definition(canonical_id)
    if builtin:
        merged = {
            **builtin,
            **payload,
            "id": canonical_id,
            "display_name": str(payload.get("display_name") or builtin["display_name"]).strip() or builtin["display_name"],
            "description": str(payload.get("description") or builtin["description"]).strip() or builtin["description"],
        }
        payload = merged

    if "display_name" not in payload or not str(payload.get("display_name") or "").strip():
        payload["display_name"] = canonical_id.replace(".", " ").replace("_", " ").title()
    if "description" not in payload or not str(payload.get("description") or "").strip():
        payload["description"] = payload["display_name"]
    if "parameters" not in payload or not isinstance(payload.get("parameters"), dict):
        payload["parameters"] = {"type": "object", "properties": {}}
    if "execution_config" not in payload or not isinstance(payload.get("execution_config"), dict):
        payload["execution_config"] = {}

    if payload.get("execution_type") == "local_script":
        payload["scope"] = "admin_only"
        payload["requires_approval"] = True

    if payload.get("scope") == "public":
        payload["is_mutating"] = False
        payload["requires_approval"] = False

    return ToolDefinition(**payload).model_dump()


def normalize_tool_definitions(values: Optional[List[Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for value in values or []:
        tool = _normalize_tool_definition(value)
        if not tool:
            continue
        tool_id = tool["id"]
        if tool_id in seen:
            continue
        normalized.append(tool)
        seen.add(tool_id)
    return normalized


def tool_definition_map(values: Optional[List[Any]]) -> Dict[str, Dict[str, Any]]:
    return {tool["id"]: tool for tool in normalize_tool_definitions(values)}


def get_tool_catalog() -> List[Dict[str, Any]]:
    catalog = []
    for tool in normalize_tool_definitions(list(BUILTIN_TOOL_DEFINITIONS.values())):
        catalog.append(
            {
                **tool,
                "is_builtin": True,
            }
        )
    return catalog


def filter_quick_chips(
    chips: List[Dict[str, Any]],
    allowed_tool_definitions: Optional[List[Any]],
    *,
    surface_scope: str,
) -> List[Dict[str, Any]]:
    allowed_map = tool_definition_map(allowed_tool_definitions)
    filtered: List[Dict[str, Any]] = []
    for chip in list(chips or []):
        if not quick_chip_has_required_fields(chip):
            continue
        action_type = str(chip.get("action_type") or "suggested_prompt").strip()
        if action_type != "tool_call":
            filtered.append(chip)
            continue

        tool_id = _canonical_tool_id(str(chip.get("value") or "").strip())
        tool = allowed_map.get(tool_id)
        if not tool:
            continue
        if surface_scope == "public" and (tool.get("scope") != "public" or bool(tool.get("is_mutating"))):
            continue

        next_chip = dict(chip)
        next_chip["value"] = tool["id"]
        filtered.append(next_chip)
    return filtered


def merge_quick_chip_sources(
    *sources: List[Dict[str, Any]],
    allowed_tool_definitions: Optional[List[Any]] = None,
    surface_scope: str,
    max_items: int = MAX_DYNAMIC_QUICK_CHIPS,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for source in sources:
        for chip in list(source or []):
            next_chip = normalize_quick_chip_definition(chip, assign_id=True)
            if next_chip:
                normalized.append(next_chip)

    filtered = filter_quick_chips(normalized, allowed_tool_definitions, surface_scope=surface_scope)
    filtered.sort(
        key=lambda chip: (
            -int(chip.get("priority") or 0),
            str(chip.get("label") or "").lower(),
            str(chip.get("id") or ""),
        )
    )

    merged: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for chip in filtered:
        chip_id = str(chip.get("id") or "").strip()
        if not chip_id or chip_id in seen_ids:
            continue
        seen_ids.add(chip_id)
        merged.append(chip)
        if len(merged) >= max_items:
            break
    return merged


def _keyword_matches_message(message: str, keyword: str) -> bool:
    cleaned_keyword = str(keyword or "").strip()
    if not cleaned_keyword:
        return False
    parts = [re.escape(part) for part in cleaned_keyword.split() if part]
    if not parts:
        return False
    pattern = r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)"
    return re.search(pattern, message, flags=re.IGNORECASE) is not None


def _extract_chip_intent_tokens(*texts: str) -> set[str]:
    tokens: set[str] = set()
    for text in texts:
        for raw_token in re.findall(r"[a-z0-9]+", str(text or "").lower()):
            if len(raw_token) < 3:
                continue
            tokens.add(CHIP_INTENT_SYNONYMS.get(raw_token, raw_token))
    return tokens


def scan_quick_chip_keywords(
    message: str,
    quick_chip_settings: Optional[Dict[str, Any]],
    allowed_tool_definitions: Optional[List[Any]] = None,
    *,
    surface_scope: str,
) -> List[Dict[str, Any]]:
    settings = normalize_quick_chip_settings(quick_chip_settings)
    matched: List[Dict[str, Any]] = []
    message_text = str(message or "").strip()
    if not message_text:
        return []
    message_tokens = _extract_chip_intent_tokens(message_text)

    for chip in settings.get("initial_chips", []):
        normalized = normalize_quick_chip_definition(chip, assign_id=True)
        if not normalized:
            continue
        if not quick_chip_has_required_fields(normalized):
            continue
        visibility_mode = normalized.get("visibility_mode")
        if visibility_mode == "always":
            matched.append(normalized)
            continue
        if visibility_mode != "keywords":
            continue
        keywords = list(normalized.get("trigger_keywords") or [])
        if any(_keyword_matches_message(message_text, keyword) for keyword in keywords):
            matched.append(normalized)
            continue

        chip_tokens = _extract_chip_intent_tokens(
            " ".join(keywords),
            str(normalized.get("ai_instruction") or ""),
            str(normalized.get("label") or ""),
        )
        if len(chip_tokens & message_tokens) >= 2:
            matched.append(normalized)

    return merge_quick_chip_sources(
        matched,
        allowed_tool_definitions=allowed_tool_definitions,
        surface_scope=surface_scope,
    )


def build_quick_chip_ai_system_hint(quick_chip_settings: Optional[Dict[str, Any]]) -> str:
    settings = normalize_quick_chip_settings(quick_chip_settings)
    eligible: List[Dict[str, Any]] = []
    for chip in settings.get("initial_chips", []):
        normalized = normalize_quick_chip_definition(chip, assign_id=True)
        if not normalized:
            continue
        if not quick_chip_has_required_fields(normalized):
            continue
        if normalized.get("visibility_mode") not in AI_VISIBLE_CHIP_MODES:
            continue
        instruction = str(normalized.get("ai_instruction") or "").strip()
        if not instruction:
            continue
        eligible.append(normalized)

    if not eligible:
        return ""

    eligible.sort(
        key=lambda chip: (
            -int(chip.get("priority") or 0),
            str(chip.get("label") or "").lower(),
        )
    )
    lines = [
        "Quick-chip suggestion guidance:",
        "Use the internal tool `ui.suggest_chips` only when a chip would genuinely help the user take the next step.",
        "Prefer chips from this catalog instead of inventing new ones. Return at most 4 chips total.",
        "The chip `label` is what the user sees in the chat UI. The chip `value` is the hidden action payload that the client can send silently.",
    ]
    for chip in eligible:
        lines.append(
            "- "
            + f"id={chip['id']}; label={chip['label']}; action_type={chip['action_type']}; "
            + f"value={chip['value']}; priority={chip.get('priority', 0)}; when={chip['ai_instruction']}"
        )
    return "\n".join(lines)


def get_startup_quick_chips(
    quick_chip_settings: Optional[Dict[str, Any]],
    allowed_tool_definitions: Optional[List[Any]] = None,
    *,
    surface_scope: str = "public",
) -> List[Dict[str, Any]]:
    settings = normalize_quick_chip_settings(quick_chip_settings)
    startup_chips: List[Dict[str, Any]] = []
    for chip in settings.get("initial_chips", []):
        visibility_mode = chip.get("visibility_mode")
        if visibility_mode not in {"always", "on_start"}:
            continue
        normalized = normalize_quick_chip_definition(chip, default_visibility_mode="on_start", assign_id=True)
        if normalized and quick_chip_has_required_fields(normalized):
            startup_chips.append(normalized)
    return merge_quick_chip_sources(
        startup_chips,
        allowed_tool_definitions=allowed_tool_definitions,
        surface_scope=surface_scope,
    )


def sanitize_quick_chips(
    raw_chips: List[dict],
    allowed_tool_definitions: Optional[List[Any]] = None,
    *,
    surface_scope: str = "public",
) -> List[Dict[str, Any]]:
    chips: List[Dict[str, Any]] = []
    for chip in list(raw_chips or []):
        normalized = normalize_quick_chip_definition(
            chip,
            default_visibility_mode="ai_only",
            assign_id=True,
        )
        if normalized and quick_chip_has_required_fields(normalized):
            chips.append(normalized)
    return merge_quick_chip_sources(
        chips,
        allowed_tool_definitions=allowed_tool_definitions,
        surface_scope=surface_scope,
    )


def build_provider_tool_specs(
    allowed_tool_definitions: List[Any],
    *,
    include_admin: bool,
    allow_ai_suggestions: bool = True,
) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for tool in normalize_tool_definitions(allowed_tool_definitions):
        if tool["scope"] == "admin_only" and not include_admin:
            continue
        if tool["id"] == "ui.suggest_chips" and not allow_ai_suggestions:
            continue
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": tool["id"],
                    "description": tool["description"],
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                },
            }
        )
    return specs


def _build_status_summary(section: str) -> Dict[str, Any]:
    report = build_supervisor_health_report()
    components = report.get("components", {}) or {}
    if section == "gateway":
        return {"gateway": components.get("gateway-agent", {})}
    if section == "tunnel":
        return {"tunnel": components.get("tunnel-agent", {})}
    if section == "health":
        return {
            "components": components,
            "safe_modes": report.get("safe_modes", []),
            "public_base_url": report.get("public_base_url"),
        }
    return {
        "hot_state": report.get("hot_state"),
        "public_base_url": report.get("public_base_url"),
        "safe_modes": report.get("safe_modes", []),
        "components": {
            key: {
                "status": value.get("status"),
                "detail": value.get("detail"),
            }
            for key, value in components.items()
        },
    }


def _extract_message_from_result(result: Dict[str, Any]) -> str:
    for key in ("message", "response", "detail", "summary"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    body = result.get("body")
    if isinstance(body, dict):
        for key in ("message", "response", "detail", "summary"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(body, str) and body.strip():
        return body.strip()
    return ""


def summarize_pending_action(tool: Dict[str, Any], payload: Dict[str, Any]) -> str:
    if tool["id"] == "knowledge.scrape.enqueue":
        url = str(payload.get("url") or "").strip()
        return f"Queue scrape for {url}" if url else "Queue scrape job"
    if tool["id"] == "knowledge.rebuild_embeddings":
        return "Rebuild embeddings for this bot"
    return f"Run {tool['display_name']}"


async def _execute_builtin_tool(
    *,
    db: Session | AsyncSession,
    bot_id: str,
    tool: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    tool_id = tool["id"]
    if tool_id == "ui.suggest_chips":
        chips = sanitize_quick_chips(list(payload.get("chips") or []))
        return {"chips": chips, "message": "Suggested quick chips updated."}
    if tool_id == "system_status.get":
        section = str(payload.get("section") or "summary").strip().lower()
        if section not in {"summary", "gateway", "tunnel", "health"}:
            section = "summary"
        summary = _build_status_summary(section)
        return {"summary": summary, "message": f"Loaded {section} system status."}
    if tool_id == "knowledge.scrape.enqueue":
        sync_db = db.sync_session if isinstance(db, AsyncSession) else db
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("Scrape URL is required")
        depth = max(1, min(3, int(payload.get("depth") or 2)))
        limit = max(1, min(500, int(payload.get("limit") or 20)))
        focus = str(payload.get("focus") or "").strip()
        sitemap_only = bool(payload.get("sitemap_only", False))
        job = ScraperJob(
            bot_id=bot_id,
            url=url,
            depth=depth,
            consent_confirmed=True,
            status="pending",
        )
        sync_db.add(job)
        sync_db.commit()
        sync_db.refresh(job)
        supervisor_service.ensure_task_run(
            sync_db,
            task_type="scraper_job",
            task_key=job.id,
            payload_json={
                "job_id": job.id,
                "url": url,
                "depth": depth,
                "limit": limit,
                "focus": focus,
                "sitemap_only": sitemap_only,
                "bot_id": bot_id,
                "append": True,
                "auto_index_bot": True,
            },
            checkpoint_json={},
            state="pending",
        )
        sync_db.commit()
        return {
            "job_id": job.id,
            "url": url,
            "status": job.status,
            "limit": limit,
            "focus": focus,
            "sitemap_only": sitemap_only,
            "message": f"Queued a scrape job for {url}.",
        }
    if tool_id == "knowledge.rebuild_embeddings":
        from .bot_service import bot_service

        sync_db = db.sync_session if isinstance(db, AsyncSession) else db
        result = await bot_service.rebuild_embeddings(sync_db, bot_id)
        return {
            **result,
            "message": "Scheduled an embeddings rebuild for this bot.",
        }
    raise ValueError(f"Unsupported builtin tool: {tool_id}")


async def _execute_webhook_tool(
    *,
    bot_id: str,
    tool: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    config = dict(tool.get("execution_config") or {})
    url = str(config.get("url") or "").strip()
    if not url:
        raise ValueError("Webhook URL is required")

    method = str(config.get("method") or "POST").strip().upper()
    headers = dict(config.get("headers") or {})
    timeout_seconds = max(1.0, float(config.get("timeout_seconds") or 20))
    request_body = {
        "bot_id": bot_id,
        "tool_id": tool["id"],
        "payload": payload,
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.request(method, url, headers=headers, json=request_body)
        response.raise_for_status()

    parsed_body: Any
    content_type = str(response.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        parsed_body = response.json()
    else:
        parsed_body = response.text
    message = ""
    if isinstance(parsed_body, dict):
        message = _extract_message_from_result({"body": parsed_body})
    elif isinstance(parsed_body, str):
        message = parsed_body.strip()
    return {
        "status_code": response.status_code,
        "body": parsed_body,
        "message": message or f"{tool['display_name']} completed successfully.",
    }


async def _execute_local_script_tool(
    *,
    bot_id: str,
    tool: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    config = dict(tool.get("execution_config") or {})
    script_path = Path(str(config.get("script_path") or "").strip())
    if not script_path:
        raise ValueError("Local script path is required")
    if not script_path.exists():
        raise ValueError(f"Local script not found: {script_path}")

    working_directory_raw = str(config.get("working_directory") or script_path.parent).strip()
    working_directory = Path(working_directory_raw)
    if not working_directory.exists() or not working_directory.is_dir():
        raise ValueError(f"Working directory is invalid: {working_directory}")

    timeout_seconds = max(
        1,
        int(config.get("timeout_seconds") or DEFAULT_LOCAL_SCRIPT_TIMEOUT_SECONDS),
    )
    if script_path.suffix.lower() == ".py":
        command = [sys.executable, str(script_path)]
    elif script_path.suffix.lower() == ".js":
        command = ["node", str(script_path)]
    else:
        command = [str(script_path)]

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(working_directory),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdin_payload = json.dumps(
        {
            "bot_id": bot_id,
            "tool_id": tool["id"],
            "payload": payload,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(stdin_payload), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise ValueError(f"Local script timed out after {timeout_seconds} seconds")

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise ValueError(stderr_text or stdout_text or f"Local script exited with code {process.returncode}")

    parsed_output: Any
    if stdout_text:
        try:
            parsed_output = json.loads(stdout_text)
        except json.JSONDecodeError:
            parsed_output = stdout_text
    else:
        parsed_output = {}

    message = ""
    if isinstance(parsed_output, dict):
        message = _extract_message_from_result(parsed_output)
    elif isinstance(parsed_output, str):
        message = parsed_output
    return {
        "stdout": parsed_output,
        "stderr": stderr_text,
        "message": message or f"{tool['display_name']} completed successfully.",
    }


async def execute_tool_definition(
    *,
    db: Session | AsyncSession,
    bot_id: str,
    tool: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    execution_type = tool.get("execution_type")
    if execution_type == "builtin":
        return await _execute_builtin_tool(db=db, bot_id=bot_id, tool=tool, payload=payload)
    if execution_type == "webhook":
        return await _execute_webhook_tool(bot_id=bot_id, tool=tool, payload=payload)
    if execution_type == "local_script":
        return await _execute_local_script_tool(bot_id=bot_id, tool=tool, payload=payload)
    raise ValueError(f"Unsupported execution type: {execution_type}")


def _build_tool_event(tool: Dict[str, Any], status: str, **extra: Any) -> Dict[str, Any]:
    event = {
        "tool_id": tool["id"],
        "status": status,
        "mutating": bool(tool.get("is_mutating")),
        "execution_type": tool.get("execution_type"),
        "scope": tool.get("scope"),
    }
    event.update(extra)
    return event


def build_action_follow_up(tool: Dict[str, Any], result: Dict[str, Any]) -> str:
    message = _extract_message_from_result(result)
    if message:
        return message
    return f"{tool['display_name']} completed successfully."


async def process_tool_call(
    *,
    db: Session | AsyncSession,
    bot_id: str,
    tool_definitions: List[Any],
    tool_id: str,
    arguments: Dict[str, Any],
    allow_admin: bool,
    execute_mutations: bool,
) -> Dict[str, Any]:
    tool = tool_definition_map(tool_definitions).get(_canonical_tool_id(tool_id))
    if not tool:
        return {"result_for_model": {"status": "unknown_tool"}, "tool_id": _canonical_tool_id(tool_id)}
    if tool["scope"] == "admin_only" and not allow_admin:
        return {"result_for_model": {"status": "forbidden"}, "tool_id": tool["id"]}

    requires_gate = bool(tool.get("requires_approval") or tool.get("is_mutating"))
    if requires_gate and not execute_mutations:
        pending_action = {
            "id": f"pending-{tool['id']}-{int(datetime.utcnow().timestamp())}",
            "tool_id": tool["id"],
            "label": tool["display_name"],
            "summary": summarize_pending_action(tool, arguments),
            "payload": arguments,
            "requires_approval": True,
            "mutating": bool(tool.get("is_mutating")),
            "execution_type": tool.get("execution_type"),
            "scope": tool.get("scope"),
        }
        return {
            "tool_id": tool["id"],
            "pending_tool_action": pending_action,
            "tool_event": _build_tool_event(tool, "approval_required"),
            "result_for_model": {"status": "approval_required", "pending_tool_action": pending_action},
        }

    result = await execute_tool_definition(
        db=db,
        bot_id=bot_id,
        tool=tool,
        payload=arguments,
    )
    processed: Dict[str, Any] = {
        "tool_id": tool["id"],
        "result": result,
        "tool_event": _build_tool_event(tool, "completed"),
        "result_for_model": {"status": "ok", "result": result},
    }
    if tool["id"] == "ui.suggest_chips":
        surface_scope = "admin" if allow_admin else "public"
        filtered_chips = filter_quick_chips(
            list(result.get("chips") or []),
            tool_definitions,
            surface_scope=surface_scope,
        )
        processed["quick_chips"] = filtered_chips
        processed["result_for_model"] = {
            "status": "ok",
            "chips": filtered_chips,
        }
    return processed


async def trigger_public_tool(
    *,
    db: Session | AsyncSession,
    bot_id: str,
    tool_definitions: List[Any],
    tool_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    tool = tool_definition_map(tool_definitions).get(_canonical_tool_id(tool_id))
    if not tool:
        raise ValueError("Tool not found")
    if tool["scope"] != "public":
        raise ValueError("Tool is not available on public surfaces")
    if tool.get("is_mutating"):
        raise ValueError("Mutating tools cannot be triggered from public surfaces")

    result = await execute_tool_definition(
        db=db,
        bot_id=bot_id,
        tool=tool,
        payload=payload,
    )
    quick_chips = filter_quick_chips(
        list(result.get("chips") or []),
        tool_definitions,
        surface_scope="public",
    )
    response_text = build_action_follow_up(tool, result)
    return {
        "status": "ok",
        "tool_id": tool["id"],
        "response": response_text,
        "quick_chips": quick_chips,
        "tool_events": [_build_tool_event(tool, "completed")],
        "pending_tool_actions": [],
        "result": result,
    }
