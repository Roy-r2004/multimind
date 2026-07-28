"""Typed, network-free Phase 5A preparation and public-boundary contracts."""

from __future__ import annotations

import re
import ipaddress
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.scraping.blueprint_execution_plan_service import sha256_hex
from app.services.scraping.discovery_url_service import canonicalize_discovery_target

PHASE5_IDENTITY_SCHEMA = "phase5_work_v1"
DIRECTORY_OBSERVATION_SCHEMA = "directory_observation_v1"
RETRIEVAL_RESULT_SCHEMA = "phase5_retrieval_result_v1"
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_FORBIDDEN_PUBLIC_KEYS = {"claim_token", "authorization", "api_key", "secret", "plan_hash", "prompt"}


class Phase5WorkKind(str, Enum):
    DIRECTORY_EXPANSION = "directory_expansion"
    HTTP_RETRIEVAL = "http_retrieval"
    FIRECRAWL_RETRIEVAL = "firecrawl_retrieval"
    PLAYWRIGHT_RETRIEVAL = "playwright_retrieval"


class Phase5WorkStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


WORK_KIND_TOOL = {
    Phase5WorkKind.DIRECTORY_EXPANSION: "directory_expansion",
    Phase5WorkKind.HTTP_RETRIEVAL: "http",
    Phase5WorkKind.FIRECRAWL_RETRIEVAL: "firecrawl",
    Phase5WorkKind.PLAYWRIGHT_RETRIEVAL: "playwright",
}


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PreparedPhase5Job(StrictContract):
    organization_id: str
    execution_id: str
    source_candidate_id: str | None = None
    crawl_node_id: str
    crawl_edge_id: str | None = None
    discovery_query_id: str | None = None
    original_url: str
    canonical_url: str | None
    source_classification: str
    work_kind: Phase5WorkKind
    selected_tool: str
    fingerprint: str
    requested_at: datetime
    rejection_category: str | None = None


class Phase5ClaimResult(StrictContract):
    outcome: Literal["claimed", "no_work", "lifecycle_blocked"]
    job_ids: tuple[str, ...] = ()


class RetryableFailure(StrictContract):
    category: str
    public_message: str
    next_retry_at: datetime


class TerminalActionFailure(StrictContract):
    category: str
    public_message: str


class StaleClaim(StrictContract):
    outcome: Literal["stale_claim"] = "stale_claim"
    job_id: str


class ProviderToolBlocker(StrictContract):
    tool: str
    category: str
    retryable: bool
    retry_after: datetime | None = None


class PublicEventDetails(StrictContract):
    work_kind: Phase5WorkKind | None = None
    status: Phase5WorkStatus | None = None
    retryable: bool | None = None
    attempt_count: int | None = Field(default=None, ge=0)
    result_count: int | None = Field(default=None, ge=0)


class SanitizedPublicEventMetadata(StrictContract):
    category: str
    message: str
    tool: str | None = None
    metadata: PublicEventDetails = Field(default_factory=PublicEventDetails)

    @field_validator("category")
    @classmethod
    def safe_category(cls, value: str) -> str:
        if not _SAFE_CODE.fullmatch(value):
            raise ValueError("category must be a sanitized machine code")
        return value


    @field_validator("message")
    @classmethod
    def bounded_message(cls, value: str) -> str:
        if len(value) > 500 or any(x in value.lower() for x in ("bearer ", "api_key=", "token=")):
            raise ValueError("message is not safe for public persistence")
        for token in re.split(r"[\s/:,]+", value):
            try:
                address = ipaddress.ip_address(token.strip("[]()"))
            except ValueError:
                continue
            if not address.is_global:
                raise ValueError("private-network addresses are not public metadata")
        return value

class SanitizedOperationalMetadata(StrictContract):
    """Private queue metadata allowlist; never raw provider/browser payloads."""
    replay_reason: str | None = None
    resource_role: str | None = None
    provider_page_ordinal: int | None = Field(default=None, ge=0)
    parser_version: str | None = None


class PreparedRetrievalResult(StrictContract):
    job_id: str
    organization_id: str
    execution_id: str
    requested_url: str
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    content_length: int | None = Field(default=None, ge=0)
    response_fingerprint: str | None = None
    result_fingerprint: str
    resource_role: str
    result_ordinal: int = Field(ge=0)
    retrieval_method: Phase5WorkKind
    cache_status: Literal["miss", "hit", "replay", "not_applicable"] | None = None
    redirect_count: int = Field(default=0, ge=0)
    fetched_at: datetime
    raw_storage_reference: str | None = None
    source_document_id: str | None = None
    parent_crawl_edge_id: str | None = None
    provider_request_id: str | None = None
    provider_result_status: str | None = None
    failure_category: str | None = None

    @field_validator("retrieval_method")
    @classmethod
    def retrieval_only(cls, value: Phase5WorkKind) -> Phase5WorkKind:
        if value is Phase5WorkKind.DIRECTORY_EXPANSION:
            raise ValueError("directory expansion cannot persist retrieval results")
        return value


class PreparedDirectoryObservation(StrictContract):
    organization_id: str
    execution_id: str
    work_job_id: str
    displayed_facility_name: str | None = None
    listing_page_url: str
    profile_url: str | None = None
    official_website_url: str | None = None
    displayed_address: str | None = None
    displayed_phone: str | None = None
    displayed_region: str | None = None
    displayed_city: str | None = None
    directory_source: str
    listing_rank: int | None = Field(default=None, ge=1)
    raw_excerpt: str | None = None
    structured_payload_reference: str | None = None
    parent_directory_node_id: str
    emitted_profile_node_id: str | None = None
    emitted_website_node_id: str | None = None
    extraction_method: str
    observed_at: datetime
    observation_fingerprint: str


class Phase5PersistenceResult(StrictContract):
    outcome: Literal["created", "existing", "persisted", "stale_claim"]
    record_id: str | None = None


def prepare_phase5_job(*, organization_id: str, execution_id: str,
                       crawl_node_id: str, original_url: str,
                       source_classification: str, work_kind: Phase5WorkKind,
                       selected_tool: str, requested_at: datetime,
                       source_candidate_id: str | None = None,
                       crawl_edge_id: str | None = None,
                       discovery_query_id: str | None = None) -> PreparedPhase5Job:
    """Canonicalize with the Phase 4 service; unsafe targets remain rejected, never fetchable."""
    expected_tool = WORK_KIND_TOOL.get(work_kind)
    if expected_tool is None or selected_tool != expected_tool:
        raise ValueError("selected_tool does not match work_kind")
    url = canonicalize_discovery_target(original_url)
    canonical = url.canonical_url if url.is_valid and url.is_statically_safe else None
    rejection = None if canonical else (url.error_code or "unsafe_url")
    payload = {
        "schema": PHASE5_IDENTITY_SCHEMA,
        "organization_id": organization_id,
        "execution_id": execution_id,
        "crawl_node_id": crawl_node_id,
        "canonical_url": canonical,
        "original_url": original_url if canonical is None else None,
        "work_kind": work_kind.value,
        "selected_tool": selected_tool,
    }
    return PreparedPhase5Job(
        organization_id=organization_id, execution_id=execution_id,
        source_candidate_id=source_candidate_id, crawl_node_id=crawl_node_id,
        crawl_edge_id=crawl_edge_id, discovery_query_id=discovery_query_id,
        original_url=original_url, canonical_url=canonical,
        source_classification=source_classification, work_kind=work_kind,
        selected_tool=selected_tool, fingerprint=sha256_hex(payload),
        requested_at=requested_at, rejection_category=rejection,
    )


def directory_observation_fingerprint(
    *, organization_id: str, execution_id: str, parent_directory_node_id: str,
    listing_page_url: str, profile_url: str | None,
    official_website_url: str | None = None,
    displayed_facility_name: str | None = None,
    displayed_address: str | None = None, listing_rank: int | None = None,
) -> str:
    """Listing identity keeps physical branches distinct without timestamps/retries."""
    def normalized(value: str | None) -> str | None:
        if value is None:
            return None
        result = canonicalize_discovery_target(value)
        if not result.is_valid or not result.is_statically_safe or not result.canonical_url:
            raise ValueError("observation identity URLs must be statically safe")
        return result.canonical_url

    return sha256_hex({
        "schema": DIRECTORY_OBSERVATION_SCHEMA,
        "organization_id": organization_id,
        "execution_id": execution_id,
        "parent_directory_node_id": parent_directory_node_id,
        "listing_page_url": normalized(listing_page_url),
        "profile_url": normalized(profile_url),
        "official_website_url": normalized(official_website_url),
        "displayed_facility_name": displayed_facility_name,
        "displayed_address": displayed_address,
        "listing_rank": listing_rank,
    })


def retrieval_result_fingerprint(
    *, organization_id: str, execution_id: str, work_job_id: str,
    retrieval_method: Phase5WorkKind, resource_url: str,
    resource_role: str, result_ordinal: int,
) -> str:
    """Stable resource identity; content changes do not create a second logical resource."""
    if retrieval_method is Phase5WorkKind.DIRECTORY_EXPANSION:
        raise ValueError("directory expansion is not a retrieval method")
    url = canonicalize_discovery_target(resource_url)
    if not url.is_valid or not url.is_statically_safe or not url.canonical_url:
        raise ValueError("retrieval result resource URL must be statically safe")
    return sha256_hex({
        "schema": RETRIEVAL_RESULT_SCHEMA,
        "organization_id": organization_id,
        "execution_id": execution_id,
        "work_job_id": work_job_id,
        "retrieval_method": retrieval_method.value,
        "canonical_resource_url": url.canonical_url,
        "resource_role": resource_role,
        "result_ordinal": result_ordinal,
    })
