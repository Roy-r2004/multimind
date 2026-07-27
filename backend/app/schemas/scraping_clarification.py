"""Typed Step 2 clarification contracts for frozen execution plans."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.scraping_execution_plan import FrozenExecutionPlan, FrozenPlanCountry

CLARIFICATION_SCHEMA_VERSION = "1"


class ClarificationType(str, Enum):
    REGION_REFERENCE_ALIAS = "REGION_REFERENCE_ALIAS"
    LANGUAGE_REFERENCE_ALIAS = "LANGUAGE_REFERENCE_ALIAS"
    SOURCE_CATEGORY_CONFLICT = "SOURCE_CATEGORY_CONFLICT"


class ClarificationSafety(str, Enum):
    SAFE_MODEL_SELECTION = "SAFE_MODEL_SELECTION"
    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"
    INFORMATIONAL_ONLY = "INFORMATIONAL_ONLY"


class ClarificationDecision(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"


class ClarificationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    NOT_REQUIRED = "not_required"
    COMPLETED = "completed"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"
    FAILED = "failed"


class ClarificationAllowedValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    label: str = Field(min_length=1)


class ClarificationConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_select_from_allowed_values: bool = True
    forbid_country_change: bool = True
    forbid_scope_expansion: bool = True
    forbid_new_urls: bool = True
    forbid_new_sources: bool = True
    forbid_new_regions: bool = True
    forbid_qualification_changes: bool = True


class TypedClarificationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str = Field(min_length=16, max_length=64)
    clarification_type: ClarificationType
    safety: ClarificationSafety
    field_path: str = Field(min_length=1)
    question: str = Field(min_length=1)
    allowed_values: list[ClarificationAllowedValue] = Field(default_factory=list)
    country: FrozenPlanCountry
    frozen_plan_excerpt: dict[str, Any] = Field(default_factory=dict)
    constraints: ClarificationConstraints = Field(default_factory=ClarificationConstraints)


class ClarificationInformationalNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1)
    source: Literal["compiler_warning", "clarification_question", "human_review_finding"]
    text: str = Field(min_length=1)


class ClarificationProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str = Field(min_length=16, max_length=64)
    clarification_type: ClarificationType
    field_path: str = Field(min_length=1)
    question: str = Field(min_length=1)
    allowed_values: list[ClarificationAllowedValue] = Field(min_length=2)
    country: FrozenPlanCountry
    frozen_plan_excerpt: dict[str, Any] = Field(default_factory=dict)
    constraints: ClarificationConstraints


class ClarificationProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str = Field(min_length=16, max_length=64)
    decision: ClarificationDecision
    selected_value: ClarificationAllowedValue | None = None
    reason: str = Field(default="", max_length=2000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_human_review: bool = False

    @model_validator(mode="after")
    def validate_selected_value_rules(self) -> ClarificationProviderResponse:
        if self.decision == ClarificationDecision.RESOLVED:
            if self.selected_value is None:
                raise ValueError("selected_value is required when decision is RESOLVED")
            if self.requires_human_review:
                raise ValueError("RESOLVED decisions cannot require human review")
        else:
            if self.selected_value is not None:
                raise ValueError("selected_value must be null unless decision is RESOLVED")
        if self.decision == ClarificationDecision.REQUIRES_HUMAN_REVIEW:
            if not self.requires_human_review:
                raise ValueError(
                    "requires_human_review must be true when decision is REQUIRES_HUMAN_REVIEW"
                )
        return self


class ValidatedClarificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str = Field(min_length=16, max_length=64)
    clarification_type: ClarificationType
    field_path: str = Field(min_length=1)
    decision: ClarificationDecision
    selected_value: ClarificationAllowedValue | None = None
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_human_review: bool = False
    source: Literal["python_deterministic", "provider", "policy_human_review"] = "provider"


class ClarificationAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CLARIFICATION_SCHEMA_VERSION
    informational_notes: list[ClarificationInformationalNote] = Field(default_factory=list)
    deterministic_resolutions: list[ValidatedClarificationDecision] = Field(default_factory=list)
    safe_candidates: list[TypedClarificationCandidate] = Field(default_factory=list)
    human_review_findings: list[TypedClarificationCandidate] = Field(default_factory=list)


class ResolvedExecutionPlanEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    source_execution_plan_hash: str = Field(min_length=64, max_length=64)
    applied_clarification_ids: list[str] = Field(default_factory=list)
    plan: FrozenExecutionPlan
