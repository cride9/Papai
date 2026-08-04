"""
app/utils/llm_client.py — Synchronous LLM API client.

Used for non-streaming calls: query intelligence, relevance judge, answer synthesis.
Blocking HTTP calls are executed in a thread pool via asyncio.to_thread().
"""

import json
import logging
import re
from typing import Optional

import requests

from app.config import settings

log = logging.getLogger(__name__)


def call_llm_sync(
    messages: list[dict],
    max_tokens: int = 600,
    temperature: float = 0.2,
) -> str:
    """Synchronous LLM call — returns response text."""
    resp = requests.post(
        settings.CHAT_ENDPOINT,
        json={
            "model": settings.CHAT_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        },
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def parse_json_response(raw: str) -> dict:
    """Robust JSON parsing from LLM output (handles markdown code blocks)."""
    content = raw.strip()

    # Handle markdown code blocks
    if "```" in content:
        parts = content.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:]
            if p.startswith("{"):
                content = p
                break

    # If no code block, find the { ... } portion
    if not content.startswith("{"):
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            content = match.group(0)

    return json.loads(content)


def call_llm_json(
    messages: list[dict],
    max_tokens: int = 800,
    temperature: float = 0.15,
) -> dict:
    """Call LLM and parse JSON response, with fallback to empty dict."""
    try:
        raw = call_llm_sync(messages, max_tokens=max_tokens, temperature=temperature)
        return parse_json_response(raw)
    except Exception as e:
        log.warning(f"LLM JSON call failed: {e}")
        return {}
