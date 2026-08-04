"""
app/chroma_client.py — Thread-safe ChromaDB singleton.

Uses the existing ChromaDB persistent database at CHROMA_PATH.
Read operations (queries) are concurrent-safe by ChromaDB's design.
Write operations (PDF indexing) use a threading.Lock for safety.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import chromadb

from app.config import settings

log = logging.getLogger(__name__)

_chroma_client: Optional[chromadb.PersistentClient] = None
_collection = None
_write_lock = threading.Lock()


def init_chroma():
    """Initialize the ChromaDB client and collection. Call once at startup."""
    global _chroma_client, _collection

    try:
        _chroma_client = chromadb.PersistentClient(path=settings.CHROMA_PATH)
        _collection = _chroma_client.get_collection(name=settings.CHROMA_COLLECTION)
        count = _collection.count()
        log.info(f"ChromaDB loaded: collection='{settings.CHROMA_COLLECTION}', {count:,} records")
    except Exception as e:
        log.error(f"ChromaDB initialization failed: {e}")
        raise


def get_collection():
    """Return the ChromaDB collection. Must be called after init_chroma()."""
    if _collection is None:
        raise RuntimeError("ChromaDB not initialized. Call init_chroma() first.")
    return _collection


def get_write_lock():
    """Return the threading.Lock for write operations."""
    return _write_lock


def get_client():
    """Return the raw ChromaDB client."""
    if _chroma_client is None:
        raise RuntimeError("ChromaDB not initialized. Call init_chroma() first.")
    return _chroma_client
