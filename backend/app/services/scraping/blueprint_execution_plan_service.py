"""Pure deterministic compiler: approved structured blueprint → FrozenExecutionPlan."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ValidationError
from app.schemas.api import (
    BlueprintCitation,
    CountryMaximumCoverageStructuredBlueprint,
)
from app.schemas.scraping_execution_plan import (
    CONTACT_PRODUCT_RULES,
    COUNTRY_CONTAINMENT_PRODUCT_RULES,
    DEDUPLICATION_PRODUCT_RULES,
    EXECUTION_PLAN_SCHEMA_VERSION,
    QUALIFICATION_OVERALL_RULE,
    REQUIRED_QUALIFICATION_CRITERIA,
    FrozenCompletionPolicy,
    FrozenContactPolicy,
    FrozenCountryContainmentPolicy,
    FrozenCrawlPolicy,
    FrozenDeduplicationPolicy,
    FrozenEvidencePolicy,
    FrozenExecutionPlan,
    FrozenPlanCountry,
    FrozenPlanScope,
    FrozenQualificationCriterion,
    FrozenQualificationPolicy,
    FrozenQuerySeed,
    FrozenQuerySeedPlan,
    FrozenSourceEntry,
    FrozenSourcePlan,
    QualificationStatus,
)
from app.services.scraping.blueprint_structured_contract import (
    normalize_structured_blueprint_payload,
)
from app.services.scraping.countries import Country, resolve_country


@dataclass(frozen=True)
class MissionCountryIdentity:
    country_code: str
    country_name: str
    country_iso3: str | None = None
    continent: str | None = None


@dataclass(frozen=True)
class CompiledExecutionPlanResult:
    blueprint_snapshot_json: dict[str, Any]
    frozen_execution_plan: FrozenExecutionPlan
    frozen_execution_plan_json: dict[str, Any]
    execution_plan_schema_version: str
    execution_plan_hash: str
    source_blueprint_hash: str


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic UTF-8 JSON bytes with sorted keys and stable separators."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        item = raw.strip()
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _source_identity(entry: FrozenSourceEntry) -> tuple[Any, ...]:
    return (
        entry.category,
        (entry.url or "").strip().casefold(),
        (entry.title or "").strip().casefold(),
        (entry.source_type or "").strip().casefold(),
        (entry.notes or "").strip().casefold(),
    )


def _dedupe_sources(entries: list[FrozenSourceEntry]) -> list[FrozenSourceEntry]:
    seen: set[tuple[Any, ...]] = set()
    result: list[FrozenSourceEntry] = []
    for entry in entries:
        key = _source_identity(entry)
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _query_identity(seed: FrozenQuerySeed) -> tuple[str, str, str]:
    return (
        seed.query.strip().casefold(),
        seed.language.strip().casefold(),
        seed.purpose.strip().casefold(),
    )


def _dedupe_query_seeds(seeds: list[FrozenQuerySeed]) -> list[FrozenQuerySeed]:
    seen: set[tuple[str, str, str]] = set()
    result: list[FrozenQuerySeed] = []
    for seed in seeds:
        key = _query_identity(seed)
        if key in seen:
            continue
        seen.add(key)
        result.append(seed)
    return result


def _citation_is_meaningful(citation: BlueprintCitation) -> bool:
    return any(
        value and str(value).strip()
        for value in (citation.url, citation.title, citation.source_type, citation.notes)
    )


def _to_source_entry(category: str, citation: BlueprintCitation) -> FrozenSourceEntry:
    return FrozenSourceEntry(
        category=category,  # type: ignore[arg-type]
        url=citation.url,
        title=citation.title,
        source_type=citation.source_type,
        notes=citation.notes,
    )


def _terminology_from_dossier(validated: CountryMaximumCoverageStructuredBlueprint) -> list[str]:
    extras = validated.country_dossier.model_extra or {}
    terms: list[str] = []
    for key, value in extras.items():
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    terms.append(item.strip())
        # Ignore non-string extras; never invent terminology.
        _ = key
    return _dedupe_strings(terms)


def _resolve_mission_country(mission_country: MissionCountryIdentity) -> Country:
    resolved = resolve_country(mission_country.country_code or mission_country.country_name)
    if mission_country.country_iso3 and mission_country.country_iso3.upper() != resolved.iso3:
        raise ValidationError(
            "Mission country ISO-3 does not match the resolved country identity."
        )
    if (
        mission_country.country_code
        and mission_country.country_code.upper() != resolved.iso2
    ):
        raise ValidationError(
            "Mission country ISO-2 does not match the resolved country identity."
        )
    return resolved


def _assert_countries_agree(
    *,
    mission_country: MissionCountryIdentity,
    resolved: Country,
    validated: CountryMaximumCoverageStructuredBlueprint,
) -> None:
    dossier = validated.country_dossier
    if dossier.country_iso3.upper() != resolved.iso3:
        raise ValidationError(
            "Structured blueprint country ISO-3 does not match the mission country."
        )
    if dossier.country_name.strip().casefold() != resolved.name.casefold():
        # Also accept mission.country_name when it matches dossier but differs slightly from
        # canonical pycountry naming only when mission name equals dossier.
        if dossier.country_name.strip().casefold() != mission_country.country_name.strip().casefold():
            raise ValidationError(
                "Structured blueprint country name does not match the mission country."
            )
        if mission_country.country_name.strip().casefold() != resolved.name.casefold():
            raise ValidationError(
                "Mission country name does not match the resolved country identity."
            )


class BlueprintExecutionPlanService:
    """Pure compiler with no database, network, AI, or provider access."""

    def compile(
        self,
        *,
        mission_id: str,
        blueprint_id: str,
        blueprint_version: int,
        mission_country: MissionCountryIdentity,
        structured_blueprint: dict[str, Any],
    ) -> CompiledExecutionPlanResult:
        if not isinstance(structured_blueprint, dict):
            raise ValidationError("A structured blueprint object is required.")
        if blueprint_version < 1:
            raise ValidationError("Blueprint version must be at least 1.")

        # Never mutate caller input.
        payload = copy.deepcopy(structured_blueprint)
        normalized = normalize_structured_blueprint_payload(payload)
        try:
            validated = CountryMaximumCoverageStructuredBlueprint.model_validate(normalized)
        except PydanticValidationError as exc:
            raise ValidationError("Structured blueprint failed validation.") from exc

        blueprint_snapshot_json = validated.model_dump(mode="json")
        source_blueprint_hash = sha256_hex(blueprint_snapshot_json)

        resolved = _resolve_mission_country(mission_country)
        _assert_countries_agree(
            mission_country=mission_country,
            resolved=resolved,
            validated=validated,
        )

        regions = _dedupe_strings(list(validated.regions))
        languages = _dedupe_strings(list(validated.languages))
        if not regions:
            raise ValidationError("At least one region is required.")
        if not languages:
            raise ValidationError("At least one language is required.")

        containment_summary = (validated.country_containment_rules.summary or "").strip()
        verification_summary = (validated.verification_rules.summary or "").strip()
        if not containment_summary:
            raise ValidationError("Country-containment instructions are required.")
        if not verification_summary:
            raise ValidationError("Verification/qualification instructions are required.")

        completion = _dedupe_strings(list(validated.completion_criteria))
        if not completion:
            raise ValidationError("Completion criteria are required.")

        query_seeds = _dedupe_query_seeds(
            [
                FrozenQuerySeed(
                    query=item.query.strip(),
                    language=item.language.strip(),
                    purpose=item.purpose.strip(),
                )
                for item in validated.query_matrix
                if item.query.strip() and item.language.strip() and item.purpose.strip()
            ]
        )
        if not query_seeds:
            raise ValidationError("Usable search/query seed information is required.")

        regulatory = _dedupe_sources(
            [
                _to_source_entry("regulatory", citation)
                for citation in validated.regulatory_sources
                if _citation_is_meaningful(citation)
            ]
        )
        commercial = _dedupe_sources(
            [
                _to_source_entry("commercial", citation)
                for citation in validated.commercial_sources
                if _citation_is_meaningful(citation)
            ]
        )
        if not regulatory and not commercial:
            raise ValidationError(
                "A source strategy with at least one meaningful regulatory or commercial "
                "source entry is required."
            )

        warnings: list[str] = []
        clarifications: list[str] = []

        if not regulatory:
            warnings.append("No meaningful regulatory sources were provided.")
        if not commercial:
            warnings.append("No meaningful commercial sources were provided.")

        null_url_sources = [
            entry
            for entry in (*regulatory, *commercial)
            if entry.url is None
        ]
        if null_url_sources:
            warnings.append(
                f"{len(null_url_sources)} source entr"
                f"{'y has' if len(null_url_sources) == 1 else 'ies have'} "
                "a null URL; unknown URLs were preserved and not invented."
            )

        if validated.weak_areas:
            clarifications.extend(
                f"Weak area noted in blueprint: {item}" for item in validated.weak_areas
            )
        if validated.human_review_questions:
            clarifications.extend(validated.human_review_questions)
        if not validated.approval_recommendation.ready:
            clarifications.append(
                "Blueprint approval recommendation is not ready: "
                f"{validated.approval_recommendation.reason}"
            )

        terminology = _terminology_from_dossier(validated)
        region_coverage = [
            {
                "region_name": item.region_name,
                "coverage_actions": list(item.coverage_actions),
            }
            for item in validated.region_coverage_plan
        ]

        plan = FrozenExecutionPlan(
            schema_version=EXECUTION_PLAN_SCHEMA_VERSION,
            mission_id=mission_id,
            blueprint_id=blueprint_id,
            blueprint_version=blueprint_version,
            source_blueprint_hash=source_blueprint_hash,
            country=FrozenPlanCountry(
                country_code=resolved.iso2,
                country_name=resolved.name,
                country_iso3=resolved.iso3,
                continent=resolved.continent,
            ),
            scope=FrozenPlanScope(
                mission_id=mission_id,
                blueprint_id=blueprint_id,
                blueprint_version=blueprint_version,
                discovery_strategy_summary=validated.discovery_strategy.summary.strip(),
                estimated_coverage_summary=validated.estimated_coverage.summary.strip() or None,
            ),
            regions=regions,
            languages=languages,
            terminology=terminology,
            source_plan=FrozenSourcePlan(
                regulatory_sources=regulatory,
                commercial_sources=commercial,
            ),
            query_seed_plan=FrozenQuerySeedPlan(seeds=query_seeds),
            crawl_policy=FrozenCrawlPolicy(
                summary=validated.crawl_strategy.summary.strip(),
                region_coverage_actions=region_coverage,
            ),
            contact_policy=FrozenContactPolicy(
                product_rules=list(CONTACT_PRODUCT_RULES),
                blueprint_summary=validated.contact_completeness_strategy.summary.strip(),
            ),
            qualification_policy=FrozenQualificationPolicy(
                statuses=[
                    QualificationStatus.PASS,
                    QualificationStatus.FAIL,
                    QualificationStatus.UNKNOWN,
                ],
                required_criteria=[
                    FrozenQualificationCriterion(
                        criterion_id=criterion_id,
                        required=True,
                        allowed_statuses=[
                            QualificationStatus.PASS,
                            QualificationStatus.FAIL,
                            QualificationStatus.UNKNOWN,
                        ],
                    )
                    for criterion_id in REQUIRED_QUALIFICATION_CRITERIA
                ],
                overall_rule=QUALIFICATION_OVERALL_RULE,
                blueprint_verification_summary=verification_summary,
            ),
            country_containment_policy=FrozenCountryContainmentPolicy(
                product_rules=list(COUNTRY_CONTAINMENT_PRODUCT_RULES),
                blueprint_summary=containment_summary,
            ),
            evidence_policy=FrozenEvidencePolicy(
                blueprint_confidence_summary=validated.confidence_model.summary.strip(),
                preserve_source_urls=True,
                invent_values=False,
            ),
            deduplication_policy=FrozenDeduplicationPolicy(
                product_rules=list(DEDUPLICATION_PRODUCT_RULES),
                blueprint_summary=validated.deduplication_rules.summary.strip(),
            ),
            completion_policy=FrozenCompletionPolicy(criteria=completion),
            risks=_dedupe_strings(list(validated.risks)),
            weak_areas=_dedupe_strings(list(validated.weak_areas)),
            human_review_questions=_dedupe_strings(list(validated.human_review_questions)),
            compiler_warnings=warnings,
            clarification_questions=_dedupe_strings(clarifications),
        )

        frozen_execution_plan_json = plan.model_dump(mode="json")
        execution_plan_hash = sha256_hex(frozen_execution_plan_json)

        return CompiledExecutionPlanResult(
            blueprint_snapshot_json=blueprint_snapshot_json,
            frozen_execution_plan=plan,
            frozen_execution_plan_json=frozen_execution_plan_json,
            execution_plan_schema_version=EXECUTION_PLAN_SCHEMA_VERSION,
            execution_plan_hash=execution_plan_hash,
            source_blueprint_hash=source_blueprint_hash,
        )


blueprint_execution_plan_service = BlueprintExecutionPlanService()
