"""Persistent deterministic Step 3 query-job generation (no providers or LLMs)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScrapingExecution, ScrapingSourceDiscoveryQuery, SourceDiscoveryQueryStatus
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

GenerationStatus = Literal["ok", "blocked", "error"]
ScopeLevel = Literal["countrywide", "region", "city"]


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
class QueryGenerationResult:
    status: GenerationStatus
    execution_id: str
    discovery_round: int
    plan_hash_snapshot: str | None
    generated_count: int = 0
    existing_count: int = 0
    total_count: int = 0
    blocked_code: str | None = None
    error_code: str | None = None
    message: str | None = None


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


def compose_expansion_query_text(
    *,
    private_paid: str,
    inpatient_residential: str,
    addiction_category: str,
    local_terminology: str,
    geographic_token: str | None,
    country_name: str,
) -> str:
    tokens = [
        private_paid,
        inpatient_residential,
        addiction_category,
        local_terminology,
        geographic_token or "",
        country_name,
    ]
    composed = normalize_display_text(" ".join(token for token in tokens if token and token.strip()))
    if not composed:
        raise QueryGenerationError("empty_query", "Composed query text is empty.")
    return composed


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


def generate_query_job_specs(
    plan: FrozenExecutionPlanV2,
    *,
    plan_hash: str,
    discovery_round: int,
) -> list[QueryJobSpec]:
    """Pure deterministic generator. Preserves approved list order. No I/O."""
    if discovery_round < 1:
        raise QueryGenerationError("invalid_discovery_round", "discovery_round must be >= 1.")

    ordered: dict[str, QueryJobSpec] = {}
    ordinal = 0

    def _emit(spec: QueryJobSpec) -> None:
        nonlocal ordinal
        validate_job_spec_bounds(spec)
        # First writer wins — seeds are emitted before expansions.
        if spec.query_job_fingerprint in ordered:
            return
        ordered[spec.query_job_fingerprint] = QueryJobSpec(
            **{**spec.__dict__, "generation_ordinal": ordinal}
        )
        ordinal += 1

    # 1) Seed jobs first, then every source category.
    for seed_index, seed in enumerate(plan.query_seed_plan.seeds):
        profile = match_seed_language(seed.language, plan.language_profiles)
        language_code = resolve_language_code(profile)
        query_text = normalize_display_text(seed.query)
        if not query_text:
            raise QueryGenerationError("empty_query", "Seed query text is empty.")
        for source_category in SOURCE_CATEGORIES:
            fingerprint = compute_query_job_fingerprint(
                plan_hash_snapshot=plan_hash,
                discovery_round=discovery_round,
                query_text=query_text,
                language_code=language_code,
                language_name=profile.name,
                scope_level="countrywide",
                region_name=None,
                important_city=None,
                source_category=source_category,
            )
            _emit(
                QueryJobSpec(
                    query_text=query_text,
                    language_code=language_code,
                    language_name=profile.name,
                    script=profile.script,
                    source_category=source_category,
                    purpose=PURPOSE_SEED,
                    priority=PRIORITY_SEED,
                    discovery_round=discovery_round,
                    generation_ordinal=0,
                    scope_level="countrywide",
                    region_name=None,
                    important_city=None,
                    country_code=plan.country.country_code,
                    country_name=plan.country.country_name,
                    plan_hash_snapshot=plan_hash,
                    query_job_fingerprint=fingerprint,
                    metadata_json={
                        "generation_source": "seed",
                        "seed_index": seed_index,
                        "seed_purpose": seed.purpose,
                        "script": profile.script,
                        "source_category": source_category,
                    },
                )
            )

    scopes: list[tuple[ScopeLevel, str | None, str | None]] = [("countrywide", None, None)]
    for region in plan.regions:
        scopes.append(("region", region, None))
    for city in plan.important_cities:
        scopes.append(("city", city.region_name, city.name))

    for source_category in SOURCE_CATEGORIES:
        purpose = (
            PURPOSE_REGULATORY if source_category == "regulatory" else PURPOSE_COMMERCIAL
        )
        for scope_level, region_name, important_city in scopes:
            priority = PRIORITY_BY_CATEGORY_SCOPE[(source_category, scope_level)]
            geo_token = important_city if scope_level == "city" else region_name
            for profile in plan.language_profiles:
                language_code = resolve_language_code(profile)
                for local_term in plan.local_terminology:
                    for inpatient in plan.inpatient_residential_terminology:
                        for private_paid in plan.private_paid_terminology:
                            for addiction in plan.addiction_categories:
                                query_text = compose_expansion_query_text(
                                    private_paid=private_paid,
                                    inpatient_residential=inpatient,
                                    addiction_category=addiction,
                                    local_terminology=local_term,
                                    geographic_token=geo_token,
                                    country_name=plan.country.country_name,
                                )
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
                                _emit(
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
                                        metadata_json={
                                            "generation_source": "expansion",
                                            "script": profile.script,
                                            "source_category": source_category,
                                            "axes": {
                                                "private_paid_terminology": private_paid,
                                                "inpatient_residential_terminology": inpatient,
                                                "addiction_category": addiction,
                                                "local_terminology": local_term,
                                                "scope_level": scope_level,
                                                "region_name": region_name,
                                                "important_city": important_city,
                                            },
                                        },
                                    )
                                )

    return list(ordered.values())


class QueryGenerationService:
    """Select plan → generate specs → persist missing pending jobs idempotently."""

    async def generate_for_execution(
        self,
        db: AsyncSession,
        execution: ScrapingExecution,
        *,
        discovery_round: int = 1,
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

        locked = await db.execute(
            select(ScrapingExecution)
            .where(ScrapingExecution.id == execution_id)
            .with_for_update()
        )
        locked_execution = locked.scalar_one_or_none()
        if locked_execution is None:
            return QueryGenerationResult(
                status="error",
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash_snapshot=None,
                error_code="execution_not_found",
                message="Execution disappeared during query generation.",
            )

        existing_rows = (
            await db.execute(
                select(ScrapingSourceDiscoveryQuery).where(
                    ScrapingSourceDiscoveryQuery.organization_id == locked_execution.organization_id,
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                    ScrapingSourceDiscoveryQuery.discovery_round == discovery_round,
                )
            )
        ).scalars().all()

        for row in existing_rows:
            if row.query_job_fingerprint is None or row.plan_hash_snapshot is None:
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
                )
            if row.plan_hash_snapshot != selection.plan_hash:
                return QueryGenerationResult(
                    status="error",
                    execution_id=execution_id,
                    discovery_round=discovery_round,
                    plan_hash_snapshot=selection.plan_hash,
                    error_code="plan_hash_conflict",
                    message="Existing query jobs belong to a different plan hash for this round.",
                )

        try:
            specs = generate_query_job_specs(
                selection.plan,
                plan_hash=selection.plan_hash,
                discovery_round=discovery_round,
            )
        except QueryGenerationError as exc:
            return QueryGenerationResult(
                status="error",
                execution_id=execution_id,
                discovery_round=discovery_round,
                plan_hash_snapshot=selection.plan_hash,
                error_code=exc.code,
                message=exc.message,
            )

        existing_by_fp = {
            row.query_job_fingerprint: row
            for row in existing_rows
            if row.query_job_fingerprint
        }
        to_insert = [spec for spec in specs if spec.query_job_fingerprint not in existing_by_fp]
        generated = 0
        for batch in _batches(to_insert, INSERT_BATCH_SIZE):
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
                    except IntegrityError:
                        continue

        total = len(specs)
        existing_count = total - generated
        return QueryGenerationResult(
            status="ok",
            execution_id=execution_id,
            discovery_round=discovery_round,
            plan_hash_snapshot=selection.plan_hash,
            generated_count=generated,
            existing_count=existing_count,
            total_count=total,
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
