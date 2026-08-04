"""
app/schemas/chat.py — Pydantic schemas for chat endpoints.
"""

from typing import Optional

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    title: str = "Új beszélgetés"


class SessionUpdate(BaseModel):
    title: str


class SessionResponse(BaseModel):
    id: int
    title: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0


class ChatSendRequest(BaseModel):
    query: str = Field(..., min_length=1)
    session_id: int


class ChatSendResponse(BaseModel):
    answer: str
    sources: list = []
    message_id: int
    session_id: int


class FeedbackRequest(BaseModel):
    message_id: int
    feedback: int = Field(..., ge=0, le=1)  # 0 = dislike, 1 = like


class FeedbackResponse(BaseModel):
    message: str = "Visszajelzés rögzítve"


class MessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    feedback: Optional[int] = None
    tokens_used: Optional[int] = None
    sources: Optional[list] = None
    created_at: Optional[str] = None
