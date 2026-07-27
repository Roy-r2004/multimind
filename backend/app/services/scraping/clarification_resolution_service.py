"""Apply validated clarification decisions onto a copy of a frozen execution plan."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.core.exceptions import ValidationError
from app.schemas.scraping_clarification import (
    ClarificationDecision,
    ClarificationType,
    ResolvedExecutionPlanEnvelope,
    ValidatedClarificationDecision,
)
from app.schemas.scraping_execution_plan import (
    FrozenExecutionPlan,
    FrozenExecutionPlanAny,
    FrozenExecutionPlanV2,
    FrozenSourceEntry,
    parse_frozen_execution_plan,
)
from app.services.scraping.blueprint_execution_plan_service import sha256_hex

_REGION_PATH = re.compile(r"^crawl_policy\.region_coverage_actions\[(\d+)\]\.region_name$")
_LANGUAGE_PATH = re.compile(r"^query_seed_plan\.seeds\[(\d+)\]\.language$")
_SOURCE_CATEGORY_PATH = re.compile(r"^source_plan\.category:[0-9a-f]{16}$")


class ClarificationResolutionService:
    def build_no_clarification_envelope(
        self,
        plan: FrozenExecutionPlanAny | dict[str, Any],
        *,
        source_execution_plan_hash: str,
    ) -> tuple[ResolvedExecutionPlanEnvelope, str]:
        validated = self._validated_plan(plan)
        envelope = ResolvedExecutionPlanEnvelope(
            source_execution_plan_hash=source_execution_plan_hash,
            applied_clarification_ids=[],
            plan=validated,
        )
        payload = envelope.model_dump(mode="json")
        return envelope, sha256_hex(payload)

    def apply(
        self,
        plan: FrozenExecutionPlanAny | dict[str, Any],
        decisions: list[ValidatedClarificationDecision],
        *,
        source_execution_plan_hash: str,
    ) -> tuple[ResolvedExecutionPlanEnvelope, str]:
        original = self._validated_plan(plan)
        # Copy within the same schema version only — never upgrade v1 → v2.
        working = parse_frozen_execution_plan(original.model_dump(mode="json"))
        applied: list[str] = []
        seen_ids: set[str] = set()

        for decision in decisions:
            if decision.clarification_id in seen_ids:
                raise ValidationError("Duplicate clarification IDs are not allowed.")
            seen_ids.add(decision.clarification_id)
            if decision.decision != ClarificationDecision.RESOLVED:
                raise ValidationError(
                    "Only RESOLVED clarification decisions can be applied to a resolved plan."
                )
            if decision.selected_value is None:
                raise ValidationError("Resolved decisions require a selected value.")
            if decision.requires_human_review:
                raise ValidationError("Human-review decisions cannot produce a resolved plan.")
            self._apply_one(working, original, decision)
            applied.append(decision.clarification_id)

        self._assert_immutable_core(original, working)
        envelope = ResolvedExecutionPlanEnvelope(
            source_execution_plan_hash=source_execution_plan_hash,
            applied_clarification_ids=applied,
            plan=working,
        )
        payload = envelope.model_dump(mode="json")
        return envelope, sha256_hex(payload)

    def _validated_plan(
        self, plan: FrozenExecutionPlanAny | dict[str, Any]
    ) -> FrozenExecutionPlanAny:
        if isinstance(plan, (FrozenExecutionPlan, FrozenExecutionPlanV2)):
            return parse_frozen_execution_plan(plan.model_dump(mode="json"))
        return parse_frozen_execution_plan(copy.deepcopy(plan))

    def _apply_one(
        self,
        working: FrozenExecutionPlanAny,
        original: FrozenExecutionPlanAny,
        decision: ValidatedClarificationDecision,
    ) -> None:
        selected = decision.selected_value
        assert selected is not None
        if decision.clarification_type == ClarificationType.REGION_REFERENCE_ALIAS:
            match = _REGION_PATH.fullmatch(decision.field_path)
            if match is None:
                raise ValidationError("Unsupported region clarification field path.")
            index = int(match.group(1))
            if index < 0 or index >= len(working.crawl_policy.region_coverage_actions):
                raise ValidationError("Region clarification index is out of range.")
            if selected.value not in original.regions:
                raise ValidationError("Selected region is not an approved frozen-plan region.")
            item = working.crawl_policy.region_coverage_actions[index]
            if not isinstance(item, dict):
                raise ValidationError("Region coverage item must be an object.")
            item["region_name"] = selected.value
            return

        if decision.clarification_type == ClarificationType.LANGUAGE_REFERENCE_ALIAS:
            match = _LANGUAGE_PATH.fullmatch(decision.field_path)
            if match is None:
                raise ValidationError("Unsupported language clarification field path.")
            index = int(match.group(1))
            if index < 0 or index >= len(working.query_seed_plan.seeds):
                raise ValidationError("Language clarification index is out of range.")
            if selected.value not in original.languages:
                raise ValidationError("Selected language is not an approved frozen-plan language.")
            working.query_seed_plan.seeds[index].language = selected.value
            return

        if decision.clarification_type == ClarificationType.SOURCE_CATEGORY_CONFLICT:
            if _SOURCE_CATEGORY_PATH.fullmatch(decision.field_path) is None:
                raise ValidationError("Unsupported source-category clarification field path.")
            if selected.value not in {"regulatory", "commercial"}:
                raise ValidationError("Source category must be regulatory or commercial.")
            self._apply_source_category(working, decision.field_path, selected.value)
            return

        raise ValidationError("Unsupported clarification type.")

    def _apply_source_category(
        self, working: FrozenExecutionPlanAny, field_path: str, selected: str
    ) -> None:
        from app.services.scraping.blueprint_execution_plan_service import sha256_hex as _hash

        marker = field_path.split(":", 1)[1]
        regulatory = list(working.source_plan.regulatory_sources)
        commercial = list(working.source_plan.commercial_sources)

        def identity(entry: FrozenSourceEntry) -> dict[str, Any]:
            return {
                "url": entry.url,
                "title": entry.title,
                "source_type": entry.source_type,
                "notes": entry.notes,
            }

        matched: FrozenSourceEntry | None = None
        for entry in (*regulatory, *commercial):
            if _hash(identity(entry))[:16] == marker:
                matched = entry
                break
        if matched is None:
            raise ValidationError("Source clarification target was not found.")

        key = (
            (matched.url or "").strip().casefold(),
            (matched.title or "").strip().casefold(),
            (matched.source_type or "").strip().casefold(),
            (matched.notes or "").strip().casefold(),
        )

        def keep(entries: list[FrozenSourceEntry]) -> list[FrozenSourceEntry]:
            return [
                entry
                for entry in entries
                if (
                    (entry.url or "").strip().casefold(),
                    (entry.title or "").strip().casefold(),
                    (entry.source_type or "").strip().casefold(),
                    (entry.notes or "").strip().casefold(),
                )
                != key
            ]

        kept_regulatory = keep(regulatory)
        kept_commercial = keep(commercial)
        chosen = FrozenSourceEntry(
            category=selected,  # type: ignore[arg-type]
            url=matched.url,
            title=matched.title,
            source_type=matched.source_type,
            notes=matched.notes,
        )
        if selected == "regulatory":
            kept_regulatory.append(chosen)
        else:
            kept_commercial.append(chosen)
        working.source_plan.regulatory_sources = kept_regulatory
        working.source_plan.commercial_sources = kept_commercial

    def _assert_immutable_core(
        self, original: FrozenExecutionPlanAny, working: FrozenExecutionPlanAny
    ) -> None:
        if type(original) is not type(working):
            raise ValidationError("Clarification cannot change execution-plan schema version.")
        if working.schema_version != original.schema_version:
            raise ValidationError("Clarification cannot change execution-plan schema version.")
        if working.country != original.country:
            raise ValidationError("Country identity cannot change during clarification.")
        if set(working.regions) - set(original.regions):
            raise ValidationError("New regions cannot be added during clarification.")
        if set(working.languages) - set(original.languages):
            raise ValidationError("New languages cannot be added during clarification.")
        if working.qualification_policy != original.qualification_policy:
            raise ValidationError("Qualification policy cannot change during clarification.")
        if working.country_containment_policy != original.country_containment_policy:
            raise ValidationError("Country-containment policy cannot change during clarification.")
        if working.completion_policy != original.completion_policy:
            raise ValidationError("Completion policy cannot change during clarification.")
        if isinstance(original, FrozenExecutionPlanV2) and isinstance(working, FrozenExecutionPlanV2):
            if working.important_cities != original.important_cities:
                raise ValidationError("Important cities cannot change during clarification.")
            if working.language_profiles != original.language_profiles:
                raise ValidationError("Language profiles cannot change during clarification.")
            if working.local_terminology != original.local_terminology:
                raise ValidationError("Local terminology cannot change during clarification.")
            if working.terminology != original.terminology:
                raise ValidationError("Terminology cannot change during clarification.")
            if working.inpatient_residential_terminology != original.inpatient_residential_terminology:
                raise ValidationError(
                    "Inpatient/residential terminology cannot change during clarification."
                )
            if working.private_paid_terminology != original.private_paid_terminology:
                raise ValidationError("Private/paid terminology cannot change during clarification.")
            if working.addiction_categories != original.addiction_categories:
                raise ValidationError("Addiction categories cannot change during clarification.")
        original_urls = {
            entry.url
            for entry in (
                *original.source_plan.regulatory_sources,
                *original.source_plan.commercial_sources,
            )
        }
        working_urls = {
            entry.url
            for entry in (
                *working.source_plan.regulatory_sources,
                *working.source_plan.commercial_sources,
            )
        }
        invented = {url for url in working_urls if url not in original_urls and url is not None}
        if invented:
            raise ValidationError("Source URL invention is not allowed during clarification.")


clarification_resolution_service = ClarificationResolutionService()
