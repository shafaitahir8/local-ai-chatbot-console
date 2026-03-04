"""
Public Chat API routes - exposed for client applications.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from ..models.database import get_db, Bot
from ..models.schemas import ChatRequest, ChatResponse, BotStatusResponse
from ..services.bot_service import bot_service
from ..services.analytics_service import analytics_service
from .auth import rate_limit_dependency

router = APIRouter(prefix="/bot", tags=["chat"])


async def get_client_info(request: Request) -> dict:
    """Extract client information from request."""
    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent")
    }


@router.post("/{bot_id}/chat", response_model=ChatResponse)
async def chat(
    bot_id: str,
    chat_request: ChatRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Send a message to a chatbot.
    
    This is the public API endpoint that clients use to interact with chatbots.
    No authentication required, but rate-limited per IP.
    """
    # Rate limiting
    await rate_limit_dependency(request, bot_id)
    
    # Check if bot exists
    bot = bot_service.get_bot(db, bot_id)
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
            session_id=chat_request.session_id or "",
            tokens_used=0,
            latency_ms=0
        )
    
    try:
        # Process chat
        result = await bot_service.chat(
            db,
            bot_id,
            chat_request.message,
            chat_request.session_id
        )
        
        # Log analytics if enabled
        if bot.analytics_enabled:
            client_info = await get_client_info(request)
            await analytics_service.log_message(
                db,
                bot_id=bot_id,
                session_id=result['session_id'],
                user_message=chat_request.message,
                assistant_response=result['response'],
                tokens_prompt=result.get('tokens_prompt', 0),
                tokens_completion=result.get('tokens_completion', 0),
                latency_ms=result['latency_ms'],
                user_ip=client_info.get('ip'),
                user_agent=client_info.get('user_agent')
            )
        
        return ChatResponse(
            response=result['response'],
            session_id=result['session_id'],
            tokens_used=result['tokens_used'],
            latency_ms=result['latency_ms']
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat processing error: {str(e)}"
        )


@router.get("/{bot_id}/status", response_model=BotStatusResponse)
async def get_bot_status(
    bot_id: str,
    db: Session = Depends(get_db)
):
    """
    Get the status of a chatbot.
    
    Public endpoint to check if a bot is available.
    """
    bot = bot_service.get_bot(db, bot_id)
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
    
    return BotStatusResponse(
        bot_id=bot_id,
        name=bot.name,
        is_available=is_available,
        reason=reason
    )


@router.get("/{bot_id}/analytics")
async def get_bot_analytics_public(
    bot_id: str,
    db: Session = Depends(get_db)
):
    """
    Get basic analytics for a chatbot.
    
    Returns limited public analytics data.
    """
    bot = bot_service.get_bot(db, bot_id)
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
