"""
app/services/auth_service.py — Authentication service.

Handles:
  - JWT token generation and validation
  - Password hashing (bcrypt)
  - Security question answer verification
  - Admin account bootstrap on first startup
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from app.config import settings

log = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    password_bytes = password.encode("utf-8")
    # Truncate to 72 bytes (bcrypt limit)
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))


# ── Security questions ────────────────────────────────────────────────────
def hash_answer(answer: str) -> str:
    """Hash a security answer (lowercased, stripped) for storage."""
    return hash_password(answer.strip().lower())


def verify_answer(plain_answer: str, hashed_answer: str) -> bool:
    """Verify a security answer against its hash."""
    return verify_password(plain_answer.strip().lower(), hashed_answer)


# ── JWT ───────────────────────────────────────────────────────────────────
def create_access_token(user_id: int, username: str, role: str) -> str:
    """Create a JWT access token."""
    expire = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token. Returns payload dict or None."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


# ── Admin bootstrap ───────────────────────────────────────────────────────
def bootstrap_admin(db_session):
    """
    Create the initial admin user if no users exist in the database.
    Called on application startup.
    """
    from app.models.user import User

    existing = db_session.query(User).first()
    if existing:
        return  # Users already exist, skip bootstrap

    admin = User(
        username=settings.ADMIN_USERNAME,
        password_hash=hash_password(settings.ADMIN_PASSWORD),
        role="admin",
    )
    db_session.add(admin)
    db_session.commit()
    log.info(f"Admin user '{settings.ADMIN_USERNAME}' bootstrapped.")
