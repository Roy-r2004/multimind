"""Frozen execution-plan contract compiled from an approved structured blueprint."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.api import BlueprintCitation, BlueprintQueryMatrixItem

EXECUTION_PLAN_SCHEMA_VERSION_V1 = "1"
EXECUTION_PLAN_SCHEMA_VERSION_V2 = "2"
# Current schema for newly compiled Step-3-capable campaigns.
EXECUTION_PLAN_SCHEMA_VERSION = EXECUTION_PLAN_SCHEMA_VERSION_V2
# Worker-readable provenance versions (historical resume + current). This is NOT a
# Step-3 deterministic-query-generation capability set — future Step 3 must require
# schema "2" independently via supports_deterministic_query_generation().
SUPPORTED_EXECUTION_PLAN_SCHEMA_VERSIONS = frozenset(
    {EXECUTION_PLAN_SCHEMA_VERSION_V1, EXECUTION_PLAN_SCHEMA_VERSION_V2}
)


def supports_deterministic_query_generation(schema_version: str | None) -> bool:
    """True only for plans that may enter future Step 3 query generation.

    Do not reuse SUPPORTED_EXECUTION_PLAN_SCHEMA_VERSIONS for this check: worker
    readability of historical v1 plans is intentionally broader than Step-3 capability.
    """
    return schema_version == EXECUTION_PLAN_SCHEMA_VERSION_V2


class QualificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


REQUIRED_QUALIFICATION_CRITERIA: tuple[str, ...] = (
    "payment_model",
    "residential_care",
    "physical_country",
    "primary_clinical_purpose",
    "qualifying_condition",
)

QUALIFICATION_OVERALL_RULE = (
    "all required criteria PASS → PASS; "
    "any required criterion FAIL → FAIL; "
    "otherwise → UNKNOWN"
)

COUNTRY_CONTAINMENT_PRODUCT_RULES: tuple[str, ...] = (
    "A treatment-delivering physical address inside the mission country is required.",
    "Phone prefix does not prove country.",
    "Country domain does not prove country.",
    "Website language does not prove country.",
    "Marketing to residents does not prove country.",
    "Parent-company or headquarters address does not prove treatment location.",
    "Confirmed foreign physical facility must be rejected as a country mismatch.",
    "Unverified treatment location must remain UNKNOWN.",
)

DEDUPLICATION_PRODUCT_RULES: tuple[str, ...] = (
    "Merge multiple sources for the same physical facility.",
    "Preserve separate physical branches at different treatment addresses.",
    "Ambiguous matches require later review.",
)

CONTACT_PRODUCT_RULES: tuple[str, ...] = (
    "Preserve the source URL for every contact value.",
    "Missing values remain null.",
    "Never invent contacts.",
)


class FrozenPlanCountry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country_code: str = Field(min_length=2, max_length=2)
    country_name: str = Field(min_length=1)
    country_iso3: str = Field(min_length=3, max_length=3)
    continent: str = Field(min_length=1)


class FrozenPlanScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_id: str = Field(min_length=1)
    blueprint_id: str = Field(min_length=1)
    blueprint_version: int = Field(ge=1)
    discovery_strategy_summary: str = Field(min_length=1)
    estimated_coverage_summary: str | None = None


class FrozenSourceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["regulatory", "commercial"]
    url: str | None = None
    title: str | None = None
    source_type: str | None = None
    notes: str | None = None


class FrozenSourcePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regulatory_sources: list[FrozenSourceEntry]
    commercial_sources: list[FrozenSourceEntry]


class FrozenQuerySeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    language: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class FrozenQuerySeedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seeds: list[FrozenQuerySeed] = Field(min_length=1)


class FrozenCrawlPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    region_coverage_actions: list[dict[str, Any]] = Field(default_factory=list)


class FrozenContactPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_rules: list[str]
    blueprint_summary: str = Field(min_length=1)


class FrozenQualificationCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    required: bool = True
    allowed_statuses: list[QualificationStatus]


class FrozenQualificationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statuses: list[QualificationStatus]
    required_criteria: list[FrozenQualificationCriterion]
    overall_rule: str
    blueprint_verification_summary: str = Field(min_length=1)


class FrozenCountryContainmentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_rules: list[str]
    blueprint_summary: str = Field(min_length=1)


class FrozenEvidencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blueprint_confidence_summary: str = Field(min_length=1)
    preserve_source_urls: bool = True
    invent_values: bool = False


class FrozenDeduplicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_rules: list[str]
    blueprint_summary: str = Field(min_length=1)


class FrozenCompletionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: list[str] = Field(min_length=1)


class FrozenImportantCity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    region_name: str = Field(min_length=1)


class FrozenLanguageProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    code: str | None = None
    script: str | None = None


class FrozenExecutionPlan(BaseModel):
    """Immutable v1 campaign execution plan. Do not add v2 fields here (hash safety)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = EXECUTION_PLAN_SCHEMA_VERSION_V1
    mission_id: str = Field(min_length=1)
    blueprint_id: str = Field(min_length=1)
    blueprint_version: int = Field(ge=1)
    source_blueprint_hash: str = Field(min_length=64, max_length=64)
    country: FrozenPlanCountry
    scope: FrozenPlanScope
    regions: list[str] = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    terminology: list[str] = Field(default_factory=list)
    source_plan: FrozenSourcePlan
    query_seed_plan: FrozenQuerySeedPlan
    crawl_policy: FrozenCrawlPolicy
    contact_policy: FrozenContactPolicy
    qualification_policy: FrozenQualificationPolicy
    country_containment_policy: FrozenCountryContainmentPolicy
    evidence_policy: FrozenEvidencePolicy
    deduplication_policy: FrozenDeduplicationPolicy
    completion_policy: FrozenCompletionPolicy
    risks: list[str] = Field(default_factory=list)
    weak_areas: list[str] = Field(default_factory=list)
    human_review_questions: list[str] = Field(default_factory=list)
    compiler_warnings: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


class FrozenExecutionPlanV2(BaseModel):
    """Immutable v2 campaign execution plan with Step 3 query dimensions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2"] = EXECUTION_PLAN_SCHEMA_VERSION_V2
    mission_id: str = Field(min_length=1)
    blueprint_id: str = Field(min_length=1)
    blueprint_version: int = Field(ge=1)
    source_blueprint_hash: str = Field(min_length=64, max_length=64)
    country: FrozenPlanCountry
    scope: FrozenPlanScope
    regions: list[str] = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    language_profiles: list[FrozenLanguageProfile] = Field(min_length=1)
    important_cities: list[FrozenImportantCity] = Field(min_length=1)
    terminology: list[str] = Field(default_factory=list)
    local_terminology: list[str] = Field(min_length=1)
    inpatient_residential_terminology: list[str] = Field(min_length=1)
    private_paid_terminology: list[str] = Field(min_length=1)
    addiction_categories: list[str] = Field(min_length=1)
    source_plan: FrozenSourcePlan
    query_seed_plan: FrozenQuerySeedPlan
    crawl_policy: FrozenCrawlPolicy
    contact_policy: FrozenContactPolicy
    qualification_policy: FrozenQualificationPolicy
    country_containment_policy: FrozenCountryContainmentPolicy
    evidence_policy: FrozenEvidencePolicy
    deduplication_policy: FrozenDeduplicationPolicy
    completion_policy: FrozenCompletionPolicy
    risks: list[str] = Field(default_factory=list)
    weak_areas: list[str] = Field(default_factory=list)
    human_review_questions: list[str] = Field(default_factory=list)
    compiler_warnings: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


FrozenExecutionPlanAny = FrozenExecutionPlan | FrozenExecutionPlanV2


def parse_frozen_execution_plan(data: dict[str, Any] | FrozenExecutionPlanAny) -> FrozenExecutionPlanAny:
    """Version-aware parse that never upgrades a v1 payload into a v2 model.

    Frozen plans always carry an explicit string schema_version of "1" or "2".
    Numeric or missing versions are rejected fail-closed (compilers always emit an
    explicit version; no historical fixture or migration stores a version-less plan).
    """
    if isinstance(data, (FrozenExecutionPlan, FrozenExecutionPlanV2)):
        # Re-validate as the same version only; do not cross-cast.
        dumped = data.model_dump(mode="json")
        if isinstance(data, FrozenExecutionPlanV2):
            return FrozenExecutionPlanV2.model_validate(dumped)
        return FrozenExecutionPlan.model_validate(dumped)

    if not isinstance(data, dict):
        raise ValueError("Frozen execution plan must be an object.")
    if "schema_version" not in data:
        raise ValueError(
            "Frozen execution plan schema_version is required and must be the string \"1\" or \"2\"."
        )
    version = data["schema_version"]
    if version == EXECUTION_PLAN_SCHEMA_VERSION_V2:
        return FrozenExecutionPlanV2.model_validate(data)
    if version == EXECUTION_PLAN_SCHEMA_VERSION_V1:
        return FrozenExecutionPlan.model_validate(data)
    raise ValueError(
        "Unsupported frozen execution plan schema version. "
        "Accepted values are the strings \"1\" and \"2\"."
    )


# Re-export nested blueprint types used by callers/tests for convenience.
__all__ = [
    "BlueprintCitation",
    "BlueprintQueryMatrixItem",
    "CONTACT_PRODUCT_RULES",
    "COUNTRY_CONTAINMENT_PRODUCT_RULES",
    "DEDUPLICATION_PRODUCT_RULES",
    "EXECUTION_PLAN_SCHEMA_VERSION",
    "EXECUTION_PLAN_SCHEMA_VERSION_V1",
    "EXECUTION_PLAN_SCHEMA_VERSION_V2",
    "FrozenContactPolicy",
    "FrozenCompletionPolicy",
    "FrozenCountryContainmentPolicy",
    "FrozenCrawlPolicy",
    "FrozenDeduplicationPolicy",
    "FrozenEvidencePolicy",
    "FrozenExecutionPlan",
    "FrozenExecutionPlanAny",
    "FrozenExecutionPlanV2",
    "FrozenImportantCity",
    "FrozenLanguageProfile",
    "FrozenPlanCountry",
    "FrozenPlanScope",
    "FrozenQualificationCriterion",
    "FrozenQualificationPolicy",
    "FrozenQuerySeed",
    "FrozenQuerySeedPlan",
    "FrozenSourceEntry",
    "FrozenSourcePlan",
    "QUALIFICATION_OVERALL_RULE",
    "QualificationStatus",
    "REQUIRED_QUALIFICATION_CRITERIA",
    "SUPPORTED_EXECUTION_PLAN_SCHEMA_VERSIONS",
    "parse_frozen_execution_plan",
    "supports_deterministic_query_generation",
]
