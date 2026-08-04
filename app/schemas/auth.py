"""
app/schemas/auth.py — Pydantic schemas for authentication endpoints.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    created_at: Optional[str] = None
    last_login: Optional[str] = None


class SecurityQuestionResponse(BaseModel):
    username: str
    question: Optional[str] = None
    question_id: int  # user ID — used as question identifier


class ResetPasswordRequest(BaseModel):
    username: str
    question_id: int
    answer: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=100)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=100)
    role: str = Field(default="user", pattern="^(user|admin)$")
    security_question: Optional[str] = None
    security_answer: Optional[str] = None


class UserCreateResponse(BaseModel):
    id: int
    username: str
    role: str
