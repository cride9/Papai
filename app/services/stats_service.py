"""
app/services/stats_service.py — Statistics aggregation for the admin dashboard.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, text

from app.database import SessionLocal
from app.models.user import User
from app.models.chat import ChatMessage
from app.models.feedback import MessageFeedback
from app.services.ws_manager import ws_manager

log = logging.getLogger(__name__)


def get_dashboard_stats() -> dict:
    """
    Aggregate dashboard statistics.

    Returns a dict matching the frontend's expected DashboardStats shape.
    """
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Total users
        total_users = db.query(func.count(User.id)).scalar() or 0

        # Active now (via WebSocket connections)
        active_now = ws_manager.get_active_count()

        # Prompts today
        prompts_today = db.query(func.count(ChatMessage.id)).filter(
            ChatMessage.role == "user",
            ChatMessage.created_at >= today_start,
        ).scalar() or 0

        # Total prompts
        total_prompts = db.query(func.count(ChatMessage.id)).filter(
            ChatMessage.role == "user",
        ).scalar() or 0

        # Feedback stats
        total_likes = db.query(func.count(MessageFeedback.id)).filter(
            MessageFeedback.feedback == 1,
        ).scalar() or 0

        total_dislikes = db.query(func.count(MessageFeedback.id)).filter(
            MessageFeedback.feedback == 0,
        ).scalar() or 0

        total_feedback = total_likes + total_dislikes
        satisfaction_pct = round((total_likes / total_feedback) * 100, 1) if total_feedback > 0 else 0.0

        # Daily prompts for the last 7 days (bar chart)
        daily_prompts = []
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day + timedelta(days=1)
            count = db.query(func.count(ChatMessage.id)).filter(
                ChatMessage.role == "user",
                ChatMessage.created_at >= day,
                ChatMessage.created_at < day_end,
            ).scalar() or 0
            daily_prompts.append({
                "date": day.strftime("%Y-%m-%d"),
                "count": count,
            })

        return {
            "total_users": total_users,
            "active_users_now": active_now,
            "prompts_today": prompts_today,
            "total_prompts": total_prompts,
            "satisfaction_pct": satisfaction_pct,
            "total_likes": total_likes,
            "total_dislikes": total_dislikes,
            "daily_prompts": daily_prompts,
        }

    finally:
        db.close()
