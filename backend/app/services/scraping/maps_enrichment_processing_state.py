"""Per-place pipeline states for cascaded Maps enrichment."""

from __future__ import annotations

from enum import Enum


class MapsEnrichmentPipelineState(str, Enum):
    PREFILTER_PENDING = "prefilter_pending"
    PREFILTER_COMPLETED = "prefilter_completed"
    WEBSITE_RESOLUTION_PENDING = "website_resolution_pending"
    WEBSITE_RESOLVED = "website_resolved"
    WEBSITE_NOT_FOUND = "website_not_found"
    CRAWL_PENDING = "crawl_pending"
    CRAWL_COMPLETED = "crawl_completed"
    CRAWL_FAILED = "crawl_failed"
    PRIMARY_EXTRACTION_PENDING = "primary_extraction_pending"
    PRIMARY_EXTRACTION_COMPLETED = "primary_extraction_completed"
    PRIMARY_EXTRACTION_FAILED = "primary_extraction_failed"
    SONAR_FALLBACK_PENDING = "sonar_fallback_pending"
    SONAR_FALLBACK_COMPLETED = "sonar_fallback_completed"
    SONAR_FALLBACK_FAILED = "sonar_fallback_failed"
    FINALIZED = "finalized"
    NEEDS_REVIEW = "needs_review"


TERMINAL_PIPELINE_STATES = frozenset(
    {
        MapsEnrichmentPipelineState.FINALIZED.value,
        MapsEnrichmentPipelineState.NEEDS_REVIEW.value,
    }
)

RETRYABLE_PIPELINE_STATES = frozenset(
    {
        MapsEnrichmentPipelineState.PRIMARY_EXTRACTION_FAILED.value,
        MapsEnrichmentPipelineState.SONAR_FALLBACK_FAILED.value,
        MapsEnrichmentPipelineState.CRAWL_FAILED.value,
        MapsEnrichmentPipelineState.WEBSITE_NOT_FOUND.value,
    }
)


def default_pipeline_state() -> str:
    return MapsEnrichmentPipelineState.PREFILTER_PENDING.value


__all__ = [
    "MapsEnrichmentPipelineState",
    "RETRYABLE_PIPELINE_STATES",
    "TERMINAL_PIPELINE_STATES",
    "default_pipeline_state",
]
