"""Regression tests for the evidence-normalization backfill: transactional,
idempotent, zero-AI-call recovery of already-stored evidence for the
Finland run 0afe4ca7-2dd3-47aa-a2cd-566e6b19b208 (and, by construction, any
other run with the same evidence-to-database mismatch).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsFacilityType,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
)
from app.services.scraping.maps_evidence_backfill import (
    BACKFILL_SOURCE_TAG,
    apply_backfill,
    build_backfill_plan,
    plan_backfill_for_place,
)


def _factory(db):
    return async_sessionmaker(bind=db.bind, expire_on_commit=False)


async def _create_run(db, auth: AuthContext) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FI",
        country_name="Finland",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


def _base_place(run_id: str, google_place_id: str, **overrides) -> MapsPlace:
    base = dict(
        run_id=run_id,
        google_place_id=google_place_id,
        raw_name=google_place_id,
        canonical_name=google_place_id,
        is_relevant=True,
        keep_drop_decision="review",
        client_eligibility=MapsClientEligibility.REVIEW.value,
        addiction_focus_confirmed=True,
        organization_scope=MapsOrganizationScope.FACILITY.value,
    )
    base.update(overrides)
    return MapsPlace(**base)


def _raahe_like_place(run_id: str, google_place_id: str, *, city: str) -> MapsPlace:
    """A real-shaped org-wide-evidence branch: ownership recoverable,
    residential_accommodation incorrectly true from another branch's page."""
    return _base_place(
        run_id,
        google_place_id,
        raw_name=f"A Klinikka {city}",
        canonical_name=f"A Klinikka {city}",
        city_name=city,
        residential_accommodation=True,
        classification_evidence={
            "ownership_status": {
                "value": "Private",
                "evidence_quote": "A-klinikka Oy is owned by Attendo, a private company.",
                "source_url": "https://www.a-klinikka.fi/",
            },
            "residential_accommodation": {
                "value": True,
                "evidence_quote": (
                    "A-klinikkakoti Hämeenlinna and Pyhtää offer around-the-clock "
                    "supported housing and communal living for addiction rehabilitation clients."
                ),
                "source_url": "https://www.a-klinikka.fi/toimipisteet/a-klinikkakoti-hameenlinna/",
            },
        },
    )


def _loimupelto_like_place(run_id: str, google_place_id: str) -> MapsPlace:
    """A genuinely self-referential place: residential_accommodation
    correctly stays true, ownership_unknown has no evidence to recover."""
    return _base_place(
        run_id,
        google_place_id,
        raw_name="Loimupelto Oy",
        canonical_name="Loimupelto Oy",
        city_name="Nurmes",
        ownership_status=MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
        residential_accommodation=True,
        classification_evidence={
            "residential_accommodation": {
                "value": True,
                "evidence_quote": "16-paikkainen hoitokoti near the care home indicate residential accommodation is provided.",
                "source_url": "https://www.loimupelto.fi/",
            },
        },
    )


# --- Dry run: exact per-facility changes, zero writes ---


@pytest.mark.asyncio
async def test_dry_run_reports_exact_changes_without_writing(db, auth):
    run = await _create_run(db, auth)
    raahe = _raahe_like_place(run.id, "raahe-1", city="Raahe")
    db.add(raahe)
    await db.commit()

    plans = await build_backfill_plan(_factory(db), run_id=run.id)
    assert len(plans) == 1
    plan = plans[0]
    changed_fields = {c.field: (c.old_value, c.new_value) for c in plan.changes}
    assert changed_fields["ownership_status"] == (None, "confirmed_non_government")
    assert changed_fields["residential_accommodation"] == (True, None)

    await db.refresh(raahe)
    assert raahe.ownership_status is None  # dry run — nothing written
    assert raahe.residential_accommodation is True
    assert raahe.classification_evidence.get("_backfill_audit") is None


# --- Apply: writes exactly the planned changes, recomputes eligibility,
# never promotes to ELIGIBLE from unsupported evidence ---


@pytest.mark.asyncio
async def test_apply_writes_exact_changes_and_recomputes_eligibility(db, auth):
    run = await _create_run(db, auth)
    raahe = _raahe_like_place(run.id, "raahe-2", city="Raahe")
    db.add(raahe)
    await db.commit()

    result = await apply_backfill(_factory(db), run_id=run.id)
    assert result["places_changed"] == 1

    await db.refresh(raahe)
    assert raahe.ownership_status == "confirmed_non_government"
    assert raahe.residential_accommodation is None
    assert raahe.client_eligibility != MapsClientEligibility.ELIGIBLE.value
    audit = raahe.classification_evidence.get("_backfill_audit")
    assert audit and audit[0]["source"] == BACKFILL_SOURCE_TAG
    assert audit[0]["eligibility_before"] == MapsClientEligibility.REVIEW.value


@pytest.mark.asyncio
async def test_apply_keeps_self_referential_residential_confirmation(db, auth):
    run = await _create_run(db, auth)
    loimupelto = _loimupelto_like_place(run.id, "loimupelto-1")
    db.add(loimupelto)
    await db.commit()

    await apply_backfill(_factory(db), run_id=run.id)

    await db.refresh(loimupelto)
    assert loimupelto.residential_accommodation is True  # unchanged — genuinely self-referential
    assert loimupelto.ownership_status == "ownership_unknown"  # no evidence to recover — stays unresolved


# --- Idempotency: running twice makes zero further changes ---


@pytest.mark.asyncio
async def test_running_backfill_twice_makes_no_further_changes(db, auth):
    run = await _create_run(db, auth)
    raahe = _raahe_like_place(run.id, "raahe-3", city="Raahe")
    db.add(raahe)
    await db.commit()

    first = await apply_backfill(_factory(db), run_id=run.id)
    assert first["places_changed"] == 1

    second = await apply_backfill(_factory(db), run_id=run.id)
    assert second["places_changed"] == 0

    await db.refresh(raahe)
    # Audit log has exactly one entry, not two — the second run genuinely
    # found nothing to do, it did not reapply/duplicate the first entry.
    assert len(raahe.classification_evidence["_backfill_audit"]) == 1


# --- Interrupted execution: transactional, all-or-nothing ---


@pytest.mark.asyncio
async def test_interrupted_execution_leaves_database_unchanged(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    raahe = _raahe_like_place(run.id, "raahe-4", city="Raahe")
    second_place = _raahe_like_place(run.id, "kotka-4", city="Kotka")
    db.add_all([raahe, second_place])
    await db.commit()

    call_count = {"n": 0}
    import app.services.scraping.maps_evidence_backfill as backfill_module

    real_derive = backfill_module._derive_lifecycle_status

    def _flaky_derive(place):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated crash mid-backfill")
        return real_derive(place)

    monkeypatch.setattr(backfill_module, "_derive_lifecycle_status", _flaky_derive)

    with pytest.raises(RuntimeError):
        await apply_backfill(_factory(db), run_id=run.id)

    # Nothing committed — both places must be exactly as they were before.
    await db.refresh(raahe)
    await db.refresh(second_place)
    assert raahe.ownership_status is None
    assert raahe.residential_accommodation is True
    assert second_place.ownership_status is None
    assert second_place.residential_accommodation is True
    assert raahe.classification_evidence.get("_backfill_audit") is None


# --- Manual exclusions and confident drops are never touched ---


@pytest.mark.asyncio
async def test_manually_excluded_place_is_never_touched(db, auth):
    run = await _create_run(db, auth)
    place = _raahe_like_place(run.id, "excluded-1", city="Raahe")
    place.client_eligibility = MapsClientEligibility.EXCLUDED.value
    place.manually_excluded_at = datetime.now(UTC)
    db.add(place)
    await db.commit()

    plans = await build_backfill_plan(_factory(db), run_id=run.id)
    assert plans == []

    result = await apply_backfill(_factory(db), run_id=run.id)
    assert result["places_changed"] == 0
    await db.refresh(place)
    assert place.ownership_status is None
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value


@pytest.mark.asyncio
async def test_confident_drop_is_never_touched(db, auth):
    run = await _create_run(db, auth)
    place = _raahe_like_place(run.id, "dropped-1", city="Raahe")
    place.keep_drop_decision = "drop"
    place.is_relevant = False
    place.client_eligibility = MapsClientEligibility.EXCLUDED.value
    db.add(place)
    await db.commit()

    plans = await build_backfill_plan(_factory(db), run_id=run.id)
    assert plans == []

    result = await apply_backfill(_factory(db), run_id=run.id)
    assert result["places_changed"] == 0
    await db.refresh(place)
    assert place.ownership_status is None


# --- Unrelated/org-wide branches: legitimate fields recover, unsupported
# ones stay unresolved, on the SAME place ---


@pytest.mark.asyncio
async def test_unrelated_branch_partial_recovery_never_reaches_eligible(db, auth):
    """A-klinikka-style branch: ownership recovers (legitimately org-wide),
    facility_type/residential never do (org-wide, not branch-specific) —
    and the place must never become ELIGIBLE from this alone."""
    run = await _create_run(db, auth)
    place = _base_place(
        run.id,
        "kotka-1",
        raw_name="A-KLINIKKA",
        canonical_name="A-KLINIKKA",
        city_name="Kotka",
        residential_accommodation=True,
        classification_evidence={
            "ownership_status": {
                "value": "Private",
                "evidence_quote": "A-klinikka Oy is owned by Attendo, a private company.",
                "source_url": "https://www.a-klinikka.fi/",
            },
            "facility_type": {
                "value": "Addiction treatment facility",
                "evidence_quote": (
                    "Palvelut Avohoito Ajoterveysseuranta Korvaushoito Selviämishoito "
                    "Vieroitushoito Kuntouttava laitoshoito Asumispalvelut Lastensuojelupalvelut"
                ),
                "source_url": "https://www.a-klinikka.fi/",
            },
            "residential_accommodation": {
                "value": True,
                "evidence_quote": "A-klinikkakoti Pyhtää ja Hämeenlinna tarjoavat ympärivuorokautista palveluasumista.",
                "source_url": "https://www.a-klinikka.fi/toimipisteet/a-klinikkakoti-pyhtaa/",
            },
        },
    )
    db.add(place)
    await db.commit()

    await apply_backfill(_factory(db), run_id=run.id)

    await db.refresh(place)
    assert place.ownership_status == "confirmed_non_government"
    assert place.facility_type is None
    assert place.residential_accommodation is None
    assert place.client_eligibility != MapsClientEligibility.ELIGIBLE.value


# --- Zero AI calls, enforced not just assumed ---


@pytest.mark.asyncio
async def test_backfill_makes_zero_ai_provider_calls(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    place = _raahe_like_place(run.id, "raahe-5", city="Raahe")
    db.add(place)
    await db.commit()

    def _fail(*_args, **_kwargs):
        raise AssertionError("the backfill must never call an AI provider")

    monkeypatch.setattr(
        "app.services.scraping.maps_classification_service.create_structured_classification_provider",
        _fail,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        _fail,
    )

    result = await apply_backfill(_factory(db), run_id=run.id)
    assert result["places_changed"] == 1


# --- 1,362-places / confident-drops preservation: the backfill never
# deletes rows, regardless of outcome ---


@pytest.mark.asyncio
async def test_backfill_never_deletes_any_rows(db, auth):
    run = await _create_run(db, auth)
    places = [
        _raahe_like_place(run.id, "raahe-6", city="Raahe"),
        _loimupelto_like_place(run.id, "loimupelto-2"),
    ]
    dropped = _raahe_like_place(run.id, "dropped-2", city="Oulu")
    dropped.keep_drop_decision = "drop"
    dropped.is_relevant = False
    excluded = _raahe_like_place(run.id, "excluded-2", city="Vaasa")
    excluded.manually_excluded_at = datetime.now(UTC)
    places.extend([dropped, excluded])
    db.add_all(places)
    await db.commit()

    await apply_backfill(_factory(db), run_id=run.id)

    from sqlalchemy import func, select

    total = (
        await db.execute(
            select(func.count()).select_from(MapsPlace).where(MapsPlace.run_id == run.id)
        )
    ).scalar_one()
    assert total == 4


# --- Regression: dry-run eligibility_after must never be None/unset, and
# must exactly match what apply actually persists. Root cause was that
# build_backfill_plan never called compute_client_eligibility at all —
# plan.eligibility_after stayed at its dataclass default (None) for every
# single affected place, which looked exactly like "recomputation produces
# NULL" from the printed report, even though apply_backfill's real
# persisted value was always a valid, non-null MapsClientEligibility
# string. compute_client_eligibility itself never returns None (every
# branch returns a MapsClientEligibility.*.value string) and there is no
# tuple-unpacking or enum/string mismatch anywhere in this path — the bug
# was purely a dry-run reporting gap, fixed by having plan_backfill_for_
# place compute eligibility_after on a snapshot so dry-run and apply are
# now structurally unable to disagree. ---


@pytest.mark.asyncio
async def test_dry_run_eligibility_after_is_never_none_for_a_changed_place(db, auth):
    run = await _create_run(db, auth)
    raahe = _raahe_like_place(run.id, "raahe-dryrun-1", city="Raahe")
    db.add(raahe)
    await db.commit()

    plans = await build_backfill_plan(_factory(db), run_id=run.id)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.eligibility_before == MapsClientEligibility.REVIEW.value
    assert plan.eligibility_after is not None
    # Ownership-only recovery, with facility_type/residential still
    # unresolved, cannot reach ELIGIBLE — it must stay REVIEW, not NULL.
    assert plan.eligibility_after == MapsClientEligibility.REVIEW.value


@pytest.mark.asyncio
async def test_dry_run_eligibility_after_matches_what_apply_actually_persists(db, auth):
    """The real requirement: dry-run's reported eligibility_after must be
    exactly what ends up in the database after --apply, for every place,
    not just plausible-looking."""
    run = await _create_run(db, auth)
    places = [
        _raahe_like_place(run.id, "raahe-match-1", city="Raahe"),
        _raahe_like_place(run.id, "kotka-match-1", city="Kotka"),
        _loimupelto_like_place(run.id, "loimupelto-match-1"),
    ]
    db.add_all(places)
    await db.commit()

    plans_by_id = {
        p.place_id: p for p in await build_backfill_plan(_factory(db), run_id=run.id)
    }

    await apply_backfill(_factory(db), run_id=run.id)

    for place in places:
        await db.refresh(place)
        if place.id in plans_by_id:
            assert place.client_eligibility == plans_by_id[place.id].eligibility_after
        # Loimupelto has no string-field evidence to recover and its
        # residential confirmation is genuinely self-referential — no
        # change at all, so it is correctly absent from the plan.


@pytest.mark.asyncio
async def test_review_to_review_insufficient_evidence_never_becomes_null(db, auth):
    """A facility whose only recoverable field is ownership (legitimately
    organization-wide), with facility_type/residential still unresolved,
    must stay REVIEW after recomputation — never NULL, never silently
    promoted to ELIGIBLE or EXCLUDED from partial evidence."""
    run = await _create_run(db, auth)
    place = _raahe_like_place(run.id, "review-to-review-1", city="Raahe")
    db.add(place)
    await db.commit()

    result = await apply_backfill(_factory(db), run_id=run.id)
    assert result["places_changed"] == 1
    plan = result["plans"][0]
    assert plan.eligibility_before == MapsClientEligibility.REVIEW.value
    assert plan.eligibility_after == MapsClientEligibility.REVIEW.value

    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.REVIEW.value
    assert place.client_eligibility is not None


@pytest.mark.asyncio
async def test_idempotent_recomputation_across_all_representative_finland_cases(db, auth):
    """Every representative shape from the real Finland audit (org-wide
    branch, self-referential, and a dropped/excluded control) recomputes
    to a real, non-null status, and a second apply changes nothing and
    reports the identical eligibility for every place that had a plan."""
    run = await _create_run(db, auth)
    org_wide_branches = [
        _raahe_like_place(run.id, f"branch-{city}", city=city)
        for city in ("Raahe", "Kotka", "Lappeenranta", "Valkeakoski", "Jyvaskyla", "Varkaus")
    ]
    self_referential = _loimupelto_like_place(run.id, "self-referential-1")
    db.add_all([*org_wide_branches, self_referential])
    await db.commit()

    first = await apply_backfill(_factory(db), run_id=run.id)
    assert first["places_changed"] == len(org_wide_branches)  # self-referential has nothing to change
    for plan in first["plans"]:
        assert plan.eligibility_after is not None
        assert plan.eligibility_after == MapsClientEligibility.REVIEW.value

    second = await apply_backfill(_factory(db), run_id=run.id)
    assert second["places_changed"] == 0

    for place in org_wide_branches:
        await db.refresh(place)
        assert place.client_eligibility == MapsClientEligibility.REVIEW.value


# --- Audit: the 22 ownership recoveries must require real, credible
# evidence of OWNERSHIP — never infer confirmed_non_government purely
# from a name containing "Oy"/"ry"/"säätiö" or a domain that merely looks
# like a foundation's. normalize_choice_with_synonyms only ever looks at
# the AI's OWN stated evidence value (e.g. literally "Private", "nonprofit")
# captured in classification_evidence — it has no access to, and never
# inspects, the place's name or website domain at all. These tests pin
# that down explicitly, including a case designed to look name-"foundation-
# like" (ending in "säätiö") whose evidence instead genuinely supports
# PROBABLE, not CONFIRMED, non-government ownership. ---


@pytest.mark.asyncio
async def test_ownership_recovery_never_triggers_on_name_alone(db, auth):
    """A name containing "säätiö" (foundation) must NOT, by itself, cause
    ownership_status to recover — there is no evidence entry at all for
    this place, so the ownership field (and everything else) stays
    unresolved."""
    run = await _create_run(db, auth)
    place = _base_place(
        run.id,
        "name-only-1",
        raw_name="Jokin Säätiö",
        canonical_name="Jokin Säätiö",
        city_name="Tampere",
        classification_evidence={},
    )
    db.add(place)
    await db.commit()

    plan = plan_backfill_for_place(place)
    assert plan.changes == []


@pytest.mark.asyncio
async def test_ownership_recovery_requires_an_actual_evidence_value_not_just_a_domain(db, auth):
    """A website domain that merely LOOKS like a foundation's (e.g.
    "-saatio.fi") is never inspected by the normalizer at all — only the
    evidence dict's own stated ``value`` is read. A source_url with no
    accompanying ``value`` recovers nothing."""
    run = await _create_run(db, auth)
    place = _base_place(
        run.id,
        "domain-only-1",
        raw_name="Example Hoitokoti",
        canonical_name="Example Hoitokoti",
        city_name="Tampere",
        classification_evidence={
            "ownership_status": {
                "evidence_quote": "See our website for details.",
                "source_url": "https://example-saatio.fi/about",
                # Deliberately no "value" key — a domain alone is not a
                # stated conclusion.
            }
        },
    )
    db.add(place)
    await db.commit()

    plan = plan_backfill_for_place(place)
    assert plan.changes == []


@pytest.mark.asyncio
async def test_ownership_recovery_with_genuine_probable_evidence_does_not_overclaim_confirmed(db, auth):
    """Evidence that only supports PROBABLE non-government ownership must
    recover to probable_non_government, never be rounded up to
    confirmed_non_government."""
    run = await _create_run(db, auth)
    place = _base_place(
        run.id,
        "probable-1",
        raw_name="Mystery Säätiö",
        canonical_name="Mystery Säätiö",
        city_name="Tampere",
        classification_evidence={
            "ownership_status": {
                "value": "probably private",
                "evidence_quote": "Likely privately funded based on available records, not independently confirmed.",
                "source_url": "https://example.org/about",
            }
        },
    )
    db.add(place)
    await db.commit()

    plan = plan_backfill_for_place(place)
    changed = {c.field: c.new_value for c in plan.changes}
    assert changed["ownership_status"] == MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value


@pytest.mark.asyncio
async def test_ownership_recovery_rejects_real_finland_name_suffix_quotes(db, auth):
    """The real audit finding: most of the 22 proposed ownership
    recoveries in the Finland run were pure name-suffix/domain inference
    ("suffix 'ry' indicates...", "domain ... indicates..."). These must
    stay unresolved — a legal-form suffix is not the same fact as actual
    ownership (a Finnish municipality can own an "Oy")."""
    run = await _create_run(db, auth)
    places = [
        _base_place(
            run.id,
            "ry-suffix-1",
            raw_name="Myllyhoitoyhdistys ry",
            canonical_name="Myllyhoitoyhdistys ry",
            city_name="Helsinki",
            classification_evidence={
                "ownership_status": {
                    "value": "Nonprofit",
                    "evidence_quote": "The suffix 'ry' indicates nonprofit ownership status in Finnish organizations.",
                    "source_url": "https://www.google.com/maps/place/?q=place_id:x",
                },
            },
        ),
        _base_place(
            run.id,
            "domain-suffix-1",
            raw_name="Live Kupoli",
            canonical_name="Live Kupoli",
            city_name="Kuopio",
            classification_evidence={
                "ownership_status": {
                    "value": "nonprofit",
                    "evidence_quote": "Website URL domain livesaatio.fi indicates nonprofit foundation (säätiö means foundation in Finnish)",
                    "source_url": "https://livesaatio.fi/",
                },
            },
        ),
    ]
    db.add_all(places)
    await db.commit()

    for place in places:
        plan = plan_backfill_for_place(place)
        assert plan.changes == []


@pytest.mark.asyncio
async def test_ownership_recovery_accepts_genuine_acquisition_fact(db, auth):
    """By contrast, "A-klinikka Oy is owned by Attendo" (or the Finnish
    "...on siirtynyt virallisesti Attendon omistukseen") states an actual
    ownership fact, not a name-pattern guess, and must still recover."""
    run = await _create_run(db, auth)
    place = _base_place(
        run.id,
        "genuine-acquisition-1",
        raw_name="A-klinikka",
        canonical_name="A-klinikka",
        city_name="Lappeenranta",
        classification_evidence={
            "ownership_status": {
                "value": "Private",
                "evidence_quote": "A-klinikka Oy on siirtynyt virallisesti Attendon omistukseen",
                "source_url": "https://www.a-klinikka.fi/",
            },
        },
    )
    db.add(place)
    await db.commit()

    plan = plan_backfill_for_place(place)
    changed = {c.field: c.new_value for c in plan.changes}
    assert changed["ownership_status"] == MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value


# --- Narrow, jurisdiction-scoped recovery from evidence saved under a
# DIFFERENT field key (Belarus run 5acff6f7, facility 32e01f47) ---
# Belarus's facility_type evidence quote names the operator as a
# "Republican public association" (a Belarus-law-defined NGO) but the
# Sonar classify pipeline, before this fix, never emitted an
# "ownership_status" evidence item at all — there was nothing for the
# existing synonym-table recovery above to find. These tests cover the
# new recover_ownership_status_from_unambiguous_ngo_evidence fallback.


def _belarus_place(run_id: str, google_place_id: str, **overrides) -> MapsPlace:
    base = dict(
        raw_name="Tsentr Statsionarnoy Pomoshchi \"Pravo Na Zhizn'\"",
        canonical_name="Tsentr Statsionarnoy Pomoshchi \"Pravo Na Zhizn'\"",
        city_name="Rudnya",
        formatted_address="ул. Сосновая 3 аг., Rudnya, Belarus",
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        operator_type=MapsOperatorType.ASSOCIATION.value,
        inpatient_treatment=True,
        residential_accommodation=True,
        classification_evidence={
            "facility_type": {
                "value": "residential addiction rehabilitation center",
                "evidence_quote": (
                    "Реабилитационный центр «Право на жизнь» Мозырского "
                    "районного отделения Республиканского общественного "
                    "объединения «Матери против наркотиков»"
                ),
                "source_url": (
                    "https://pmplus.by/activities/social-rehabilitation/"
                    "rehabilitation/gomelskaya-oblast/"
                ),
            },
        },
    )
    base.update(overrides)
    return _base_place(run_id, google_place_id, **base)


async def _create_belarus_run(db, auth: AuthContext) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


@pytest.mark.asyncio
async def test_belarus_candidate_recovers_confirmed_ownership_from_facility_type_evidence(db, auth):
    """The actual Belarus run 5acff6f7 facility 32e01f47: the quote
    explicitly names a Belarus-law-defined non-government legal form AND
    specifically identifies it as THIS facility's operator — must recover
    CONFIRMED_NON_GOVERNMENT, and reach ELIGIBLE end to end."""
    run = await _create_belarus_run(db, auth)
    place = _belarus_place(run.id, "belarus-1")
    db.add(place)
    await db.commit()

    plan = plan_backfill_for_place(place, country_code=run.country_code)
    changed = {c.field: c.new_value for c in plan.changes}
    assert changed["ownership_status"] == MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value
    assert plan.eligibility_after == MapsClientEligibility.ELIGIBLE.value

    result = await apply_backfill(_factory(db), run_id=run.id)
    assert result["places_changed"] == 1
    await db.refresh(place)
    assert place.ownership_status == MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value


@pytest.mark.asyncio
async def test_ngo_recovery_never_extrapolates_belarus_legal_form_to_other_countries(db, auth):
    """The exact same evidence quote, for a run in a country with no
    allowlist entry, must recover nothing — a jurisdiction's own legal
    designation is never extrapolated to a different country's law."""
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FI",
        country_name="Finland",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.commit()
    place = _belarus_place(run.id, "wrong-country-1")
    db.add(place)
    await db.commit()

    plan = plan_backfill_for_place(place, country_code=run.country_code)
    assert "ownership_status" not in {c.field for c in plan.changes}


@pytest.mark.asyncio
async def test_ngo_recovery_rejects_hedged_quote(db, auth):
    """A hedged statement about the same legal form must not recover —
    hedge-language protection applies here exactly as everywhere else."""
    run = await _create_belarus_run(db, auth)
    place = _belarus_place(
        run.id,
        "hedged-1",
        classification_evidence={
            "facility_type": {
                "value": "residential addiction rehabilitation center",
                "evidence_quote": (
                    "Возможно, относится к Общественному объединению «Матери "
                    "против наркотиков», но это не подтверждено"
                ),
                "source_url": "https://pmplus.by/unclear-page",
            },
        },
    )
    db.add(place)
    await db.commit()

    plan = plan_backfill_for_place(place, country_code=run.country_code)
    assert "ownership_status" not in {c.field for c in plan.changes}


@pytest.mark.asyncio
async def test_ngo_recovery_rejects_unrelated_organization_mention(db, auth):
    """The legal-form phrase appearing in a quote about a DIFFERENT,
    unrelated organization/branch (no name/address tie to this specific
    facility) must not recover — generic nearby mentions are not operator
    identity."""
    run = await _create_belarus_run(db, auth)
    place = _belarus_place(
        run.id,
        "unrelated-org-1",
        classification_evidence={
            "facility_type": {
                "value": "residential addiction rehabilitation center",
                "evidence_quote": (
                    "Минское общественное объединение «Здоровая нация» "
                    "работает отдельно от других региональных центров"
                ),
                "source_url": "https://example.by/other-org",
            },
        },
    )
    db.add(place)
    await db.commit()

    plan = plan_backfill_for_place(place, country_code=run.country_code)
    assert "ownership_status" not in {c.field for c in plan.changes}


@pytest.mark.asyncio
async def test_ngo_recovery_does_not_run_when_ownership_already_confirmed(db, auth):
    """Idempotency / no-double-recovery: once ownership_status is already
    CONFIRMED (either direction), this fallback never runs again —
    consistent with every other already-resolved field in this backfill.
    PROBABLE_NON_GOVERNMENT is deliberately NOT tested here as a
    no-op case — see
    test_belarus_candidate_already_at_probable_default_still_recovers,
    the actual Belarus run 5acff6f7 shape: a place that went through
    Sonar before this fix was deployed is left holding the stale
    hardcoded PROBABLE_NON_GOVERNMENT default, and must still be
    upgradeable once stronger, already-saved evidence exists."""
    run = await _create_belarus_run(db, auth)
    confirmed_non_government = _belarus_place(
        run.id,
        "already-confirmed-non-gov-1",
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    )
    confirmed_government = _belarus_place(
        run.id,
        "already-confirmed-gov-1",
        ownership_status=MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
    )
    db.add_all([confirmed_non_government, confirmed_government])
    await db.commit()

    for place in (confirmed_non_government, confirmed_government):
        plan = plan_backfill_for_place(place, country_code=run.country_code)
        assert "ownership_status" not in {c.field for c in plan.changes}


@pytest.mark.asyncio
async def test_belarus_candidate_already_at_probable_default_still_recovers(db, auth):
    """The actual discrepancy reported from the live Belarus run 5acff6f7
    dry run: this facility's ownership_status was already persisted as
    PROBABLE_NON_GOVERNMENT (the Sonar classify path's old hardcoded
    default, written before resolve_sonar_ownership_status existed) —
    which made it look "already resolved" and skip the fallback entirely,
    even though the stronger, already-saved facility_type evidence was
    sitting right there unconsulted. PROBABLE_NON_GOVERNMENT must still be
    upgradeable; only CONFIRMED_* values are protected."""
    run = await _create_belarus_run(db, auth)
    place = _belarus_place(
        run.id,
        "belarus-already-probable-1",
        city_name="Mozyr",  # the real run's actual stored city_name
        ownership_status=MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
    )
    db.add(place)
    await db.commit()

    plan = plan_backfill_for_place(place, country_code=run.country_code)
    changed = {c.field: c.new_value for c in plan.changes}
    assert changed["ownership_status"] == MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value
    assert plan.eligibility_after == MapsClientEligibility.ELIGIBLE.value

    # Re-running after apply must be a true no-op (idempotent): the field
    # is now CONFIRMED, which is fully protected going forward.
    place.ownership_status = changed["ownership_status"]
    second_plan = plan_backfill_for_place(place, country_code=run.country_code)
    assert "ownership_status" not in {c.field for c in second_plan.changes}


def test_ngo_recovery_function_never_fires_for_a_government_ownership_claim():
    """This recovery path only ever produces CONFIRMED_NON_GOVERNMENT —
    it has no code path that could ever return CONFIRMED_GOVERNMENT, so a
    government-operated facility can never be flipped to eligible by it.
    (The actual exclusion of CONFIRMED_GOVERNMENT ownership is
    compute_client_eligibility's own, separate, pre-existing rule.)"""
    from app.services.scraping.maps_evidence_normalization import (
        recover_ownership_status_from_unambiguous_ngo_evidence,
    )

    place = _belarus_place("run-x", "government-1")
    recovered = recover_ownership_status_from_unambiguous_ngo_evidence(
        place, country_code="BY"
    )
    assert recovered in (None, MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value)
    assert recovered != MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value
