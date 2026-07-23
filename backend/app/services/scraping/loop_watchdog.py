"""Progress-based loop detection for unbounded scraping executions.

The watchdog does not impose page, document, chunk, candidate, crawl-depth, or runtime caps.
It stops only when the same queued work repeats or the execution snapshot remains unchanged
for several rounds. URL-pattern growth is warning-only because large directories can legitimately
contain many pages with the same route shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LONG_HEX_RE = re.compile(r"^[0-9a-f]{12,}$", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^\d+$")
_MIXED_ID_RE = re.compile(r"^(?=.*\d)[a-z0-9_-]{16,}$", re.IGNORECASE)


@dataclass(frozen=True)
class LoopSnapshot:
    total_tasks: int
    queued_tasks: int
    terminal_tasks: int
    source_candidates: int
    source_documents: int
    retrieval_attempts: int

    def as_metadata(self) -> dict[str, int]:
        return {
            "total_tasks": self.total_tasks,
            "queued_tasks": self.queued_tasks,
            "terminal_tasks": self.terminal_tasks,
            "source_candidates": self.source_candidates,
            "source_documents": self.source_documents,
            "retrieval_attempts": self.retrieval_attempts,
        }


@dataclass(frozen=True)
class WatchdogSignal:
    severity: Literal["warning", "stop"]
    reason: str
    message: str
    metadata: dict[str, object]


class ScrapingLoopWatchdog:
    def __init__(
        self,
        *,
        enabled: bool = True,
        repeated_task_stop_threshold: int = 3,
        stagnant_round_warning_threshold: int = 2,
        stagnant_round_stop_threshold: int = 5,
        url_pattern_warning_threshold: int = 250,
        repeated_content_stop_threshold: int = 8,
    ) -> None:
        self.enabled = enabled
        self.repeated_task_stop_threshold = max(repeated_task_stop_threshold, 2)
        self.stagnant_round_warning_threshold = max(stagnant_round_warning_threshold, 1)
        self.stagnant_round_stop_threshold = max(
            stagnant_round_stop_threshold,
            self.stagnant_round_warning_threshold + 1,
        )
        self.url_pattern_warning_threshold = max(url_pattern_warning_threshold, 1)
        self.repeated_content_stop_threshold = max(repeated_content_stop_threshold, 2)
        self._task_seen_counts: dict[str, int] = {}
        self._last_snapshot: LoopSnapshot | None = None
        self._stagnant_rounds = 0
        self._url_pattern_counts: dict[str, int] = {}
        self._warned_url_pattern_counts: set[tuple[str, int]] = set()
        self._content_hash_counts: dict[str, int] = {}

    def observe_round(
        self,
        snapshot: LoopSnapshot,
        queued_task_ids: list[str],
    ) -> list[WatchdogSignal]:
        if not self.enabled:
            return []

        signals: list[WatchdogSignal] = []
        repeated: list[tuple[str, int]] = []
        for task_id in queued_task_ids:
            count = self._task_seen_counts.get(task_id, 0) + 1
            self._task_seen_counts[task_id] = count
            if count >= self.repeated_task_stop_threshold:
                repeated.append((task_id, count))

        if repeated:
            signals.append(
                WatchdogSignal(
                    severity="stop",
                    reason="repeated_queued_tasks",
                    message=(
                        "Loop watchdog stopped the scrape because the same queued task appeared "
                        "in multiple processing rounds without reaching a terminal state."
                    ),
                    metadata={
                        **snapshot.as_metadata(),
                        "repeated_tasks": [
                            {"task_id": task_id, "seen_count": count}
                            for task_id, count in repeated[:25]
                        ],
                        "threshold": self.repeated_task_stop_threshold,
                    },
                )
            )

        if self._last_snapshot == snapshot and snapshot.queued_tasks > 0:
            self._stagnant_rounds += 1
        else:
            self._stagnant_rounds = 0
        self._last_snapshot = snapshot

        if self._stagnant_rounds == self.stagnant_round_warning_threshold:
            signals.append(
                WatchdogSignal(
                    severity="warning",
                    reason="no_progress",
                    message=(
                        "Loop watchdog detected repeated processing rounds with no change in tasks, "
                        "sources, documents, or retrieval attempts."
                    ),
                    metadata={
                        **snapshot.as_metadata(),
                        "stagnant_rounds": self._stagnant_rounds,
                        "stop_threshold": self.stagnant_round_stop_threshold,
                    },
                )
            )
        elif self._stagnant_rounds >= self.stagnant_round_stop_threshold:
            signals.append(
                WatchdogSignal(
                    severity="stop",
                    reason="no_progress",
                    message=(
                        "Loop watchdog stopped the scrape after several processing rounds made no "
                        "observable progress."
                    ),
                    metadata={
                        **snapshot.as_metadata(),
                        "stagnant_rounds": self._stagnant_rounds,
                        "threshold": self.stagnant_round_stop_threshold,
                    },
                )
            )
        return signals

    def observe_url(self, url: str, *, crawl_depth: int) -> WatchdogSignal | None:
        if not self.enabled:
            return None
        pattern = crawl_pattern_signature(url)
        count = self._url_pattern_counts.get(pattern, 0) + 1
        self._url_pattern_counts[pattern] = count

        threshold = self.url_pattern_warning_threshold
        should_warn = count >= threshold and (
            count == threshold or (count % threshold == 0)
        )
        warning_key = (pattern, count)
        if not should_warn or warning_key in self._warned_url_pattern_counts:
            return None
        self._warned_url_pattern_counts.add(warning_key)
        return WatchdogSignal(
            severity="warning",
            reason="repeating_url_pattern",
            message=(
                "Loop watchdog noticed many unique URLs with the same route pattern. The crawl "
                "continues, but this can indicate pagination, calendar, session, or generated-link expansion."
            ),
            metadata={
                "url_pattern": pattern,
                "pattern_count": count,
                "crawl_depth": crawl_depth,
                "warning_threshold": threshold,
                "sample_url": url[:1000],
            },
        )

    def observe_content_hash(
        self,
        content_hash: str,
        *,
        url: str,
        crawl_depth: int,
    ) -> WatchdogSignal | None:
        if not self.enabled or not content_hash:
            return None
        count = self._content_hash_counts.get(content_hash, 0) + 1
        self._content_hash_counts[content_hash] = count
        if count < self.repeated_content_stop_threshold:
            return None
        return WatchdogSignal(
            severity="stop",
            reason="repeated_identical_content",
            message=(
                "Loop watchdog stopped the scrape because many different crawl URLs returned "
                "exactly the same document content. This usually indicates a soft-404, generated "
                "pagination trap, session-link loop, or redirect-like website behavior."
            ),
            metadata={
                "content_hash_prefix": content_hash[:16],
                "identical_document_count": count,
                "threshold": self.repeated_content_stop_threshold,
                "crawl_depth": crawl_depth,
                "sample_url": url[:1000],
            },
        )


def crawl_pattern_signature(url: str) -> str:
    """Collapse changing identifiers while preserving host, route shape, and query-key shape."""
    parsed = urlsplit(url)
    normalized_segments: list[str] = []
    for segment in parsed.path.split("/"):
        if not segment:
            continue
        lowered = segment.casefold()
        if (
            _UUID_RE.fullmatch(lowered)
            or _LONG_HEX_RE.fullmatch(lowered)
            or _NUMERIC_RE.fullmatch(lowered)
            or _MIXED_ID_RE.fullmatch(lowered)
        ):
            normalized_segments.append("{id}")
        else:
            normalized_segments.append(lowered[:80])
    query_keys = sorted({key.casefold()[:80] for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    path = "/" + "/".join(normalized_segments)
    query_shape = "&".join(query_keys)
    return f"{(parsed.hostname or '').casefold()}{path}?{query_shape}"
