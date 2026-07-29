"""Persistent deterministic Step 3 query-job generation (no providers or LLMs)."""

from __future__ import annotations

import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingSourceDiscoveryQuery,
    SourceDiscoveryQueryStatus,
)
from app.schemas.scraping_clarification import ClarificationStatus, ResolvedExecutionPlanEnvelope
from app.schemas.scraping_execution_plan import (
    FrozenExecutionPlanV2,
    FrozenLanguageProfile,
    supports_deterministic_query_generation,
    parse_frozen_execution_plan,
)
from app.services.scraping.blueprint_execution_plan_service import sha256_hex

IDENTITY_SCHEMA_VERSION = "1"
INSERT_BATCH_SIZE = 250

# ORM column length limits — fail closed; never silently truncate.
COUNTRY_CODE_MAX = 2
COUNTRY_NAME_MAX = 120
REGION_NAME_MAX = 160
IMPORTANT_CITY_MAX = 160
LANGUAGE_CODE_MAX = 16
LANGUAGE_NAME_MAX = 120
SOURCE_CATEGORY_MAX = 120
PURPOSE_MAX = 80

SOURCE_CATEGORIES: tuple[str, ...] = ("regulatory", "commercial")

PURPOSE_SEED = "seed_source_discovery"
PURPOSE_REGULATORY = "regulatory_source_discovery"
PURPOSE_COMMERCIAL = "commercial_source_discovery"

# Query-family identity (stored on metadata_json; purpose stays regulatory/commercial/seed).
QUERY_FAMILY_SEED = "seed"
QUERY_FAMILY_REGULATORY = "regulatory"
QUERY_FAMILY_FACILITY = "facility_discovery"
QUERY_FAMILY_PRIVATE = "private_provider"
QUERY_FAMILY_ADDICTION = "addiction_specific"

# Stable product templates (not blueprint axes). Kept short and deterministic.
# Regulatory family: geography × language × template × regulatory category only.
REGULATORY_QUERY_TEMPLATES: tuple[str, ...] = (
    "registry",
    "licensing",
    "government",
    "ministry",
    "authority",
    "official directory",
)
# Addiction family: geography × language × addiction × template × commercial only.
ADDICTION_FACILITY_TEMPLATES: tuple[str, ...] = (
    "rehabilitation",
    "treatment center",
)

PRIORITY_SEED = 100
PRIORITY_BY_CATEGORY_SCOPE: dict[tuple[str, str], int] = {
    ("regulatory", "countrywide"): 200,
    ("regulatory", "region"): 210,
    ("regulatory", "city"): 220,
    ("commercial", "countrywide"): 300,
    ("commercial", "region"): 310,
    ("commercial", "city"): 320,
}

# Keep in sync with execution_orchestrator.LANGUAGE_CODE_BY_NAME (do not invent codes).
LANGUAGE_CODE_BY_NAME = {
    "arabic": "ar",
    "bengali": "bn",
    "catalan": "ca",
    "chinese": "zh",
    "dutch": "nl",
    "english": "en",
    "french": "fr",
    "german": "de",
    "hindi": "hi",
    "indonesian": "id",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "malay": "ms",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
    "turkish": "tr",
    "urdu": "ur",
}

GenerationStatus = Literal["ok", "blocked", "error", "interrupted"]
ScopeLevel = Literal["countrywide", "region", "city"]
InterruptReason = Literal["paused", "cancelled"]


@dataclass(frozen=True)
class PlanSelection:
    plan: FrozenExecutionPlanV2
    plan_hash: str
    provenance: Literal["resolved", "frozen"]


@dataclass(frozen=True)
class QueryJobSpec:
    query_text: str
    language_code: str
    language_name: str
    script: str | None
    source_category: str
    purpose: str
    priority: int
    discovery_round: int
    generation_ordinal: int
    scope_level: ScopeLevel
    region_name: str | None
    important_city: str | None
    country_code: str
    country_name: str
    plan_hash_snapshot: str
    query_job_fingerprint: str
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryFamilyCounts:
    """Raw (pre-fingerprint-collapse) job counts per deterministic query family.

    Formulas (scopes = 1 + |regions| + |cities|):
    - seed:               |seeds| × |SOURCE_CATEGORIES|
    - regulatory:         scopes × |langs| × |REGULATORY_QUERY_TEMPLATES|
                          (regulatory category only)
    - facility_discovery: scopes × |langs| × |local| × |inpatient|
                          (commercial category only)
    - private_provider:   scopes × |langs| × |local| × |private|
                          (commercial category only)
    - addiction_specific: scopes × |langs| × |addiction| × |ADDICTION_FACILITY_TEMPLATES|
                          (commercial category only)

    Total scale ≈ O(scopes × langs × family_terms), not the legacy all-axis product
    O(scopes × langs × local × inpatient × private × addiction × categories).
    """

    seed: int
    regulatory: int
    facility_discovery: int
    private_provider: int
    addiction_specific: int

    @property
    def total(self) -> int:
        return (
            self.seed
            + self.regulatory
            + self.facility_discovery
            + self.private_provider
            + self.addiction_specific
        )


@dataclass(frozen=True)
class QueryGenerationResult:
    """Outcome of one generate_for_execution invocation.

    Count fields (do not overload):
    - generated_count: rows newly committed by this invocation
    - existing_count: requested deterministic jobs not inserted by this invocation
      because they already existed or another concurrent invocation won the conflict
    - total_count: currently persisted rows when this invocation returns
      (not the eventual unique final size after an interrupted stream)
    - expected_raw_count: deterministic raw family-workload estimate before dedup
    """

    status: GenerationStatus
    execution_id: str
    discovery_round: int
    plan_hash_snapshot: str | None
    generated_count: int = 0
    existing_count: int = 0
    total_count: int = 0
    expected_raw_count: int = 0
    blocked_code: str | None = None
    error_code: str | None = None
    message: str | None = None
    interrupt_reason: InterruptReason | None = None


class QueryGenerationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_display_text(value: str) -> str:
    """NFC + trim + single spaces; preserve approved casing for display query_text."""
    normalized = unicodedata.normalize("NFC", value or "")
    return " ".join(normalized.split())


def normalize_identity_text(value: str | None) -> str | None:
    """Fingerprint normalization: NFC, casefold, whitespace collapse; null stays null."""
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", value).casefold()
    collapsed = " ".join(normalized.split())
    return collapsed or None


def _compose_query_tokens(*tokens: str | None) -> str:
    composed = normalize_display_text(
        " ".join(token for token in tokens if token and str(token).strip())
    )
    if not composed:
        raise QueryGenerationError("empty_query", "Composed query text is empty.")
    return composed


def compose_expansion_query_text(
    *,
    private_paid: str,
    inpatient_residential: str,
    addiction_category: str,
    local_terminology: str,
    geographic_token: str | None,
    country_name: str,
) -> str:
    """Legacy all-axis composer retained for unit tests / audit of old Cartesian form."""
    return _compose_query_tokens(
        private_paid,
        inpatient_residential,
        addiction_category,
        local_terminology,
        geographic_token,
        country_name,
    )


def compose_regulatory_query_text(
    *,
    regulatory_template: str,
    geographic_token: str | None,
    country_name: str,
) -> str:
    return _compose_query_tokens(regulatory_template, geographic_token, country_name)


def compose_facility_query_text(
    *,
    inpatient_residential: str,
    local_terminology: str,
    geographic_token: str | None,
    country_name: str,
) -> str:
    return _compose_query_tokens(
        inpatient_residential, local_terminology, geographic_token, country_name
    )


def compose_private_provider_query_text(
    *,
    private_paid: str,
    local_terminology: str,
    geographic_token: str | None,
    country_name: str,
) -> str:
    return _compose_query_tokens(private_paid, local_terminology, geographic_token, country_name)


def compose_addiction_query_text(
    *,
    addiction_category: str,
    facility_template: str,
    geographic_token: str | None,
    country_name: str,
) -> str:
    return _compose_query_tokens(
        addiction_category, facility_template, geographic_token, country_name
    )


def resolve_language_code(profile: FrozenLanguageProfile) -> str:
    if profile.code and profile.code.strip():
        code = profile.code.strip()
        if len(code) > LANGUAGE_CODE_MAX:
            raise QueryGenerationError(
                "field_too_long",
                "Approved language code exceeds the database limit.",
            )
        return code
    mapped = LANGUAGE_CODE_BY_NAME.get(normalize_identity_text(profile.name) or "")
    if mapped:
        return mapped
    return "und"


def match_seed_language(
    seed_language: str, profiles: list[FrozenLanguageProfile]
) -> FrozenLanguageProfile:
    needle = normalize_identity_text(seed_language)
    if not needle:
        raise QueryGenerationError("seed_language_unmatched", "Seed language is blank.")
    matches: list[FrozenLanguageProfile] = []
    seen_ids: set[int] = set()
    for profile in profiles:
        matched = False
        if normalize_identity_text(profile.name) == needle:
            matched = True
        elif profile.code and normalize_identity_text(profile.code) == needle:
            matched = True
        if matched:
            identity = id(profile)
            if identity not in seen_ids:
                seen_ids.add(identity)
                matches.append(profile)
    if not matches:
        raise QueryGenerationError(
            "seed_language_unmatched",
            "Seed language does not match an approved language profile.",
        )
    if len(matches) > 1:
        raise QueryGenerationError(
            "seed_language_ambiguous",
            "Seed language matches more than one approved language profile.",
        )
    return matches[0]


def fingerprint_payload(
    *,
    plan_hash_snapshot: str,
    discovery_round: int,
    query_text: str,
    language_code: str,
    language_name: str,
    scope_level: ScopeLevel,
    region_name: str | None,
    important_city: str | None,
    source_category: str,
) -> dict[str, Any]:
    """Semantic job identity. Purpose and generation_source are intentionally excluded."""
    return {
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "plan_hash_snapshot": plan_hash_snapshot,
        "discovery_round": discovery_round,
        "query_text": normalize_identity_text(query_text),
        "language_code": normalize_identity_text(language_code),
        "language_name": normalize_identity_text(language_name),
        "scope_level": scope_level,
        "region_name": normalize_identity_text(region_name),
        "important_city": normalize_identity_text(important_city),
        "source_category": normalize_identity_text(source_category),
    }


def compute_query_job_fingerprint(**kwargs: Any) -> str:
    return sha256_hex(fingerprint_payload(**kwargs))


def select_authoritative_plan(execution: ScrapingExecution) -> PlanSelection:
    """Version-aware Step 3 plan selection with fail-closed hash verification."""
    status = (execution.clarification_status or "").strip()
    if status == ClarificationStatus.COMPLETED.value:
        if not execution.resolved_execution_plan_json or not execution.resolved_execution_plan_hash:
            raise QueryGenerationError(
                "resolved_plan_missing",
                "Completed clarification requires resolved plan JSON and hash.",
            )
        try:
            envelope = ResolvedExecutionPlanEnvelope.model_validate(
                execution.resolved_execution_plan_json
            )
        except Exception as exc:
            raise QueryGenerationError(
                "resolved_plan_malformed",
                "Resolved execution plan envelope is malformed.",
            ) from exc
        if envelope.source_execution_plan_hash != execution.execution_plan_hash:
            raise QueryGenerationError(
                "resolved_source_hash_mismatch",
                "Resolved envelope source hash does not match execution plan hash.",
            )
        recomputed = sha256_hex(envelope.model_dump(mode="json"))
        if recomputed != execution.resolved_execution_plan_hash:
            raise QueryGenerationError(
                "resolved_plan_hash_mismatch",
                "Resolved execution plan hash does not match stored provenance.",
            )
        plan = envelope.plan
        if not isinstance(plan, FrozenExecutionPlanV2):
            raise QueryGenerationError(
                "unsupported_plan_version",
                "Step 3 requires FrozenExecutionPlan schema version 2.",
            )
        if not supports_deterministic_query_generation(plan.schema_version):
            raise QueryGenerationError(
                "unsupported_plan_version",
                "Step 3 requires FrozenExecutionPlan schema version 2.",
            )
        return PlanSelection(
            plan=plan, plan_hash=execution.resolved_execution_plan_hash, provenance="resolved"
        )

    if status == ClarificationStatus.NOT_REQUIRED.value:
        if not execution.frozen_execution_plan_json or not execution.execution_plan_hash:
            raise QueryGenerationError(
                "frozen_plan_missing",
                "not_required clarification requires frozen plan JSON and hash.",
            )
        try:
            plan = parse_frozen_execution_plan(execution.frozen_execution_plan_json)
        except Exception as exc:
            raise QueryGenerationError(
                "frozen_plan_malformed",
                "Frozen execution plan is malformed.",
            ) from exc
        if not isinstance(plan, FrozenExecutionPlanV2):
            raise QueryGenerationError(
                "unsupported_plan_version",
                "Step 3 requires FrozenExecutionPlan schema version 2.",
            )
        if not supports_deterministic_query_generation(plan.schema_version):
            raise QueryGenerationError(
                "unsupported_plan_version",
                "Step 3 requires FrozenExecutionPlan schema version 2.",
            )
        recomputed = sha256_hex(plan.model_dump(mode="json"))
        if recomputed != execution.execution_plan_hash:
            raise QueryGenerationError(
                "frozen_plan_hash_mismatch",
                "Frozen execution plan hash does not match stored provenance.",
            )
        return PlanSelection(plan=plan, plan_hash=execution.execution_plan_hash, provenance="frozen")

    blocked = {
        ClarificationStatus.PENDING.value: "clarification_pending",
        ClarificationStatus.IN_PROGRESS.value: "clarification_in_progress",
        ClarificationStatus.REQUIRES_HUMAN_REVIEW.value: "clarification_requires_human_review",
        ClarificationStatus.FAILED.value: "clarification_failed",
    }.get(status, "clarification_not_ready")
    raise QueryGenerationError(blocked, f"Clarification status {status!r} blocks Step 3.")


def _require_bounded(value: str, *, field_name: str, max_length: int) -> str:
    if len(value) > max_length:
        raise QueryGenerationError(
            "field_too_long",
            f"{field_name} exceeds the database limit.",
        )
    return value


def _require_optional_bounded(
    value: str | None, *, field_name: str, max_length: int
) -> str | None:
    if value is None:
        return None
    return _require_bounded(value, field_name=field_name, max_length=max_length)


def validate_job_spec_bounds(spec: QueryJobSpec) -> None:
    """Fail closed against ORM column lengths. Never truncate."""
    _require_bounded(spec.country_code, field_name="country_code", max_length=COUNTRY_CODE_MAX)
    _require_bounded(spec.country_name, field_name="country_name", max_length=COUNTRY_NAME_MAX)
    _require_optional_bounded(
        spec.region_name, field_name="region_name", max_length=REGION_NAME_MAX
    )
    _require_optional_bounded(
        spec.important_city, field_name="important_city", max_length=IMPORTANT_CITY_MAX
    )
    _require_bounded(spec.language_code, field_name="language_code", max_length=LANGUAGE_CODE_MAX)
    _require_bounded(spec.language_name, field_name="language_name", max_length=LANGUAGE_NAME_MAX)
    _require_bounded(
        spec.source_category, field_name="source_category", max_length=SOURCE_CATEGORY_MAX
    )
    _require_bounded(spec.purpose, field_name="purpose", max_length=PURPOSE_MAX)
    if not spec.query_text.strip():
        raise QueryGenerationError("empty_query", "Query text is empty.")


def _validate_plan_axes(plan: FrozenExecutionPlanV2) -> tuple[int, int, int, int, int, int, int, int]:
    """Return (seeds, regions, cities, langs, local, inpatient, private, addiction)."""
    seed_n = len(plan.query_seed_plan.seeds)
    region_n = len(plan.regions)
    city_n = len(plan.important_cities)
    lang_n = len(plan.language_profiles)
    local_n = len(plan.local_terminology)
    inpatient_n = len(plan.inpatient_residential_terminology)
    private_n = len(plan.private_paid_terminology)
    addiction_n = len(plan.addiction_categories)

    for label, count in (
        ("language_profiles", lang_n),
        ("local_terminology", local_n),
        ("inpatient_residential_terminology", inpatient_n),
        ("private_paid_terminology", private_n),
        ("addiction_categories", addiction_n),
    ):
        if count < 1:
            raise QueryGenerationError(
                "malformed_axes",
                f"Blueprint axis '{label}' must contain at least one value.",
            )
    if seed_n < 0 or region_n < 0 or city_n < 0:
        raise QueryGenerationError("malformed_axes", "Blueprint axis lengths are invalid.")
    if not REGULATORY_QUERY_TEMPLATES or not ADDICTION_FACILITY_TEMPLATES:
        raise QueryGenerationError("malformed_axes", "Product query templates are empty.")
    return seed_n, region_n, city_n, lang_n, local_n, inpatient_n, private_n, addiction_n


def estimate_query_family_counts(plan: FrozenExecutionPlanV2) -> QueryFamilyCounts:
    """Integer-only raw family workload before semantic fingerprint collapse.

    See QueryFamilyCounts docstring for exact per-family formulas.
    Special/high-value blueprint queries are preserved via the seed family
    (query_matrix → query_seed_plan); follow-up gap jobs are out of scope here.
    """
    seed_n, region_n, city_n, lang_n, local_n, inpatient_n, private_n, addiction_n = (
        _validate_plan_axes(plan)
    )
    scopes = 1 + region_n + city_n
    return QueryFamilyCounts(
        seed=seed_n * len(SOURCE_CATEGORIES),
        regulatory=scopes * lang_n * len(REGULATORY_QUERY_TEMPLATES),
        facility_discovery=scopes * lang_n * local_n * inpatient_n,
        private_provider=scopes * lang_n * local_n * private_n,
        addiction_specific=scopes * lang_n * addiction_n * len(ADDICTION_FACILITY_TEMPLATES),
    )


def estimate_raw_combination_count(plan: FrozenExecutionPlanV2) -> int:
    """Raw family-workload size before fingerprint collapse (sum of family formulas)."""
    return estimate_query_family_counts(plan).total


def estimate_legacy_cartesian_combination_count(plan: FrozenExecutionPlanV2) -> int:
    """Audit helper: old all-axis Cartesian size (not used for expected_raw_count).

    legacy = seeds×|categories|
           + |categories|×scopes×langs×local×inpatient×private×addiction
    """
    seed_n, region_n, city_n, lang_n, local_n, inpatient_n, private_n, addiction_n = (
        _validate_plan_axes(plan)
    )
    scopes = 1 + region_n + city_n
    category_n = len(SOURCE_CATEGORIES)
    return seed_n * category_n + (
        category_n * scopes * lang_n * local_n * inpatient_n * private_n * addiction_n
    )


def iter_query_job_specs(
    plan: FrozenExecutionPlanV2,
    *,
    plan_hash: str,
    discovery_round: int,
    fingerprint_seen: set[str] | None = None,
    ordinal_start: int = 0,
    fingerprint_lookup_counter: list[int] | None = None,
) -> Iterator[QueryJobSpec]:
    """Lazy deterministic generator. Yields unique jobs in seed-then-family order.

    Order: seeds → regulatory → facility_discovery → private_provider → addiction_specific.
    Semantic collapse uses fingerprint set membership (O(1) average), not pairwise
    comparison. First writer wins so seeds beat equivalent family expansions.
    """
    if discovery_round < 1:
        raise QueryGenerationError("invalid_discovery_round", "discovery_round must be >= 1.")
    # Force axis validation up front (also used for expected-count metadata).
    estimate_raw_combination_count(plan)

    seen = fingerprint_seen if fingerprint_seen is not None else set()
    ordinal = ordinal_start

    def _lookup(fingerprint: str) -> bool:
        if fingerprint_lookup_counter is not None:
            fingerprint_lookup_counter[0] += 1
        return fingerprint in seen

    def _accept(spec: QueryJobSpec) -> QueryJobSpec | None:
        nonlocal ordinal
        validate_job_spec_bounds(spec)
        if _lookup(spec.query_job_fingerprint):
            return None
        seen.add(spec.query_job_fingerprint)
        accepted = QueryJobSpec(**{**spec.__dict__, "generation_ordinal": ordinal})
        ordinal += 1
        return accepted

    def _emit(
        *,
        query_text: str,
        profile: FrozenLanguageProfile,
        language_code: str,
        source_category: str,
        purpose: str,
        priority: int,
        scope_level: ScopeLevel,
        region_name: str | None,
        important_city: str | None,
        metadata_json: dict[str, Any],
    ) -> Iterator[QueryJobSpec]:
        fingerprint = compute_query_job_fingerprint(
            plan_hash_snapshot=plan_hash,
            discovery_round=discovery_round,
            query_text=query_text,
            language_code=language_code,
            language_name=profile.name,
            scope_level=scope_level,
            region_name=region_name,
            important_city=important_city,
            source_category=source_category,
        )
        accepted = _accept(
            QueryJobSpec(
                query_text=query_text,
                language_code=language_code,
                language_name=profile.name,
                script=profile.script,
                source_category=source_category,
                purpose=purpose,
                priority=priority,
                discovery_round=discovery_round,
                generation_ordinal=0,
                scope_level=scope_level,
                region_name=region_name,
                important_city=important_city,
                country_code=plan.country.country_code,
                country_name=plan.country.country_name,
                plan_hash_snapshot=plan_hash,
                query_job_fingerprint=fingerprint,
                metadata_json=metadata_json,
            )
        )
        if accepted is not None:
            yield accepted

    # --- Family A / F: approved seeds (query_matrix) with both source categories ---
    for seed_index, seed in enumerate(plan.query_seed_plan.seeds):
        profile = match_seed_language(seed.language, list(plan.language_profiles))
        language_code = resolve_language_code(profile)
        query_text = normalize_display_text(seed.query)
        if not query_text:
            raise QueryGenerationError("empty_query", "Seed query text is empty.")
        for source_category in SOURCE_CATEGORIES:
            yield from _emit(
                query_text=query_text,
                profile=profile,
                language_code=language_code,
                source_category=source_category,
                purpose=PURPOSE_SEED,
                priority=PRIORITY_SEED,
                scope_level="countrywide",
                region_name=None,
                important_city=None,
                metadata_json={
                    "generation_source": "seed",
                    "query_family": QUERY_FAMILY_SEED,
                    "seed_index": seed_index,
                    "seed_purpose": seed.purpose,
                    "script": profile.script,
                    "source_category": source_category,
                },
            )

    scopes: list[tuple[ScopeLevel, str | None, str | None]] = [("countrywide", None, None)]
    for region in plan.regions:
        scopes.append(("region", region, None))
    for city in plan.important_cities:
        scopes.append(("city", city.region_name, city.name))

    # --- Family B: regulatory (geography × language × regulatory template) ---
    for scope_level, region_name, important_city in scopes:
        priority = PRIORITY_BY_CATEGORY_SCOPE[("regulatory", scope_level)]
        geo_token = important_city if scope_level == "city" else region_name
        for profile in plan.language_profiles:
            language_code = resolve_language_code(profile)
            for regulatory_template in REGULATORY_QUERY_TEMPLATES:
                query_text = compose_regulatory_query_text(
                    regulatory_template=regulatory_template,
                    geographic_token=geo_token,
                    country_name=plan.country.country_name,
                )
                yield from _emit(
                    query_text=query_text,
                    profile=profile,
                    language_code=language_code,
                    source_category="regulatory",
                    purpose=PURPOSE_REGULATORY,
                    priority=priority,
                    scope_level=scope_level,
                    region_name=region_name,
                    important_city=important_city,
                    metadata_json={
                        "generation_source": "family_expansion",
                        "query_family": QUERY_FAMILY_REGULATORY,
                        "script": profile.script,
                        "source_category": "regulatory",
                        "axes": {
                            "regulatory_template": regulatory_template,
                            "scope_level": scope_level,
                            "region_name": region_name,
                            "important_city": important_city,
                        },
                    },
                )

    # --- Family C: general facility discovery (geography × language × local × inpatient) ---
    for scope_level, region_name, important_city in scopes:
        priority = PRIORITY_BY_CATEGORY_SCOPE[("commercial", scope_level)]
        geo_token = important_city if scope_level == "city" else region_name
        for profile in plan.language_profiles:
            language_code = resolve_language_code(profile)
            for local_term in plan.local_terminology:
                for inpatient in plan.inpatient_residential_terminology:
                    query_text = compose_facility_query_text(
                        inpatient_residential=inpatient,
                        local_terminology=local_term,
                        geographic_token=geo_token,
                        country_name=plan.country.country_name,
                    )
                    yield from _emit(
                        query_text=query_text,
                        profile=profile,
                        language_code=language_code,
                        source_category="commercial",
                        purpose=PURPOSE_COMMERCIAL,
                        priority=priority,
                        scope_level=scope_level,
                        region_name=region_name,
                        important_city=important_city,
                        metadata_json={
                            "generation_source": "family_expansion",
                            "query_family": QUERY_FAMILY_FACILITY,
                            "script": profile.script,
                            "source_category": "commercial",
                            "axes": {
                                "local_terminology": local_term,
                                "inpatient_residential_terminology": inpatient,
                                "scope_level": scope_level,
                                "region_name": region_name,
                                "important_city": important_city,
                            },
                        },
                    )

    # --- Family D: private provider (geography × language × local × private) ---
    for scope_level, region_name, important_city in scopes:
        priority = PRIORITY_BY_CATEGORY_SCOPE[("commercial", scope_level)]
        geo_token = important_city if scope_level == "city" else region_name
        for profile in plan.language_profiles:
            language_code = resolve_language_code(profile)
            for local_term in plan.local_terminology:
                for private_paid in plan.private_paid_terminology:
                    query_text = compose_private_provider_query_text(
                        private_paid=private_paid,
                        local_terminology=local_term,
                        geographic_token=geo_token,
                        country_name=plan.country.country_name,
                    )
                    yield from _emit(
                        query_text=query_text,
                        profile=profile,
                        language_code=language_code,
                        source_category="commercial",
                        purpose=PURPOSE_COMMERCIAL,
                        priority=priority,
                        scope_level=scope_level,
                        region_name=region_name,
                        important_city=important_city,
                        metadata_json={
                            "generation_source": "family_expansion",
                            "query_family": QUERY_FAMILY_PRIVATE,
                            "script": profile.script,
                            "source_category": "commercial",
                            "axes": {
                                "local_terminology": local_term,
                                "private_paid_terminology": private_paid,
                                "scope_level": scope_level,
                                "region_name": region_name,
                                "important_city": important_city,
                            },
                        },
                    )

    # --- Family E: addiction-specific (geography × language × addiction × facility template) ---
    for scope_level, region_name, important_city in scopes:
        priority = PRIORITY_BY_CATEGORY_SCOPE[("commercial", scope_level)]
        geo_token = important_city if scope_level == "city" else region_name
        for profile in plan.language_profiles:
            language_code = resolve_language_code(profile)
            for addiction in plan.addiction_categories:
                for facility_template in ADDICTION_FACILITY_TEMPLATES:
                    query_text = compose_addiction_query_text(
                        addiction_category=addiction,
                        facility_template=facility_template,
                        geographic_token=geo_token,
                        country_name=plan.country.country_name,
                    )
                    yield from _emit(
                        query_text=query_text,
                        profile=profile,
                        language_code=language_code,
                        source_category="commercial",
                        purpose=PURPOSE_COMMERCIAL,
                        priority=priority,
                        scope_level=scope_level,
                        region_name=region_name,
                        important_city=important_city,
                        metadata_json={
                            "generation_source": "family_expansion",
                            "query_family": QUERY_FAMILY_ADDICTION,
                            "script": profile.script,
                            "source_category": "commercial",
                            "axes": {
                                "addiction_category": addiction,
                                "facility_template": facility_template,
                                "scope_level": scope_level,
                                "region_name": region_name,
                                "important_city": important_city,
                            },
                        },
                    )


def generate_query_job_specs(
    plan: FrozenExecutionPlanV2,
    *,
    plan_hash: str,
    discovery_round: int,
) -> list[QueryJobSpec]:
    """Materializing helper for ordering unit tests. Persistence uses iter_query_job_specs."""
    return list(
        iter_query_job_specs(plan, plan_hash=plan_hash, discovery_round=discovery_round)
    )


class QueryGenerationService:
    """Select plan → stream specs → persist missing pending jobs idempotently."""

    async def generate_for_execution(
        self,
        db: AsyncSession,
        execution: ScrapingExecution,
        *,
        discovery_round: int = 1,
        check_interrupt: Callable[[AsyncSession, ScrapingExecution], Awaitable[bool]] | None = None,
        fingerprint_lookup_counter: list[int] | None = None,
    ) -> QueryGenerationResult:
        execution_id = execution.id
        if not supports_deterministic_query_generation(execution.execution_plan_schema_version):
            return QueryGenerationResult(
                status="blocked",
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash_snapshot=None,
                blocked_code="unsupported_plan_version",
                message="Step 3 requires FrozenExecutionPlan schema version 2.",
            )

        try:
            selection = select_authoritative_plan(execution)
        except QueryGenerationError as exc:
            status: GenerationStatus = (
                "blocked" if exc.code.startswith("clarification_") else "error"
            )
            return QueryGenerationResult(
                status=status,
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash_snapshot=None,
                blocked_code=exc.code if status == "blocked" else None,
                error_code=exc.code if status == "error" else None,
                message=exc.message,
            )

        try:
            expected_raw = estimate_raw_combination_count(selection.plan)
        except QueryGenerationError as exc:
            return QueryGenerationResult(
                status="error",
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash_snapshot=selection.plan_hash,
                error_code=exc.code,
                message=exc.message,
            )

        # Do not hold FOR UPDATE across CPU/spec work or insert batches.
        loaded = await db.execute(
            select(ScrapingExecution).where(ScrapingExecution.id == execution_id)
        )
        locked_execution = loaded.scalar_one_or_none()
        if locked_execution is None:
            return QueryGenerationResult(
                status="error",
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash_snapshot=None,
                error_code="execution_not_found",
                message="Execution disappeared during query generation.",
                expected_raw_count=expected_raw,
            )

        if check_interrupt is not None and await check_interrupt(db, locked_execution):
            return _interrupted_result(
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash=selection.plan_hash,
                execution=locked_execution,
                expected_raw_count=expected_raw,
            )

        # Minimal restart state: fingerprints + ordinals only (not full ORM rows/text).
        contamination = await db.execute(
            select(func.count())
            .select_from(ScrapingSourceDiscoveryQuery)
            .where(
                ScrapingSourceDiscoveryQuery.organization_id == locked_execution.organization_id,
                ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                ScrapingSourceDiscoveryQuery.discovery_round == discovery_round,
                or_(
                    ScrapingSourceDiscoveryQuery.query_job_fingerprint.is_(None),
                    ScrapingSourceDiscoveryQuery.plan_hash_snapshot.is_(None),
                ),
            )
        )
        if int(contamination.scalar_one() or 0) > 0:
            return QueryGenerationResult(
                status="error",
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash_snapshot=selection.plan_hash,
                error_code="legacy_contamination",
                message=(
                    "Existing query rows without fingerprint/plan hash cannot mix "
                    "with plan-backed Step 3 jobs."
                ),
                expected_raw_count=expected_raw,
            )

        hash_conflict = await db.execute(
            select(func.count())
            .select_from(ScrapingSourceDiscoveryQuery)
            .where(
                ScrapingSourceDiscoveryQuery.organization_id == locked_execution.organization_id,
                ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                ScrapingSourceDiscoveryQuery.discovery_round == discovery_round,
                ScrapingSourceDiscoveryQuery.plan_hash_snapshot.is_not(None),
                ScrapingSourceDiscoveryQuery.plan_hash_snapshot != selection.plan_hash,
            )
        )
        if int(hash_conflict.scalar_one() or 0) > 0:
            return QueryGenerationResult(
                status="error",
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash_snapshot=selection.plan_hash,
                error_code="plan_hash_conflict",
                message="Existing query jobs belong to a different plan hash for this round.",
                expected_raw_count=expected_raw,
            )

        existing_fp_rows = (
            await db.execute(
                select(
                    ScrapingSourceDiscoveryQuery.query_job_fingerprint,
                    ScrapingSourceDiscoveryQuery.generation_ordinal,
                ).where(
                    ScrapingSourceDiscoveryQuery.organization_id
                    == locked_execution.organization_id,
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                    ScrapingSourceDiscoveryQuery.discovery_round == discovery_round,
                    ScrapingSourceDiscoveryQuery.query_job_fingerprint.is_not(None),
                )
            )
        ).all()
        existing_fps = {
            str(row.query_job_fingerprint)
            for row in existing_fp_rows
            if row.query_job_fingerprint
        }
        existing_count = len(existing_fps)
        ordinal_start = 0
        if existing_fp_rows:
            ordinal_start = max(int(row.generation_ordinal or 0) for row in existing_fp_rows) + 1

        await db.refresh(locked_execution)
        if check_interrupt is not None and await check_interrupt(db, locked_execution):
            return _interrupted_result(
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash=selection.plan_hash,
                execution=locked_execution,
                existing_count=existing_count,
                total_count=existing_count,
                expected_raw_count=expected_raw,
            )

        # Seed-win collapse + skip already-persisted fingerprints via shared seen set.
        seen: set[str] = set(existing_fps)
        stream = iter_query_job_specs(
            selection.plan,
            plan_hash=selection.plan_hash,
            discovery_round=discovery_round,
            fingerprint_seen=seen,
            ordinal_start=ordinal_start,
            fingerprint_lookup_counter=fingerprint_lookup_counter,
        )

        generated = 0
        conflict_existing = 0
        unique_intended = existing_count
        batch: list[QueryJobSpec] = []
        batch_commits = 0

        async def _flush_batch() -> QueryGenerationResult | None:
            nonlocal generated, conflict_existing, batch_commits, batch
            if not batch:
                return None
            if check_interrupt is not None:
                await db.refresh(locked_execution)
                if await check_interrupt(db, locked_execution):
                    return _interrupted_result(
                        execution_id=execution_id,
                        discovery_round=discovery_round,
                        plan_hash=selection.plan_hash,
                        execution=locked_execution,
                        generated_count=generated,
                        existing_count=existing_count,
                        total_count=unique_intended,
                        expected_raw_count=expected_raw,
                    )
            try:
                async with db.begin_nested():
                    for spec in batch:
                        db.add(_row_from_spec(locked_execution, spec))
                    await db.flush()
                generated += len(batch)
            except IntegrityError:
                for spec in batch:
                    try:
                        async with db.begin_nested():
                            db.add(_row_from_spec(locked_execution, spec))
                            await db.flush()
                        generated += 1
                    except IntegrityError as exc:
                        # Do not classify arbitrary insert failures as existing rows.
                        # The savepoint has rolled back, so verify that this exact
                        # deterministic fingerprint now exists before continuing.
                        conflict_winner = await db.scalar(
                            select(ScrapingSourceDiscoveryQuery.id).where(
                                ScrapingSourceDiscoveryQuery.organization_id
                                == locked_execution.organization_id,
                                ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                                ScrapingSourceDiscoveryQuery.discovery_round
                                == discovery_round,
                                ScrapingSourceDiscoveryQuery.query_job_fingerprint
                                == spec.query_job_fingerprint,
                            )
                        )
                        if conflict_winner is None:
                            raise exc
                        conflict_existing += 1
            await db.commit()
            batch_commits += 1
            batch = []
            await db.refresh(locked_execution)
            if check_interrupt is not None and await check_interrupt(db, locked_execution):
                return _interrupted_result(
                    execution_id=execution_id,
                    discovery_round=discovery_round,
                    plan_hash=selection.plan_hash,
                    execution=locked_execution,
                    generated_count=generated,
                    existing_count=existing_count,
                    total_count=unique_intended,
                    expected_raw_count=expected_raw,
                )
            return None

        try:
            for spec in stream:
                # Specs yielded from iter are already unique vs seen (incl. existing).
                unique_intended += 1
                batch.append(spec)
                if len(batch) >= INSERT_BATCH_SIZE:
                    interrupted = await _flush_batch()
                    if interrupted is not None:
                        return interrupted
            interrupted = await _flush_batch()
            if interrupted is not None:
                return interrupted
        except QueryGenerationError as exc:
            return QueryGenerationResult(
                status="error",
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash_snapshot=selection.plan_hash,
                error_code=exc.code,
                message=exc.message,
                generated_count=generated,
                existing_count=existing_count,
                total_count=unique_intended,
                expected_raw_count=expected_raw,
            )

        return QueryGenerationResult(
            status="ok",
            execution_id=execution_id,
            discovery_round=discovery_round,
            plan_hash_snapshot=selection.plan_hash,
            generated_count=generated,
            existing_count=existing_count + conflict_existing,
            total_count=unique_intended,
            expected_raw_count=expected_raw,
        )


def _interrupted_result(
    *,
    execution_id: str,
    discovery_round: int,
    plan_hash: str | None,
    execution: ScrapingExecution,
    generated_count: int = 0,
    existing_count: int = 0,
    total_count: int = 0,
    expected_raw_count: int = 0,
) -> QueryGenerationResult:
    reason: InterruptReason = (
        "cancelled"
        if execution.status
        in {
            ScrapingExecutionStatus.CANCEL_REQUESTED,
            ScrapingExecutionStatus.CANCELLED,
        }
        or (
            execution.cancel_requested_at is not None
            and (
                execution.pause_requested_at is None
                or execution.cancel_requested_at >= execution.pause_requested_at
            )
            and execution.status
            in {
                ScrapingExecutionStatus.PAUSE_REQUESTED,
                ScrapingExecutionStatus.PAUSED,
                ScrapingExecutionStatus.CANCEL_REQUESTED,
                ScrapingExecutionStatus.CANCELLED,
            }
        )
        else "paused"
    )
    return QueryGenerationResult(
        status="interrupted",
        execution_id=execution_id,
        discovery_round=discovery_round,
        plan_hash_snapshot=plan_hash,
        generated_count=generated_count,
        existing_count=existing_count,
        total_count=total_count,
        expected_raw_count=expected_raw_count,
        interrupt_reason=reason,
        message="Query generation stopped at a safe batch boundary.",
    )


def _row_from_spec(
    execution: ScrapingExecution, spec: QueryJobSpec
) -> ScrapingSourceDiscoveryQuery:
    validate_job_spec_bounds(spec)
    return ScrapingSourceDiscoveryQuery(
        organization_id=execution.organization_id,
        execution_id=execution.id,
        coverage_cell_id=None,
        task_id=None,
        country_code=spec.country_code,
        country_name=spec.country_name,
        region_code=None,
        region_name=spec.region_name,
        language_code=spec.language_code,
        language_name=spec.language_name,
        source_category=spec.source_category,
        query_text=spec.query_text,
        provider=None,
        status=SourceDiscoveryQueryStatus.PENDING,
        requested_at=None,
        result_count=0,
        metadata_json=spec.metadata_json,
        purpose=spec.purpose,
        priority=spec.priority,
        discovery_round=spec.discovery_round,
        generation_ordinal=spec.generation_ordinal,
        query_job_fingerprint=spec.query_job_fingerprint,
        plan_hash_snapshot=spec.plan_hash_snapshot,
        scope_level=spec.scope_level,
        important_city=spec.important_city,
    )


def _batches(items: list[QueryJobSpec], size: int) -> Iterator[list[QueryJobSpec]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def public_query_metadata(row: ScrapingSourceDiscoveryQuery) -> dict[str, Any]:
    """Public API metadata: empty for plan-backed jobs; legacy rows keep prior payload."""
    if row.query_job_fingerprint:
        return {}
    raw = row.metadata_json or {}
    return dict(raw) if isinstance(raw, dict) else {}


query_generation_service = QueryGenerationService()
