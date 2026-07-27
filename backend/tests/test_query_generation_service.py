"""Focused Step 3B deterministic query-generation coverage (no providers)."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_country_blueprint_foundation import valid_structured_blueprint_v2
from test_mission_campaign_lifecycle import _approved_mission_with_team_plan

from app.core.dependencies import AuthContext
from app.db.models import (
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingSourceDiscoveryQuery,
    SourceDiscoveryQueryStatus,
)
from app.schemas.scraping_clarification import (
    ClarificationStatus,
    ResolvedExecutionPlanEnvelope,
)
from app.schemas.scraping_execution_plan import FrozenExecutionPlanV2
from app.services.scraping import mission_campaign_mock_worker
from app.services.scraping.blueprint_execution_plan_service import (
    BlueprintExecutionPlanService,
    MissionCountryIdentity,
    sha256_hex,
)
from app.services.scraping.execution_service import execution_service
from app.services.scraping.query_generation_service import (
    PURPOSE_COMMERCIAL,
    PURPOSE_REGULATORY,
    PURPOSE_SEED,
    PRIORITY_SEED,
    SOURCE_CATEGORIES,
    QueryGenerationError,
    compose_expansion_query_text,
    compute_query_job_fingerprint,
    fingerprint_payload,
    generate_query_job_specs,
    match_seed_language,
    normalize_display_text,
    normalize_identity_text,
    public_query_metadata,
    query_generation_service,
    select_authoritative_plan,
    validate_job_spec_bounds,
)
from app.services.scraping.source_discovery_service import source_discovery_service

AUSTRIA = MissionCountryIdentity(
    country_code="AT",
    country_name="Austria",
    country_iso3="AUT",
    continent="Europe",
)


def _compile_v2(payload: dict | None = None) -> FrozenExecutionPlanV2:
    compiled = BlueprintExecutionPlanService().compile(
        mission_id="mission-1",
        blueprint_id="blueprint-1",
        blueprint_version=3,
        mission_country=AUSTRIA,
        structured_blueprint=payload or valid_structured_blueprint_v2(),
        require_v2=True,
    )
    plan = compiled.frozen_execution_plan
    assert isinstance(plan, FrozenExecutionPlanV2)
    return plan


def _expected_expansion_count(plan: FrozenExecutionPlanV2) -> int:
    scopes = 1 + len(plan.regions) + len(plan.important_cities)
    return (
        len(SOURCE_CATEGORIES)
        * scopes
        * len(plan.language_profiles)
        * len(plan.local_terminology)
        * len(plan.inpatient_residential_terminology)
        * len(plan.private_paid_terminology)
        * len(plan.addiction_categories)
    )


def test_compose_and_normalize_are_deterministic() -> None:
    text = compose_expansion_query_text(
        private_paid=" privat ",
        inpatient_residential="stationär",
        addiction_category="alcohol",
        local_terminology="Suchtbehandlung",
        geographic_token="Vienna",
        country_name="Austria",
    )
    assert text == "privat stationär alcohol Suchtbehandlung Vienna Austria"
    assert normalize_identity_text("  Café  ") == "café"
    assert normalize_display_text("  Café  Bar ") == "Café Bar"


def test_generate_specs_ordering_cartesian_seeds_and_priorities() -> None:
    plan = _compile_v2()
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    specs = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)

    seed_count = len(plan.query_seed_plan.seeds) * len(SOURCE_CATEGORIES)
    assert len(specs) == seed_count + _expected_expansion_count(plan)
    assert all(spec.discovery_round == 1 for spec in specs)
    assert [spec.generation_ordinal for spec in specs] == list(range(len(specs)))
    assert all(s.purpose == PURPOSE_SEED for s in specs[:seed_count])
    assert all(s.priority == 100 for s in specs[:seed_count])
    assert specs[0].scope_level == "countrywide"
    assert specs[0].region_name is None
    assert specs[0].important_city is None

    expansions = specs[seed_count:]
    assert expansions[0].source_category == "regulatory"
    assert expansions[0].purpose == PURPOSE_REGULATORY
    assert expansions[0].priority == 200
    city_jobs = [s for s in expansions if s.scope_level == "city"]
    assert city_jobs
    assert all(s.region_name == s.important_city or s.region_name for s in city_jobs)
    for job in city_jobs:
        assert job.region_name is not None
        assert job.important_city is not None
        parents = {c.region_name for c in plan.important_cities if c.name == job.important_city}
        assert job.region_name in parents

    commercial = [s for s in expansions if s.source_category == "commercial"]
    assert commercial
    assert all(s.purpose == PURPOSE_COMMERCIAL for s in commercial)
    assert {s.priority for s in commercial} <= {300, 310, 320}


def test_seed_wins_semantic_collision_and_identity_axes_remain_distinct() -> None:
    """Seed+expansion same semantic identity → one job; seed wins. Other axes stay distinct."""
    payload = valid_structured_blueprint_v2()
    payload["private_paid_terminology"] = ["private"]
    payload["inpatient_residential_terminology"] = ["inpatient"]
    payload["addiction_categories"] = ["addiction"]
    payload["local_terminology"] = ["rehabilitation"]
    payload["regions"] = ["Vienna"]
    payload["important_cities"] = [{"name": "Vienna", "region_name": "Vienna"}]
    payload["query_matrix"] = [
        {
            "query": "private inpatient addiction rehabilitation Austria",
            "language": "German",
            "purpose": "seed purpose free text",
        }
    ]
    plan = _compile_v2(payload)
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    specs = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    matching = [
        s
        for s in specs
        if normalize_identity_text(s.query_text)
        == normalize_identity_text("private inpatient addiction rehabilitation Austria")
        and s.source_category == "regulatory"
        and s.scope_level == "countrywide"
    ]
    assert len(matching) == 1
    winner = matching[0]
    assert winner.purpose == PURPOSE_SEED
    assert winner.priority == PRIORITY_SEED
    assert winner.metadata_json.get("generation_source") == "seed"

    # Different language / scope / category / round / plan_hash remain distinct.
    base_fp = winner.query_job_fingerprint
    assert (
        compute_query_job_fingerprint(
            plan_hash_snapshot=plan_hash,
            discovery_round=1,
            query_text=winner.query_text,
            language_code="en",
            language_name="English",
            scope_level="countrywide",
            region_name=None,
            important_city=None,
            source_category="regulatory",
        )
        != base_fp
    )
    assert (
        compute_query_job_fingerprint(
            plan_hash_snapshot=plan_hash,
            discovery_round=1,
            query_text=winner.query_text,
            language_code=winner.language_code,
            language_name=winner.language_name,
            scope_level="region",
            region_name="Vienna",
            important_city=None,
            source_category="regulatory",
        )
        != base_fp
    )
    assert (
        compute_query_job_fingerprint(
            plan_hash_snapshot=plan_hash,
            discovery_round=1,
            query_text=winner.query_text,
            language_code=winner.language_code,
            language_name=winner.language_name,
            scope_level="countrywide",
            region_name=None,
            important_city=None,
            source_category="commercial",
        )
        != base_fp
    )
    assert (
        compute_query_job_fingerprint(
            plan_hash_snapshot=plan_hash,
            discovery_round=2,
            query_text=winner.query_text,
            language_code=winner.language_code,
            language_name=winner.language_name,
            scope_level="countrywide",
            region_name=None,
            important_city=None,
            source_category="regulatory",
        )
        != base_fp
    )
    assert (
        compute_query_job_fingerprint(
            plan_hash_snapshot="f" * 64,
            discovery_round=1,
            query_text=winner.query_text,
            language_code=winner.language_code,
            language_name=winner.language_name,
            scope_level="countrywide",
            region_name=None,
            important_city=None,
            source_category="regulatory",
        )
        != base_fp
    )


def test_fingerprint_excludes_purpose_and_purpose_still_persisted() -> None:
    plan = _compile_v2()
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    specs = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    assert specs
    payload = fingerprint_payload(
        plan_hash_snapshot=plan_hash,
        discovery_round=1,
        query_text=specs[0].query_text,
        language_code=specs[0].language_code,
        language_name=specs[0].language_name,
        scope_level=specs[0].scope_level,
        region_name=specs[0].region_name,
        important_city=specs[0].important_city,
        source_category=specs[0].source_category,
    )
    assert set(payload.keys()) == {
        "identity_schema_version",
        "plan_hash_snapshot",
        "discovery_round",
        "query_text",
        "language_code",
        "language_name",
        "scope_level",
        "region_name",
        "important_city",
        "source_category",
    }
    assert "purpose" not in payload
    assert "generation_source" not in payload
    assert "priority" not in payload
    assert "generation_ordinal" not in payload
    assert "provider" not in payload
    assert "requested_at" not in payload
    assert "id" not in payload
    # Purpose is still carried on the job spec for persistence.
    assert all(s.purpose in {PURPOSE_SEED, PURPOSE_REGULATORY, PURPOSE_COMMERCIAL} for s in specs)
    seeds = [s for s in specs if s.purpose == PURPOSE_SEED]
    assert seeds
    assert all(s.priority == PRIORITY_SEED for s in seeds)


def test_deterministic_ordinals_after_dedupe() -> None:
    payload = valid_structured_blueprint_v2()
    payload["private_paid_terminology"] = ["private"]
    payload["inpatient_residential_terminology"] = ["inpatient"]
    payload["addiction_categories"] = ["addiction"]
    payload["local_terminology"] = ["rehabilitation"]
    payload["regions"] = ["Vienna"]
    payload["important_cities"] = [{"name": "Vienna", "region_name": "Vienna"}]
    payload["query_matrix"] = [
        {
            "query": "private inpatient addiction rehabilitation Austria",
            "language": "German",
            "purpose": "seed",
        }
    ]
    plan = _compile_v2(payload)
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    specs = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    assert [s.generation_ordinal for s in specs] == list(range(len(specs)))
    fingerprints = [s.query_job_fingerprint for s in specs]
    assert len(fingerprints) == len(set(fingerprints))


def test_oversized_bounded_fields_fail_closed() -> None:
    plan = _compile_v2()
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    oversized = plan.model_copy(
        update={"country": plan.country.model_copy(update={"country_name": "X" * 121})}
    )
    with pytest.raises(QueryGenerationError) as exc_info:
        generate_query_job_specs(oversized, plan_hash=plan_hash, discovery_round=1)
    assert exc_info.value.code == "field_too_long"
    assert "X" * 121 not in exc_info.value.message

    region_oversized = plan.model_copy(update={"regions": ["R" * 161]})
    with pytest.raises(QueryGenerationError) as region_exc:
        generate_query_job_specs(region_oversized, plan_hash=plan_hash, discovery_round=1)
    assert region_exc.value.code == "field_too_long"
    assert "R" * 161 not in region_exc.value.message


def test_query_text_text_column_accepts_over_512_chars() -> None:
    payload = valid_structured_blueprint_v2()
    long_query = ("private inpatient addiction rehabilitation Austria " * 20).strip()
    assert len(long_query) > 512
    payload["private_paid_terminology"] = ["private"]
    payload["inpatient_residential_terminology"] = ["inpatient"]
    payload["addiction_categories"] = ["addiction"]
    payload["local_terminology"] = ["rehabilitation"]
    payload["regions"] = ["Vienna"]
    payload["important_cities"] = [{"name": "Vienna", "region_name": "Vienna"}]
    payload["query_matrix"] = [
        {"query": long_query, "language": "German", "purpose": "long seed"}
    ]
    plan = _compile_v2(payload)
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    specs = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    long_specs = [s for s in specs if len(s.query_text) > 512]
    assert long_specs
    for spec in long_specs:
        validate_job_spec_bounds(spec)


def test_ambiguous_seed_language_matching() -> None:
    from app.schemas.scraping_execution_plan import FrozenLanguageProfile

    plan = _compile_v2()
    plan_hash = sha256_hex(plan.model_dump(mode="json"))

    # Unmatched
    unmatched = plan.model_copy(
        update={
            "query_seed_plan": plan.query_seed_plan.model_copy(
                update={
                    "seeds": [
                        plan.query_seed_plan.seeds[0].model_copy(update={"language": "Klingon"})
                    ]
                }
            )
        }
    )
    with pytest.raises(QueryGenerationError) as unmatched_exc:
        generate_query_job_specs(unmatched, plan_hash=plan_hash, discovery_round=1)
    assert unmatched_exc.value.code == "seed_language_unmatched"

    # Duplicate name → ambiguous
    dup_name = plan.model_copy(
        update={
            "language_profiles": [
                FrozenLanguageProfile(name="German", code="de", script="Latn"),
                FrozenLanguageProfile(name="German", code="de-AT", script="Latn"),
            ]
        }
    )
    with pytest.raises(QueryGenerationError) as name_exc:
        generate_query_job_specs(dup_name, plan_hash=plan_hash, discovery_round=1)
    assert name_exc.value.code == "seed_language_ambiguous"

    # Duplicate code match via seed language "de"
    dup_code = plan.model_copy(
        update={
            "language_profiles": [
                FrozenLanguageProfile(name="German", code="de", script="Latn"),
                FrozenLanguageProfile(name="Deutsch", code="de", script="Latn"),
            ],
            "query_seed_plan": plan.query_seed_plan.model_copy(
                update={
                    "seeds": [
                        plan.query_seed_plan.seeds[0].model_copy(update={"language": "de"})
                    ]
                }
            ),
        }
    )
    with pytest.raises(QueryGenerationError) as code_exc:
        generate_query_job_specs(dup_code, plan_hash=plan_hash, discovery_round=1)
    assert code_exc.value.code == "seed_language_ambiguous"

    # Unique name and unique code succeed
    profile = match_seed_language("German", list(plan.language_profiles))
    assert profile.name == "German"
    by_code = match_seed_language("de", list(plan.language_profiles))
    assert by_code.code == "de"
    ok = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    assert ok


def test_public_query_metadata_secrecy() -> None:
    plan_backed = ScrapingSourceDiscoveryQuery(
        organization_id="org",
        country_code="AT",
        country_name="Austria",
        language_code="de",
        language_name="German",
        source_category="regulatory",
        query_text="seed",
        status=SourceDiscoveryQueryStatus.PENDING,
        purpose=PURPOSE_SEED,
        priority=100,
        discovery_round=1,
        generation_ordinal=0,
        scope_level="countrywide",
        query_job_fingerprint="a" * 64,
        plan_hash_snapshot="b" * 64,
        metadata_json={"generation_source": "seed", "seed_index": 0, "axes": {"x": 1}},
    )
    assert public_query_metadata(plan_backed) == {}

    legacy = ScrapingSourceDiscoveryQuery(
        organization_id="org",
        country_code="AT",
        country_name="Austria",
        region_name="Vienna",
        language_code="de",
        language_name="German",
        source_category="regulatory",
        query_text="legacy",
        provider="serper",
        status=SourceDiscoveryQueryStatus.SUCCEEDED,
        purpose="legacy_source_discovery",
        priority=500,
        discovery_round=1,
        generation_ordinal=0,
        scope_level="region",
        query_job_fingerprint=None,
        plan_hash_snapshot=None,
        metadata_json={"purpose": "from-metadata", "generation_source": "legacy_planner"},
    )
    assert public_query_metadata(legacy) == {
        "purpose": "from-metadata",
        "generation_source": "legacy_planner",
    }


def test_duplicate_identity_within_generation_collapses() -> None:
    plan = _compile_v2()
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    specs = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    fingerprints = [s.query_job_fingerprint for s in specs]
    assert len(fingerprints) == len(set(fingerprints))


def test_later_round_allows_same_visible_query() -> None:
    plan = _compile_v2()
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    round1 = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    round2 = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=2)
    assert len(round1) == len(round2)
    assert {s.query_job_fingerprint for s in round1}.isdisjoint(
        {s.query_job_fingerprint for s in round2}
    )


def test_unmatched_seed_language_fails_closed() -> None:
    payload = valid_structured_blueprint_v2()
    payload["query_matrix"] = [
        {"query": "Austria rehab", "language": "Klingon", "purpose": "discovery"}
    ]
    plan = _compile_v2(payload)
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    with pytest.raises(QueryGenerationError) as exc_info:
        generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    assert exc_info.value.code == "seed_language_unmatched"


def test_plan_selection_completed_and_not_required_and_blocked() -> None:
    plan = _compile_v2()
    frozen_json = plan.model_dump(mode="json")
    frozen_hash = sha256_hex(frozen_json)
    envelope = ResolvedExecutionPlanEnvelope(
        source_execution_plan_hash=frozen_hash,
        applied_clarification_ids=[],
        plan=plan,
    )
    resolved_json = envelope.model_dump(mode="json")
    resolved_hash = sha256_hex(resolved_json)

    completed = ScrapingExecution(
        clarification_status=ClarificationStatus.COMPLETED.value,
        frozen_execution_plan_json=frozen_json,
        execution_plan_hash=frozen_hash,
        resolved_execution_plan_json=resolved_json,
        resolved_execution_plan_hash=resolved_hash,
        execution_plan_schema_version="2",
    )
    selected = select_authoritative_plan(completed)
    assert selected.provenance == "resolved"
    assert selected.plan_hash == resolved_hash
    assert isinstance(selected.plan, FrozenExecutionPlanV2)

    not_required = ScrapingExecution(
        clarification_status=ClarificationStatus.NOT_REQUIRED.value,
        frozen_execution_plan_json=frozen_json,
        execution_plan_hash=frozen_hash,
        execution_plan_schema_version="2",
    )
    selected_frozen = select_authoritative_plan(not_required)
    assert selected_frozen.provenance == "frozen"
    assert selected_frozen.plan_hash == frozen_hash

    for status in (
        ClarificationStatus.PENDING.value,
        ClarificationStatus.IN_PROGRESS.value,
        ClarificationStatus.REQUIRES_HUMAN_REVIEW.value,
        ClarificationStatus.FAILED.value,
        None,
    ):
        blocked = ScrapingExecution(
            clarification_status=status,
            frozen_execution_plan_json=frozen_json,
            execution_plan_hash=frozen_hash,
            execution_plan_schema_version="2",
        )
        with pytest.raises(Exception):
            select_authoritative_plan(blocked)


def test_plan_selection_rejects_hash_mismatch_and_v1() -> None:
    plan = _compile_v2()
    frozen_json = plan.model_dump(mode="json")
    execution = ScrapingExecution(
        clarification_status=ClarificationStatus.NOT_REQUIRED.value,
        frozen_execution_plan_json=frozen_json,
        execution_plan_hash="0" * 64,
        execution_plan_schema_version="2",
    )
    with pytest.raises(QueryGenerationError) as exc_info:
        select_authoritative_plan(execution)
    assert exc_info.value.code == "frozen_plan_hash_mismatch"

    from test_blueprint_execution_plan_v2 import _HISTORICAL_V1_FROZEN_PLAN

    v1_raw = copy.deepcopy(_HISTORICAL_V1_FROZEN_PLAN)
    v1_hash = sha256_hex(v1_raw)
    execution_v1 = ScrapingExecution(
        clarification_status=ClarificationStatus.NOT_REQUIRED.value,
        frozen_execution_plan_json=v1_raw,
        execution_plan_hash=v1_hash,
        execution_plan_schema_version="1",
    )
    with pytest.raises(QueryGenerationError) as v1_exc:
        select_authoritative_plan(execution_v1)
    assert v1_exc.value.code == "unsupported_plan_version"


def test_generation_does_not_mutate_plan() -> None:
    plan = _compile_v2()
    before = copy.deepcopy(plan.model_dump(mode="json"))
    plan_hash = sha256_hex(before)
    generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    assert plan.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_persist_idempotent_partial_and_no_provider_calls(
    db: AsyncSession, auth: AuthContext, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    execution.clarification_status = ClarificationStatus.NOT_REQUIRED.value
    execution.resolved_execution_plan_json = None
    await db.commit()

    discover = AsyncMock()
    monkeypatch.setattr(source_discovery_service, "discover", discover)
    planner = AsyncMock()
    monkeypatch.setattr(source_discovery_service, "planner", planner)

    first = await query_generation_service.generate_for_execution(db, execution, discovery_round=1)
    await db.commit()
    assert first.status == "ok"
    assert first.generated_count == first.total_count > 0
    assert first.existing_count == 0
    assert discover.await_count == 0
    assert planner.plan_queries.await_count == 0 if hasattr(planner, "plan_queries") else True

    rows = (
        await db.execute(
            select(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == execution.id
            )
        )
    ).scalars().all()
    assert len(rows) == first.total_count
    assert all(row.status == SourceDiscoveryQueryStatus.PENDING for row in rows)
    assert all(row.provider is None for row in rows)
    assert all(row.requested_at is None for row in rows)
    assert all(row.query_job_fingerprint for row in rows)
    assert all(row.plan_hash_snapshot == first.plan_hash_snapshot for row in rows)

    # Partial recovery: delete some rows, rerun inserts only missing.
    for row in rows[:3]:
        await db.delete(row)
    await db.commit()
    await db.refresh(execution)
    second = await query_generation_service.generate_for_execution(db, execution, discovery_round=1)
    await db.commit()
    assert second.status == "ok"
    assert second.generated_count == 3
    assert second.total_count == first.total_count

    third = await query_generation_service.generate_for_execution(db, execution, discovery_round=1)
    await db.commit()
    assert third.status == "ok"
    assert third.generated_count == 0
    assert third.existing_count == third.total_count == first.total_count

    # Equivalent row already present: existing rows are not rewritten.
    sample = (
        await db.execute(
            select(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == execution.id
            )
        )
    ).scalars().first()
    assert sample is not None
    original_ordinal = sample.generation_ordinal
    sample.purpose = "mutated_should_stick"
    await db.commit()
    await db.refresh(execution)
    fourth = await query_generation_service.generate_for_execution(db, execution, discovery_round=1)
    await db.commit()
    assert fourth.status == "ok"
    assert fourth.generated_count == 0
    await db.refresh(sample)
    assert sample.purpose == "mutated_should_stick"
    assert sample.generation_ordinal == original_ordinal


@pytest.mark.asyncio
async def test_contamination_and_plan_hash_conflict_fail_closed(
    db: AsyncSession, auth: AuthContext, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    execution.clarification_status = ClarificationStatus.NOT_REQUIRED.value
    await db.commit()

    db.add(
        ScrapingSourceDiscoveryQuery(
            organization_id=execution.organization_id,
            execution_id=execution.id,
            country_code=execution.country_code,
            country_name=execution.country_name,
            region_name="Beirut",
            language_code="en",
            language_name="English",
            source_category="regulatory",
            query_text="legacy contaminant",
            provider="serper",
            status=SourceDiscoveryQueryStatus.SUCCEEDED,
            purpose="legacy_source_discovery",
            priority=500,
            discovery_round=1,
            generation_ordinal=0,
            scope_level="region",
            query_job_fingerprint=None,
            plan_hash_snapshot=None,
        )
    )
    await db.commit()
    await db.refresh(execution)
    contaminated = await query_generation_service.generate_for_execution(
        db, execution, discovery_round=1
    )
    assert contaminated.status == "error"
    assert contaminated.error_code == "legacy_contamination"

    await db.execute(
        delete(ScrapingSourceDiscoveryQuery).where(
            ScrapingSourceDiscoveryQuery.execution_id == execution.id
        )
    )
    await db.commit()
    ok = await query_generation_service.generate_for_execution(db, execution, discovery_round=1)
    await db.commit()
    assert ok.status == "ok"
    row = (
        await db.execute(
            select(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == execution.id
            )
        )
    ).scalars().first()
    assert row is not None
    row.plan_hash_snapshot = "f" * 64
    await db.commit()
    await db.refresh(execution)
    conflict = await query_generation_service.generate_for_execution(db, execution, discovery_round=1)
    assert conflict.status == "error"
    assert conflict.error_code == "plan_hash_conflict"


@pytest.mark.asyncio
async def test_api_response_hides_fingerprints_and_allows_null_provider(
    db: AsyncSession, auth: AuthContext, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    execution.clarification_status = ClarificationStatus.NOT_REQUIRED.value
    await db.commit()
    result = await query_generation_service.generate_for_execution(db, execution, discovery_round=1)
    await db.commit()
    assert result.status == "ok"
    responses = await source_discovery_service.list_queries(db, auth, execution.id, limit=10)
    assert responses
    sample = responses[0]
    assert sample.provider is None
    assert sample.requested_at is None
    assert sample.purpose is not None
    assert sample.priority is not None
    assert sample.discovery_round == 1
    assert sample.scope_level in {"countrywide", "region", "city"}
    dumped = sample.model_dump()
    assert "query_job_fingerprint" not in dumped
    assert "plan_hash_snapshot" not in dumped
    assert sample.metadata_json == {}

    # Legacy rows without fingerprint keep prior metadata payload.
    legacy = ScrapingSourceDiscoveryQuery(
        organization_id=execution.organization_id,
        execution_id=execution.id,
        country_code=execution.country_code,
        country_name=execution.country_name,
        region_name="Beirut",
        language_code="en",
        language_name="English",
        source_category="regulatory",
        query_text="legacy visible",
        provider="serper",
        status=SourceDiscoveryQueryStatus.SUCCEEDED,
        purpose="legacy_source_discovery",
        priority=500,
        discovery_round=1,
        generation_ordinal=0,
        scope_level="region",
        query_job_fingerprint=None,
        plan_hash_snapshot=None,
        metadata_json={"purpose": "kept", "generation_source": "legacy_planner"},
    )
    db.add(legacy)
    await db.commit()
    responses = await source_discovery_service.list_queries(db, auth, execution.id, limit=500)
    legacy_resp = next(r for r in responses if r.query_text == "legacy visible")
    assert legacy_resp.metadata_json == {
        "purpose": "kept",
        "generation_source": "legacy_planner",
    }
    plan_backed = next(r for r in responses if r.query_text != "legacy visible")
    assert plan_backed.metadata_json == {}


@pytest.mark.asyncio
async def test_worker_runs_step3_after_clarification_and_blocks_on_failure(
    db: AsyncSession, auth: AuthContext, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)

    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)
    execution = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert execution is not None
    assert execution.status == ScrapingExecutionStatus.COMPLETED
    count = (
        await db.execute(
            select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == execution.id
            )
        )
    ).scalar_one()
    assert count > 0

    # Failure path blocks later mock stages.
    mission2, blueprint = await _approved_mission_with_team_plan(db, auth)
    summary2 = await execution_service.start_mission_campaign(db, auth, mission2.id)
    execution2 = await db.get(ScrapingExecution, summary2.id)
    assert execution2 is not None

    async def boom(*_args, **_kwargs):
        from app.services.scraping.query_generation_service import QueryGenerationResult

        return QueryGenerationResult(
            status="error",
            execution_id=execution2.id,
            discovery_round=1,
            plan_hash_snapshot=None,
            error_code="forced",
            message="forced",
        )

    monkeypatch.setattr(query_generation_service, "generate_for_execution", boom)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary2.id)
    failed = await db.get(ScrapingExecution, summary2.id, populate_existing=True)
    assert failed is not None
    assert failed.status == ScrapingExecutionStatus.FAILED
    assert failed.error_message == "Deterministic query generation failed."
    assert failed.progress_percent in {0, None} or failed.progress_percent < 20


@pytest.mark.asyncio
async def test_historical_v1_compatible_skips_step3_without_planner(
    db: AsyncSession, auth: AuthContext, monkeypatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    # Simulate unsupported Step 3 schema while remaining worker-readable for mock stages.
    execution.execution_plan_schema_version = "1"
    # Replace frozen plan with a minimal v1-shaped payload already hashed would fail provenance;
    # instead only gate Step 3 via schema version while keeping v2 plan for provenance.
    await db.commit()

    discover = AsyncMock()
    monkeypatch.setattr(source_discovery_service, "discover", discover)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)

    # Provenance validation rejects schema "1" if plan is v2 — expect failed provenance OR
    # if we only change the gate field after claim... worker validates before claim.
    # Reset to queued and use legacy path: clear step1 fields.
    execution.status = ScrapingExecutionStatus.QUEUED
    execution.frozen_execution_plan_json = None
    execution.blueprint_snapshot_json = None
    execution.execution_plan_hash = None
    execution.execution_plan_schema_version = None
    execution.blueprint_version_snapshot = blueprint.version
    await db.commit()

    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)
    done = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert done is not None
    assert done.status == ScrapingExecutionStatus.COMPLETED
    count = (
        await db.execute(
            select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == done.id
            )
        )
    ).scalar_one()
    assert count == 0
    assert discover.await_count == 0


def test_no_campaign_wide_query_cap_in_generator() -> None:
    payload = valid_structured_blueprint_v2()
    payload["regions"] = [f"Region {i}" for i in range(1, 6)]
    payload["important_cities"] = [
        {"name": f"City {i}", "region_name": f"Region {((i - 1) % 5) + 1}"} for i in range(1, 6)
    ]
    payload["local_terminology"] = [f"term{i}" for i in range(1, 4)]
    payload["inpatient_residential_terminology"] = ["inpatient", "residential"]
    payload["private_paid_terminology"] = ["private", "paid"]
    payload["addiction_categories"] = ["alcohol", "opioids", "gambling"]
    plan = _compile_v2(payload)
    plan_hash = sha256_hex(plan.model_dump(mode="json"))
    specs = generate_query_job_specs(plan, plan_hash=plan_hash, discovery_round=1)
    expected = len(plan.query_seed_plan.seeds) * 2 + _expected_expansion_count(plan)
    assert len(specs) == expected
    assert expected > 100
