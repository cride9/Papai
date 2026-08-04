"""
app/database.py — SQLAlchemy engine and session factory.

Uses the existing catalog_database.sqlite in WAL mode for concurrent reads.
New tables (users, chat_sessions, chat_messages, message_feedback, audit_logs)
are created alongside the existing documents/parts tables.
"""

import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

log = logging.getLogger(__name__)

# ── Engine ────────────────────────────────────────────────────────────────
engine = create_engine(
    settings.DB_URL,
    connect_args={"check_same_thread": False},
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

# Enable WAL mode for better concurrent read performance
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


@contextmanager
def get_db():
    """Yield a database session and ensure it is closed."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """
    Create all new tables if they don't exist.
    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.

    Also migrates the existing 'documents' table if it's missing columns.
    """
    from app.models import user, chat, feedback, audit_log, document  # noqa: F401 — register models

    Base.metadata.create_all(bind=engine)

    # Migrate the existing 'documents' table if needed
    _migrate_documents_table()

    log.info("Database tables verified / created.")


def _migrate_documents_table():
    """Add missing columns to the existing documents table."""
    import sqlite3
    from sqlalchemy import inspect

    # Check if documents table exists
    insp = inspect(engine)
    if "documents" not in insp.get_table_names():
        return

    # Get existing columns
    existing_cols = {col["name"] for col in insp.get_columns("documents")}

    # Columns to add if missing
    needed_cols = {
        "status": "TEXT DEFAULT 'COMPLETED'",
        "processed_at": "TEXT",
    }

    # Use raw sqlite3 connection for ALTER TABLE
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        for col_name, col_def in needed_cols.items():
            if col_name not in existing_cols:
                sql = f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}"
                cursor.execute(sql)
                log.info(f"Migrated documents table: added column '{col_name}'")
        raw_conn.commit()
    finally:
        raw_conn.close()
