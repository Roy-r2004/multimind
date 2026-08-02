"""In-memory cost/quota accounting for one Maps census phase (discovery,
website search, or enrichment), persisted additively onto
``MapsCensusRun.quota_metrics`` so a resumed/paused run keeps its running
totals instead of resetting them.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from app.db.models import MapsCensusRun

COUNTER_FIELDS: tuple[str, ...] = (
    "google_places_requests",
    "google_places_pages",
    "profile_model_calls",
    "planner_model_calls",
    "classifier_calls",
    "website_lookup_calls",
    "enrichment_calls",
    "primary_extraction_calls",
    "sonar_fallback_calls",
    "sonar_repair_calls",
    "crawl_requests",
    "estimated_tokens",
)


@dataclass
class MapsQuotaMetrics:
    google_places_requests: int = 0
    google_places_pages: int = 0
    profile_model_calls: int = 0
    planner_model_calls: int = 0
    classifier_calls: int = 0
    website_lookup_calls: int = 0
    enrichment_calls: int = 0
    primary_extraction_calls: int = 0
    sonar_fallback_calls: int = 0
    sonar_repair_calls: int = 0
    crawl_requests: int = 0
    estimated_tokens: int = 0
    runtime_seconds: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


class MapsQuotaTracker:
    """Accumulates counters for the current call; merge into the persisted
    run-level totals with :func:`merge_quota_metrics` when the phase ends."""

    def __init__(self) -> None:
        self.metrics = MapsQuotaMetrics()
        self._start = time.monotonic()

    def add_places_request(self, *, pages: int = 1) -> None:
        self.metrics.google_places_requests += 1
        self.metrics.google_places_pages += max(0, pages)

    def add_profile_call(self, count: int = 1) -> None:
        self.metrics.profile_model_calls += count

    def add_planner_call(self, count: int = 1) -> None:
        self.metrics.planner_model_calls += count

    def add_classifier_call(self, count: int = 1) -> None:
        self.metrics.classifier_calls += count

    def add_website_lookup_call(self, count: int = 1) -> None:
        self.metrics.website_lookup_calls += count

    def add_enrichment_call(self, count: int = 1) -> None:
        self.metrics.enrichment_calls += count

    def add_primary_extraction_call(self, count: int = 1) -> None:
        self.metrics.primary_extraction_calls += count

    def add_sonar_fallback_call(self, count: int = 1) -> None:
        self.metrics.sonar_fallback_calls += count

    def add_sonar_repair_call(self, count: int = 1) -> None:
        self.metrics.sonar_repair_calls += count

    def add_crawl_request(self, count: int = 1) -> None:
        self.metrics.crawl_requests += count

    def add_tokens(self, tokens: int) -> None:
        self.metrics.estimated_tokens += max(0, int(tokens))

    def snapshot(self) -> dict[str, float]:
        self.metrics.runtime_seconds = round(time.monotonic() - self._start, 3)
        return self.metrics.as_dict()


def empty_quota_metrics() -> dict[str, float]:
    return MapsQuotaMetrics().as_dict()


async def merge_quota_metrics(
    session_factory, *, run_id: str, tracker: MapsQuotaTracker
) -> dict[str, float]:
    """Additively merge ``tracker``'s counters into ``run.quota_metrics``.

    ``runtime_seconds`` is summed too (each phase's own elapsed wall time),
    giving a cumulative cost signal across discovery/website/enrichment
    phases and any resumed continuations of the same run.
    """
    updates = tracker.snapshot()
    async with session_factory() as db:
        run = await db.get(MapsCensusRun, run_id)
        if run is None:
            return {}
        merged = dict(run.quota_metrics or empty_quota_metrics())
        for key in COUNTER_FIELDS:
            merged[key] = merged.get(key, 0) + updates.get(key, 0)
        merged["runtime_seconds"] = round(
            merged.get("runtime_seconds", 0.0) + updates.get("runtime_seconds", 0.0), 3
        )
        run.quota_metrics = merged
        await db.commit()
        return merged
