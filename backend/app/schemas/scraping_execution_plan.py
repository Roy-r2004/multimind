"""Frozen execution-plan contract compiled from an approved structured blueprint."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.api import BlueprintCitation, BlueprintQueryMatrixItem

EXECUTION_PLAN_SCHEMA_VERSION = "1"


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


class FrozenExecutionPlan(BaseModel):
    """Immutable campaign execution plan compiled from approved structured blueprint JSON."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = EXECUTION_PLAN_SCHEMA_VERSION
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


# Re-export nested blueprint types used by callers/tests for convenience.
__all__ = [
    "BlueprintCitation",
    "BlueprintQueryMatrixItem",
    "CONTACT_PRODUCT_RULES",
    "COUNTRY_CONTAINMENT_PRODUCT_RULES",
    "DEDUPLICATION_PRODUCT_RULES",
    "EXECUTION_PLAN_SCHEMA_VERSION",
    "FrozenContactPolicy",
    "FrozenCompletionPolicy",
    "FrozenCountryContainmentPolicy",
    "FrozenCrawlPolicy",
    "FrozenDeduplicationPolicy",
    "FrozenEvidencePolicy",
    "FrozenExecutionPlan",
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
]
