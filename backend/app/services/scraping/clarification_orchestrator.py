"""Coordinate typed clarification analysis, provider calls, and resolved-plan persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.db.models import ScrapingExecution, ScrapingExecutionStatus
from app.schemas.scraping_clarification import (
    CLARIFICATION_SCHEMA_VERSION,
    ClarificationAnalysis,
    ClarificationDecision,
    ClarificationProviderRequest,
    ClarificationSafety,
    ClarificationStatus,
    TypedClarificationCandidate,
    ValidatedClarificationDecision,
)
from app.schemas.scraping_execution_plan import FrozenExecutionPlan
from app.services.scraping.clarification_policy_service import clarification_policy_service
from app.services.scraping.clarification_provider import (
    ClarificationProvider,
    ClarificationProviderError,
    OpenRouterClarificationProvider,
    build_clarification_provider,
)
from app.services.scraping.clarification_resolution_service import (
    clarification_resolution_service,
)
from app.services.scraping.execution_service import execution_service

TERMINAL_CLARIFICATION_STATUSES = {
    ClarificationStatus.NOT_REQUIRED.value,
    ClarificationStatus.COMPLETED.value,
    ClarificationStatus.REQUIRES_HUMAN_REVIEW.value,
    ClarificationStatus.FAILED.value,
}


@dataclass
class ClarificationPhaseResult:
    status: ClarificationStatus
    provider_calls: int
    continue_campaign: bool


class ClarificationOrchestrator:
    def __init__(self, provider: ClarificationProvider | None = None) -> None:
        self._provider = provider

    def _resolve_provider(self) -> ClarificationProvider:
        if self._provider is not None:
            return self._provider
        return build_clarification_provider()

    async def run(
        self,
        db: AsyncSession,
        execution: ScrapingExecution,
        *,
        check_interrupt=None,
    ) -> ClarificationPhaseResult:
        """Run clarification for a Step 1 campaign. Never mutates Step 1 frozen fields."""
        if execution.frozen_execution_plan_json is None or not execution.execution_plan_hash:
            return ClarificationPhaseResult(
                status=ClarificationStatus.NOT_REQUIRED,
                provider_calls=0,
                continue_campaign=True,
            )

        existing_status = execution.clarification_status
        if existing_status in TERMINAL_CLARIFICATION_STATUSES:
            return ClarificationPhaseResult(
                status=ClarificationStatus(existing_status),
                provider_calls=0,
                continue_campaign=existing_status
                in {
                    ClarificationStatus.NOT_REQUIRED.value,
                    ClarificationStatus.COMPLETED.value,
                },
            )

        plan = FrozenExecutionPlan.model_validate(execution.frozen_execution_plan_json)
        analysis = clarification_policy_service.analyze(plan)

        if analysis.human_review_findings:
            await self._persist_human_review(db, execution, analysis, decisions=[])
            return ClarificationPhaseResult(
                status=ClarificationStatus.REQUIRES_HUMAN_REVIEW,
                provider_calls=0,
                continue_campaign=False,
            )

        if not analysis.safe_candidates and not analysis.deterministic_resolutions:
            await self._persist_not_required(db, execution, analysis, plan)
            return ClarificationPhaseResult(
                status=ClarificationStatus.NOT_REQUIRED,
                provider_calls=0,
                continue_campaign=True,
            )

        if not analysis.safe_candidates:
            decisions = list(analysis.deterministic_resolutions)
            await self._persist_completed(db, execution, analysis, plan, decisions, calls=0)
            return ClarificationPhaseResult(
                status=ClarificationStatus.COMPLETED,
                provider_calls=0,
                continue_campaign=True,
            )

        return await self._run_provider_phase(
            db, execution, analysis, plan, check_interrupt=check_interrupt
        )

    async def _run_provider_phase(
        self,
        db: AsyncSession,
        execution: ScrapingExecution,
        analysis: ClarificationAnalysis,
        plan: FrozenExecutionPlan,
        *,
        check_interrupt,
    ) -> ClarificationPhaseResult:
        if check_interrupt is not None and await check_interrupt(db, execution):
            return ClarificationPhaseResult(
                status=ClarificationStatus(
                    execution.clarification_status or ClarificationStatus.PENDING.value
                ),
                provider_calls=0,
                continue_campaign=False,
            )

        try:
            provider = self._resolve_provider()
        except ClarificationProviderError as exc:
            await self._persist_failed(db, execution, analysis, exc)
            return ClarificationPhaseResult(
                status=ClarificationStatus.FAILED,
                provider_calls=0,
                continue_campaign=False,
            )

        settings = get_settings()
        now = datetime.now(UTC)
        stored_decisions = self._load_decisions(execution)
        stored_ids = {item.clarification_id for item in stored_decisions}
        requests_payload = [
            self._request_from_candidate(candidate).model_dump(mode="json")
            for candidate in analysis.safe_candidates
        ]

        execution.clarification_status = ClarificationStatus.IN_PROGRESS.value
        execution.clarification_schema_version = CLARIFICATION_SCHEMA_VERSION
        execution.clarification_requests_json = {
            "informational_notes": [
                note.model_dump(mode="json") for note in analysis.informational_notes
            ],
            "safe_candidates": [
                candidate.model_dump(mode="json") for candidate in analysis.safe_candidates
            ],
            "human_review_findings": [],
            "provider_requests": requests_payload,
        }
        execution.clarification_started_at = execution.clarification_started_at or now
        execution.clarification_attempt_count = int(execution.clarification_attempt_count or 0)
        execution.current_stage = "clarification"
        execution.current_stage_label = "Clarification"
        execution.latest_message = "Resolving typed blueprint clarifications."
        if isinstance(provider, OpenRouterClarificationProvider):
            execution.clarification_model_slug_snapshot = (
                settings.openrouter_scraper_clarification_model.strip() or None
            )
            execution.current_provider = "OpenRouter"
            execution.current_model = None  # never expose slug to cockpit model field
        else:
            # Dependency-injected ClarificationProvider (tests only); never a production fake.
            execution.current_provider = "ClarificationProvider"
            execution.current_model = None
        await execution_service.emit_event(
            db,
            execution.id,
            "clarification_started",
            "Typed clarification phase started.",
            metadata={"required_count": len(analysis.safe_candidates)},
        )
        await db.commit()

        decisions = list(analysis.deterministic_resolutions)
        decisions.extend(
            item for item in stored_decisions if item.source != "python_deterministic"
        )
        provider_calls = 0
        max_attempts = max(1, settings.openrouter_scraper_clarification_max_attempts)

        for candidate in analysis.safe_candidates:
            if check_interrupt is not None and await check_interrupt(db, execution):
                return ClarificationPhaseResult(
                    status=ClarificationStatus.IN_PROGRESS,
                    provider_calls=provider_calls,
                    continue_campaign=False,
                )
            if candidate.clarification_id in stored_ids:
                continue
            request = self._request_from_candidate(candidate)
            response = None
            last_error: ClarificationProviderError | None = None
            for attempt in range(max_attempts):
                if check_interrupt is not None and await check_interrupt(db, execution):
                    return ClarificationPhaseResult(
                        status=ClarificationStatus.IN_PROGRESS,
                        provider_calls=provider_calls,
                        continue_campaign=False,
                    )
                try:
                    response = await provider.clarify(request)
                    provider_calls += 1
                    execution.clarification_attempt_count = (
                        int(execution.clarification_attempt_count or 0) + 1
                    )
                    last_error = None
                    break
                except ClarificationProviderError as exc:
                    last_error = exc
                    provider_calls += 1
                    execution.clarification_attempt_count = (
                        int(execution.clarification_attempt_count or 0) + 1
                    )
                    execution.clarification_error_code = exc.category
                    execution.clarification_provider_metadata_json = {
                        "safe_diagnostics": exc.safe_diagnostics()
                    }
                    await db.commit()
                    if not exc.retryable or attempt + 1 >= max_attempts:
                        break
            if response is None:
                assert last_error is not None
                await self._persist_failed(db, execution, analysis, last_error)
                return ClarificationPhaseResult(
                    status=ClarificationStatus.FAILED,
                    provider_calls=provider_calls,
                    continue_campaign=False,
                )

            try:
                validated = self._validate_against_candidate(candidate, response)
            except ValidationError as exc:
                await self._persist_human_review(
                    db,
                    execution,
                    analysis,
                    decisions=decisions,
                    rejection_reason=str(exc),
                )
                return ClarificationPhaseResult(
                    status=ClarificationStatus.REQUIRES_HUMAN_REVIEW,
                    provider_calls=provider_calls,
                    continue_campaign=False,
                )

            decisions.append(validated)
            stored_ids.add(validated.clarification_id)
            execution.clarification_decisions_json = [
                item.model_dump(mode="json") for item in decisions
            ]
            await execution_service.emit_event(
                db,
                execution.id,
                "clarification_decision_recorded",
                "A typed clarification decision was recorded.",
                metadata={
                    "clarification_id": validated.clarification_id,
                    "decision": validated.decision.value,
                },
            )
            await db.commit()

            if validated.decision != ClarificationDecision.RESOLVED or validated.requires_human_review:
                await self._persist_human_review(db, execution, analysis, decisions=decisions)
                return ClarificationPhaseResult(
                    status=ClarificationStatus.REQUIRES_HUMAN_REVIEW,
                    provider_calls=provider_calls,
                    continue_campaign=False,
                )

        await self._persist_completed(
            db, execution, analysis, plan, decisions, calls=provider_calls
        )
        return ClarificationPhaseResult(
            status=ClarificationStatus.COMPLETED,
            provider_calls=provider_calls,
            continue_campaign=True,
        )

    def _request_from_candidate(
        self, candidate: TypedClarificationCandidate
    ) -> ClarificationProviderRequest:
        if candidate.safety != ClarificationSafety.SAFE_MODEL_SELECTION:
            raise ValidationError("Only SAFE_MODEL_SELECTION candidates may call the provider.")
        if len(candidate.allowed_values) < 2:
            raise ValidationError("Safe clarification candidates require at least two allowed values.")
        return ClarificationProviderRequest(
            clarification_id=candidate.clarification_id,
            clarification_type=candidate.clarification_type,
            field_path=candidate.field_path,
            question=candidate.question,
            allowed_values=candidate.allowed_values,
            country=candidate.country,
            frozen_plan_excerpt=candidate.frozen_plan_excerpt,
            constraints=candidate.constraints,
        )

    def _validate_against_candidate(
        self,
        candidate: TypedClarificationCandidate,
        response,
    ) -> ValidatedClarificationDecision:
        if response.clarification_id != candidate.clarification_id:
            raise ValidationError("Unknown clarification ID.")
        if response.decision == ClarificationDecision.RESOLVED:
            assert response.selected_value is not None
            allowed_values = {item.value for item in candidate.allowed_values}
            if response.selected_value.value not in allowed_values:
                raise ValidationError("Selected value is outside allowed values.")
        return ValidatedClarificationDecision(
            clarification_id=response.clarification_id,
            clarification_type=candidate.clarification_type,
            field_path=candidate.field_path,
            decision=response.decision,
            selected_value=response.selected_value,
            reason=response.reason,
            confidence=response.confidence,
            requires_human_review=response.requires_human_review
            or response.decision != ClarificationDecision.RESOLVED,
            source="provider",
        )

    def _load_decisions(
        self, execution: ScrapingExecution
    ) -> list[ValidatedClarificationDecision]:
        raw = execution.clarification_decisions_json
        if not isinstance(raw, list):
            return []
        return [ValidatedClarificationDecision.model_validate(item) for item in raw]

    async def _persist_not_required(
        self,
        db: AsyncSession,
        execution: ScrapingExecution,
        analysis: ClarificationAnalysis,
        plan: FrozenExecutionPlan,
    ) -> None:
        envelope, resolved_hash = clarification_resolution_service.build_no_clarification_envelope(
            plan, source_execution_plan_hash=execution.execution_plan_hash or ""
        )
        now = datetime.now(UTC)
        execution.clarification_status = ClarificationStatus.NOT_REQUIRED.value
        execution.clarification_schema_version = CLARIFICATION_SCHEMA_VERSION
        execution.clarification_requests_json = {
            "informational_notes": [
                note.model_dump(mode="json") for note in analysis.informational_notes
            ],
            "safe_candidates": [],
            "human_review_findings": [],
        }
        execution.clarification_decisions_json = []
        execution.resolved_execution_plan_json = envelope.model_dump(mode="json")
        execution.resolved_execution_plan_hash = resolved_hash
        execution.clarification_attempt_count = int(execution.clarification_attempt_count or 0)
        execution.clarification_started_at = execution.clarification_started_at or now
        execution.clarification_completed_at = now
        execution.clarification_error_code = None
        await execution_service.emit_event(
            db,
            execution.id,
            "clarification_not_required",
            "No typed clarification candidates were required.",
            metadata={"informational_note_count": len(analysis.informational_notes)},
        )
        await db.commit()

    async def _persist_completed(
        self,
        db: AsyncSession,
        execution: ScrapingExecution,
        analysis: ClarificationAnalysis,
        plan: FrozenExecutionPlan,
        decisions: list[ValidatedClarificationDecision],
        *,
        calls: int,
    ) -> None:
        envelope, resolved_hash = clarification_resolution_service.apply(
            plan,
            decisions,
            source_execution_plan_hash=execution.execution_plan_hash or "",
        )
        now = datetime.now(UTC)
        execution.clarification_status = ClarificationStatus.COMPLETED.value
        execution.clarification_schema_version = CLARIFICATION_SCHEMA_VERSION
        execution.clarification_requests_json = {
            "informational_notes": [
                note.model_dump(mode="json") for note in analysis.informational_notes
            ],
            "safe_candidates": [
                candidate.model_dump(mode="json") for candidate in analysis.safe_candidates
            ],
            "human_review_findings": [],
        }
        execution.clarification_decisions_json = [
            item.model_dump(mode="json") for item in decisions
        ]
        execution.resolved_execution_plan_json = envelope.model_dump(mode="json")
        execution.resolved_execution_plan_hash = resolved_hash
        execution.clarification_completed_at = now
        execution.clarification_error_code = None
        await execution_service.emit_event(
            db,
            execution.id,
            "clarification_completed",
            "Typed clarification phase completed.",
            metadata={
                "resolved_count": len(decisions),
                "provider_calls": calls,
            },
        )
        await db.commit()

    async def _persist_human_review(
        self,
        db: AsyncSession,
        execution: ScrapingExecution,
        analysis: ClarificationAnalysis,
        *,
        decisions: list[ValidatedClarificationDecision],
        rejection_reason: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        execution.clarification_status = ClarificationStatus.REQUIRES_HUMAN_REVIEW.value
        execution.clarification_schema_version = CLARIFICATION_SCHEMA_VERSION
        execution.clarification_requests_json = {
            "informational_notes": [
                note.model_dump(mode="json") for note in analysis.informational_notes
            ],
            "safe_candidates": [
                candidate.model_dump(mode="json") for candidate in analysis.safe_candidates
            ],
            "human_review_findings": [
                finding.model_dump(mode="json") for finding in analysis.human_review_findings
            ],
            "rejection_reason": rejection_reason,
        }
        execution.clarification_decisions_json = [
            item.model_dump(mode="json") for item in decisions
        ]
        execution.resolved_execution_plan_json = None
        execution.resolved_execution_plan_hash = None
        execution.clarification_completed_at = now
        execution.clarification_error_code = rejection_reason and "scope_or_unresolved"
        execution.current_stage = "clarification"
        execution.current_stage_label = "Clarification needs review"
        execution.latest_message = (
            "Clarification needs human review. Review the blueprint and start a new campaign "
            "after approving a revised version. This campaign will not continue automatically."
        )
        execution.status = ScrapingExecutionStatus.PAUSED
        execution.paused_at = now
        await execution_service.emit_event(
            db,
            execution.id,
            "clarification_requires_human_review",
            "Typed clarification requires human review before the campaign can continue.",
            metadata={"finding_count": len(analysis.human_review_findings)},
        )
        await db.commit()

    async def _persist_failed(
        self,
        db: AsyncSession,
        execution: ScrapingExecution,
        analysis: ClarificationAnalysis,
        exc: ClarificationProviderError,
    ) -> None:
        now = datetime.now(UTC)
        execution.clarification_status = ClarificationStatus.FAILED.value
        execution.clarification_schema_version = CLARIFICATION_SCHEMA_VERSION
        execution.clarification_error_code = exc.category
        execution.clarification_provider_metadata_json = {
            "safe_diagnostics": exc.safe_diagnostics()
        }
        execution.clarification_completed_at = now
        execution.resolved_execution_plan_json = None
        execution.resolved_execution_plan_hash = None
        execution.current_stage = "clarification"
        execution.current_stage_label = "Clarification failed"
        execution.latest_message = "Clarification failed. Review configuration and retry later."
        execution.status = ScrapingExecutionStatus.FAILED
        execution.completed_at = now
        execution.error_message = "Clarification phase failed."
        await execution_service.emit_event(
            db,
            execution.id,
            "clarification_failed",
            "Typed clarification phase failed.",
            metadata={"error_code": exc.category},
        )
        await db.commit()


clarification_orchestrator = ClarificationOrchestrator()
