"""
app/schemas/admin.py — Pydantic schemas for admin endpoints.
"""

from typing import Optional

from pydantic import BaseModel


class DashboardStats(BaseModel):
    total_users: int = 0
    active_users_now: int = 0
    prompts_today: int = 0
    total_prompts: int = 0
    satisfaction_pct: float = 0.0
    total_likes: int = 0
    total_dislikes: int = 0
    daily_prompts: list = []


class UserAdminResponse(BaseModel):
    id: int
    username: str
    role: str
    total_prompts: int = 0
    total_likes: int = 0
    total_dislikes: int = 0
    last_login: Optional[str] = None


class TempPasswordResponse(BaseModel):
    temp_password: str


class PDFUploadResponse(BaseModel):
    id: int
    filename: str
    page_count: int


class PDFListResponse(BaseModel):
    id: int
    filename: str
    page_count: int = 0
    status: str = "COMPLETED"
    uploaded_at: Optional[str] = None


class LogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    action: str
    details: Optional[str] = None
    timestamp: Optional[str] = None
