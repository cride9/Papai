"""
app/config.py — Centralized configuration using pydantic-settings.

All paths reference the existing databases and PDF directory.
Set secrets via environment variables or a .env file.
"""

from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    # ── Paths ──────────────────────────────────────────────────────────
    PDF_FOLDER: str = "./pdfs"
    DB_URL: str = "sqlite:///databases/catalog_database.sqlite"
    CHROMA_PATH: str = "./databases/chroma_text_db_deploy"
    CHROMA_COLLECTION: str = "alkatreszek_hu_2048"

    # ── LLM / Embedding endpoints ───────────────────────────────────────
    EMBED_ENDPOINT: str = "http://26.114.75.255:8081/v1/embeddings"
    CHAT_ENDPOINT: str = "http://26.114.75.255:8080/v1/chat/completions"
    EMBED_MODEL: str = "qwen3-embedding"
    CHAT_MODEL: str = "qwen"
    EMBED_DIM: int = 2048

    # ── RAG tuning ──────────────────────────────────────────────────────
    TOP_K: int = 20
    FUSION_TOP_K: int = 30
    MAX_TOKENS: int = 2048
    TEMPERATURE: float = 0.2

    # ── Auth ────────────────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480  # 8 hours

    # ── Admin bootstrap ─────────────────────────────────────────────────
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"

    # ── Server ──────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
