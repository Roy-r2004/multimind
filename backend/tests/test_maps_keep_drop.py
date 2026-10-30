"""Tests for the strict keep/drop gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
    MapsWebsiteCrawlCache,
)
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState
from app.services.scraping.maps_export_service import _is_eligible_center
from app.services.scraping.maps_keep_drop_service import (
    KeepDropDecision,
    _crawl_excerpt_for_place,
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
        return SimpleNamespace(provider="nano", provider_model="anthropic/claude-haiku-5.5")

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


@pytest.mark.asyncio
async def test_crawl_excerpt_prefers_own_branch_path_over_sibling(db, auth):
    """Regression: the crawl cache now holds one row per (domain,
    seed_path) — a branch must get ITS OWN crawled content back, not
    whatever happened to be cached first for a sibling branch on the same
    domain."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(
        run.id,
        "g-hospital",
        "A-klinikkasäätiön Päihdesairaala",
        official_website="https://www.a-klinikka.fi/toimipisteet/paihdesairaala/",
    )
    db.add(place)
    now = datetime.now(UTC)
    db.add(
        MapsWebsiteCrawlCache(
            normalized_domain="www.a-klinikka.fi",
            seed_path="/toimipisteet/a-klinikkakoti-hameenlinna",
            pages=[{"url": "sibling", "excerpt": "Sibling branch content"}],
            fetched_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    db.add(
        MapsWebsiteCrawlCache(
            normalized_domain="www.a-klinikka.fi",
            seed_path="/toimipisteet/paihdesairaala",
            pages=[{"url": "own", "excerpt": "Paihdesairaala inpatient detox and rehab"}],
            fetched_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    await db.commit()

    excerpt = await _crawl_excerpt_for_place(db, place, max_chars=1000)
    assert excerpt is not None
    assert "Paihdesairaala inpatient detox and rehab" in excerpt
    assert "Sibling branch content" not in excerpt


def test_keep_drop_model_resolves_to_gpt5_nano_via_openrouter():
    """Model selection/provider routing for the primary Keep/Drop call —
    no mocking, exercises the real catalog + settings default. gpt-5-nano
    is the model the Google Places baseline was validated with."""
    from app.core.config import get_settings
    from app.llm.catalog import get_model

    settings = get_settings()
    model = get_model(settings.maps_keep_drop_model)
    assert model.provider == "openrouter"
    assert model.provider_model == "openai/gpt-5-nano"


def test_apply_keep_drop_keep_is_eligible_and_still_routes_to_phase2():
    """The strict gate decides eligibility, as in the Google Places runs; the
    place still goes through Phase 2 so hard negative evidence can veto it,
    and it is only delivered with a phone and a website."""
    place = _place("r1", "g1", "Centre Rehab")
    decision = KeepDropDecision.model_validate(json.loads(KEEP_JSON))
    apply_keep_drop(place, decision, source="nano")
    assert place.keep_drop_decision == "keep"
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert place.lifecycle_status == MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value
    assert place.verification_verdict == "confirmed"
    assert place.is_relevant is True
    # PREFILTER_PENDING, not CLASSIFICATION_COMPLETED: a bare keep/drop
    # verdict must route INTO mandatory eligibility classification
    # (ownership/facility-type/residential extraction), not skip past it —
    # CLASSIFICATION_COMPLETED is _classify_place's own exit marker for a
    # place that has actually been through that extraction (see
    # build_classification_query, which deliberately excludes
    # CLASSIFICATION_COMPLETED from its candidate pool). Stamping it here
    # previously made every freshly-decided KEEP/REVIEW place skip
    # straight to optional detail enrichment with ownership_status/
    # facility_type/residential_accommodation never extracted — the root
    # cause of "55 relevant, 0 eligible" in the Finland run
    # ad4f7947-ffae-4e22-acbd-45c74c037cfd.
    assert place.enrichment_pipeline_state == (
        MapsEnrichmentPipelineState.PREFILTER_PENDING.value
    )
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value
    # No phone or website yet: eligible but not deliverable.
    assert not _is_eligible_center(place)
    place.international_phone_number = "+213 21 00 00 00"
    place.raw_website = "https://rehab.example.dz"
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
    """Both calls stayed under the confidence threshold: drop, as the Google
    Places gate did. A false keep is worse than a false drop."""
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
async def test_classifier_failure_never_defaults_to_drop(db, auth, monkeypatch):
    """Root-cause regression: Belarus run 5acff6f7-21f8-4427-88a1-38d0a394ce83
    lost 130/1106 discovered places to a single ~60s DNS blip because a
    total classifier failure used to fall back to a confident DROP —
    silently and permanently excluding places that were never actually
    judged at all. It must now become a technical-failure REVIEW instead,
    findable by its distinct reason/source."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(run.id, "g1", "Broken Provider Clinic")
    db.add(place)
    await db.commit()

    nano = _FakeProvider(RuntimeError("boom"))
    sonar = _FakeProvider(RuntimeError("boom"))
    _patch_models(monkeypatch, nano, sonar)

    decision, source = await classify_place_keep_drop(
        db, place, country_code="DZ", country_name="Algeria"
    )
    assert decision.decision == "review"
    assert decision.confidence == 0.0
    assert decision.reason == "classifier_unavailable"
    assert source == "technical_failure"
    # A non-retryable error (not a connection/timeout/DNS failure) must not
    # be retried — nothing about trying again would fix a plain exception.
    assert nano.calls == 1
    assert sonar.calls == 1


@pytest.mark.asyncio
async def test_transient_connection_error_recovers_on_retry(db, auth, monkeypatch):
    """A DNS/connection blip (the exact failure mode observed in production)
    must be retried, bounded, with backoff — and a genuine verdict reached
    once the provider recovers must be used, not discarded."""
    import httpx

    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(run.id, "g1", "Recovers After Retry Clinic")
    db.add(place)
    await db.commit()

    class _FlakyThenOkProvider:
        def __init__(self, text: str, *, fail_times: int) -> None:
            self._text = text
            self._fail_times = fail_times
            self.calls = 0

        async def complete(self, **_kwargs):
            self.calls += 1
            if self.calls <= self._fail_times:
                raise httpx.ConnectError("[Errno -2] Name or service not known")
            return SimpleNamespace(text=self._text)

    nano = _FlakyThenOkProvider(KEEP_JSON, fail_times=1)
    sonar = _FakeProvider(DROP_JSON)
    _patch_models(monkeypatch, nano, sonar)
    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.get_settings",
        lambda: SimpleNamespace(
            maps_keep_drop_retry_max_attempts=2,
            maps_keep_drop_retry_backoff_seconds=0.0,
            maps_keep_drop_confidence_threshold=0.85,
            maps_keep_drop_max_crawl_excerpt_chars=4000,
            maps_keep_drop_sonar_fallback_enabled=True,
            maps_keep_drop_model="claude-haiku",
        ),
    )

    decision, source = await classify_place_keep_drop(
        db, place, country_code="DZ", country_name="Algeria"
    )
    assert decision.decision == "keep"
    assert source == "nano"
    assert nano.calls == 2  # one failure, one successful retry
    assert sonar.calls == 0  # never needed — nano recovered and was confident


@pytest.mark.asyncio
async def test_retries_are_bounded_not_unlimited(db, auth, monkeypatch):
    """A persistently unreachable provider must still give up after the
    configured number of attempts — never retry forever — and the result
    must be the safe technical-failure REVIEW, not a drop."""
    import httpx

    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(run.id, "g1", "Persistently Unreachable Clinic")
    db.add(place)
    await db.commit()

    class _AlwaysFailsProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **_kwargs):
            self.calls += 1
            raise httpx.ConnectError("[Errno -2] Name or service not known")

    nano = _AlwaysFailsProvider()
    sonar = _AlwaysFailsProvider()
    _patch_models(monkeypatch, nano, sonar)
    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.get_settings",
        lambda: SimpleNamespace(
            maps_keep_drop_retry_max_attempts=3,
            maps_keep_drop_retry_backoff_seconds=0.0,
            maps_keep_drop_confidence_threshold=0.85,
            maps_keep_drop_max_crawl_excerpt_chars=4000,
            maps_keep_drop_sonar_fallback_enabled=True,
            maps_keep_drop_model="claude-haiku",
        ),
    )

    decision, source = await classify_place_keep_drop(
        db, place, country_code="DZ", country_name="Algeria"
    )
    assert decision.decision == "review"
    assert decision.reason == "classifier_unavailable"
    assert source == "technical_failure"
    assert nano.calls == 3  # exactly the configured bound, not unlimited
    assert sonar.calls == 3


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
    # Kept -> routed to Phase 2, but NOT yet an eligible center — keep/drop
    # alone never establishes final eligibility.
    assert by_gid["g1"].keep_drop_decision == "keep"
    assert not _is_eligible_center(by_gid["g1"])
    assert by_gid["g1"].enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value
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


@pytest.mark.asyncio
async def test_retry_failed_keep_drop_clears_only_failure_decisions(db, auth, monkeypatch):
    """Places auto-dropped on classifier errors get their decision cleared,
    restored to a re-judgeable candidate state, and re-enqueued; genuine model
    drops and keeps stay decided untouched."""
    from app.services.scraping.maps_admin_service import maps_admin_service
    from app.services.scraping.maps_census_service import maps_census_service
    from app.services.scraping.maps_keep_drop_service import (
        KeepDropDecision,
        apply_keep_drop,
        build_keep_drop_query,
    )

    run = _run(auth)
    db.add(run)
    await db.flush()
    # Model the real post-apply_keep_drop state: the DROP branch stamps
    # lifecycle_status=unrelated / client_eligibility=excluded /
    # is_relevant=False regardless of *why* it dropped — reproduce that via
    # apply_keep_drop itself rather than hand-setting fields, so the test
    # actually exercises the bug (candidate-filter exclusion) this endpoint
    # must undo.
    unavailable = _place(run.id, "g1", "Comunita Il Cenacolo")
    apply_keep_drop(
        unavailable,
        KeepDropDecision(decision="drop", reason="classifier_unavailable", confidence=0.0, evidence=[]),
        source="nano",
    )
    errored = _place(run.id, "g2", "Zajednica Emanuel")
    apply_keep_drop(
        errored,
        KeepDropDecision(decision="drop", reason="keep_drop_error: RuntimeError: boom", confidence=0.0, evidence=[]),
        source="nano",
    )
    # The CURRENT (post-fix) shape of a technical failure: review, not drop —
    # must also be found and re-queued by this same endpoint.
    technical_failure = _place(run.id, "g5", "Dom Nadiya")
    apply_keep_drop(
        technical_failure,
        KeepDropDecision(decision="review", reason="classifier_unavailable", confidence=0.0, evidence=[]),
        source="technical_failure",
    )
    genuine_drop = _place(run.id, "g3", "CHU Public Hospital")
    apply_keep_drop(
        genuine_drop,
        KeepDropDecision(decision="drop", reason="Public hospital (government)", confidence=0.97, evidence=[]),
        source="nano",
    )
    kept = _place(run.id, "g4", "Centre Rehab Prive")
    apply_keep_drop(
        kept,
        KeepDropDecision(decision="keep", reason="Private NGO addiction treatment center", confidence=0.95, evidence=[]),
        source="nano",
    )
    db.add_all([unavailable, errored, technical_failure, genuine_drop, kept])
    await db.commit()

    enqueued: list[str] = []

    async def fake_enqueue(job_name, run_id, **_kwargs):
        enqueued.append(job_name)

    monkeypatch.setattr(maps_census_service, "_enqueue_job", fake_enqueue)

    result = await maps_admin_service.retry_failed_keep_drop(db, auth, run.id)
    assert "3 places" in result.message
    assert enqueued == ["run_maps_keep_drop_job"]

    for place in (unavailable, errored, technical_failure, genuine_drop, kept):
        await db.refresh(place)

    # The failure-decisions are cleared AND restored to the candidate state
    # build_keep_drop_query looks for — this is the part the first version of
    # this endpoint got wrong (it only cleared keep_drop_decision, leaving
    # is_relevant=False / lifecycle_status=unrelated, so the next pass
    # bulk-stamped them right back to drop via stamp_non_candidate_drops
    # without ever calling the classifier).
    for place in (unavailable, errored, technical_failure):
        assert place.keep_drop_decision is None
        assert place.keep_drop_reason is None
        assert place.is_relevant is True
        assert place.lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW.value
        assert place.client_eligibility == MapsClientEligibility.REVIEW.value

    assert genuine_drop.keep_drop_decision == "drop"
    assert genuine_drop.keep_drop_reason == "Public hospital (government)"
    assert genuine_drop.is_relevant is False
    assert kept.keep_drop_decision == "keep"

    # The next keep/drop pass must actually pick the restored places back up.
    candidates = (await db.execute(build_keep_drop_query(run.id))).scalars().all()
    candidate_ids = {p.id for p in candidates}
    assert unavailable.id in candidate_ids
    assert errored.id in candidate_ids
    assert genuine_drop.id not in candidate_ids

    # Nothing to retry the second time — no job enqueued.
    enqueued.clear()
    again = await maps_admin_service.retry_failed_keep_drop(db, auth, run.id)
    assert "No failure-dropped places" in again.message
    assert enqueued == []


# --- C-2 correction: a keep/drop pass already "running" for a run_id must
# make a second concurrent pass (whether started inline by run_census or by
# an admin retry/revalidate action) a true no-op — no batch fetch, no paid
# classifier call — and the admin actions that reset decisions before
# enqueuing a new pass must refuse outright with a clear error rather than
# silently racing. ---


@pytest.mark.asyncio
async def test_concurrent_keep_drop_pass_is_skipped_not_double_billed(db, auth, monkeypatch):
    """A second run_keep_drop_pass call while one is already "running" for
    the same run_id must never fetch a batch or call the classifier."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    db.add(_place(run.id, "g1", "Centre Rehab Prive"))
    await db.commit()

    calls: list[str] = []

    async def fake_classify(_session, place, *, country_code, country_name):
        calls.append(place.id)
        return KeepDropDecision.model_validate(json.loads(KEEP_JSON)), "nano"

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        fake_classify,
    )

    from app.services.scraping.maps_census_service import maps_census_service

    session_factory = maps_census_service._session_factory(db)

    # Simulate "a pass is already running" the same way run_keep_drop_pass
    # itself would have left it mid-sweep.
    run.processing_state = {"keep_drop_status": "running", "keep_drop_heartbeat_at": "2099-01-01T00:00:00+00:00"}
    await db.commit()

    summary = await run_keep_drop_pass(
        session_factory, run_id=run.id, country_code="DZ", country_name="Algeria"
    )
    assert summary == {"skipped_busy": 1}
    assert calls == []  # never called the paid classifier


@pytest.mark.asyncio
async def test_try_start_keep_drop_pass_claims_once(db, auth):
    from app.services.scraping.maps_census_service import maps_census_service
    from app.services.scraping.maps_keep_drop_service import try_start_keep_drop_pass

    run = _run(auth)
    db.add(run)
    await db.commit()

    session_factory = maps_census_service._session_factory(db)
    first = await try_start_keep_drop_pass(session_factory, run_id=run.id)
    assert first is not None

    second = await try_start_keep_drop_pass(session_factory, run_id=run.id)
    assert second is None  # already claimed as "running"


@pytest.mark.asyncio
async def test_revalidate_keeps_rejects_when_pass_already_running(db, auth):
    from app.core.exceptions import ConflictError
    from app.services.scraping.maps_admin_service import maps_admin_service

    run = _run(auth, processing_state={"keep_drop_status": "running"})
    db.add(run)
    await db.flush()
    kept = _place(run.id, "g1", "Centre Rehab Prive", keep_drop_decision="keep")
    db.add(kept)
    await db.commit()

    with pytest.raises(ConflictError):
        await maps_admin_service.revalidate_keeps(db, auth, run.id)

    # The blocked action must not have touched the place's decision.
    await db.refresh(kept)
    assert kept.keep_drop_decision == "keep"


@pytest.mark.asyncio
async def test_retry_failed_keep_drop_rejects_when_pass_already_running(db, auth):
    from app.core.exceptions import ConflictError
    from app.services.scraping.maps_admin_service import maps_admin_service
    from app.services.scraping.maps_keep_drop_service import KeepDropDecision, apply_keep_drop

    run = _run(auth, processing_state={"keep_drop_status": "running"})
    db.add(run)
    await db.flush()
    unavailable = _place(run.id, "g1", "Comunita Il Cenacolo")
    apply_keep_drop(
        unavailable,
        KeepDropDecision(decision="drop", reason="classifier_unavailable", confidence=0.0, evidence=[]),
        source="nano",
    )
    db.add(unavailable)
    await db.commit()

    with pytest.raises(ConflictError):
        await maps_admin_service.retry_failed_keep_drop(db, auth, run.id)

    await db.refresh(unavailable)
    assert unavailable.keep_drop_decision == "drop"


@pytest.mark.asyncio
async def test_keep_drop_retry_projection_counts_without_mutating(db, auth):
    from app.services.scraping.maps_admin_service import maps_admin_service
    from app.services.scraping.maps_keep_drop_service import KeepDropDecision, apply_keep_drop

    run = _run(auth)
    db.add(run)
    await db.flush()
    unavailable = _place(run.id, "g1", "Comunita Il Cenacolo")
    apply_keep_drop(
        unavailable,
        KeepDropDecision(decision="drop", reason="classifier_unavailable", confidence=0.0, evidence=[]),
        source="nano",
    )
    genuine_drop = _place(run.id, "g2", "CHU Public Hospital")
    apply_keep_drop(
        genuine_drop,
        KeepDropDecision(decision="drop", reason="Public hospital", confidence=0.97, evidence=[]),
        source="nano",
    )
    db.add_all([unavailable, genuine_drop])
    await db.commit()

    projection = await maps_admin_service.keep_drop_retry_projection(db, auth, run.id)

    assert projection["candidate_count"] == 1
    assert projection["max_total_calls_upper_bound"] == 2  # nano + sonar fallback, per candidate
    assert projection["keep_drop_already_running"] is False

    await db.refresh(unavailable)
    await db.refresh(genuine_drop)
    assert unavailable.keep_drop_decision == "drop"  # untouched by the dry run
    assert genuine_drop.keep_drop_decision == "drop"
