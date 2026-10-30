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
    CLASSIFICATION_COMPLETED = "classification_completed"
    CLASSIFICATION_FAILED_RETRYABLE = "classification_failed_retryable"
    DETAIL_NOT_REQUIRED = "detail_not_required"
    DETAIL_ENRICHMENT_PENDING = "detail_enrichment_pending"
    DETAIL_ENRICHMENT_COMPLETED = "detail_enrichment_completed"
    DETAIL_ENRICHMENT_FAILED = "detail_enrichment_failed"
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

# Pipeline states a place can sit in before mandatory classification has
# produced a result for it — the single source of truth for
# build_classification_query's WHERE clause (maps_classification_service.py)
# and for the "classification_pending" bucket in the Phase 2 breakdown
# (maps_census_service._phase2_breakdown). Keeping one definition means the
# query that actually claims/processes places and the count shown to the
# user can never silently drift apart.
PRE_CLASSIFICATION_PIPELINE_STATES = frozenset(
    {
        MapsEnrichmentPipelineState.PREFILTER_PENDING.value,
        MapsEnrichmentPipelineState.PREFILTER_COMPLETED.value,
        MapsEnrichmentPipelineState.WEBSITE_RESOLUTION_PENDING.value,
        MapsEnrichmentPipelineState.WEBSITE_RESOLVED.value,
        MapsEnrichmentPipelineState.WEBSITE_NOT_FOUND.value,
        MapsEnrichmentPipelineState.CRAWL_PENDING.value,
        MapsEnrichmentPipelineState.CRAWL_COMPLETED.value,
        MapsEnrichmentPipelineState.CRAWL_FAILED.value,
        MapsEnrichmentPipelineState.PRIMARY_EXTRACTION_PENDING.value,
        MapsEnrichmentPipelineState.PRIMARY_EXTRACTION_FAILED.value,
        MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value,
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
