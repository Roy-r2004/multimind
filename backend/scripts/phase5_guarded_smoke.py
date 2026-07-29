"""User-run guarded Phase 5 smoke. Preview-only unless confirmations are supplied."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError as AppValidationError
from app.db.models import (
    CrawlNodeSourceClassification, Organization, OrgMembership,
    ScrapingBlueprint, ScrapingBlueprintStatus, ScrapingCrawlNode,
    ScrapingExecution, ScrapingExecutionStatus, ScrapingMission,
    ScrapingMissionStatus, ScrapingPhase5RetrievalResult,
    ScrapingPhase5WorkJob, ScrapingSourceCandidate, ScrapingSourceDiscoveryQuery,
    ScrapingSourceDocument, SourceCandidateStatus, SourceDiscoveryQueryStatus,
)
from app.db.session import AsyncSessionLocal
from app.services.scraping.phase5_contracts import Phase5WorkKind, prepare_phase5_job
from app.services.scraping.phase5_job_service import (
    claim_guarded_controlled_http_job, create_job_idempotently,
    persist_retrieval_resources,
)
from app.services.scraping.phase5_retrieval_service import NormalHttpRetriever
from app.services.scraping.blueprint_execution_plan_service import (
    MissionCountryIdentity,
    blueprint_execution_plan_service,
    sha256_hex,
)
from app.services.scraping.countries import resolve_country
from app.services.scraping.discovery_url_service import (
    canonicalize_discovery_target,
    compute_canonical_url_hash,
)
from app.services.scraping.source_retrieval_service import (
    SourceRetrievalError,
    SourceRetrievalService,
)


KNOWN_UNSUITABLE_EXECUTION_ID = "bdda236a-9810-47f4-b2f6-2bf24cd48b90"
ACTIVE_EXECUTION_STATUSES = {
    "queued", "running", "pause_requested", "cancel_requested",
}
PROFILE_CLASSIFICATIONS = {
    CrawlNodeSourceClassification.FACILITY_PROFILE.value,
    CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value,
    CrawlNodeSourceClassification.GOVERNMENT_SOURCE.value,
    CrawlNodeSourceClassification.REGISTRY.value,
}
EXCLUDED_CLASSIFICATIONS = {
    CrawlNodeSourceClassification.DIRECTORY.value,
    CrawlNodeSourceClassification.PDF.value,
    CrawlNodeSourceClassification.SOCIAL_PROFILE.value,
    CrawlNodeSourceClassification.IRRELEVANT.value,
}
PROFILE_PATH_SIGNALS = (
    "facility", "facilities", "centre", "center", "clinic", "hospital",
    "rehabilitation", "rehab", "treatment", "addiction", "residence",
    "residential", "program", "programme",
)
SEARCH_PATH_SIGNALS = ("search", "results", "find-a-", "find_", "directory")
LOGIN_PATH_SIGNALS = ("login", "signin", "sign-in", "auth", "redirect")
BINARY_SUFFIXES = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".jpg", ".jpeg",
    ".png", ".gif",
)
CONTROLLED_PROFILE_SCHEMA = "phase5_guarded_controlled_profile_v1"
SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()/-]{0,119}$")


def _value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def validate_profile_listing_request(
    organization_id: str | None, country: str | None, limit: int | None,
) -> tuple[str, str, int]:
    organization_id = (organization_id or "").strip()
    country = (country or "").strip().upper()
    if not organization_id:
        raise ValueError("organization_id is required")
    if len(country) != 2 or not country.isalpha():
        raise ValueError("country must be a two-letter ISO country code")
    if limit is None or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    return organization_id, country, limit


def validate_controlled_profile_request(
    organization_id: str | None,
    country_code: str | None,
    raw_url: str | None,
    label: str | None,
) -> dict:
    organization_id = (organization_id or "").strip()
    country_code = (country_code or "").strip().upper()
    label = (label or "").strip()
    if not organization_id:
        raise ValueError("organization_id is required")
    try:
        country = resolve_country(country_code)
    except AppValidationError as exc:
        raise ValueError("invalid country") from exc
    if country.iso2 != country_code:
        raise ValueError("country must be a two-letter ISO country code")
    if not SAFE_LABEL.fullmatch(label):
        raise ValueError("label must be 1-120 safe display characters")
    canonical = canonicalize_discovery_target(raw_url or "")
    if not canonical.is_valid or not canonical.is_statically_safe:
        raise ValueError(canonical.error_code or "unsafe_url")
    if canonical.scheme != "https":
        raise ValueError("controlled profile URL must use HTTPS")
    return {
        "organization_id": organization_id,
        "country": country,
        "label": label,
        "canonical_url": canonical.canonical_url,
        "canonical_host": canonical.hostname,
        "canonical_domain": canonical.normalized_domain or canonical.hostname,
        "canonical_url_hash": compute_canonical_url_hash(canonical.canonical_url),
    }


def _stable_uuid(kind: str, identity: dict) -> str:
    digest = hashlib.sha256(json.dumps(
        {
            "schema": CONTROLLED_PROFILE_SCHEMA,
            "kind": kind,
            "identity": identity,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return str(uuid.UUID(hex=digest[:32]))


def controlled_profile_identities(request: dict) -> dict[str, str]:
    identity = {
        "organization_id": request["organization_id"],
        "country_code": request["country"].iso2,
        "canonical_url": request["canonical_url"],
        "label": request["label"],
    }
    return {
        kind: _stable_uuid(kind, identity)
        for kind in (
            "mission", "blueprint", "execution", "discovery_query",
            "source_candidate", "crawl_node",
        )
    }


def controlled_execution_reuse_exclusion(
    execution_id: str, status: str, node_count: int, document_count: int,
) -> str | None:
    if execution_id == KNOWN_UNSUITABLE_EXECUTION_ID:
        return "known_zero_candidate_execution_must_not_be_reused"
    if status in ACTIVE_EXECUTION_STATUSES and status != ScrapingExecutionStatus.PAUSED.value:
        return "controlled_profile_execution_is_active"
    if node_count > 100 or document_count > 100:
        return "controlled_profile_execution_is_oversized"
    return None


def _controlled_structured_blueprint(request: dict) -> dict:
    country = request["country"]
    url = request["canonical_url"]
    label = request["label"]
    citation = {
        "url": url,
        "title": label,
        "source_type": "official_facility_profile",
        "notes": "Manually selected guarded validation source.",
    }
    strategy = {"summary": "Guarded validation of one manually selected profile."}
    return {
        "schema_version": "2",
        "country_dossier": {
            "country_name": country.name,
            "country_iso3": country.iso3,
            "continent": country.continent,
        },
        "regions": ["Mount Lebanon"],
        "languages": ["English"],
        "language_profiles": [{"name": "English", "code": "en", "script": "Latn"}],
        "important_cities": [{"name": "Hammana", "region_name": "Mount Lebanon"}],
        "local_terminology": ["rehabilitation"],
        "inpatient_residential_terminology": ["residential rehabilitation"],
        "private_paid_terminology": ["private rehabilitation"],
        "addiction_categories": ["addiction"],
        "regulatory_sources": [citation],
        "commercial_sources": [],
        "query_matrix": [{
            "query": f"{label} official facility profile",
            "language": "English",
            "purpose": "guarded_validation",
        }],
        "region_coverage_plan": [{
            "region_name": "Mount Lebanon",
            "coverage_actions": ["Validate one manually selected official profile"],
        }],
        "discovery_strategy": strategy,
        "crawl_strategy": strategy,
        "contact_completeness_strategy": strategy,
        "verification_rules": strategy,
        "country_containment_rules": strategy,
        "deduplication_rules": strategy,
        "confidence_model": strategy,
        "completion_criteria": ["One controlled profile is available for guarded retrieval."],
        "risks": ["Single-source smoke is not a coverage claim."],
        "citations": [citation],
        "estimated_coverage": strategy,
        "weak_areas": ["Only one explicitly selected source is in scope."],
        "human_review_questions": ["Does the retrieved profile contain a real facility?"],
        "approval_recommendation": {
            "ready": True,
            "reason": "Explicitly bounded guarded validation source.",
        },
    }


async def _controlled_existing_state(
    session: AsyncSession, request: dict, ids: dict[str, str],
) -> dict:
    rows = {
        "mission": await session.get(ScrapingMission, ids["mission"]),
        "blueprint": await session.get(ScrapingBlueprint, ids["blueprint"]),
        "execution": await session.get(ScrapingExecution, ids["execution"]),
        "discovery_query": await session.get(
            ScrapingSourceDiscoveryQuery, ids["discovery_query"]),
        "source_candidate": await session.get(
            ScrapingSourceCandidate, ids["source_candidate"]),
        "crawl_node": await session.get(ScrapingCrawlNode, ids["crawl_node"]),
    }
    present = sum(value is not None for value in rows.values())
    if present not in {0, len(rows)}:
        raise ValueError("incomplete_controlled_profile_graph")
    if not present:
        return {"strategy": "create_new_controlled_graph", "rows": rows}
    mission = rows["mission"]
    blueprint = rows["blueprint"]
    execution = rows["execution"]
    discovery_query = rows["discovery_query"]
    node = rows["crawl_node"]
    candidate = rows["source_candidate"]
    expected_org = request["organization_id"]
    owned = (
        mission.org_id == expected_org
        and execution.organization_id == expected_org
        and discovery_query.organization_id == expected_org
        and node.organization_id == expected_org
        and candidate.organization_id == expected_org
    )
    if not owned:
        raise ValueError("controlled_profile_tenant_mismatch")
    status = _value(execution.status)
    node_count = int(await session.scalar(
        select(func.count()).select_from(ScrapingCrawlNode).where(
            ScrapingCrawlNode.organization_id == expected_org,
            ScrapingCrawlNode.execution_id == execution.id,
        )) or 0)
    document_count = int(await session.scalar(
        select(func.count()).select_from(ScrapingSourceDocument).where(
            ScrapingSourceDocument.organization_id == expected_org,
            ScrapingSourceDocument.execution_id == execution.id,
        )) or 0)
    exclusion = controlled_execution_reuse_exclusion(
        execution.id, status, node_count, document_count)
    if exclusion:
        raise ValueError(exclusion)
    if (
        execution.country_code != request["country"].iso2
        or blueprint.mission_id != mission.id
        or mission.active_blueprint_id != blueprint.id
        or execution.mission_id != mission.id
        or execution.blueprint_id != blueprint.id
        or discovery_query.execution_id != execution.id
        or discovery_query.country_code != request["country"].iso2
        or node.canonical_url != request["canonical_url"]
        or node.execution_id != execution.id
        or _value(node.source_classification)
        != CrawlNodeSourceClassification.FACILITY_PROFILE.value
        or candidate.execution_id != execution.id
        or candidate.discovery_query_id != discovery_query.id
        or candidate.crawl_node_id != node.id
    ):
        raise ValueError("controlled_profile_graph_identity_mismatch")
    return {"strategy": "reuse_existing_controlled_graph", "rows": rows}


async def preview_controlled_profile(session: AsyncSession, request: dict) -> dict:
    ids = controlled_profile_identities(request)
    existing = await _controlled_existing_state(session, request, ids)
    creates = 6 if existing["strategy"] == "create_new_controlled_graph" else 0
    return {
        "mode": "preview_controlled_profile",
        "organization_id": request["organization_id"],
        "country": request["country"].iso2,
        "canonical_url": request["canonical_url"],
        "canonical_host": request["canonical_host"],
        "canonical_domain": request["canonical_domain"],
        "ssrf_validation": "static_safe_dns_validation_pending_confirmation",
        "creation_strategy": existing["strategy"],
        "mission_strategy": "dedicated_deterministic_mission",
        "blueprint_strategy": "dedicated_approved_compiled_blueprint",
        "run_strategy": "not_required_team_plan_is_null",
        "execution_id": ids["execution"],
        "source_candidate_id": ids["source_candidate"],
        "crawl_node_id": ids["crawl_node"],
        "planned_source_candidate_identity": ids["source_candidate"],
        "planned_crawl_node_identity": request["canonical_url_hash"],
        "planned_node_classification": "facility_profile",
        "rows_that_would_be_created": creates,
        "http_request": False,
        "provider_call": False,
        "worker_start": False,
        "retrieval": False,
        "extraction": False,
        "publication": False,
        "excel": False,
    }


async def _assert_dns_safe(request: dict) -> None:
    try:
        validated = await SourceRetrievalService()._validate_url(
            request["canonical_url"])
    except SourceRetrievalError as exc:
        raise ValueError(f"dns_ssrf_validation_failed:{exc.status.value}") from exc
    if validated.scheme != "https" or validated.hostname != request["canonical_host"]:
        raise ValueError("dns_ssrf_validation_identity_mismatch")


async def create_controlled_profile(
    session: AsyncSession, request: dict, *, creator_id: str,
) -> dict:
    ids = controlled_profile_identities(request)
    existing = await _controlled_existing_state(session, request, ids)
    if existing["strategy"] == "reuse_existing_controlled_graph":
        return await _controlled_creation_result(session, request, ids, created=False)

    country = request["country"]
    structured = _controlled_structured_blueprint(request)
    compiled = blueprint_execution_plan_service.compile(
        mission_id=ids["mission"],
        blueprint_id=ids["blueprint"],
        blueprint_version=1,
        mission_country=MissionCountryIdentity(
            country_code=country.iso2,
            country_name=country.name,
            country_iso3=country.iso3,
            continent=country.continent,
        ),
        structured_blueprint=structured,
        require_v2=True,
    )
    now = datetime.now(UTC)
    mission = ScrapingMission(
        id=ids["mission"],
        org_id=request["organization_id"],
        created_by=creator_id,
        model_set_id="guarded-controlled-smoke",
        title=request["label"],
        original_prompt="Guarded validation of one manually selected facility profile.",
        country_code=country.iso2,
        country_name=country.name,
        country_iso3=country.iso3,
        continent=country.continent,
        status=ScrapingMissionStatus.APPROVED,
        active_blueprint_id=ids["blueprint"],
    )
    blueprint = ScrapingBlueprint(
        id=ids["blueprint"],
        mission_id=mission.id,
        version=1,
        status=ScrapingBlueprintStatus.APPROVED,
        blueprint_json={"guarded_controlled_smoke": True},
        display_name=request["label"],
        model_set_id="guarded-controlled-smoke",
        approved_by=creator_id,
        approved_at=now,
        country_name_snapshot=country.name,
        country_iso3_snapshot=country.iso3,
        continent_snapshot=country.continent,
        provider="manual_guarded_validation",
        prompt_template_version=CONTROLLED_PROFILE_SCHEMA,
        structured_blueprint=structured,
    )
    execution = ScrapingExecution(
        id=ids["execution"],
        organization_id=request["organization_id"],
        mission_id=mission.id,
        blueprint_id=blueprint.id,
        team_plan_id=None,
        execution_type="mission_campaign",
        mode="real",
        execution_origin="guarded_controlled_profile",
        blueprint_version_snapshot=1,
        blueprint_snapshot_json=compiled.blueprint_snapshot_json,
        frozen_execution_plan_json=compiled.frozen_execution_plan_json,
        execution_plan_schema_version=compiled.execution_plan_schema_version,
        execution_plan_hash=compiled.execution_plan_hash,
        execution_plan_compiled_at=now,
        clarification_status="not_required",
        created_by=creator_id,
        status=ScrapingExecutionStatus.PAUSED,
        country_code=country.iso2,
        country_name=country.name,
        paused_at=now,
        current_stage="phase5_controlled_profile_ready",
        latest_message="Guarded controlled profile created; retrieval not started.",
        regions_total=1,
    )
    query_fingerprint = sha256_hex({
        "schema": CONTROLLED_PROFILE_SCHEMA,
        "kind": "manual_query_provenance",
        "execution_id": execution.id,
        "canonical_url": request["canonical_url"],
    })
    discovery_query = ScrapingSourceDiscoveryQuery(
        id=ids["discovery_query"],
        organization_id=request["organization_id"],
        execution_id=execution.id,
        country_code=country.iso2,
        country_name=country.name,
        region_name=None,
        language_code="en",
        language_name="English",
        source_category="official_facility_profile",
        query_text=f"{request['label']} official facility profile",
        provider="manual_guarded_validation",
        status=SourceDiscoveryQueryStatus.SUCCEEDED,
        requested_at=now,
        completed_at=now,
        result_count=1,
        metadata_json={
            "provenance": "manually_selected_guarded_validation_source",
            "external_provider_call": False,
        },
        purpose="guarded_validation",
        priority=0,
        discovery_round=1,
        generation_ordinal=0,
        query_job_fingerprint=query_fingerprint,
        plan_hash_snapshot=compiled.execution_plan_hash,
        scope_level="countrywide",
    )
    node = ScrapingCrawlNode(
        id=ids["crawl_node"],
        organization_id=request["organization_id"],
        execution_id=execution.id,
        canonical_url=request["canonical_url"],
        canonical_url_hash=request["canonical_url_hash"],
        hostname=request["canonical_host"],
        domain=request["canonical_domain"],
        source_classification=CrawlNodeSourceClassification.FACILITY_PROFILE,
        first_seen_at=now,
    )
    candidate = ScrapingSourceCandidate(
        id=ids["source_candidate"],
        organization_id=request["organization_id"],
        execution_id=execution.id,
        discovery_query_id=discovery_query.id,
        crawl_node_id=node.id,
        provider="manual_guarded_validation",
        provider_result_id=None,
        rank=1,
        provider_page_number=1,
        url=request["canonical_url"],
        canonical_url=request["canonical_url"],
        domain=request["canonical_domain"],
        title=request["label"],
        snippet="Manually selected official facility profile for guarded validation.",
        country_code=country.iso2,
        country_name=country.name,
        region_name="Mount Lebanon",
        language_code="en",
        language_name="English",
        source_category="official_facility_profile",
        initial_relevance_score=1,
        initial_trust_tier="high",
        status=SourceCandidateStatus.DISCOVERED,
        discovered_at=now,
        metadata_json={
            "provenance": "manually_selected_guarded_validation_source",
            "automatic_discovery": False,
        },
    )
    session.add_all([mission, blueprint, execution, discovery_query, node, candidate])
    await session.flush()
    return await _controlled_creation_result(session, request, ids, created=True)


async def _controlled_creation_result(
    session: AsyncSession, request: dict, ids: dict[str, str], *, created: bool,
) -> dict:
    retrieval_count = int(await session.scalar(
        select(func.count()).select_from(ScrapingPhase5RetrievalResult).where(
            ScrapingPhase5RetrievalResult.organization_id == request["organization_id"],
            ScrapingPhase5RetrievalResult.execution_id == ids["execution"],
        )) or 0)
    document_count = int(await session.scalar(
        select(func.count()).select_from(ScrapingSourceDocument).where(
            ScrapingSourceDocument.organization_id == request["organization_id"],
            ScrapingSourceDocument.execution_id == ids["execution"],
        )) or 0)
    return {
        "created": created,
        "mission_id": ids["mission"],
        "blueprint_id": ids["blueprint"],
        "run_id": None,
        "execution_id": ids["execution"],
        "source_candidate_id": ids["source_candidate"],
        "crawl_node_id": ids["crawl_node"],
        "canonical_url": request["canonical_url"],
        "execution_status": "paused",
        "retrieval_result_count": retrieval_count,
        "source_document_count": document_count,
        "phase5_preview_command": (
            "python -m scripts.phase5_guarded_smoke "
            f"--organization-id {request['organization_id']} "
            f"--execution-id {ids['execution']} --crawl-node-id {ids['crawl_node']}"
        ),
    }


async def run_controlled_profile(args) -> None:
    request = validate_controlled_profile_request(
        args.organization_id, args.country, args.url, args.label)
    if not args.confirm_create:
        async with AsyncSessionLocal() as session:
            print(json.dumps(
                await preview_controlled_profile(session, request),
                sort_keys=True,
                separators=(",", ":"),
            ))
        return

    # Normal Phase 5 DNS-aware validation. No HTTP request; deliberately outside TX.
    await _assert_dns_safe(request)
    async with AsyncSessionLocal.begin() as session:
        organization = await session.get(Organization, request["organization_id"])
        if organization is None:
            raise SystemExit("Organization was not found.")
        creator_id = await session.scalar(
            select(OrgMembership.user_id)
            .where(OrgMembership.org_id == request["organization_id"])
            .order_by(OrgMembership.user_id)
            .limit(1)
        )
        if creator_id is None:
            raise SystemExit("Organization has no user available for controlled provenance.")
        result = await create_controlled_profile(
            session, request, creator_id=creator_id)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


def _profile_target_decision(item: dict) -> tuple[int | None, str | None, list[str]]:
    """Return deterministic score/reason, or an exclusion code and safe warnings."""
    warnings: list[str] = []
    if item["execution_id"] == KNOWN_UNSUITABLE_EXECUTION_ID:
        return None, "known_zero_candidate_execution", warnings
    if item["execution_status"] in ACTIVE_EXECUTION_STATUSES:
        return None, "active_execution", warnings
    if item["execution_node_count"] > 100 or item["execution_document_count"] > 100:
        return None, "large_execution", warnings
    if item["successful_retrieval_count"] or item["source_document_count"]:
        return None, "already_retrieved", warnings
    if item["terminal_failed_job_count"] and not item["retryable_failed_job_count"]:
        return None, "terminal_retrieval_failure", warnings

    safe_url = canonicalize_discovery_target(item["url"])
    if not safe_url.is_valid or not safe_url.is_statically_safe:
        return None, safe_url.error_code or "unsafe_url", warnings
    parsed = urlsplit(safe_url.canonical_url or "")
    path = parsed.path.lower()
    host = safe_url.hostname or ""
    classification = item["classification"]
    if classification in EXCLUDED_CLASSIFICATIONS:
        return None, f"excluded_classification_{classification}", warnings
    if any(token in path for token in LOGIN_PATH_SIGNALS):
        return None, "login_or_redirect_page", warnings
    if path.endswith(BINARY_SUFFIXES):
        return None, "binary_or_pdf_resource", warnings
    if any(token in path for token in SEARCH_PATH_SIGNALS):
        return None, "search_or_directory_page", warnings
    if host.endswith((
        "facebook.com", "instagram.com", "linkedin.com", "x.com",
        "twitter.com", "youtube.com", "maps.google.com", "goo.gl",
    )):
        return None, "social_or_map_only", warnings

    path_signals = [token for token in PROFILE_PATH_SIGNALS if token in path]
    is_homepage = path in {"", "/"}
    if is_homepage and classification not in PROFILE_CLASSIFICATIONS:
        return None, "generic_homepage", warnings
    if classification not in PROFILE_CLASSIFICATIONS and not path_signals:
        return None, "no_facility_profile_signal", warnings
    if not item["source_candidate_id"]:
        return None, "missing_source_candidate_provenance", warnings

    score = 0
    if classification == CrawlNodeSourceClassification.FACILITY_PROFILE.value:
        score += 70
    elif classification == CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value:
        score += 55
    elif classification in {
        CrawlNodeSourceClassification.GOVERNMENT_SOURCE.value,
        CrawlNodeSourceClassification.REGISTRY.value,
    }:
        score += 45
    score += min(len(path_signals), 4) * 8
    if safe_url.scheme == "https":
        score += 12
    if item["execution_status"] == ScrapingExecutionStatus.PAUSED.value:
        score += 20
    if 1 <= item["execution_node_count"] <= 10:
        score += 18
    if item["provider"].lower() == "serper":
        score += 8
    if item["discovery_query_id"]:
        score += 4
    if is_homepage:
        score -= 15
        warnings.append("homepage_requires_manual_profile_review")

    if (classification == CrawlNodeSourceClassification.FACILITY_PROFILE.value
            and item["execution_node_count"] <= 10):
        reason = "official_single_facility_profile"
    elif classification in {
        CrawlNodeSourceClassification.GOVERNMENT_SOURCE.value,
        CrawlNodeSourceClassification.REGISTRY.value,
    }:
        reason = "official_regulator_facility_entry"
    elif path_signals:
        reason = "explicit_facility_url_signals"
    elif item["execution_status"] == ScrapingExecutionStatus.PAUSED.value:
        reason = "small_paused_execution"
    else:
        reason = "safe_https_profile_candidate"
    return score, reason, warnings


async def list_profile_targets(session, *, organization_id: str, country: str,
                               limit: int, execution_id: str | None = None) -> list[dict]:
    """Read-only target discovery. The caller owns the session and never commits it."""
    node_counts = (
        select(
            ScrapingCrawlNode.execution_id.label("execution_id"),
            func.count(ScrapingCrawlNode.id).label("node_count"),
        )
        .where(ScrapingCrawlNode.organization_id == organization_id)
        .group_by(ScrapingCrawlNode.execution_id)
        .subquery()
    )
    document_counts = (
        select(
            ScrapingSourceDocument.execution_id.label("execution_id"),
            func.count(ScrapingSourceDocument.id).label("document_count"),
        )
        .where(ScrapingSourceDocument.organization_id == organization_id)
        .group_by(ScrapingSourceDocument.execution_id)
        .subquery()
    )
    query = (
        select(
            ScrapingCrawlNode,
            ScrapingSourceCandidate,
            ScrapingExecution,
            ScrapingSourceDiscoveryQuery,
            node_counts.c.node_count,
            func.coalesce(document_counts.c.document_count, 0),
        )
        .join(
            ScrapingExecution,
            (ScrapingExecution.id == ScrapingCrawlNode.execution_id)
            & (ScrapingExecution.organization_id == ScrapingCrawlNode.organization_id),
        )
        .join(
            ScrapingSourceCandidate,
            (ScrapingSourceCandidate.crawl_node_id == ScrapingCrawlNode.id)
            & (ScrapingSourceCandidate.execution_id == ScrapingCrawlNode.execution_id)
            & (ScrapingSourceCandidate.organization_id == ScrapingCrawlNode.organization_id),
        )
        .join(
            ScrapingSourceDiscoveryQuery,
            (ScrapingSourceDiscoveryQuery.id == ScrapingSourceCandidate.discovery_query_id)
            & (ScrapingSourceDiscoveryQuery.execution_id == ScrapingCrawlNode.execution_id)
            & (ScrapingSourceDiscoveryQuery.organization_id
               == ScrapingCrawlNode.organization_id),
        )
        .join(node_counts, node_counts.c.execution_id == ScrapingCrawlNode.execution_id)
        .outerjoin(
            document_counts,
            document_counts.c.execution_id == ScrapingCrawlNode.execution_id,
        )
        .where(
            ScrapingCrawlNode.organization_id == organization_id,
            ScrapingSourceCandidate.country_code == country,
        )
        .order_by(
            ScrapingCrawlNode.execution_id,
            ScrapingCrawlNode.id,
            ScrapingSourceCandidate.id,
        )
        .limit(2000)
    )
    if execution_id:
        query = query.where(ScrapingCrawlNode.execution_id == execution_id)
    rows = (await session.execute(query)).all()
    targets: list[dict] = []
    seen_node_ids: set[str] = set()
    for (node, candidate, execution, discovery_query, execution_node_count,
         execution_document_count) in rows:
        if node.id in seen_node_ids:
            continue
        seen_node_ids.add(node.id)
        job_rows = (await session.execute(select(ScrapingPhase5WorkJob).where(
            ScrapingPhase5WorkJob.organization_id == organization_id,
            ScrapingPhase5WorkJob.execution_id == node.execution_id,
            ScrapingPhase5WorkJob.crawl_node_id == node.id,
        ))).scalars().all()
        retrieval_rows = (await session.execute(
            select(ScrapingPhase5RetrievalResult)
            .join(
                ScrapingPhase5WorkJob,
                ScrapingPhase5WorkJob.id == ScrapingPhase5RetrievalResult.work_job_id,
            )
            .where(
                ScrapingPhase5RetrievalResult.organization_id == organization_id,
                ScrapingPhase5RetrievalResult.execution_id == node.execution_id,
                ScrapingPhase5WorkJob.crawl_node_id == node.id,
            )
        )).scalars().all()
        document_count = int(await session.scalar(
            select(func.count()).select_from(ScrapingSourceDocument).where(
                ScrapingSourceDocument.organization_id == organization_id,
                ScrapingSourceDocument.execution_id == node.execution_id,
                ScrapingSourceDocument.source_candidate_id == candidate.id,
            )) or 0)
        terminal = [
            job for job in job_rows
            if _value(job.status) in {"blocked", "rejected", "failed"}
        ]
        item = {
            "organization_id": organization_id,
            "execution_id": node.execution_id,
            "execution_status": _value(execution.status),
            "country_code": country,
            "crawl_node_id": node.id,
            "source_candidate_id": candidate.id,
            "discovery_query_id": candidate.discovery_query_id,
            "query_job_fingerprint": discovery_query.query_job_fingerprint,
            "provider": candidate.provider,
            "provider_page_number": candidate.provider_page_number,
            "url": node.canonical_url,
            "canonical_host": node.hostname,
            "canonical_domain": node.domain,
            "classification": _value(node.source_classification),
            # Crawl nodes do not persist a numeric depth; direct Serper nodes are roots.
            "crawl_depth": 0 if candidate.discovery_query_id else None,
            "retrieval_job_count": len(job_rows),
            "successful_retrieval_count": sum(
                1 for row in retrieval_rows
                if not row.failure_category and row.source_document_id),
            "source_document_count": document_count,
            "execution_node_count": int(execution_node_count),
            "execution_document_count": int(execution_document_count),
            "terminal_failed_job_count": len(terminal),
            "retryable_failed_job_count": sum(
                1 for job in terminal
                if job.max_attempts is not None and job.attempt_count < job.max_attempts),
        }
        score, reason, warnings = _profile_target_decision(item)
        if score is None:
            continue
        item.update(score=score, recommendation_reason=reason, warnings=warnings)
        item.pop("terminal_failed_job_count")
        item.pop("retryable_failed_job_count")
        targets.append(item)
    targets.sort(key=lambda item: (
        -item["score"], item["execution_id"], item["crawl_node_id"]))
    return targets[:limit]


async def run_profile_target_listing(args) -> None:
    organization_id, country, limit = validate_profile_listing_request(
        args.organization_id, args.country, args.limit)
    async with AsyncSessionLocal() as session:
        targets = await list_profile_targets(
            session,
            organization_id=organization_id,
            country=country,
            limit=limit,
            execution_id=args.execution_id,
        )
    if not targets:
        print("no_suitable_existing_profile_node")
        print("A new small controlled mission/source must be created; no state was changed.")
        return
    for rank, target in enumerate(targets, 1):
        target["rank"] = rank
        target["safest_recommendation"] = rank == 1
        print(json.dumps(target, sort_keys=True, separators=(",", ":")))


async def main(args) -> None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(ScrapingCrawlNode, ScrapingSourceCandidate)
            .join(ScrapingSourceCandidate,
                  ScrapingSourceCandidate.crawl_node_id == ScrapingCrawlNode.id)
            .where(
                ScrapingCrawlNode.id == args.crawl_node_id,
                ScrapingCrawlNode.organization_id == args.organization_id,
                ScrapingCrawlNode.execution_id == args.execution_id,
                ScrapingSourceCandidate.organization_id == args.organization_id,
                ScrapingSourceCandidate.execution_id == args.execution_id)
            .limit(1))).first()
    if row is None:
        raise SystemExit("Selected owned crawl node/source candidate was not found.")
    node, candidate = row
    print({
        "mode": "preview" if not args.confirm_http else "confirmed_http",
        "execution_id": args.execution_id,
        "crawl_node_id": args.crawl_node_id,
        "automatic_continuation": False,
        "phase6": False,
        "firecrawl_confirmed": args.confirm_firecrawl,
        "playwright_confirmed": args.confirm_playwright,
    })
    if not args.confirm_http:
        return
    now = datetime.now(UTC)
    job = prepare_phase5_job(
        organization_id=args.organization_id, execution_id=args.execution_id,
        source_candidate_id=candidate.id, crawl_node_id=node.id,
        original_url=node.canonical_url,
        source_classification=node.source_classification.value,
        work_kind=Phase5WorkKind.HTTP_RETRIEVAL,
        selected_tool="http", requested_at=now)
    # Seeding is its own short transaction. A later claim/transport failure never
    # erases the deterministic job, so diagnostics remain inspectable and replayable.
    async with AsyncSessionLocal.begin() as session:
        created = await create_job_idempotently(session, job)
    async with AsyncSessionLocal.begin() as session:
        claim_result = await claim_guarded_controlled_http_job(
            session,
            job_id=created.record_id,
            organization_id=args.organization_id,
            execution_id=args.execution_id,
            crawl_node_id=node.id,
            lease_duration=timedelta(minutes=5),
        )
    print(json.dumps(claim_result.diagnostic, sort_keys=True, separators=(",", ":")))
    if claim_result.outcome == "already_retrieved":
        return
    if claim_result.claim is None:
        raise SystemExit(claim_result.outcome)
    claimed = claim_result.claim
    result = await NormalHttpRetriever().retrieve(
        url=node.canonical_url, requested_at=now, fetched_at=datetime.now(UTC))
    if result.outcome != "succeeded":
        raise SystemExit(f"HTTP smoke stopped safely: {result.failure_category}")
    async with AsyncSessionLocal.begin() as session:
        persisted = await persist_retrieval_resources(
            session, claimed_job=claimed, resources=result.resources,
            completed_at=datetime.now(UTC))
        if persisted and persisted[0].outcome == "stale_claim":
            print(json.dumps({
                "guarded_outcome": "job_state_invalid",
                "persistence_outcome": "stale_claim",
            }, sort_keys=True, separators=(",", ":")))
            return
        execution = await session.scalar(select(ScrapingExecution).where(
            ScrapingExecution.id == args.execution_id,
            ScrapingExecution.organization_id == args.organization_id).with_for_update())
        if execution is None:
            raise SystemExit("Execution ownership changed before smoke completion.")
        execution.status = ScrapingExecutionStatus.PAUSED
        execution.paused_at = datetime.now(UTC)
        execution.completed_at = None
        execution.current_stage = "phase5_smoke_review"
        execution.latest_message = "Guarded HTTP retrieval complete; Package A not started."
    print({
        "http_resources": len(result.resources),
        "retrieval_results": len(persisted),
        "execution_paused_after_proof": True,
        "phase6_started": False,
        "publication_invoked": False,
        "excel_invoked": False,
        "historical_mock_stages_started": False,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--execution-id")
    parser.add_argument("--crawl-node-id")
    parser.add_argument("--list-profile-targets", action="store_true")
    parser.add_argument("--create-controlled-profile", action="store_true")
    parser.add_argument("--country", type=str.upper)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--url")
    parser.add_argument("--label")
    parser.add_argument("--confirm-create", action="store_true")
    parser.add_argument("--confirm-http", action="store_true")
    parser.add_argument("--confirm-firecrawl", action="store_true")
    parser.add_argument("--confirm-playwright", action="store_true")
    parsed = parser.parse_args()
    if parsed.list_profile_targets and parsed.create_controlled_profile:
        parser.error("choose only one guarded mode")
    if parsed.create_controlled_profile:
        if (
            parsed.confirm_http
            or parsed.confirm_firecrawl
            or parsed.confirm_playwright
            or parsed.crawl_node_id
            or parsed.limit is not None
        ):
            parser.error("controlled creation cannot be combined with retrieval/listing flags")
        try:
            validate_controlled_profile_request(
                parsed.organization_id, parsed.country, parsed.url, parsed.label)
        except ValueError as exc:
            parser.error(str(exc))
        asyncio.run(run_controlled_profile(parsed))
    elif parsed.list_profile_targets:
        if parsed.confirm_http or parsed.confirm_firecrawl or parsed.confirm_playwright:
            parser.error("--list-profile-targets cannot be combined with confirmation flags")
        if parsed.confirm_create or parsed.url is not None or parsed.label is not None:
            parser.error("--list-profile-targets cannot use controlled-creation arguments")
        try:
            validate_profile_listing_request(
                parsed.organization_id, parsed.country, parsed.limit)
        except ValueError as exc:
            parser.error(str(exc))
        asyncio.run(run_profile_target_listing(parsed))
    else:
        if parsed.confirm_create or parsed.url is not None or parsed.label is not None:
            parser.error("controlled-creation arguments require --create-controlled-profile")
        if not parsed.execution_id or not parsed.crawl_node_id:
            parser.error("--execution-id and --crawl-node-id are required for smoke mode")
        if parsed.country is not None or parsed.limit is not None:
            parser.error("--country and --limit are only valid with --list-profile-targets")
        asyncio.run(main(parsed))
