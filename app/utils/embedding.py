"""
app/utils/embedding.py — Embedding generation with 2048-dim Matryoshka truncation.

Preserves the exact truncation logic from create_chromadb.py:
  - Native 4096-dim embeddings from the API
  - Truncate to 2048 + L2 renormalize (Matryoshka-style)
"""

import logging

import numpy as np
import requests

from app.config import settings

log = logging.getLogger(__name__)


def get_embedding(text: str, task_type: str = "search_query") -> list[float]:
    """
    Generate embedding with task-specific prompting.

    Args:
        text: The text to embed.
        task_type: "search_query" or "search_document" — prepended as a prompt prefix.
    """
    if task_type == "search_query":
        prompted_text = f"search_query: {text}"
    elif task_type == "search_document":
        prompted_text = f"search_document: {text}"
    else:
        prompted_text = text

    resp = requests.post(
        settings.EMBED_ENDPOINT,
        json={"input": [prompted_text], "model": settings.EMBED_MODEL},
        timeout=60,
    )
    resp.raise_for_status()
    raw_embedding = resp.json()["data"][0]["embedding"]

    # Truncate to target dim + L2 renormalize (Matryoshka)
    arr = np.array(raw_embedding[: settings.EMBED_DIM], dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tolist()
