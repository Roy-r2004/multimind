"""Choose a best-supported value across multiple sources."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def choose_best_value(candidates: list[dict[str, Any]]) -> str | None:
    if not candidates:
        return None
    scores: dict[str, float] = defaultdict(float)
    for candidate in candidates:
        value = str(candidate.get("value") or "").strip()
        if not value:
            continue
        scores[value] += float(candidate.get("weight") or 1.0)
    if not scores:
        return None
    return max(scores.items(), key=lambda item: (item[1], len(item[0]), item[0]))[0]
