"""
app/models/user.py — User ORM model.
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user")  # "user" | "admin"
    security_question = Column(String(500), nullable=True)
    security_answer_hash = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
            "last_login": (self.last_login.isoformat() + "Z") if self.last_login else None,
        }
