"""
Admin dashboard API routes.
"""

from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..models.database import get_db, AdminUser
from ..models.schemas import LoginRequest, LoginResponse, UserCreate
from ..services.analytics_service import analytics_service
from .auth import (
    get_current_user, create_access_token, hash_password,
    verify_password, ensure_default_admin, ACCESS_TOKEN_EXPIRE_HOURS
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
    """Get analytics for all bots."""
    return analytics_service.get_all_bots_analytics(db, days)


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
