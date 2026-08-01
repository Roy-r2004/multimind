"""Focused coverage for newly instrumented OpenRouter cost paths."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import valid_blueprint
from sqlalchemy import func, select

from app.core.dependencies import AuthContext
from app.db.models import CostRecord, CostRecordStatus, UsageKind
from app.llm.providers import LLMResponse
from app.schemas.api import SourceDiscoveryContext
from app.scraping.blueprint_orchestrator import BlueprintOrchestrator
from app.services.cost_recorder import cost_recorder
from app.services.scraping.cost_tracking import (
    record_scraping_llm,
    resolve_mission_owner_user_id,
)
from app.services.scraping.facility_ai_cleanup_service import FacilityAiCleanupService
from app.services.scraping.maps_census_service import MapsCensusService
from app.services.scraping.maps_grid_planner import MapsGridPlanner
from app.services.scraping.official_source_seed_service import OfficialSourceSeedPlanner
from app.services.scraping.source_discovery_service import SourceDiscoveryQueryPlanner
from app.services.scraping.team_planner_service import TeamPlannerService
from app.services.usage_service import usage_service


def _llm(text: str = "{}", cost: float = 0.05) -> LLMResponse:
    return LLMResponse(
        text=text,
        tokens_input=12,
        tokens_output=8,
        cost_usd=cost,
        raw={"id": "gen-test"},
    )


@pytest.mark.asyncio
async def test_document_suggest_and_lesson_discuss_labels_and_totals(db, auth: AuthContext):
    await cost_recorder.record_llm_success(
        db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        model_id="gpt-4.1",
        kind=UsageKind.DOCUMENT,
        operation="document_suggest",
        idempotency_key="document-suggest:turn-1",
        response=_llm(cost=0.11),
    )
    await cost_recorder.record_llm_success(
        db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        model_id="gpt-4.1",
        kind=UsageKind.LESSON,
        operation="lesson_discuss",
        idempotency_key="lesson-discuss:turn-2:gpt-4.1",
        response=_llm(cost=0.22),
    )
    await db.commit()

    # Idempotent retry must not duplicate.
    await cost_recorder.record_llm_success(
        db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        model_id="gpt-4.1",
        kind=UsageKind.DOCUMENT,
        operation="document_suggest",
        idempotency_key="document-suggest:turn-1",
        response=_llm(cost=0.11),
    )
    await db.commit()

    summary = await usage_service.user_summary(db, auth)
    assert summary["all_time_usd"] == pytest.approx(0.33)
    rows = (
        await db.execute(
            select(CostRecord).where(CostRecord.user_id == auth.user.id)
        )
    ).scalars().all()
    ops = {r.operation for r in rows}
    assert "document_suggest" in ops
    assert "lesson_discuss" in ops
    assert all("prompt" not in (r.metadata_ or {}) for r in rows)


@pytest.mark.asyncio
async def test_blueprint_and_planner_record_once(db, auth: AuthContext, monkeypatch):
    orchestrator = BlueprintOrchestrator()
    calls = {"n": 0}

    class Provider:
        async def complete(self, **kwargs):
            calls["n"] += 1
            return _llm(text=__import__("json").dumps(valid_blueprint()))

    monkeypatch.setattr(orchestrator._providers, "get_provider", lambda _p: Provider())
    mission = SimpleNamespace(
        id="mission-bp-1",
        org_id=auth.org_id,
        created_by=auth.user.id,
        title="Mission",
        original_prompt="Find facilities",
    )
    model_set = SimpleNamespace(
        models=["gpt-4.1"],
        verdict_model="gpt-4.1",
        name="Research",
        slug="research-set",
    )
    await orchestrator.generate(
        mission,
        model_set,
        db=db,
        blueprint_id="bp-1",
        user_id=auth.user.id,
    )
    await db.commit()

    bp_rows = (
        await db.execute(
            select(CostRecord).where(CostRecord.operation == "blueprint_research")
        )
    ).scalars().all()
    assert len(bp_rows) == 5
    structure = (
        await db.execute(
            select(CostRecord).where(CostRecord.operation == "blueprint_structure")
        )
    ).scalars().all()
    assert len(structure) == 1
    assert calls["n"] >= 6

    # Retry same keys — no duplicates.
    await record_scraping_llm(
        db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        mission_id=mission.id,
        model_id="gpt-4.1",
        kind=UsageKind.BLUEPRINT,
        operation="blueprint_structure",
        idempotency_key="blueprint-structure:bp-1",
        response=_llm(),
    )
    await db.commit()
    structure2 = (
        await db.execute(
            select(CostRecord).where(CostRecord.operation == "blueprint_structure")
        )
    ).scalars().all()
    assert len(structure2) == 1

    planner = TeamPlannerService()

    class PlannerProvider:
        async def complete(self, **kwargs):
            return _llm(
                text=(
                    '{"recommended_agent_count":2,"rationale":"ok","agents":['
                    '{"sequence":1,"name":"A","role":"source_discovery","purpose":"p",'
                    '"instructions":"i","assigned_scope":{},"model_id":"gpt-4.1","depends_on":[]},'
                    '{"sequence":2,"name":"B","role":"verification","purpose":"p",'
                    '"instructions":"i","assigned_scope":{},"model_id":"gpt-4.1","depends_on":[1]}'
                    "]}"
                )
            )

    monkeypatch.setattr(
        "app.services.scraping.team_planner_service.get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _p: PlannerProvider()),
    )
    blueprint = SimpleNamespace(id="bp-plan", version=1, blueprint_json={"x": 1})
    await planner.plan_team(
        mission=mission,
        blueprint=blueprint,
        model_set=model_set,
        db=db,
        run_id="run-1",
        user_id=auth.user.id,
    )
    await db.commit()
    plan_rows = (
        await db.execute(select(CostRecord).where(CostRecord.operation == "team_planner"))
    ).scalars().all()
    assert len(plan_rows) == 1
    assert plan_rows[0].kind == UsageKind.PLANNER

    summary = await usage_service.user_summary(db, auth)
    assert summary["all_time_usd"] > 0


@pytest.mark.asyncio
async def test_discovery_maps_cleanup_and_ownership(db, auth: AuthContext, monkeypatch):
    context = SourceDiscoveryContext(
        organization_id=auth.org_id,
        execution_id="exec-1",
        coverage_cell_id="cell-1",
        country_code="US",
        country_name="United States",
        region_name="California",
        language_code="en",
        language_name="English",
        source_category="directory",
        mission_goal="Find rehab facilities",
    )

    class Provider:
        async def complete(self, **kwargs):
            return _llm(text='{"queries":[{"query":"rehab california","language_code":"en","purpose":"find"}]}')

    monkeypatch.setattr(
        "app.services.scraping.source_discovery_service.get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _p: Provider()),
    )
    planner = SourceDiscoveryQueryPlanner()
    await planner.plan_queries(
        context, db=db, user_id=auth.user.id, mission_id="mission-1"
    )
    await db.commit()
    discovery = (
        await db.execute(select(CostRecord).where(CostRecord.operation == "discovery_plan"))
    ).scalars().all()
    assert len(discovery) == 1
    assert discovery[0].user_id == auth.user.id

    # Serper/Places are not recorded — only LLM path above.

    class SeedProvider:
        async def complete(self, **kwargs):
            return _llm(text='{"sources":[]}')

    monkeypatch.setattr(
        "app.services.scraping.official_source_seed_service.get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _p: SeedProvider()),
    )
    await OfficialSourceSeedPlanner().plan(
        country_code="US",
        country_name="United States",
        mission_goal="Find",
        requested_fields=["name"],
        registry_hints=[],
        source_strategy=[],
        db=db,
        org_id=auth.org_id,
        user_id=None,
        mission_id="mission-1",
        execution_id="exec-1",
    )
    await db.commit()
    seed = (
        await db.execute(
            select(CostRecord).where(CostRecord.operation == "official_source_seed")
        )
    ).scalars().all()
    assert len(seed) == 1
    assert seed[0].user_id is None  # ambiguous / unattributed stays Admin-only

    extras = await usage_service.org_extras(db, auth)
    assert extras["all_time_usd"] >= seed[0].cost_usd

    class GridProvider:
        async def complete(self, **kwargs):
            return _llm(
                text='{"cells":[{"region_name":"CA","city_name":"LA","query_text":"rehab"}]}'
            )

    monkeypatch.setattr(
        "app.services.scraping.maps_grid_planner.get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _p: GridProvider()),
    )
    await MapsGridPlanner().plan(
        country_code="US",
        country_name="United States",
        max_cells=5,
        db=db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        run_id="maps-run-1",
    )
    await db.commit()

    service = MapsCensusService()
    decisions = await service._classify_batch(
        provider=SimpleNamespace(complete=AsyncMock(return_value=_llm(text='{"decisions":[]}'))),
        model_slug="openai/gpt-4.1",
        model_id="gpt-4.1",
        country_code="US",
        country_name="United States",
        payloads=[{"place_id": "p1", "name": "Clinic", "place_types": [], "address": "x"}],
        org_id=auth.org_id,
        user_id=auth.user.id,
        run_id="maps-run-1",
        batch_index=0,
        db=db,
    )
    await db.commit()
    assert isinstance(decisions, list)
    classify = (
        await db.execute(select(CostRecord).where(CostRecord.operation == "maps_classify"))
    ).scalars().all()
    assert len(classify) == 1

    # Failed provider call after attempt — cost helper must be invoked as failed.
    recorded: list[dict] = []

    async def capture_record(db_arg, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        "app.services.scraping.facility_ai_cleanup_service.record_scraping_llm",
        capture_record,
    )
    await FacilityAiCleanupService()._plan_batch(
        provider=SimpleNamespace(
            complete=AsyncMock(side_effect=RuntimeError("provider down"))
        ),
        model_slug="openai/gpt-4.1",
        country_code="US",
        country_name="United States",
        mission_goal="Find",
        facility_ids=["f1"],
        facility_payloads=[{"id": "f1"}],
        org_id=auth.org_id,
        user_id=auth.user.id,
        mission_id="mission-1",
        execution_id="exec-1",
        batch_index=0,
        model_id="gpt-4.1",
    )
    assert any(r.get("failed") and r.get("operation") == "facility_cleanup" for r in recorded)

    owner, mission_id = await resolve_mission_owner_user_id(db, execution_id="missing")
    assert owner is None
    assert mission_id is None


@pytest.mark.asyncio
async def test_failed_llm_records_and_friendly_ops_do_not_store_content(db, auth: AuthContext):
    await cost_recorder.record_llm_failure(
        db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        model_id="gpt-4.1",
        kind=UsageKind.DOCUMENT,
        operation="document_suggest",
        idempotency_key="document-suggest:turn-x:failed",
        error_code="document_suggest_failed",
        metadata={"prompt": "SECRET", "chunk_id": "ok"},
    )
    await db.commit()
    row = (
        await db.execute(
            select(CostRecord).where(
                CostRecord.idempotency_key == "document-suggest:turn-x:failed"
            )
        )
    ).scalar_one()
    assert row.status == CostRecordStatus.FAILED.value
    assert row.metadata_ is not None
    assert "prompt" not in row.metadata_
    assert row.metadata_.get("chunk_id") == "ok"

    count = await db.scalar(select(func.count()).select_from(CostRecord))
    assert count >= 1
