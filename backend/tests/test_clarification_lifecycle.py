"""Clarification lifecycle coverage against mission campaigns."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_mission_campaign_lifecycle import (
    _approved_mission_with_team_plan,
    lebanon_structured_blueprint,
)

from app.core.config import Settings
from app.core.exceptions import ConflictError
from app.db.models import ScrapingExecution, ScrapingExecutionStatus
from app.schemas.scraping_clarification import (
    ClarificationDecision,
    ClarificationProviderRequest,
    ClarificationProviderResponse,
    ClarificationStatus,
)
from app.schemas.scraping_execution_plan import FrozenExecutionPlan
from app.services.scraping import mission_campaign_mock_worker
from app.services.scraping.blueprint_execution_plan_service import (
    BlueprintExecutionPlanService,
    MissionCountryIdentity,
)
from app.services.scraping.clarification_orchestrator import ClarificationOrchestrator
from app.services.scraping.clarification_policy_service import ClarificationPolicyService
from app.services.scraping.clarification_provider import (
    ClarificationProviderError,
    OpenRouterClarificationProvider,
    build_clarification_provider,
)
from app.services.scraping.execution_service import execution_service


@dataclass
class RecordingClarificationProvider:
    """Test-local ClarificationProvider double; not part of production app code."""

    calls: list[ClarificationProviderRequest] = field(default_factory=list)
    raise_error: ClarificationProviderError | None = None
    responses: dict[str, ClarificationProviderResponse] = field(default_factory=dict)

    async def clarify(
        self, request: ClarificationProviderRequest
    ) -> ClarificationProviderResponse:
        self.calls.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        if request.clarification_id in self.responses:
            return self.responses[request.clarification_id]
        return ClarificationProviderResponse(
            clarification_id=request.clarification_id,
            decision=ClarificationDecision.RESOLVED,
            selected_value=request.allowed_values[0],
            reason="Test double selected the first allowed value.",
            confidence=1.0,
            requires_human_review=False,
        )


async def _patch_enqueue(monkeypatch) -> None:
    async def no_enqueue(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", no_enqueue)
    monkeypatch.setattr(execution_service, "_publish_event", no_enqueue)


@pytest.mark.asyncio
async def test_no_typed_ambiguity_means_zero_provider_calls_and_mock_continues(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    factory_calls: list[object] = []

    def unexpected_factory(*_args, **_kwargs):
        factory_calls.append(True)
        raise AssertionError("No provider factory call is allowed on the no-candidate path.")

    monkeypatch.setattr(
        "app.services.scraping.clarification_orchestrator.build_clarification_provider",
        unexpected_factory,
    )
    # Production orchestrator (no injected provider) must not need configuration.
    monkeypatch.setattr(
        "app.services.scraping.mission_campaign_mock_worker.clarification_orchestrator",
        ClarificationOrchestrator(),
    )
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    assert summary.clarification_status == ClarificationStatus.PENDING.value
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)

    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    execution = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert execution is not None
    assert execution.status == ScrapingExecutionStatus.COMPLETED
    assert execution.clarification_status == ClarificationStatus.NOT_REQUIRED.value
    assert execution.resolved_execution_plan_hash
    assert factory_calls == []
    frozen = FrozenExecutionPlan.model_validate(execution.frozen_execution_plan_json)
    resolved = FrozenExecutionPlan.model_validate(
        execution.resolved_execution_plan_json["plan"]
    )
    assert frozen.model_dump(mode="json") == resolved.model_dump(mode="json")


@pytest.mark.asyncio
async def test_typed_candidate_without_model_config_fails_closed(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    payload = lebanon_structured_blueprint()
    payload["regions"] = ["Lower Beirut", "Upper Beirut"]
    payload["region_coverage_plan"] = [
        {"region_name": "Beirut", "coverage_actions": ["Search registry"]}
    ]
    blueprint.structured_blueprint = payload
    await db.commit()

    monkeypatch.setattr(
        "app.services.scraping.clarification_orchestrator.get_settings",
        lambda: Settings(
            openrouter_api_key="test-key",
            openrouter_scraper_clarification_model="",
            openrouter_scraper_clarification_max_attempts=2,
        ),
    )
    monkeypatch.setattr(
        "app.services.scraping.mission_campaign_mock_worker.clarification_orchestrator",
        ClarificationOrchestrator(),
    )
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)

    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    execution = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert execution is not None
    assert execution.status == ScrapingExecutionStatus.FAILED
    assert execution.clarification_status == ClarificationStatus.FAILED.value
    assert execution.clarification_error_code == "configuration_missing"
    assert execution.resolved_execution_plan_json is None
    assert execution.progress_percent == 0


@pytest.mark.asyncio
async def test_safe_clarification_completes_with_test_local_provider(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    payload = lebanon_structured_blueprint()
    payload["regions"] = ["Lower Beirut", "Upper Beirut"]
    payload["region_coverage_plan"] = [
        {"region_name": "Beirut", "coverage_actions": ["Search registry"]}
    ]
    blueprint.structured_blueprint = payload
    await db.commit()

    double = RecordingClarificationProvider()
    monkeypatch.setattr(
        "app.services.scraping.mission_campaign_mock_worker.clarification_orchestrator",
        ClarificationOrchestrator(provider=double),
    )
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)

    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    execution = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert execution is not None
    assert execution.status == ScrapingExecutionStatus.COMPLETED
    assert execution.clarification_status == ClarificationStatus.COMPLETED.value
    assert len(double.calls) == 1
    assert execution.resolved_execution_plan_json is not None
    original_frozen = execution.frozen_execution_plan_json
    await db.refresh(execution)
    assert execution.frozen_execution_plan_json == original_frozen


@pytest.mark.asyncio
async def test_human_review_pauses_before_mock_stages(db: AsyncSession, auth, monkeypatch) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    payload = lebanon_structured_blueprint()
    payload["regions"] = ["Beirut"]
    payload["region_coverage_plan"] = [
        {"region_name": "Tripoli", "coverage_actions": ["Search registry"]}
    ]
    blueprint.structured_blueprint = payload
    await db.commit()

    factory_calls: list[object] = []

    def unexpected_factory(*_args, **_kwargs):
        factory_calls.append(True)
        raise AssertionError("Human-review findings must not create a provider.")

    monkeypatch.setattr(
        "app.services.scraping.clarification_orchestrator.build_clarification_provider",
        unexpected_factory,
    )
    monkeypatch.setattr(
        "app.services.scraping.mission_campaign_mock_worker.clarification_orchestrator",
        ClarificationOrchestrator(),
    )
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)

    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    execution = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert execution is not None
    assert execution.status == ScrapingExecutionStatus.PAUSED
    assert execution.clarification_status == ClarificationStatus.REQUIRES_HUMAN_REVIEW.value
    assert execution.resolved_execution_plan_json is None
    assert factory_calls == []
    assert execution.progress_percent == 0
    with pytest.raises(ConflictError, match="human review"):
        await execution_service.resume_mission_campaign(db, auth, mission.id, summary.id)
    detail = await execution_service.get_mission_campaign_detail(
        db, auth, mission.id, summary.id
    )
    assert detail.can_resume is False
    assert detail.execution.clarification_requires_human_review is True


@pytest.mark.asyncio
async def test_completed_clarification_phase_is_idempotent(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    orchestrator = ClarificationOrchestrator()
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    first = await orchestrator.run(db, execution)
    assert first.status == ClarificationStatus.NOT_REQUIRED
    second = await orchestrator.run(db, execution)
    assert second.status == ClarificationStatus.NOT_REQUIRED
    assert second.provider_calls == 0


@pytest.mark.asyncio
async def test_partial_decisions_resume_from_stored_state(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    # Legitimate multi-match aliases: "Beirut" ⊆ Lower/Upper Beirut;
    # "Sidon" ⊆ Lower/Upper Sidon. Unrelated tokens like "district"/"area"
    # are NOT used — those do not substring-match and become human review.
    payload = lebanon_structured_blueprint()
    payload["regions"] = ["Lower Beirut", "Upper Beirut", "Lower Sidon", "Upper Sidon"]
    payload["region_coverage_plan"] = [
        {"region_name": "Beirut", "coverage_actions": ["Search A"]},
        {"region_name": "Sidon", "coverage_actions": ["Search B"]},
    ]
    blueprint.structured_blueprint = payload
    await db.commit()
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    frozen_before = copy.deepcopy(execution.frozen_execution_plan_json)
    hash_before = execution.execution_plan_hash
    analysis = ClarificationPolicyService().analyze(
        FrozenExecutionPlan.model_validate(execution.frozen_execution_plan_json)
    )
    assert analysis.human_review_findings == []
    assert len(analysis.safe_candidates) >= 2
    assert all(len(item.allowed_values) >= 2 for item in analysis.safe_candidates)
    first_id = analysis.safe_candidates[0].clarification_id
    second_id = analysis.safe_candidates[1].clarification_id

    call_count = {"n": 0}

    @dataclass
    class CountingProvider:
        calls: list[ClarificationProviderRequest] = field(default_factory=list)

        async def clarify(self, request: ClarificationProviderRequest):
            self.calls.append(request)
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ClarificationProviderResponse(
                    clarification_id=request.clarification_id,
                    decision=ClarificationDecision.RESOLVED,
                    selected_value=request.allowed_values[0],
                    reason="first",
                    confidence=1.0,
                )
            raise ClarificationProviderError("network", "transient", retryable=True)

    provider = CountingProvider()
    monkeypatch.setattr(
        "app.services.scraping.clarification_orchestrator.get_settings",
        lambda: Settings(
            openrouter_api_key="test-key",
            openrouter_scraper_clarification_model="openai/test-luna-slug",
            openrouter_scraper_clarification_max_attempts=1,
        ),
    )
    result = await ClarificationOrchestrator(provider=provider).run(db, execution)
    assert result.status == ClarificationStatus.FAILED
    await db.refresh(execution)
    decisions = execution.clarification_decisions_json or []
    assert any(item.get("clarification_id") == first_id for item in decisions)
    assert execution.frozen_execution_plan_json == frozen_before
    assert execution.execution_plan_hash == hash_before

    execution.clarification_status = ClarificationStatus.IN_PROGRESS.value
    execution.status = ScrapingExecutionStatus.RUNNING
    execution.resolved_execution_plan_json = None
    execution.resolved_execution_plan_hash = None
    execution.clarification_error_code = None
    await db.commit()
    healthy = RecordingClarificationProvider()
    resumed = await ClarificationOrchestrator(provider=healthy).run(db, execution)
    assert resumed.status == ClarificationStatus.COMPLETED
    assert all(call.clarification_id != first_id for call in healthy.calls)
    assert any(call.clarification_id == second_id for call in healthy.calls)
    await db.refresh(execution)
    assert execution.frozen_execution_plan_json == frozen_before
    assert execution.execution_plan_hash == hash_before
    applied = execution.resolved_execution_plan_json["applied_clarification_ids"]
    assert first_id in applied
    assert second_id in applied
    assert {item.get("clarification_id") for item in (execution.clarification_decisions_json or [])} >= {
        first_id,
        second_id,
    }


@pytest.mark.asyncio
async def test_provider_failure_stores_safe_failure(db: AsyncSession, auth, monkeypatch) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    payload = lebanon_structured_blueprint()
    payload["regions"] = ["Lower Beirut", "Upper Beirut"]
    payload["region_coverage_plan"] = [
        {"region_name": "Beirut", "coverage_actions": ["Search registry"]}
    ]
    blueprint.structured_blueprint = payload
    await db.commit()
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    provider = RecordingClarificationProvider(
        raise_error=ClarificationProviderError(
            "authentication", "Clarification provider request failed.", retryable=False
        )
    )
    result = await ClarificationOrchestrator(provider=provider).run(db, execution)
    assert result.status == ClarificationStatus.FAILED
    await db.refresh(execution)
    assert execution.clarification_status == ClarificationStatus.FAILED.value
    assert execution.clarification_error_code == "authentication"
    assert execution.resolved_execution_plan_json is None
    meta = execution.clarification_provider_metadata_json or {}
    assert "api_key" not in str(meta).casefold()


@pytest.mark.asyncio
async def test_pause_and_cancel_checked_before_provider_calls(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    payload = lebanon_structured_blueprint()
    payload["regions"] = ["Lower Beirut", "Upper Beirut"]
    payload["region_coverage_plan"] = [
        {"region_name": "Beirut", "coverage_actions": ["Search registry"]}
    ]
    blueprint.structured_blueprint = payload
    await db.commit()
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    double = RecordingClarificationProvider()

    async def pause_interrupt(_db, _execution) -> bool:
        return True

    result = await ClarificationOrchestrator(provider=double).run(
        db, execution, check_interrupt=pause_interrupt
    )
    assert result.continue_campaign is False
    assert double.calls == []


@pytest.mark.asyncio
async def test_free_form_strings_do_not_trigger_provider() -> None:
    payload = lebanon_structured_blueprint()
    payload["weak_areas"] = ["Unindexed sites"]
    payload["human_review_questions"] = ["Review borderline providers."]
    plan = BlueprintExecutionPlanService().compile(
        mission_id="mission-1",
        blueprint_id="blueprint-1",
        blueprint_version=1,
        mission_country=MissionCountryIdentity(
            country_code="LB",
            country_name="Lebanon",
            country_iso3="LBN",
            continent="Asia",
        ),
        structured_blueprint=payload,
    ).frozen_execution_plan
    analysis = ClarificationPolicyService().analyze(plan)
    assert analysis.safe_candidates == []
    assert any("Unindexed sites" in note.text for note in analysis.informational_notes)


@pytest.mark.asyncio
async def test_historical_null_step1_campaign_skips_clarification(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    execution.frozen_execution_plan_json = None
    execution.execution_plan_hash = None
    execution.execution_plan_schema_version = None
    execution.blueprint_snapshot_json = None
    execution.clarification_status = None
    await db.commit()

    def unexpected_factory(*_args, **_kwargs):
        raise AssertionError("Historical null Step 1 must not create a provider.")

    monkeypatch.setattr(
        "app.services.scraping.clarification_orchestrator.build_clarification_provider",
        unexpected_factory,
    )
    result = await ClarificationOrchestrator().run(db, execution)
    assert result.status == ClarificationStatus.NOT_REQUIRED
    assert result.continue_campaign is True


@pytest.mark.asyncio
async def test_api_summary_exposes_clarification_metadata_only(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    await _patch_enqueue(monkeypatch)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    await ClarificationOrchestrator().run(db, execution)
    detail = await execution_service.get_mission_campaign_detail(
        db, auth, mission.id, summary.id
    )
    assert detail.execution.clarification_status == ClarificationStatus.NOT_REQUIRED.value
    assert detail.execution.clarification_required_count == 0
    assert detail.execution.resolved_execution_plan_hash
    dumped = detail.execution.model_dump()
    assert "clarification_requests_json" not in dumped
    assert "clarification_model_slug_snapshot" not in dumped
    assert "frozen_execution_plan_json" not in dumped


def test_build_clarification_provider_never_returns_fake() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )
    provider = build_clarification_provider(settings)
    assert type(provider) is OpenRouterClarificationProvider
    assert "Fake" not in type(provider).__name__
