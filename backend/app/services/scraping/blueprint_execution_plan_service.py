"""Pure deterministic compiler: approved structured blueprint → FrozenExecutionPlan."""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ValidationError
from app.schemas.api import (
    STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2,
    BlueprintCitation,
    CountryMaximumCoverageStructuredBlueprint,
    CountryMaximumCoverageStructuredBlueprintV2,
    StructuredBlueprintAny,
)
from app.schemas.scraping_execution_plan import (
    CONTACT_PRODUCT_RULES,
    COUNTRY_CONTAINMENT_PRODUCT_RULES,
    DEDUPLICATION_PRODUCT_RULES,
    EXECUTION_PLAN_SCHEMA_VERSION_V1,
    EXECUTION_PLAN_SCHEMA_VERSION_V2,
    QUALIFICATION_OVERALL_RULE,
    REQUIRED_QUALIFICATION_CRITERIA,
    FrozenCompletionPolicy,
    FrozenContactPolicy,
    FrozenCountryContainmentPolicy,
    FrozenCrawlPolicy,
    FrozenDeduplicationPolicy,
    FrozenEvidencePolicy,
    FrozenExecutionPlan,
    FrozenExecutionPlanAny,
    FrozenExecutionPlanV2,
    FrozenImportantCity,
    FrozenLanguageProfile,
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
    detect_structured_blueprint_schema_version,
    normalize_structured_blueprint_payload,
    normalize_token,
    parse_structured_blueprint,
    validate_structured_blueprint_for_campaign,
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
    frozen_execution_plan: FrozenExecutionPlanAny
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
    """Hash a JSON-serializable value. Pass dicts/lists — never pre-canonicalized bytes."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        item = _nfc(raw)
        if not item:
            continue
        key = normalize_token(item)
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


def _terminology_from_dossier_v1(validated: CountryMaximumCoverageStructuredBlueprint) -> list[str]:
    """v1-only: flatten dossier extras. Not used as v2 source of truth."""
    extras = validated.country_dossier.model_extra or {}
    terms: list[str] = []
    for key, value in extras.items():
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    terms.append(item.strip())
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
    validated: StructuredBlueprintAny,
) -> None:
    dossier = validated.country_dossier
    if dossier.country_iso3.upper() != resolved.iso3:
        raise ValidationError(
            "Structured blueprint country ISO-3 does not match the mission country."
        )
    if dossier.country_name.strip().casefold() != resolved.name.casefold():
        if dossier.country_name.strip().casefold() != mission_country.country_name.strip().casefold():
            raise ValidationError(
                "Structured blueprint country name does not match the mission country."
            )
        if mission_country.country_name.strip().casefold() != resolved.name.casefold():
            raise ValidationError(
                "Mission country name does not match the resolved country identity."
            )


def _validate_v2_dimensions(validated: CountryMaximumCoverageStructuredBlueprintV2) -> tuple[
    list[str],
    list[str],
    list[FrozenLanguageProfile],
    list[FrozenImportantCity],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
]:
    regions = _dedupe_strings(list(validated.regions))
    languages = _dedupe_strings(list(validated.languages))
    if not regions:
        raise ValidationError("At least one region is required.")
    if not languages:
        raise ValidationError("At least one language is required.")

    region_keys = {normalize_token(region): region for region in regions}
    language_keys = {normalize_token(language): language for language in languages}

    profiles: list[FrozenLanguageProfile] = []
    seen_profile_names: set[str] = set()
    seen_codes: dict[str, str] = {}
    for item in validated.language_profiles:
        name = _nfc(item.name)
        if not name:
            raise ValidationError("Language profile name must be non-empty.")
        key = normalize_token(name)
        if key not in language_keys:
            raise ValidationError(
                f"Language profile {name!r} must correspond to an approved languages entry."
            )
        code = _nfc(item.code) if isinstance(item.code, str) and item.code.strip() else None
        script = _nfc(item.script) if isinstance(item.script, str) and item.script.strip() else None
        if code:
            code_key = normalize_token(code)
            prior = seen_codes.get(code_key)
            if prior is not None and prior != key:
                raise ValidationError(
                    f"Duplicate language code {code!r} cannot be assigned to different languages."
                )
            seen_codes[code_key] = key
        if key in seen_profile_names:
            # Deterministic dedupe of identical profile names; first display wins.
            continue
        seen_profile_names.add(key)
        # Never invent missing code/script.
        profiles.append(
            FrozenLanguageProfile(name=language_keys[key], code=code or None, script=script or None)
        )
    if not profiles:
        raise ValidationError("At least one language profile is required.")
    if set(seen_profile_names) != set(language_keys):
        raise ValidationError(
            "Language profiles must cover every approved language exactly once by name."
        )

    cities: list[FrozenImportantCity] = []
    seen_city_pairs: set[tuple[str, str]] = set()
    city_parent_by_name: dict[str, str] = {}
    for item in validated.important_cities:
        name = _nfc(item.name)
        parent = _nfc(item.region_name)
        if not name:
            raise ValidationError("Important city name must be non-empty.")
        if not parent:
            raise ValidationError("Important city parent region must be non-empty.")
        parent_key = normalize_token(parent)
        if parent_key not in region_keys:
            raise ValidationError(
                f"Important city {name!r} parent region {parent!r} must match an approved region."
            )
        name_key = normalize_token(name)
        prior_parent = city_parent_by_name.get(name_key)
        if prior_parent is not None and prior_parent != parent_key:
            raise ValidationError(
                f"Important city {name!r} has conflicting parent regions."
            )
        city_parent_by_name[name_key] = parent_key
        pair = (name_key, parent_key)
        if pair in seen_city_pairs:
            # Deterministic dedupe of identical city/region pairs; first display wins.
            continue
        seen_city_pairs.add(pair)
        cities.append(FrozenImportantCity(name=name, region_name=region_keys[parent_key]))
    if not cities:
        raise ValidationError("At least one important city is required.")

    local_terminology = _dedupe_strings(list(validated.local_terminology))
    inpatient = _dedupe_strings(list(validated.inpatient_residential_terminology))
    private_paid = _dedupe_strings(list(validated.private_paid_terminology))
    addiction = _dedupe_strings(list(validated.addiction_categories))
    if not local_terminology:
        raise ValidationError("Local terminology is required.")
    if not inpatient:
        raise ValidationError("Inpatient/residential terminology is required.")
    if not private_paid:
        raise ValidationError("Private/paid terminology is required.")
    if not addiction:
        raise ValidationError("Addiction categories are required.")

    return (
        regions,
        languages,
        profiles,
        cities,
        local_terminology,
        inpatient,
        private_paid,
        addiction,
        local_terminology,  # terminology mirror for backward-compatible field
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
        require_v2: bool = True,
    ) -> CompiledExecutionPlanResult:
        """Compile a frozen execution plan.

        New mission campaigns must pass require_v2=True (default). Historical/test
        callers may set require_v2=False to compile legacy v1 fixtures without
        inventing v2 dimensions.
        """
        if not isinstance(structured_blueprint, dict):
            raise ValidationError("A structured blueprint object is required.")
        if blueprint_version < 1:
            raise ValidationError("Blueprint version must be at least 1.")

        payload = copy.deepcopy(structured_blueprint)
        try:
            version = detect_structured_blueprint_schema_version(payload)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if require_v2:
            try:
                validated_v2 = validate_structured_blueprint_for_campaign(payload)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            except PydanticValidationError as exc:
                raise ValidationError(
                    "Structured blueprint v2 failed validation."
                ) from exc
            return self._compile_v2(
                mission_id=mission_id,
                blueprint_id=blueprint_id,
                blueprint_version=blueprint_version,
                mission_country=mission_country,
                validated=validated_v2,
            )

        if version == STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2:
            try:
                validated_any = parse_structured_blueprint(payload)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            except PydanticValidationError as exc:
                raise ValidationError("Structured blueprint failed validation.") from exc
            if not isinstance(validated_any, CountryMaximumCoverageStructuredBlueprintV2):
                raise ValidationError("Structured blueprint v2 failed validation.")
            return self._compile_v2(
                mission_id=mission_id,
                blueprint_id=blueprint_id,
                blueprint_version=blueprint_version,
                mission_country=mission_country,
                validated=validated_any,
            )

        return self._compile_v1(
            mission_id=mission_id,
            blueprint_id=blueprint_id,
            blueprint_version=blueprint_version,
            mission_country=mission_country,
            structured_blueprint=payload,
        )

    def _compile_v2(
        self,
        *,
        mission_id: str,
        blueprint_id: str,
        blueprint_version: int,
        mission_country: MissionCountryIdentity,
        validated: CountryMaximumCoverageStructuredBlueprintV2,
    ) -> CompiledExecutionPlanResult:
        blueprint_snapshot_json = validated.model_dump(mode="json")
        source_blueprint_hash = sha256_hex(blueprint_snapshot_json)

        resolved = _resolve_mission_country(mission_country)
        _assert_countries_agree(
            mission_country=mission_country,
            resolved=resolved,
            validated=validated,
        )

        (
            regions,
            languages,
            language_profiles,
            important_cities,
            local_terminology,
            inpatient,
            private_paid,
            addiction,
            terminology,
        ) = _validate_v2_dimensions(validated)

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
                    query=_nfc(item.query),
                    language=_nfc(item.language),
                    purpose=_nfc(item.purpose),
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
        null_url_sources = [entry for entry in (*regulatory, *commercial) if entry.url is None]
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

        region_coverage = [
            {
                "region_name": item.region_name,
                "coverage_actions": list(item.coverage_actions),
            }
            for item in validated.region_coverage_plan
        ]

        plan = FrozenExecutionPlanV2(
            schema_version=EXECUTION_PLAN_SCHEMA_VERSION_V2,
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
            language_profiles=language_profiles,
            important_cities=important_cities,
            terminology=terminology,
            local_terminology=local_terminology,
            inpatient_residential_terminology=inpatient,
            private_paid_terminology=private_paid,
            addiction_categories=addiction,
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
            execution_plan_schema_version=EXECUTION_PLAN_SCHEMA_VERSION_V2,
            execution_plan_hash=execution_plan_hash,
            source_blueprint_hash=source_blueprint_hash,
        )

    def _compile_v1(
        self,
        *,
        mission_id: str,
        blueprint_id: str,
        blueprint_version: int,
        mission_country: MissionCountryIdentity,
        structured_blueprint: dict[str, Any],
    ) -> CompiledExecutionPlanResult:
        """Compile historical v1 blueprints without inventing v2 dimensions."""
        normalized = normalize_structured_blueprint_payload(structured_blueprint)
        try:
            validated = CountryMaximumCoverageStructuredBlueprint.model_validate(normalized)
        except PydanticValidationError as exc:
            raise ValidationError("Structured blueprint failed validation.") from exc

        blueprint_snapshot_json = validated.model_dump(mode="json", exclude_none=True)
        # Keep historical shape: omit null schema_version from dump for stable hashes in tests.
        if blueprint_snapshot_json.get("schema_version") is None:
            blueprint_snapshot_json.pop("schema_version", None)
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
        null_url_sources = [entry for entry in (*regulatory, *commercial) if entry.url is None]
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

        terminology = _terminology_from_dossier_v1(validated)
        region_coverage = [
            {
                "region_name": item.region_name,
                "coverage_actions": list(item.coverage_actions),
            }
            for item in validated.region_coverage_plan
        ]

        plan = FrozenExecutionPlan(
            schema_version=EXECUTION_PLAN_SCHEMA_VERSION_V1,
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
            execution_plan_schema_version=EXECUTION_PLAN_SCHEMA_VERSION_V1,
            execution_plan_hash=execution_plan_hash,
            source_blueprint_hash=source_blueprint_hash,
        )


blueprint_execution_plan_service = BlueprintExecutionPlanService()
