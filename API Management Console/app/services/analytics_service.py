"""
Analytics Service - Tracks and aggregates usage statistics.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import Counter
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from sqlalchemy.exc import IntegrityError

from ..models.database import Message, Analytics, AnalyticsSession, Bot, get_db_context


logger = logging.getLogger(__name__)


class AnalyticsService:
    """
    Service for tracking and retrieving analytics data.
    """
    
    def log_message_sync(
        self,
        db: Session,
        bot_id: str,
        session_id: str,
        user_message: str,
        assistant_response: str,
        tokens_prompt: int,
        tokens_completion: int,
        latency_ms: int,
        user_ip: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Message:
        """
        Log a chat message and update analytics.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            session_id: User session ID
            user_message: User's message
            assistant_response: Bot's response
            tokens_prompt: Prompt tokens used
            tokens_completion: Completion tokens used
            latency_ms: Response latency in milliseconds
            user_ip: User's IP address
            user_agent: User's browser agent
            
        Returns:
            Created Message object
        """
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        is_new_session = False
        try:
            session_guard = AnalyticsSession(
                bot_id=bot_id,
                date=today,
                session_id=session_id,
            )
            db.add(session_guard)
            db.flush()
            is_new_session = True
        except IntegrityError:
            db.rollback()
            logger.debug(
                "Analytics session already exists for bot=%s session=%s date=%s",
                bot_id,
                session_id,
                today.isoformat(),
            )

        # Create message log
        message = Message(
            bot_id=bot_id,
            session_id=session_id,
            user_message=user_message,
            assistant_response=assistant_response,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            tokens_total=tokens_prompt + tokens_completion,
            latency_ms=latency_ms,
            user_ip=user_ip,
            user_agent=user_agent
        )
        db.add(message)
        
        # Update bot message count and tracked metrics
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if bot:
            old_count = bot.messages_used or 0
            bot.messages_used = old_count + 1
            bot.token_usage = (bot.token_usage or 0) + tokens_prompt + tokens_completion
            if old_count == 0:
                bot.avg_latency = latency_ms / 1000.0
            else:
                bot.avg_latency = ((bot.avg_latency or 0) * old_count + latency_ms / 1000.0) / (old_count + 1)
        
        # Update daily analytics
        self._update_daily_analytics(
            db,
            bot_id,
            tokens_prompt + tokens_completion,
            latency_ms,
            is_new_session=is_new_session,
        )
        
        db.commit()
        db.refresh(message)
        
        return message
    
    async def log_message(
        self,
        db: Session,
        bot_id: str,
        session_id: str,
        user_message: str,
        assistant_response: str,
        tokens_prompt: int,
        tokens_completion: int,
        latency_ms: int,
        user_ip: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Message:
        return self.log_message_sync(
            db=db,
            bot_id=bot_id,
            session_id=session_id,
            user_message=user_message,
            assistant_response=assistant_response,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            latency_ms=latency_ms,
            user_ip=user_ip,
            user_agent=user_agent,
        )

    def _update_daily_analytics(
        self,
        db: Session,
        bot_id: str,
        tokens: int,
        latency_ms: int,
        *,
        is_new_session: bool,
    ):
        """Update daily analytics aggregation."""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Get or create today's analytics
        analytics = db.query(Analytics).filter(
            Analytics.bot_id == bot_id,
            Analytics.date == today
        ).first()
        
        if not analytics:
            analytics = Analytics(
                bot_id=bot_id,
                date=today,
                total_messages=0,
                total_tokens=0,
                avg_latency_ms=0,
                unique_sessions=0,
                estimated_cost=0
            )
            db.add(analytics)
        
        # Update metrics
        old_total = analytics.total_messages
        analytics.total_messages += 1
        analytics.total_tokens += tokens
        
        # Update average latency (incremental)
        if old_total == 0:
            analytics.avg_latency_ms = latency_ms
        else:
            analytics.avg_latency_ms = (
                (analytics.avg_latency_ms * old_total + latency_ms) / 
                (old_total + 1)
            )
        
        if is_new_session:
            analytics.unique_sessions += 1
    
    def get_bot_analytics(
        self,
        db: Session,
        bot_id: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Get analytics for a specific bot.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            days: Number of days to include
            
        Returns:
            Analytics data dictionary
        """
        start_date = datetime.utcnow() - timedelta(days=days)
        
        # Get daily analytics
        daily_stats = db.query(Analytics).filter(
            Analytics.bot_id == bot_id,
            Analytics.date >= start_date
        ).order_by(Analytics.date).all()
        
        # Calculate totals
        total_messages = sum(a.total_messages for a in daily_stats)
        total_tokens = sum(a.total_tokens for a in daily_stats)
        total_sessions = sum(a.unique_sessions for a in daily_stats)
        
        avg_latency = 0
        if daily_stats:
            avg_latency = sum(a.avg_latency_ms * a.total_messages for a in daily_stats) / max(total_messages, 1)
        
        # Get top questions
        top_questions = self._get_top_questions(db, bot_id, start_date)
        
        # Estimate cost
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        estimated_cost = self._estimate_cost(total_tokens, bot.provider_type if bot else "openai")
        
        return {
            'bot_id': bot_id,
            'period_start': start_date.isoformat(),
            'period_end': datetime.utcnow().isoformat(),
            'total_messages': total_messages,
            'total_tokens': total_tokens,
            'avg_latency_ms': round(avg_latency, 2),
            'unique_sessions': total_sessions,
            'estimated_cost': estimated_cost,
            'daily_breakdown': [
                {
                    'date': a.date.isoformat(),
                    'messages': a.total_messages,
                    'tokens': a.total_tokens,
                    'sessions': a.unique_sessions,
                    'avg_latency_ms': round(a.avg_latency_ms, 2)
                }
                for a in daily_stats
            ],
            'top_questions': top_questions
        }
    
    def _get_top_questions(
        self,
        db: Session,
        bot_id: str,
        start_date: datetime,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get most common questions."""
        messages = db.query(Message.user_message).filter(
            Message.bot_id == bot_id,
            Message.created_at >= start_date
        ).all()
        
        # Simple word/phrase frequency
        question_counter = Counter()
        for (msg,) in messages:
            # Normalize
            normalized = msg.lower().strip()
            if len(normalized) > 10:  # Skip very short messages
                question_counter[normalized] += 1
        
        return [
            {'question': q, 'count': c}
            for q, c in question_counter.most_common(limit)
        ]
    
    def _estimate_cost(self, total_tokens: int, provider: str) -> float:
        """Estimate cost based on provider and tokens."""
        # Rough estimates per 1M tokens (combined input/output average)
        cost_per_million = {
            'openai': 2.00,  # GPT-4.1-mini average
            'claude': 5.00,  # Claude average
            'local': 0.00,  # No API cost
        }
        
        rate = cost_per_million.get(provider, 2.00)
        return round((total_tokens / 1_000_000) * rate, 4)
    
    def get_all_bots_analytics(
        self,
        db: Session,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get analytics summary for all bots.
        
        Args:
            db: Database session
            days: Number of days to include
            
        Returns:
            List of analytics summaries
        """
        bots = db.query(Bot).all()
        return [
            {
                'bot_id': bot.id,
                'bot_name': bot.name,
                **self.get_bot_analytics(db, bot.id, days)
            }
            for bot in bots
        ]
    
    def get_message_logs(
        self,
        db: Session,
        bot_id: str,
        limit: int = 100,
        offset: int = 0,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get message logs for a bot.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            limit: Max messages to return
            offset: Skip this many messages
            session_id: Filter by session (optional)
            
        Returns:
            Dictionary with messages and pagination
        """
        query = db.query(Message).filter(Message.bot_id == bot_id)
        
        if session_id:
            query = query.filter(Message.session_id == session_id)
        
        total = query.count()
        messages = query.order_by(desc(Message.created_at)).offset(offset).limit(limit).all()
        
        return {
            'messages': [m.to_dict() for m in messages],
            'total': total,
            'limit': limit,
            'offset': offset
        }
    
    def get_usage_over_time(
        self,
        db: Session,
        bot_id: str,
        days: int = 30,
        granularity: str = 'day'
    ) -> List[Dict[str, Any]]:
        """
        Get usage data over time for charting.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            days: Number of days
            granularity: 'day' or 'hour'
            
        Returns:
            List of time-series data points
        """
        start_date = datetime.utcnow() - timedelta(days=days)
        
        if granularity == 'hour':
            # Hourly for recent data
            messages = db.query(
                func.strftime('%Y-%m-%d %H:00', Message.created_at).label('period'),
                func.count(Message.id).label('count'),
                func.sum(Message.tokens_total).label('tokens')
            ).filter(
                Message.bot_id == bot_id,
                Message.created_at >= start_date
            ).group_by('period').all()
        else:
            # Daily
            messages = db.query(
                func.date(Message.created_at).label('period'),
                func.count(Message.id).label('count'),
                func.sum(Message.tokens_total).label('tokens')
            ).filter(
                Message.bot_id == bot_id,
                Message.created_at >= start_date
            ).group_by('period').all()
        
        return [
            {
                'period': str(m.period),
                'messages': m.count,
                'tokens': m.tokens or 0
            }
            for m in messages
        ]
    
    def reset_bot_analytics(self, db: Session, bot_id: str) -> bool:
        """
        Reset analytics for a bot (used when cloning).
        
        Args:
            db: Database session
            bot_id: Bot identifier
            
        Returns:
            True if reset successful
        """
        # Delete messages
        db.query(Message).filter(Message.bot_id == bot_id).delete()
        
        # Delete analytics
        db.query(Analytics).filter(Analytics.bot_id == bot_id).delete()
        
        # Reset bot counter
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if bot:
            bot.messages_used = 0
        
        db.commit()
        return True


# Singleton instance
analytics_service = AnalyticsService()
