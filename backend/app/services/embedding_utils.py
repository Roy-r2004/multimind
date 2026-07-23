"""Portable text embeddings for Brain retrieval (local hash + optional OpenRouter)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

EMBED_DIM = 256
OPENROUTER_EMBED_URL = "https://openrouter.ai/api/v1/embeddings"
OPENROUTER_EMBED_MODEL = "openai/text-embedding-3-small"

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.I)


def local_embed(text: str, *, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic bag-of-tokens hashing embedder (no network)."""
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall((text or "").lower())
    if not tokens:
        return vec
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    return _l2_normalize(vec)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


async def embed_text(text: str) -> list[float]:
    """Prefer OpenRouter embeddings when configured; otherwise local hash vectors."""
    settings = get_settings()
    api_key = settings.openrouter_api_key
    if not api_key:
        return local_embed(text)
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if settings.openrouter_site_url or settings.public_app_url:
            headers["HTTP-Referer"] = settings.openrouter_site_url or settings.public_app_url
        if settings.openrouter_app_name:
            headers["X-Title"] = settings.openrouter_app_name
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                OPENROUTER_EMBED_URL,
                headers=headers,
                json={"model": OPENROUTER_EMBED_MODEL, "input": (text or "")[:8000]},
            )
            resp.raise_for_status()
            data = resp.json()
            vector = data["data"][0]["embedding"]
            return _l2_normalize([float(x) for x in vector])
    except Exception as exc:  # noqa: BLE001 — fail-open to local embed
        logger.warning("openrouter_embed_failed", error=str(exc))
        return local_embed(text)
