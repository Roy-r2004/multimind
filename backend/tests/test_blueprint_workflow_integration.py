#!/usr/bin/env python
"""SQLite integration coverage for Phase 1B workflow services; no provider calls."""

import pytest
from conftest import create_model_set, create_other_auth
from httpx import ASGITransport, AsyncClient
from test_country_blueprint_foundation import valid_structured_blueprint

from app.core.dependencies import get_auth_context
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import ScrapingBlueprint, ScrapingBlueprintStatus
from app.db.session import get_db
from app.main import create_app
from app.schemas.api import (
    ScrapingBlueprintChangeRequest,
    ScrapingBlueprintRejectRequest,
    ScrapingMissionCreate,
)
from app.services.scraping.blueprint_service import blueprint_service
from app.services.scraping.mission_service import mission_service


async def mission_and_blueprint(db, auth, status=ScrapingBlueprintStatus.READY_FOR_REVIEW):
    await create_model_set(db, auth)
    mission = await mission_service.create_mission(
        db,
        auth,
        ScrapingMissionCreate(
            title="Austria", country="Austria", original_prompt="Plan", model_set_id="research-set"
        ),
    )
    blueprint = ScrapingBlueprint(
        mission_id=mission.id,
        version=1,
        status=status,
        model_set_id="research-set",
        country_name_snapshot="Austria",
        country_iso3_snapshot="AUT",
        continent_snapshot="Europe",
        provider="openrouter",
        provider_model_id="test-model",
        human_readable_blueprint="Original",
        structured_blueprint=valid_structured_blueprint(),
        citations=[],
    )
    db.add(blueprint)
    await db.flush()
    return mission, blueprint


@pytest.mark.asyncio
async def test_review_blueprint_edit_persists_only_editable_content(db, auth):
    _, blueprint = await mission_and_blueprint(db, auth)
    original = (blueprint.mission_id, blueprint.version, blueprint.provider, blueprint.country_iso3_snapshot)
    response = await blueprint_service.edit_blueprint(
        db,
        auth,
        blueprint.id,
        human_readable_blueprint="Updated",
        structured_blueprint=valid_structured_blueprint(),
    )
    assert response.human_readable_blueprint == "Updated"
    assert (blueprint.mission_id, blueprint.version, blueprint.provider, blueprint.country_iso3_snapshot) == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        ScrapingBlueprintStatus.QUEUED,
        ScrapingBlueprintStatus.RUNNING,
        ScrapingBlueprintStatus.FAILED,
        ScrapingBlueprintStatus.APPROVED,
        ScrapingBlueprintStatus.REJECTED,
        ScrapingBlueprintStatus.DISCARDED,
    ],
)
async def test_non_editable_statuses_reject_edits(db, auth, status):
    _, blueprint = await mission_and_blueprint(db, auth, status)
    with pytest.raises(ValidationError):
        await blueprint_service.edit_blueprint(
            db, auth, blueprint.id, human_readable_blueprint="x", structured_blueprint={}
        )


@pytest.mark.asyncio
async def test_other_organization_cannot_read_or_edit_blueprint(db, auth):
    _, blueprint = await mission_and_blueprint(db, auth)
    other = await create_other_auth(db)
    with pytest.raises(NotFoundError):
        await blueprint_service.get_blueprint(db, other, blueprint.id)
    with pytest.raises(NotFoundError):
        await blueprint_service.edit_blueprint(
            db, other, blueprint.id, human_readable_blueprint="x", structured_blueprint={}
        )


@pytest.mark.asyncio
async def test_approval_sets_active_and_preserves_previous_version(db, auth):
    mission, first = await mission_and_blueprint(db, auth)
    approved = await blueprint_service.approve_blueprint(db, auth, first.id)
    assert approved.status == "approved"
    assert approved.campaign_execution_available is False
    refreshed = await mission_service.get_mission(db, auth, mission.id)
    assert refreshed.active_blueprint_id == first.id


@pytest.mark.asyncio
async def test_active_generation_conflict_is_safe_http_409(db, auth, monkeypatch):
    mission, active = await mission_and_blueprint(db, auth, ScrapingBlueprintStatus.QUEUED)

    async def no_enqueue(_blueprint_id: str) -> None:
        return None

    monkeypatch.setattr(blueprint_service, "enqueue_blueprint", no_enqueue)
    app = create_app()

    async def override_db():
        yield db

    async def override_auth():
        return auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/scraping/missions/{mission.id}/blueprints/generate")

    assert response.status_code == 409
    body = response.json()
    assert body["message"] == "Blueprint generation is already in progress."
    assert "IntegrityError" not in str(body)
    assert "Traceback" not in str(body)
    rows = (await db.execute(
        ScrapingBlueprint.__table__.select().where(ScrapingBlueprint.mission_id == mission.id)
    )).mappings().all()
    assert [(row["id"], row["version"], row["status"]) for row in rows] == [
        (active.id, 1, ScrapingBlueprintStatus.QUEUED.value)
    ]


@pytest.mark.asyncio
async def test_revision_and_regeneration_create_new_queued_versions(db, auth, monkeypatch):
    mission, source = await mission_and_blueprint(db, auth)

    async def no_enqueue(_blueprint_id: str) -> None:
        return None

    monkeypatch.setattr(blueprint_service, "enqueue_blueprint", no_enqueue)
    revision = await blueprint_service.request_changes(
        db,
        auth,
        source.id,
        ScrapingBlueprintChangeRequest(change_instructions="Add regional registry evidence."),
    )
    regenerated = await blueprint_service.regenerate_blueprint(db, auth, source.id)

    assert (revision.mission_id, revision.version, revision.status) == (mission.id, 2, "queued")
    assert revision.revision_request == "Add regional registry evidence."
    revision_row = await db.get(ScrapingBlueprint, revision.id)
    assert revision_row is not None
    assert "Previous human blueprint:\nOriginal" in (revision_row.rendered_prompt_snapshot or "")
    assert (regenerated.mission_id, regenerated.version, regenerated.status) == (
        mission.id,
        3,
        "queued",
    )


@pytest.mark.asyncio
async def test_other_organization_cannot_revise_regenerate_reject_or_discard(db, auth):
    _, blueprint = await mission_and_blueprint(db, auth)
    other = await create_other_auth(db)

    with pytest.raises(NotFoundError):
        await blueprint_service.request_changes(
            db,
            other,
            blueprint.id,
            ScrapingBlueprintChangeRequest(change_instructions="Unauthorised"),
        )
    with pytest.raises(NotFoundError):
        await blueprint_service.regenerate_blueprint(db, other, blueprint.id)
    with pytest.raises(NotFoundError):
        await blueprint_service.reject_blueprint(
            db, other, blueprint.id, ScrapingBlueprintRejectRequest(reason="Unauthorised")
        )
    with pytest.raises(NotFoundError):
        await blueprint_service.discard_blueprint(db, other, blueprint.id)


@pytest.mark.asyncio
async def test_reject_and_discard_are_terminal_and_authorized(db, auth):
    _, review_ready = await mission_and_blueprint(db, auth)
    rejected = await blueprint_service.reject_blueprint(
        db, auth, review_ready.id, ScrapingBlueprintRejectRequest(reason="Coverage is incomplete.")
    )
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "Coverage is incomplete."
    with pytest.raises(ValidationError):
        await blueprint_service.discard_blueprint(db, auth, review_ready.id)

    _, discardable = await mission_and_blueprint(db, auth)
    discarded = await blueprint_service.discard_blueprint(db, auth, discardable.id)
    assert discarded.status == "discarded"
    assert discarded.discarded_at is not None
    with pytest.raises(ValidationError):
        await blueprint_service.reject_blueprint(
            db, auth, discardable.id, ScrapingBlueprintRejectRequest(reason="Too late")
        )
