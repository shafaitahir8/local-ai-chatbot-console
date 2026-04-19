"""
Public Chat API routes - exposed for client applications.
"""

import logging
import os
import json
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from ..models.database import get_async_db, Bot
from ..models.schemas import ChatRequest, ChatResponse, BotStatusResponse, PublicToolTriggerRequest, ToolTriggerResponse
from ..services.bot_service import bot_service, ProviderTemporarilyUnavailableError
from ..services.chat_request_service import chat_request_service
from ..services.safe_mode_service import safe_mode_service
from ..services.circuit_breaker import CircuitBreakerOpenError
from ..services.logging_utils import log_event
from ..services.metrics_service import metrics_service
from ..services.startup_warmup_service import startup_warmup_service
from ..services.supervisor_service import supervisor_service
from ..services.tool_service import get_startup_quick_chips, normalize_tool_settings, trigger_public_tool
from .auth import enforce_public_bot_token, enforce_public_json_request, rate_limit_dependency_async

router = APIRouter(prefix="/api/bot", tags=["chat"])
logger = logging.getLogger(__name__)


async def get_client_info(request: Request) -> dict:
    """Extract client information from request."""
    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent")
    }


def _stream_event(event_type: str, **payload) -> bytes:
    return (json.dumps({"type": event_type, **payload}, ensure_ascii=False) + "\n").encode("utf-8")


@router.post("/{bot_id}/chat", response_model=ChatResponse)
async def chat(
    bot_id: str,
    chat_request: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Send a message to a chatbot.
    
    This is the public API endpoint that clients use to interact with chatbots.
    No authentication required, but rate-limited per IP.
    """
    await enforce_public_json_request(request)
    await enforce_public_bot_token(request)
    # Rate limiting
    await rate_limit_dependency_async(request, bot_id, db)
    
    # Check if bot exists
    bot = await bot_service.get_bot_async(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chatbot not found"
        )
    
    # Check if bot is available
    if not bot.is_available():
        reason = "This chatbot is currently inactive."
        if bot.expiration_date and bot.expiration_date < __import__('datetime').datetime.utcnow():
            reason = "This chatbot has expired."
        elif bot.message_limit and bot.messages_used >= bot.message_limit:
            reason = "This chatbot has reached its message limit."
        
        return ChatResponse(
            response=reason,
            session_id=(chat_request.session_id or "").strip() or bot_service.create_session_id(),
            tokens_used=0,
            latency_ms=0,
            retry_count=0,
            fallback_used=False,
            status_message="bot_unavailable",
            degraded_components=[],
            data_freshness="fresh",
            safe_modes=[],
            quick_chips=[],
            tool_events=[],
            pending_tool_actions=[],
        )
    
    try:
        provider = None
        try:
            provider = bot_service._get_provider(bot)
        except Exception:
            provider = None
        active_modes = await safe_mode_service.get_active_modes_async(db, bot_id=bot_id, provider=provider)

        explicit_idempotency_key = (
            chat_request.idempotency_key
            or request.headers.get("Idempotency-Key")
        )
        request_record = await chat_request_service.prepare_async(
            db,
            bot_id=bot_id,
            session_id=chat_request.session_id,
            message=chat_request.message,
            idempotency_key=chat_request_service.build_idempotency_key(
                bot_id=bot_id,
                session_id=chat_request.session_id,
                message=chat_request.message,
                explicit_key=explicit_idempotency_key,
            ),
        )
        if request_record.state == "completed" and request_record.response_payload:
            return ChatResponse(**request_record.response_payload)
        if request_record.state == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An identical chat request is already in progress",
            )
        await chat_request_service.mark_running_async(
            db,
            request_record,
            lease_owner=f"api-{os.getpid()}",
        )
        await db.commit()

        # Process chat
        result = await bot_service.chat(
            db=db,
            bot_id=bot_id,
            message=chat_request.message,
            session_id=chat_request.session_id,
            include_rag="RAG_BYPASS_EXPLICIT" not in active_modes,
            persist_memory="DB_READ_ONLY" not in active_modes,
        )
        degraded = list(result.get('degraded_components', []))
        if "RAG_BYPASS_EXPLICIT" in active_modes and "rag" not in degraded:
            degraded.append("rag")
        if "DB_READ_ONLY" in active_modes and "memory" not in degraded:
            degraded.append("memory")
        result['degraded_components'] = degraded
        result['data_freshness'] = "stale" if degraded else result.get('data_freshness', "fresh")
        result['safe_modes'] = safe_mode_service.serialize_modes(active_modes)
        
        # Log analytics if enabled
        if bot.analytics_enabled and "DB_READ_ONLY" not in active_modes:
            try:
                client_info = await get_client_info(request)
                await supervisor_service.enqueue_outbox_async(
                    db,
                    event_type="analytics_write",
                    aggregate_type="bot",
                    aggregate_id=bot_id,
                    payload_json={
                        "bot_id": bot_id,
                        "session_id": result['session_id'],
                        "user_message": chat_request.message,
                        "assistant_response": result['response'],
                        "tokens_prompt": result.get('tokens_prompt', 0),
                        "tokens_completion": result.get('tokens_completion', 0),
                        "latency_ms": result['latency_ms'],
                        "user_ip": client_info.get('ip'),
                        "user_agent": client_info.get('user_agent'),
                    },
                    dedupe_key=f"analytics_write:{request_record.id}",
                )
            except Exception as analytics_error:
                metrics_service.inc_counter(
                    "db_write_fail_total",
                    labels={"operation": "analytics_write"},
                )
                log_event(
                    logger,
                    logging.WARNING,
                    "analytics_logging_failed",
                    component="analytics",
                    bot_id=bot_id,
                    session_id=result.get("session_id"),
                    dependency="database",
                    degraded_components=result.get("degraded_components", []),
                    error=str(analytics_error),
                )
                degraded = list(result.get('degraded_components', []))
                if "analytics" not in degraded:
                    degraded.append("analytics")
                result['degraded_components'] = degraded
                result['data_freshness'] = "stale"
                result['status_message'] = "completed_with_degraded_analytics"
        elif bot.analytics_enabled and "DB_READ_ONLY" in active_modes:
            degraded = list(result.get('degraded_components', []))
            if "analytics" not in degraded:
                degraded.append("analytics")
            result['degraded_components'] = degraded
            result['data_freshness'] = "stale"

        response_payload = {
            'response': result['response'],
            'session_id': result['session_id'],
            'tokens_used': result['tokens_used'],
            'latency_ms': result['latency_ms'],
            'retry_count': result.get('retry_count', 0),
            'fallback_used': result.get('fallback_used', False),
            'status_message': result.get('status_message', "completed"),
            'degraded_components': result.get('degraded_components', []),
            'data_freshness': result.get('data_freshness', "fresh"),
            'safe_modes': result.get('safe_modes', []),
            'context_scope': result.get('context_scope'),
            'context_sources': result.get('context_sources', []),
            'citations': result.get('citations', []),
            'quick_chips': result.get('quick_chips', []),
            'tool_events': result.get('tool_events', []),
            'pending_tool_actions': result.get('pending_tool_actions', []),
        }
        await chat_request_service.mark_completed_async(
            db,
            request_record,
            response_payload=response_payload,
            degraded_flags=response_payload['degraded_components'],
        )
        await db.commit()
        
        return ChatResponse(**response_payload)
        
    except Exception as e:
        error_message = str(e)
        if 'request_record' in locals():
            try:
                await chat_request_service.mark_failed_async(db, request_record, error_message)
                await db.commit()
            except Exception:
                await db.rollback()
        logger.exception("Chat processing error for bot %s: %s", bot_id, error_message)

        # Return user-friendly provider diagnostics as chat text so embeds don't fail hard.
        if "Gemini API error: 429" in error_message or "RESOURCE_EXHAUSTED" in error_message:
            return ChatResponse(
                response=(
                    "Google Gemini quota is exhausted for this API key/project. "
                    "Enable billing or increase quota, then try again."
                ),
                session_id=(chat_request.session_id or "").strip() or bot_service.create_session_id(),
                tokens_used=0,
                latency_ms=0,
                retry_count=0,
                fallback_used=False,
                status_message="gemini_quota_exhausted",
                degraded_components=[],
                data_freshness="fresh",
                safe_modes=[],
                quick_chips=[],
                tool_events=[],
                pending_tool_actions=[],
            )
        if "Gemini API error: 401" in error_message or "Gemini API key is required" in error_message:
            return ChatResponse(
                response=(
                    "Google Gemini API key is invalid or missing. "
                    "Update the key in bot settings and retry."
                ),
                session_id=(chat_request.session_id or "").strip() or bot_service.create_session_id(),
                tokens_used=0,
                latency_ms=0,
                retry_count=0,
                fallback_used=False,
                status_message="gemini_auth_error",
                degraded_components=[],
                data_freshness="fresh",
                safe_modes=[],
                quick_chips=[],
                tool_events=[],
                pending_tool_actions=[],
            )
        if error_message.startswith("MODEL_DOWNLOAD_STARTED::"):
            model_name = error_message.split("::", 1)[1]
            return ChatResponse(
                response=(
                    f"The local model '{model_name}' was not installed. "
                    "I started downloading it now. Please try again in a few minutes."
                ),
                session_id=(chat_request.session_id or "").strip() or bot_service.create_session_id(),
                tokens_used=0,
                latency_ms=0,
                retry_count=0,
                fallback_used=False,
                status_message="model_download_started",
                degraded_components=[],
                data_freshness="fresh",
                safe_modes=[],
                quick_chips=[],
                tool_events=[],
                pending_tool_actions=[],
            )
        if error_message.startswith("MODEL_DOWNLOAD_IN_PROGRESS::"):
            model_name = error_message.split("::", 1)[1]
            return ChatResponse(
                response=(
                    f"The local model '{model_name}' is still downloading. "
                    "Please wait and try again shortly."
                ),
                session_id=(chat_request.session_id or "").strip() or bot_service.create_session_id(),
                tokens_used=0,
                latency_ms=0,
                retry_count=0,
                fallback_used=False,
                status_message="model_download_in_progress",
                degraded_components=[],
                data_freshness="fresh",
                safe_modes=[],
                quick_chips=[],
                tool_events=[],
                pending_tool_actions=[],
            )

        if isinstance(e, HTTPException):
            raise
        if isinstance(e, ProviderTemporarilyUnavailableError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "detail": str(e),
                    "retry_after_seconds": e.retry_after,
                },
            )
        if isinstance(e, CircuitBreakerOpenError):
            metrics_service.set_gauge(
                "provider_circuit_open",
                1,
                labels={"dependency": bot.provider_type, "breaker": "chat"},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "detail": "Provider circuit is open",
                    "retry_after_seconds": e.retry_after,
                },
            )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat processing error: {error_message}"
        )


@router.post("/{bot_id}/chat/stream")
async def chat_stream(
    bot_id: str,
    chat_request: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """Stream chat progress events immediately so the UI can react before generation completes."""
    await enforce_public_json_request(request)
    await enforce_public_bot_token(request)
    await rate_limit_dependency_async(request, bot_id, db)

    bot = await bot_service.get_bot_async(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chatbot not found",
        )

    if not bot.is_available():
        response_payload = {
            "response": "This chatbot is currently inactive.",
            "session_id": (chat_request.session_id or "").strip() or bot_service.create_session_id(),
            "tokens_used": 0,
            "latency_ms": 0,
            "retry_count": 0,
            "fallback_used": False,
            "status_message": "bot_unavailable",
            "degraded_components": [],
            "data_freshness": "fresh",
            "safe_modes": [],
            "quick_chips": [],
            "tool_events": [],
            "pending_tool_actions": [],
        }

        async def _unavailable_stream():
            yield _stream_event("status", stage="opening_session", message="Bot unavailable.")
            yield _stream_event("complete", **response_payload)

        return StreamingResponse(_unavailable_stream(), media_type="application/x-ndjson")

    provider = None
    try:
        provider = bot_service._get_provider(bot)
    except Exception:
        provider = None
    active_modes = await safe_mode_service.get_active_modes_async(db, bot_id=bot_id, provider=provider)

    explicit_idempotency_key = (
        chat_request.idempotency_key
        or request.headers.get("Idempotency-Key")
    )
    request_record = await chat_request_service.prepare_async(
        db,
        bot_id=bot_id,
        session_id=chat_request.session_id,
        message=chat_request.message,
        idempotency_key=chat_request_service.build_idempotency_key(
            bot_id=bot_id,
            session_id=chat_request.session_id,
            message=chat_request.message,
            explicit_key=explicit_idempotency_key,
        ),
    )
    if request_record.state == "completed" and request_record.response_payload:
        async def _cached_stream():
            yield _stream_event("status", stage="completed", message="Idempotent replay hit.")
            yield _stream_event("complete", **request_record.response_payload)

        return StreamingResponse(_cached_stream(), media_type="application/x-ndjson")
    if request_record.state == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An identical chat request is already in progress",
        )

    await chat_request_service.mark_running_async(
        db,
        request_record,
        lease_owner=f"api-{os.getpid()}",
    )
    await db.commit()

    warmup_status = startup_warmup_service.get_bot_hot_status(bot)

    async def event_stream():
        yield _stream_event(
            "status",
            stage="prepare_request",
            message="Validating request...",
            hot_state=warmup_status.get("hot_state"),
            hot_ready=warmup_status.get("hot_ready"),
        )
        yield _stream_event("status", stage="searching_knowledge_base", message="Searching knowledge base...")
        yield _stream_event("status", stage="checking_module_references", message="Checking module references...")
        if not warmup_status.get("hot_ready"):
            yield _stream_event(
                "status",
                stage="warming_provider",
                message=warmup_status.get("detail") or "Warming model and loading context...",
                hot_state=warmup_status.get("hot_state"),
                hot_ready=warmup_status.get("hot_ready"),
            )
        yield _stream_event("status", stage="awaiting_response", message="Drafting response...")

        try:
            result = await bot_service.chat(
                db=db,
                bot_id=bot_id,
                message=chat_request.message,
                session_id=chat_request.session_id,
                include_rag="RAG_BYPASS_EXPLICIT" not in active_modes,
                persist_memory="DB_READ_ONLY" not in active_modes,
            )
            degraded = list(result.get("degraded_components", []))
            if "RAG_BYPASS_EXPLICIT" in active_modes and "rag" not in degraded:
                degraded.append("rag")
            if "DB_READ_ONLY" in active_modes and "memory" not in degraded:
                degraded.append("memory")
            result["degraded_components"] = degraded
            result["data_freshness"] = "stale" if degraded else result.get("data_freshness", "fresh")
            result["safe_modes"] = safe_mode_service.serialize_modes(active_modes)

            if bot.analytics_enabled and "DB_READ_ONLY" not in active_modes:
                try:
                    client_info = await get_client_info(request)
                    await supervisor_service.enqueue_outbox_async(
                        db,
                        event_type="analytics_write",
                        aggregate_type="bot",
                        aggregate_id=bot_id,
                        payload_json={
                            "bot_id": bot_id,
                            "session_id": result["session_id"],
                            "user_message": chat_request.message,
                            "assistant_response": result["response"],
                            "tokens_prompt": result.get("tokens_prompt", 0),
                            "tokens_completion": result.get("tokens_completion", 0),
                            "latency_ms": result["latency_ms"],
                            "user_ip": client_info.get("ip"),
                            "user_agent": client_info.get("user_agent"),
                        },
                        dedupe_key=f"analytics_write:{request_record.id}",
                    )
                except Exception as analytics_error:
                    metrics_service.inc_counter(
                        "db_write_fail_total",
                        labels={"operation": "analytics_write"},
                    )
                    log_event(
                        logger,
                        logging.WARNING,
                        "analytics_logging_failed",
                        component="analytics",
                        bot_id=bot_id,
                        session_id=result.get("session_id"),
                        dependency="database",
                        degraded_components=result.get("degraded_components", []),
                        error=str(analytics_error),
                    )
                    degraded = list(result.get("degraded_components", []))
                    if "analytics" not in degraded:
                        degraded.append("analytics")
                    result["degraded_components"] = degraded
                    result["data_freshness"] = "stale"
                    result["status_message"] = "completed_with_degraded_analytics"

            response_payload = {
                "response": result["response"],
                "session_id": result["session_id"],
                "tokens_used": result["tokens_used"],
                "latency_ms": result["latency_ms"],
                "retry_count": result.get("retry_count", 0),
                "fallback_used": result.get("fallback_used", False),
                "status_message": result.get("status_message", "completed"),
                "degraded_components": result.get("degraded_components", []),
                "data_freshness": result.get("data_freshness", "fresh"),
                "safe_modes": result.get("safe_modes", []),
                "context_scope": result.get("context_scope"),
                "context_sources": result.get("context_sources", []),
                "citations": result.get("citations", []),
                "quick_chips": result.get("quick_chips", []),
                "tool_events": result.get("tool_events", []),
                "pending_tool_actions": result.get("pending_tool_actions", []),
            }
            await chat_request_service.mark_completed_async(
                db,
                request_record,
                response_payload=response_payload,
                degraded_flags=response_payload["degraded_components"],
            )
            await db.commit()
            yield _stream_event("status", stage="streaming_answer", message="Streaming answer...")
            yield _stream_event("chunk", content=response_payload["response"])
            yield _stream_event("complete", **response_payload)
        except Exception as exc:
            try:
                await chat_request_service.mark_failed_async(db, request_record, str(exc))
                await db.commit()
            except Exception:
                await db.rollback()
            logger.exception("Streaming chat processing error for bot %s: %s", bot_id, exc)
            yield _stream_event("error", detail=str(exc))

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.post("/{bot_id}/tools/trigger", response_model=ToolTriggerResponse)
async def trigger_public_tool_action(
    bot_id: str,
    tool_request: PublicToolTriggerRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    await enforce_public_json_request(request)
    await enforce_public_bot_token(request)
    await rate_limit_dependency_async(request, bot_id, db)

    bot = await bot_service.get_bot_async(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chatbot not found",
        )
    if not bot.is_available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chatbot is not available",
        )

    tool_settings = normalize_tool_settings(
        bot.tool_settings_json,
        fallback_enabled=bool(bot.tools_enabled),
    )
    if not tool_settings.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tool calling is not enabled for this bot",
        )

    try:
        result = await trigger_public_tool(
            db=db,
            bot_id=bot_id,
            tool_definitions=bot.allowed_tools_json,
            tool_id=tool_request.tool_id,
            payload=tool_request.payload,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST
        if "not available on public surfaces" in detail:
            status_code = status.HTTP_403_FORBIDDEN
        raise HTTPException(status_code=status_code, detail=detail) from exc

    return ToolTriggerResponse(
        status="ok",
        tool_id=result["tool_id"],
        response=result["response"],
        session_id=(tool_request.session_id or "").strip() or bot_service.create_session_id(),
        quick_chips=result.get("quick_chips", []),
        tool_events=result.get("tool_events", []),
        pending_tool_actions=result.get("pending_tool_actions", []),
        result=result.get("result", {}),
    )


@router.get("/{bot_id}/status", response_model=BotStatusResponse)
async def get_bot_status(
    bot_id: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get the status of a chatbot.
    
    Public endpoint to check if a bot is available.
    """
    await enforce_public_bot_token(request)
    await rate_limit_dependency_async(request, bot_id, db)
    bot = await bot_service.get_bot_async(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chatbot not found"
        )
    
    is_available = bot.is_available()
    reason = None
    
    if not is_available:
        if not bot.is_active:
            reason = "Bot is disabled"
        elif bot.expiration_date and bot.expiration_date < __import__('datetime').datetime.utcnow():
            reason = "Bot has expired"
        elif bot.message_limit and bot.messages_used >= bot.message_limit:
            reason = "Message limit reached"
    
    startup_welcome = bot_service.get_startup_welcome_message(bot_id)
    warmup_status = startup_warmup_service.get_bot_hot_status(bot)
    quick_chip_settings = {
        **(bot.quick_chips_json or {}),
        "initial_chips": get_startup_quick_chips(bot.quick_chips_json, bot.allowed_tools_json, surface_scope="public"),
    }
    
    return BotStatusResponse(
        bot_id=bot_id,
        name=bot.name,
        is_available=is_available,
        reason=reason,
        welcome_message=bot.welcome_message,
        startup_welcome_ready=bool(startup_welcome.get("ready")),
        startup_welcome_message=startup_welcome.get("message"),
        startup_welcome_generated_at=startup_welcome.get("generated_at"),
        hot_state=warmup_status.get("hot_state", "cold"),
        hot_ready=bool(warmup_status.get("hot_ready")),
        provider_ready=bool(warmup_status.get("provider_ready")),
        index_hydrated=bool(warmup_status.get("index_hydrated")),
        warmup_detail=warmup_status.get("detail"),
        quick_chip_settings=quick_chip_settings,
    )


@router.get("/{bot_id}/analytics")
async def get_bot_analytics_public(
    bot_id: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get basic analytics for a chatbot.
    
    Returns limited public analytics data.
    """
    await enforce_public_bot_token(request)
    await rate_limit_dependency_async(request, bot_id, db)

    bot = await bot_service.get_bot_async(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chatbot not found"
        )
    
    # Return limited public analytics
    return {
        "bot_id": bot_id,
        "name": bot.name,
        "total_messages": bot.messages_used,
        "is_available": bot.is_available()
    }
