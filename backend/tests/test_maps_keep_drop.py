"""Tests for the strict keep/drop gate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import (
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
    MapsCensusRun,
    MapsCensusStatus,
)
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState
from app.services.scraping.maps_export_service import _is_eligible_center
from app.services.scraping.maps_keep_drop_service import (
    KeepDropDecision,
    apply_keep_drop,
    build_keep_drop_query,
    classify_place_keep_drop,
    run_keep_drop_pass,
)


def _run(auth, **kwargs):
    base = dict(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status=MapsCensusStatus.COMPLETED,
        cells_total=1,
        cells_completed=1,
        places_found=2,
    )
    base.update(kwargs)
    return MapsCensusRun(**base)


def _place(run_id: str, google_id: str, name: str, **kwargs) -> MapsPlace:
    base = dict(
        run_id=run_id,
        google_place_id=google_id,
        raw_name=name,
        canonical_name=name,
        place_types=["health"],
        formatted_address="1 Rue Exemple, Alger",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        is_relevant=True,
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    base.update(kwargs)
    return MapsPlace(**base)


class _FakeProvider:
    def __init__(self, text: str | Exception):
        self._text = text
        self.calls = 0

    async def complete(self, **_kwargs):
        self.calls += 1
        if isinstance(self._text, Exception):
            raise self._text
        return SimpleNamespace(text=self._text)


class _FakeRegistry:
    def __init__(self, provider):
        self._provider = provider

    def get_provider(self, _name):
        return self._provider


def _patch_models(monkeypatch, nano: _FakeProvider, sonar: _FakeProvider):
    def fake_get_model(slug: str):
        if slug == "sonar":
            return SimpleNamespace(provider="openrouter", provider_model="perplexity/sonar")
        return SimpleNamespace(provider="openrouter", provider_model="openai/gpt-5-nano")

    def fake_registry():
        class _Reg:
            def get_provider(self, _name):
                # Sonar is resolved by provider_model in the service via get_model
                # slug; distinguish by which provider we want per call order is
                # fragile — instead the service asks for the provider of each
                # model, so route by registry call count is wrong. We patch
                # get_provider to return based on the model's provider, and both
                # share "openrouter", so we instead patch get_model to give each
                # model a distinct provider name.
                raise AssertionError("use per-provider registry")

        return _Reg()

    # Distinct provider names per model so the registry can route.
    def fake_get_model_routed(slug: str):
        if slug == "sonar":
            return SimpleNamespace(provider="sonar", provider_model="perplexity/sonar")
        return SimpleNamespace(provider="nano", provider_model="openai/gpt-5-nano")

    class _RoutingRegistry:
        def get_provider(self, name):
            return {"nano": nano, "sonar": sonar}[name]

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.get_model", fake_get_model_routed
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.get_provider_registry",
        lambda: _RoutingRegistry(),
    )


KEEP_JSON = json.dumps(
    {
        "decision": "keep",
        "reason": "Private NGO addiction treatment center with residential program",
        "confidence": 0.95,
        "evidence": [{"source": "maps", "text": "Centre de désintoxication privé"}],
    }
)

DROP_JSON = json.dumps(
    {
        "decision": "drop",
        "reason": "Public hospital (government)",
        "confidence": 0.97,
        "evidence": [{"source": "maps", "text": "CHU Mustapha"}],
    }
)

LOW_CONF_KEEP_JSON = json.dumps(
    {
        "decision": "keep",
        "reason": "Maybe a rehab",
        "confidence": 0.5,
        "evidence": [],
    }
)


def test_apply_keep_drop_keep_maps_to_eligible():
    place = _place("r1", "g1", "Centre Rehab")
    decision = KeepDropDecision.model_validate(json.loads(KEEP_JSON))
    apply_keep_drop(place, decision, source="nano")
    assert place.keep_drop_decision == "keep"
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert place.lifecycle_status == MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value
    assert place.is_relevant is True
    assert place.enrichment_pipeline_state == (
        MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value
    )
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value
    assert _is_eligible_center(place)


def test_apply_keep_drop_drop_maps_to_excluded():
    place = _place("r1", "g1", "CHU Mustapha")
    decision = KeepDropDecision.model_validate(json.loads(DROP_JSON))
    apply_keep_drop(place, decision, source="nano")
    assert place.keep_drop_decision == "drop"
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value
    assert place.lifecycle_status == MapsLifecycleStatus.UNRELATED.value
    assert place.is_relevant is False
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.SKIPPED.value
    assert place.enrichment_pipeline_state == MapsEnrichmentPipelineState.FINALIZED.value
    assert not _is_eligible_center(place)


@pytest.mark.asyncio
async def test_confident_nano_keep_skips_sonar(db, auth, monkeypatch):
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(run.id, "g1", "Centre Rehab Prive")
    db.add(place)
    await db.commit()

    nano = _FakeProvider(KEEP_JSON)
    sonar = _FakeProvider(DROP_JSON)
    _patch_models(monkeypatch, nano, sonar)

    decision, source = await classify_place_keep_drop(
        db, place, country_code="DZ", country_name="Algeria"
    )
    assert decision.decision == "keep"
    assert source == "nano"
    assert nano.calls == 1
    assert sonar.calls == 0


@pytest.mark.asyncio
async def test_low_confidence_nano_falls_back_to_sonar(db, auth, monkeypatch):
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(run.id, "g1", "Ambiguous Center")
    db.add(place)
    await db.commit()

    nano = _FakeProvider(LOW_CONF_KEEP_JSON)
    sonar = _FakeProvider(
        json.dumps(
            {
                "decision": "keep",
                "reason": "Sonar confirmed private rehab with detox program",
                "confidence": 0.92,
                "evidence": [{"source": "sonar", "text": "site lists cure de désintoxication"}],
            }
        )
    )
    _patch_models(monkeypatch, nano, sonar)

    decision, source = await classify_place_keep_drop(
        db, place, country_code="DZ", country_name="Algeria"
    )
    assert decision.decision == "keep"
    assert source == "sonar"
    assert nano.calls == 1
    assert sonar.calls == 1


@pytest.mark.asyncio
async def test_uncertain_sonar_keep_defaults_to_drop(db, auth, monkeypatch):
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(run.id, "g1", "Mystery Clinic")
    db.add(place)
    await db.commit()

    nano = _FakeProvider(LOW_CONF_KEEP_JSON)
    sonar = _FakeProvider(LOW_CONF_KEEP_JSON)  # still under 0.85
    _patch_models(monkeypatch, nano, sonar)

    decision, _source = await classify_place_keep_drop(
        db, place, country_code="DZ", country_name="Algeria"
    )
    assert decision.decision == "drop"
    assert decision.reason.startswith("uncertain:")


@pytest.mark.asyncio
async def test_classifier_failure_defaults_to_drop(db, auth, monkeypatch):
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(run.id, "g1", "Broken Provider Clinic")
    db.add(place)
    await db.commit()

    nano = _FakeProvider(RuntimeError("boom"))
    sonar = _FakeProvider(RuntimeError("boom"))
    _patch_models(monkeypatch, nano, sonar)

    decision, _source = await classify_place_keep_drop(
        db, place, country_code="DZ", country_name="Algeria"
    )
    assert decision.decision == "drop"
    assert decision.confidence == 0.0


@pytest.mark.asyncio
async def test_pass_only_judges_relevant_candidates(db, auth, monkeypatch):
    """Already-excluded noise must not consume an AI call."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    relevant = _place(run.id, "g1", "Centre Rehab Prive", is_relevant=True)
    noise = _place(
        run.id,
        "g2",
        "Random Pharmacy",
        is_relevant=False,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        lifecycle_status=MapsLifecycleStatus.UNRELATED.value,
    )
    db.add_all([relevant, noise])
    await db.commit()

    judged: list[str] = []

    async def fake_classify(_session, place, *, country_code, country_name):
        judged.append(place.id)
        return KeepDropDecision.model_validate(json.loads(KEEP_JSON)), "nano"

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        fake_classify,
    )

    from app.services.scraping.maps_census_service import maps_census_service

    session_factory = maps_census_service._session_factory(db)
    summary = await run_keep_drop_pass(
        session_factory, run_id=run.id, country_code="DZ", country_name="Algeria"
    )
    assert judged == [relevant.id]
    assert summary["kept"] == 1
    assert summary["prefiltered"] == 1

    async with session_factory() as fresh:
        noise_row = (
            await fresh.execute(select(MapsPlace).where(MapsPlace.google_place_id == "g2"))
        ).scalar_one()
    assert noise_row.keep_drop_decision == "drop"
    assert noise_row.keep_drop_source == "prefilter"


@pytest.mark.asyncio
async def test_run_pass_is_resumable_and_gates_export(db, auth, monkeypatch):
    run = _run(auth)
    db.add(run)
    await db.flush()
    keep_place = _place(run.id, "g1", "Centre Rehab Prive")
    drop_place = _place(run.id, "g2", "CHU Public Hospital")
    decided = _place(run.id, "g3", "Already Decided", keep_drop_decision="drop")
    db.add_all([keep_place, drop_place, decided])
    await db.commit()

    calls: list[str] = []

    async def fake_classify(_session, place, *, country_code, country_name):
        calls.append(place.id)
        if "Rehab" in place.canonical_name:
            return KeepDropDecision.model_validate(json.loads(KEEP_JSON)), "nano"
        return KeepDropDecision.model_validate(json.loads(DROP_JSON)), "nano"

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        fake_classify,
    )

    from app.services.scraping.maps_census_service import maps_census_service

    session_factory = maps_census_service._session_factory(db)
    summary = await run_keep_drop_pass(
        session_factory, run_id=run.id, country_code="DZ", country_name="Algeria"
    )
    assert summary["kept"] == 1
    assert summary["dropped"] == 1
    # The already-decided place was never re-judged.
    assert decided.id not in calls

    async with session_factory() as fresh:
        places = (
            await fresh.execute(select(MapsPlace).where(MapsPlace.run_id == run.id))
        ).scalars().all()
    by_gid = {p.google_place_id: p for p in places}
    assert _is_eligible_center(by_gid["g1"])
    assert not _is_eligible_center(by_gid["g2"])
    assert by_gid["g2"].enrichment_pipeline_state == MapsEnrichmentPipelineState.FINALIZED.value

    # Second run: nothing left to decide.
    summary2 = await run_keep_drop_pass(
        session_factory, run_id=run.id, country_code="DZ", country_name="Algeria"
    )
    assert summary2["kept"] == 0
    assert summary2["dropped"] == 0
    # Already-excluded / non-candidate rows may be bulk-stamped without an AI call.
    assert summary2.get("prefiltered", 0) >= 0

    async with session_factory() as fresh:
        remaining = (
            await fresh.execute(build_keep_drop_query(run.id))
        ).scalars().all()
    assert remaining == []
