"""
Admin dashboard API routes.
"""

import os
import urllib.request
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Body
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..models.database import get_db, AdminUser, Bot
from ..models.schemas import LoginRequest, LoginResponse, UserCreate
from ..services.analytics_service import analytics_service
from ..services.tunnel_service import tunnel_service
from ..services.api_gateway_service import api_gateway_service
from ..services.encryption_service import encryption_service
from .auth import (
    get_current_user, create_access_token, hash_password,
    verify_password, ensure_default_admin, ACCESS_TOKEN_EXPIRE_HOURS
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _provider_company(provider: str) -> str:
    mapping = {
        "openai": "OpenAI",
        "claude": "Anthropic",
        "gemini": "Google",
        "local": "Local/Ollama",
    }
    return mapping.get((provider or "").lower(), provider or "Unknown")


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + ("*" * max(0, len(key) - 4)) + key[-2:]
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _build_api_key_records(db: Session, reveal: bool = False):
    records = []
    bots = db.query(Bot).order_by(Bot.updated_at.desc()).all()
    for bot in bots:
        if not bot.api_key_encrypted:
            continue

        decrypted = None
        decrypt_error = False
        try:
            decrypted = encryption_service.decrypt(bot.api_key_encrypted)
        except Exception:
            decrypt_error = True

        records.append({
            "id": f"{bot.id}:api_key",
            "bot_id": bot.id,
            "bot_name": bot.name,
            "provider": bot.provider_type,
            "company": _provider_company(bot.provider_type),
            "model": bot.model,
            "is_active": bot.is_active,
            "created_at": bot.created_at.isoformat() if bot.created_at else None,
            "updated_at": bot.updated_at.isoformat() if bot.updated_at else None,
            "masked_key": _mask_key(decrypted) if decrypted else "Unavailable",
            "key_value": decrypted if reveal and decrypted else None,
            "key_available": bool(decrypted),
            "decrypt_error": decrypt_error,
        })

    return records


@router.post("/login", response_model=LoginResponse)
async def login(
    login_data: LoginRequest,
    response: Response,
    db: Session = Depends(get_db)
):
    """
    Admin login endpoint.
    Returns a JWT token for authenticated access.
    """
    # Ensure default admin exists
    ensure_default_admin(db)
    
    user = db.query(AdminUser).filter(
        AdminUser.username == login_data.username
    ).first()
    
    if not user or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )
    
    # Create access token
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    )
    
    # Set cookie as well
    response.set_cookie(
        key="session_token",
        value=access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        samesite="lax"
    )
    
    return LoginResponse(access_token=access_token)


@router.post("/logout")
async def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie("session_token")
    return {"message": "Logged out successfully"}


@router.get("/me")
async def get_current_admin(
    current_user: str = Depends(get_current_user)
):
    """Get current admin user info."""
    return {"username": current_user}


@router.get("/system-status")
async def get_system_status(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Get runtime system status used by the settings and editor views."""
    # Database check
    try:
        db.execute(text("SELECT 1"))
        database_status = "Connected"
    except Exception:
        database_status = "Disconnected"

    # FAISS check
    try:
        import faiss  # noqa: F401
        faiss_status = "Loaded"
    except Exception:
        faiss_status = "Unavailable"

    # Provider config checks
    openai_status = "Configured" if os.getenv("OPENAI_API_KEY") else "Not configured"

    local_llm_url = os.getenv("LOCAL_LLM_URL", "http://localhost:11434").rstrip("/")
    local_llm_status = "Not configured"
    try:
        with urllib.request.urlopen(f"{local_llm_url}/api/tags", timeout=2):
            local_llm_status = "Running"
    except Exception:
        if os.getenv("LOCAL_LLM_URL"):
            local_llm_status = "Configured"

    tunnel_status = tunnel_service.get_status()
    cloudflare_status = "Running" if tunnel_status.get("running") else "Unavailable"
    gateway_status = api_gateway_service.get_status()
    tunnel_token = os.getenv("TUNNEL_SHARED_SECRET", "").strip()

    return {
        "server": "Running",
        "database": database_status,
        "faiss": faiss_status,
        "openai": openai_status,
        "local_llm": local_llm_status,
        "cloudflare_tunnel": cloudflare_status,
        "public_base_url": tunnel_status.get("public_base_url"),
        "cloudflare_stable": tunnel_status.get("stable", False),
        "cloudflare_source": tunnel_status.get("source"),
        "cloudflare_error": tunnel_status.get("last_error"),
        "tunnel_token_required": bool(tunnel_token),
        "tunnel_token": tunnel_token or None,
        "api_gateway_running": gateway_status.get("running", False),
        "api_gateway_port": gateway_status.get("port"),
    }


@router.post("/change-password")
async def change_password(
    old_password: str,
    new_password: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Change admin password."""
    user = db.query(AdminUser).filter(
        AdminUser.username == current_user
    ).first()
    
    if not user or not verify_password(old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid current password"
        )
    
    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters"
        )
    
    user.password_hash = hash_password(new_password)
    db.commit()
    
    return {"message": "Password changed successfully"}


@router.post("/verify-password")
async def verify_admin_password(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Verify admin password for sensitive in-session actions."""
    password = (payload or {}).get("password", "")
    if not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is required"
        )

    user = db.query(AdminUser).filter(AdminUser.username == current_user).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin password"
        )

    return {"verified": True}


@router.get("/api-keys")
async def list_api_keys(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """List stored API keys in masked form."""
    records = _build_api_key_records(db, reveal=False)
    return {
        "keys": records,
        "total": len(records),
        "revealed": False,
    }


@router.post("/api-keys/reveal")
async def reveal_api_keys(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Reveal decrypted API keys after password verification."""
    password = (payload or {}).get("password", "")
    if not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is required"
        )

    user = db.query(AdminUser).filter(AdminUser.username == current_user).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin password"
        )

    records = _build_api_key_records(db, reveal=True)
    return {
        "keys": records,
        "total": len(records),
        "revealed": True,
    }


@router.get("/dashboard/stats")
async def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Get dashboard overview statistics."""
    from ..models.database import Bot, Message
    from sqlalchemy import func
    from datetime import datetime, timedelta
    
    # Total bots
    total_bots = db.query(Bot).count()
    active_bots = db.query(Bot).filter(Bot.is_active == True).count()
    
    # Messages last 30 days
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    total_messages = db.query(Message).filter(
        Message.created_at >= thirty_days_ago
    ).count()
    
    # Total tokens
    total_tokens = db.query(func.sum(Message.tokens_total)).filter(
        Message.created_at >= thirty_days_ago
    ).scalar() or 0
    
    # Messages today
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    messages_today = db.query(Message).filter(
        Message.created_at >= today
    ).count()
    
    # Average latency
    avg_latency = db.query(func.avg(Message.latency_ms)).filter(
        Message.created_at >= thirty_days_ago
    ).scalar() or 0
    
    return {
        "total_bots": total_bots,
        "active_bots": active_bots,
        "total_messages_30d": total_messages,
        "total_tokens_30d": total_tokens,
        "messages_today": messages_today,
        "avg_latency_ms": round(avg_latency, 2)
    }


@router.get("/analytics/all")
async def get_all_analytics(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Get aggregated analytics for all bots."""
    per_bot = analytics_service.get_all_bots_analytics(db, days)
    
    total_messages = sum(b.get('total_messages', 0) for b in per_bot)
    total_tokens = sum(b.get('total_tokens', 0) for b in per_bot)
    active_bots = len([b for b in per_bot if b.get('total_messages', 0) > 0])
    
    # Weighted average latency
    avg_latency_ms = 0
    if total_messages > 0:
        avg_latency_ms = sum(
            b.get('avg_latency_ms', 0) * b.get('total_messages', 0) for b in per_bot
        ) / total_messages
    
    # Merge daily breakdowns across all bots
    daily_map: dict = {}
    for b in per_bot:
        for day in b.get('daily_breakdown', []):
            d = day.get('date', '')
            if d not in daily_map:
                daily_map[d] = {'date': d, 'messages': 0, 'tokens': 0}
            daily_map[d]['messages'] += day.get('messages', 0)
            daily_map[d]['tokens'] += day.get('tokens', 0)
    daily_usage = sorted(daily_map.values(), key=lambda x: x['date'])
    
    # Merge top questions across all bots
    from collections import Counter
    q_counter: Counter = Counter()
    for b in per_bot:
        for q in b.get('top_questions', []):
            q_counter[q['question']] += q['count']
    top_questions = [{'question': q, 'count': c} for q, c in q_counter.most_common(10)]
    
    return {
        'total_messages': total_messages,
        'total_tokens': total_tokens,
        'avg_latency_ms': round(avg_latency_ms, 2),
        'active_bots': active_bots,
        'daily_usage': daily_usage,
        'top_questions': top_questions,
    }


@router.get("/analytics/{bot_id}")
async def get_bot_analytics(
    bot_id: str,
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Get detailed analytics for a specific bot."""
    return analytics_service.get_bot_analytics(db, bot_id, days)


@router.get("/analytics/{bot_id}/messages")
async def get_bot_messages(
    bot_id: str,
    limit: int = 100,
    offset: int = 0,
    session_id: str = None,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Get message logs for a bot."""
    return analytics_service.get_message_logs(db, bot_id, limit, offset, session_id)


@router.get("/analytics/{bot_id}/usage")
async def get_bot_usage(
    bot_id: str,
    days: int = 30,
    granularity: str = "day",
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Get usage over time for charting."""
    return analytics_service.get_usage_over_time(db, bot_id, days, granularity)
