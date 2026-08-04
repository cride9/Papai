"""
app/routers/auth.py — Authentication endpoints.

POST /auth/login          — Login with username/password
GET  /auth/me             — Get current user
GET  /auth/security-question/{username} — Get security question for password reset
POST /auth/reset-password — Reset password with security answer
POST /auth/register       — Create new user (admin-only)
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import get_db
from app.models.user import User
from app.models.audit_log import AuditLog
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    UserResponse,
    SecurityQuestionResponse,
    ResetPasswordRequest,
    RegisterRequest,
    UserCreateResponse,
)
from app.services.auth_service import (
    verify_password,
    create_access_token,
    hash_password,
    hash_answer,
    verify_answer,
)
from app.middleware.auth import get_current_user, require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    """Authenticate user and return JWT token."""
    with get_db() as db:
        user = db.query(User).filter(User.username == req.username).first()

        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        # Update last_login
        user.last_login = datetime.utcnow()
        db.commit()

        # Log
        audit = AuditLog(user_id=user.id, action="login", details=f"User '{user.username}' logged in")
        db.add(audit)
        db.commit()

        token = create_access_token(user.id, user.username, user.role)

        return TokenResponse(
            access_token=token,
            user=user.to_dict(),
        )


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return UserResponse(**current_user.to_dict())


@router.get("/security-question/{username}", response_model=SecurityQuestionResponse)
def get_security_question(username: str):
    """Get the security question for a user (public endpoint for password reset flow)."""
    with get_db() as db:
        user = db.query(User).filter(User.username == username).first()

        if not user:
            # Don't reveal whether user exists — return a generic response
            return SecurityQuestionResponse(username=username, question=None, question_id=0)

        if not user.security_question:
            return SecurityQuestionResponse(username=username, question=None, question_id=user.id)

        return SecurityQuestionResponse(
            username=user.username,
            question=user.security_question,
            question_id=user.id,
        )


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest):
    """Reset password using security question answer."""
    with get_db() as db:
        user = db.query(User).filter(
            User.username == req.username,
            User.id == req.question_id,
        ).first()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid username or question ID",
            )

        if not user.security_answer_hash:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No security question configured for this account",
            )

        if not verify_answer(req.answer, user.security_answer_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incorrect security answer",
            )

        user.password_hash = hash_password(req.new_password)

        audit = AuditLog(
            user_id=user.id,
            action="password_reset",
            details=f"User '{user.username}' reset password via security question",
        )
        db.add(audit)
        db.commit()

        return {"message": "Password reset successful"}


@router.post("/register", response_model=UserCreateResponse)
def register(req: RegisterRequest, _admin: User = Depends(require_admin)):
    """Create a new user (admin-only)."""
    with get_db() as db:
        existing = db.query(User).filter(User.username == req.username).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User '{req.username}' already exists",
            )

        new_user = User(
            username=req.username,
            password_hash=hash_password(req.password),
            role=req.role,
        )

        if req.security_question and req.security_answer:
            new_user.security_question = req.security_question
            new_user.security_answer_hash = hash_answer(req.security_answer)

        db.add(new_user)
        db.flush()

        audit = AuditLog(
            user_id=_admin.id,
            action="user_created",
            details=f"Admin '{_admin.username}' created user '{new_user.username}' (role: {new_user.role})",
        )
        db.add(audit)
        db.commit()

        log.info(f"User created: {new_user.username} (role: {new_user.role})")

        return UserCreateResponse(
            id=new_user.id,
            username=new_user.username,
            role=new_user.role,
        )
