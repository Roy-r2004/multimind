"""Deterministic typed clarification analysis over frozen execution plans."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.schemas.scraping_clarification import (
    CLARIFICATION_SCHEMA_VERSION,
    ClarificationAllowedValue,
    ClarificationAnalysis,
    ClarificationConstraints,
    ClarificationDecision,
    ClarificationInformationalNote,
    ClarificationSafety,
    ClarificationType,
    TypedClarificationCandidate,
    ValidatedClarificationDecision,
)
from app.schemas.scraping_execution_plan import FrozenExecutionPlan, FrozenSourceEntry
from app.services.scraping.blueprint_execution_plan_service import sha256_hex


def _normalize_token(value: str) -> str:
    collapsed = re.sub(r"[^a-z0-9]+", " ", value.casefold())
    return " ".join(collapsed.split())


def _candidate_id(payload: dict[str, Any]) -> str:
    return sha256_hex(payload)[:40]


def _source_key(entry: FrozenSourceEntry) -> tuple[str, str, str, str]:
    return (
        (entry.url or "").strip().casefold(),
        (entry.title or "").strip().casefold(),
        (entry.source_type or "").strip().casefold(),
        (entry.notes or "").strip().casefold(),
    )


def _plausible_matches(reference: str, options: list[str]) -> list[str]:
    normalized_reference = _normalize_token(reference)
    if not normalized_reference:
        return []
    matches: list[str] = []
    for option in options:
        normalized_option = _normalize_token(option)
        if not normalized_option:
            continue
        if (
            normalized_reference == normalized_option
            or normalized_reference in normalized_option
            or normalized_option in normalized_reference
        ):
            matches.append(option)
    return matches


class ClarificationPolicyService:
    """Analyze a frozen plan without mutating it or calling providers."""

    def analyze(self, plan: FrozenExecutionPlan | dict[str, Any]) -> ClarificationAnalysis:
        validated = (
            plan
            if isinstance(plan, FrozenExecutionPlan)
            else FrozenExecutionPlan.model_validate(copy.deepcopy(plan))
        )
        informational = self._informational_notes(validated)
        deterministic: list[ValidatedClarificationDecision] = []
        safe: list[TypedClarificationCandidate] = []
        human: list[TypedClarificationCandidate] = []

        for item in self._region_findings(validated):
            self._bucket(item, deterministic, safe, human)
        for item in self._language_findings(validated):
            self._bucket(item, deterministic, safe, human)
        for item in self._source_category_findings(validated):
            self._bucket(item, deterministic, safe, human)

        return ClarificationAnalysis(
            schema_version=CLARIFICATION_SCHEMA_VERSION,
            informational_notes=informational,
            deterministic_resolutions=deterministic,
            safe_candidates=safe,
            human_review_findings=human,
        )

    def _bucket(
        self,
        item: ValidatedClarificationDecision | TypedClarificationCandidate,
        deterministic: list[ValidatedClarificationDecision],
        safe: list[TypedClarificationCandidate],
        human: list[TypedClarificationCandidate],
    ) -> None:
        if isinstance(item, ValidatedClarificationDecision):
            deterministic.append(item)
            return
        if item.safety == ClarificationSafety.SAFE_MODEL_SELECTION:
            safe.append(item)
        elif item.safety == ClarificationSafety.REQUIRES_HUMAN_REVIEW:
            human.append(item)

    def _informational_notes(
        self, plan: FrozenExecutionPlan
    ) -> list[ClarificationInformationalNote]:
        notes: list[ClarificationInformationalNote] = []
        for index, warning in enumerate(plan.compiler_warnings):
            text = warning.strip()
            if not text:
                continue
            notes.append(
                ClarificationInformationalNote(
                    note_id=f"compiler_warning:{index}",
                    source="compiler_warning",
                    text=text,
                )
            )
        for index, question in enumerate(plan.clarification_questions):
            text = question.strip()
            if not text:
                continue
            notes.append(
                ClarificationInformationalNote(
                    note_id=f"clarification_question:{index}",
                    source="clarification_question",
                    text=text,
                )
            )
        return notes

    def _region_findings(
        self, plan: FrozenExecutionPlan
    ) -> list[ValidatedClarificationDecision | TypedClarificationCandidate]:
        findings: list[ValidatedClarificationDecision | TypedClarificationCandidate] = []
        regions = list(plan.regions)
        for index, coverage in enumerate(plan.crawl_policy.region_coverage_actions):
            if not isinstance(coverage, dict):
                continue
            reference = str(coverage.get("region_name") or "").strip()
            if not reference:
                continue
            field_path = f"crawl_policy.region_coverage_actions[{index}].region_name"
            if any(_normalize_token(reference) == _normalize_token(region) for region in regions):
                continue
            matches = _plausible_matches(reference, regions)
            if len(matches) == 1:
                findings.append(
                    ValidatedClarificationDecision(
                        clarification_id=_candidate_id(
                            {
                                "type": ClarificationType.REGION_REFERENCE_ALIAS.value,
                                "field_path": field_path,
                                "reference": reference,
                                "selected": matches[0],
                            }
                        ),
                        clarification_type=ClarificationType.REGION_REFERENCE_ALIAS,
                        field_path=field_path,
                        decision=ClarificationDecision.RESOLVED,
                        selected_value=ClarificationAllowedValue(
                            value=matches[0], label=matches[0]
                        ),
                        reason="Unique normalized region match resolved in Python.",
                        confidence=1.0,
                        requires_human_review=False,
                        source="python_deterministic",
                    )
                )
                continue
            if len(matches) >= 2:
                findings.append(
                    self._safe_candidate(
                        clarification_type=ClarificationType.REGION_REFERENCE_ALIAS,
                        field_path=field_path,
                        question=(
                            f"Which approved region should map to coverage reference "
                            f"'{reference}'?"
                        ),
                        allowed_values=[
                            ClarificationAllowedValue(value=item, label=item) for item in matches
                        ],
                        country=plan.country,
                        excerpt={
                            "reference_region_name": reference,
                            "canonical_regions": regions,
                            "coverage_item": coverage,
                        },
                    )
                )
                continue
            findings.append(
                self._human_candidate(
                    clarification_type=ClarificationType.REGION_REFERENCE_ALIAS,
                    field_path=field_path,
                    question=(
                        f"Coverage reference '{reference}' does not match any approved region."
                    ),
                    country=plan.country,
                    excerpt={
                        "reference_region_name": reference,
                        "canonical_regions": regions,
                        "coverage_item": coverage,
                    },
                )
            )
        return findings

    def _language_findings(
        self, plan: FrozenExecutionPlan
    ) -> list[ValidatedClarificationDecision | TypedClarificationCandidate]:
        findings: list[ValidatedClarificationDecision | TypedClarificationCandidate] = []
        languages = list(plan.languages)
        for index, seed in enumerate(plan.query_seed_plan.seeds):
            reference = seed.language.strip()
            if not reference:
                continue
            field_path = f"query_seed_plan.seeds[{index}].language"
            if any(
                _normalize_token(reference) == _normalize_token(language)
                for language in languages
            ):
                continue
            matches = _plausible_matches(reference, languages)
            if len(matches) == 1:
                findings.append(
                    ValidatedClarificationDecision(
                        clarification_id=_candidate_id(
                            {
                                "type": ClarificationType.LANGUAGE_REFERENCE_ALIAS.value,
                                "field_path": field_path,
                                "reference": reference,
                                "selected": matches[0],
                            }
                        ),
                        clarification_type=ClarificationType.LANGUAGE_REFERENCE_ALIAS,
                        field_path=field_path,
                        decision=ClarificationDecision.RESOLVED,
                        selected_value=ClarificationAllowedValue(
                            value=matches[0], label=matches[0]
                        ),
                        reason="Unique normalized language match resolved in Python.",
                        confidence=1.0,
                        requires_human_review=False,
                        source="python_deterministic",
                    )
                )
                continue
            if len(matches) >= 2:
                findings.append(
                    self._safe_candidate(
                        clarification_type=ClarificationType.LANGUAGE_REFERENCE_ALIAS,
                        field_path=field_path,
                        question=(
                            f"Which approved language should map to query-seed language "
                            f"'{reference}'?"
                        ),
                        allowed_values=[
                            ClarificationAllowedValue(value=item, label=item) for item in matches
                        ],
                        country=plan.country,
                        excerpt={
                            "reference_language": reference,
                            "canonical_languages": languages,
                            "query_seed": seed.model_dump(mode="json"),
                        },
                    )
                )
                continue
            findings.append(
                self._human_candidate(
                    clarification_type=ClarificationType.LANGUAGE_REFERENCE_ALIAS,
                    field_path=field_path,
                    question=(
                        f"Query-seed language '{reference}' does not match any approved language."
                    ),
                    country=plan.country,
                    excerpt={
                        "reference_language": reference,
                        "canonical_languages": languages,
                        "query_seed": seed.model_dump(mode="json"),
                    },
                )
            )
        return findings

    def _source_category_findings(
        self, plan: FrozenExecutionPlan
    ) -> list[TypedClarificationCandidate]:
        findings: list[TypedClarificationCandidate] = []
        regulatory_keys = {
            _source_key(entry): entry for entry in plan.source_plan.regulatory_sources
        }
        for commercial in plan.source_plan.commercial_sources:
            key = _source_key(commercial)
            regulatory = regulatory_keys.get(key)
            if regulatory is None:
                continue
            identity = {
                "url": commercial.url,
                "title": commercial.title,
                "source_type": commercial.source_type,
                "notes": commercial.notes,
            }
            findings.append(
                self._safe_candidate(
                    clarification_type=ClarificationType.SOURCE_CATEGORY_CONFLICT,
                    field_path=f"source_plan.category:{sha256_hex(identity)[:16]}",
                    question=(
                        "This source appears in both regulatory and commercial collections. "
                        "Which category should it use?"
                    ),
                    allowed_values=[
                        ClarificationAllowedValue(value="regulatory", label="Regulatory"),
                        ClarificationAllowedValue(value="commercial", label="Commercial"),
                    ],
                    country=plan.country,
                    excerpt={
                        "source": identity,
                        "present_in": ["regulatory", "commercial"],
                    },
                )
            )
        return findings

    def _safe_candidate(
        self,
        *,
        clarification_type: ClarificationType,
        field_path: str,
        question: str,
        allowed_values: list[ClarificationAllowedValue],
        country,
        excerpt: dict[str, Any],
    ) -> TypedClarificationCandidate:
        payload = {
            "type": clarification_type.value,
            "field_path": field_path,
            "question": question,
            "allowed_values": [item.model_dump(mode="json") for item in allowed_values],
            "country": country.model_dump(mode="json"),
            "excerpt": excerpt,
        }
        return TypedClarificationCandidate(
            clarification_id=_candidate_id(payload),
            clarification_type=clarification_type,
            safety=ClarificationSafety.SAFE_MODEL_SELECTION,
            field_path=field_path,
            question=question,
            allowed_values=allowed_values,
            country=country,
            frozen_plan_excerpt=excerpt,
            constraints=ClarificationConstraints(),
        )

    def _human_candidate(
        self,
        *,
        clarification_type: ClarificationType,
        field_path: str,
        question: str,
        country,
        excerpt: dict[str, Any],
    ) -> TypedClarificationCandidate:
        payload = {
            "type": clarification_type.value,
            "field_path": field_path,
            "question": question,
            "country": country.model_dump(mode="json"),
            "excerpt": excerpt,
            "safety": ClarificationSafety.REQUIRES_HUMAN_REVIEW.value,
        }
        return TypedClarificationCandidate(
            clarification_id=_candidate_id(payload),
            clarification_type=clarification_type,
            safety=ClarificationSafety.REQUIRES_HUMAN_REVIEW,
            field_path=field_path,
            question=question,
            allowed_values=[],
            country=country,
            frozen_plan_excerpt=excerpt,
            constraints=ClarificationConstraints(),
        )


clarification_policy_service = ClarificationPolicyService()
