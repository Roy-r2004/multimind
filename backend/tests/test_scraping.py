import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.db.models import ScrapingBlueprint, ScrapingBlueprintStatus, ScrapingMission, ScrapingMissionStatus
from app.main import create_app
from app.schemas.api import (
    ScrapingBlueprintChangeRequest,
    ScrapingBlueprintContent,
    ScrapingBlueprintRejectRequest,
    ScrapingBlueprintRenameRequest,
    ScrapingMissionCreate,
    ScrapingMissionUpdate,
)
from app.scraping.blueprint_orchestrator import BlueprintOrchestrator
from app.services.domain_service import project_service
from app.services.scraping.blueprint_service import blueprint_service
from app.services.scraping.mission_service import mission_service
from conftest import create_model_set, create_other_auth, create_project, valid_blueprint


class FakeOrchestrator:
    def __init__(self, payload: dict | None = None, should_fail: bool = False) -> None:
        self.payload = payload or valid_blueprint()
        self.should_fail = should_fail
        self.calls = []

    async def generate(self, mission, model_set, previous_blueprint=None, change_instructions=None):
        self.calls.append((mission, model_set, previous_blueprint, change_instructions))
        if self.should_fail:
            raise RuntimeError("provider failed")
        return ScrapingBlueprintContent.model_validate(self.payload)


async def create_mission(db: AsyncSession, auth: AuthContext) -> str:
    await create_model_set(db, auth)
    mission = await mission_service.create_mission(
        db,
        auth,
        ScrapingMissionCreate(
            title="Mission",
            original_prompt="Find facilities",
            model_set_id="research-set",
        ),
    )
    return mission.id


async def create_blueprint_version(
    db: AsyncSession,
    auth: AuthContext,
    *,
    mission_id: str | None = None,
    version: int = 1,
    status: ScrapingBlueprintStatus = ScrapingBlueprintStatus.DRAFT,
    active: bool = False,
) -> ScrapingBlueprint:
    if mission_id is None:
        mission_id = await create_mission(db, auth)
    blueprint = ScrapingBlueprint(
        mission_id=mission_id,
        version=version,
        status=status,
        blueprint_json=None if status == ScrapingBlueprintStatus.GENERATING else valid_blueprint(),
        model_set_id="research-set",
        judge_model_id="gpt-4.1",
    )
    db.add(blueprint)
    await db.flush()
    mission = await db.get(ScrapingMission, mission_id)
    assert mission is not None
    if active:
        mission.active_blueprint_id = blueprint.id
        mission.status = ScrapingMissionStatus.APPROVED
    elif status == ScrapingBlueprintStatus.GENERATING:
        mission.status = ScrapingMissionStatus.BLUEPRINT_GENERATING
    elif status == ScrapingBlueprintStatus.DRAFT:
        mission.status = ScrapingMissionStatus.AWAITING_APPROVAL
    elif status == ScrapingBlueprintStatus.FAILED:
        mission.status = ScrapingMissionStatus.FAILED
    elif status == ScrapingBlueprintStatus.REJECTED:
        mission.status = ScrapingMissionStatus.REJECTED
    await db.flush()
    return blueprint


@pytest.mark.asyncio
async def test_unauthenticated_mission_creation_returns_authentication_error(db: AsyncSession):
    from app.db.session import get_db

    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/scraping/missions", json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_mission_creation_succeeds(db: AsyncSession, auth: AuthContext):
    await create_model_set(db, auth)
    mission = await mission_service.create_mission(
        db,
        auth,
        ScrapingMissionCreate(title=" Mission ", original_prompt=" Prompt ", model_set_id="research-set"),
    )
    assert mission.title == "Mission"
    assert mission.original_prompt == "Prompt"
    assert mission.status == "draft"


@pytest.mark.asyncio
async def test_mission_creation_rejects_empty_title(db: AsyncSession, auth: AuthContext):
    await create_model_set(db, auth)
    with pytest.raises(Exception, match="Mission title is required"):
        await mission_service.create_mission(
            db,
            auth,
            ScrapingMissionCreate(title=" ", original_prompt="Prompt", model_set_id="research-set"),
        )


@pytest.mark.asyncio
async def test_mission_creation_rejects_empty_prompt(db: AsyncSession, auth: AuthContext):
    await create_model_set(db, auth)
    with pytest.raises(Exception, match="Mission prompt is required"):
        await mission_service.create_mission(
            db,
            auth,
            ScrapingMissionCreate(title="Mission", original_prompt=" ", model_set_id="research-set"),
        )


@pytest.mark.asyncio
async def test_mission_creation_rejects_another_organizations_model_set(db: AsyncSession, auth: AuthContext):
    other = await create_other_auth(db)
    await create_model_set(db, other, slug="other-set")
    with pytest.raises(Exception, match="ModelSet not found"):
        await mission_service.create_mission(
            db,
            auth,
            ScrapingMissionCreate(title="Mission", original_prompt="Prompt", model_set_id="other-set"),
        )


@pytest.mark.asyncio
async def test_mission_creation_rejects_another_organizations_project(db: AsyncSession, auth: AuthContext):
    await create_model_set(db, auth)
    other = await create_other_auth(db)
    other_project = await create_project(db, other)
    with pytest.raises(Exception, match="Project not found"):
        await mission_service.create_mission(
            db,
            auth,
            ScrapingMissionCreate(
                title="Mission",
                original_prompt="Prompt",
                model_set_id="research-set",
                project_id=other_project.id,
            ),
        )


@pytest.mark.asyncio
async def test_mission_list_returns_only_current_organization_missions(db: AsyncSession, auth: AuthContext):
    await create_mission(db, auth)
    other = await create_other_auth(db)
    await create_mission(db, other)
    missions = await mission_service.list_missions(db, auth)
    assert len(missions) == 1


@pytest.mark.asyncio
async def test_mission_detail_rejects_another_organization(db: AsyncSession, auth: AuthContext):
    other = await create_other_auth(db)
    mission_id = await create_mission(db, other)
    with pytest.raises(Exception, match="ScrapingMission not found"):
        await mission_service.get_mission(db, auth, mission_id)


@pytest.mark.asyncio
async def test_blueprint_generation_creates_version_1_and_uses_selected_model_set(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    fake = FakeOrchestrator()
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: fake)
    blueprint = await blueprint_service.generate_blueprint(db, auth, mission_id)
    assert blueprint.version == 1
    assert blueprint.model_set_id == "research-set"
    assert fake.calls[0][1].slug == "research-set"


@pytest.mark.asyncio
async def test_second_blueprint_generation_creates_version_2(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    first = await blueprint_service.generate_blueprint(db, auth, mission_id)
    await blueprint_service.reject_blueprint(db, auth, first.id, ScrapingBlueprintRejectRequest(reason="No"))
    second = await blueprint_service.generate_blueprint(db, auth, mission_id)
    assert second.version == 2


@pytest.mark.asyncio
async def test_valid_mocked_judge_json_is_saved(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    blueprint = await blueprint_service.generate_blueprint(db, auth, mission_id)
    assert blueprint.blueprint_json is not None
    assert blueprint.status == "draft"


@pytest.mark.asyncio
async def test_invalid_repair_output_marks_blueprint_failed(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr(
        "app.services.scraping.blueprint_service.get_blueprint_orchestrator",
        lambda: FakeOrchestrator(should_fail=True),
    )
    blueprint = await blueprint_service.generate_blueprint(db, auth, mission_id)
    assert blueprint.status == "failed"
    assert blueprint.blueprint_json is None


@pytest.mark.asyncio
async def test_invalid_judge_json_triggers_one_repair_call():
    from app.llm.providers import LLMResponse

    class Provider:
        def __init__(self):
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            return LLMResponse(text=__import__("json").dumps(valid_blueprint()), tokens_input=1, tokens_output=1)

    provider = Provider()
    orchestrator = BlueprintOrchestrator()
    result = await orchestrator._parse_validate_or_repair(
        provider=provider,
        model="openai/gpt-4.1",
        invalid_output='{"mission_summary": {}}',
    )
    assert result.mission_summary.goal == "Find target entities"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_approve_draft_blueprint_succeeds_sets_active_status_and_supersedes(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    older = await blueprint_service.generate_blueprint(db, auth, mission_id)
    await blueprint_service.reject_blueprint(db, auth, older.id, ScrapingBlueprintRejectRequest(reason="Change"))
    newer = await blueprint_service.generate_blueprint(db, auth, mission_id)
    approved = await blueprint_service.approve_blueprint(db, auth, newer.id)
    mission = await mission_service.get_mission(db, auth, mission_id)
    assert approved.status == "approved"
    assert mission.active_blueprint_id == newer.id
    assert mission.status == "approved"


@pytest.mark.asyncio
async def test_approve_non_draft_blueprint_fails(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    blueprint = await blueprint_service.generate_blueprint(db, auth, mission_id)
    await blueprint_service.approve_blueprint(db, auth, blueprint.id)
    with pytest.raises(Exception, match="Only draft blueprints can be approved"):
        await blueprint_service.approve_blueprint(db, auth, blueprint.id)


@pytest.mark.asyncio
async def test_approval_supersedes_older_versions_and_does_not_start_scraping(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    first = await blueprint_service.generate_blueprint(db, auth, mission_id)
    await blueprint_service.approve_blueprint(db, auth, first.id)
    second = await blueprint_service.generate_blueprint(db, auth, mission_id)
    await blueprint_service.approve_blueprint(db, auth, second.id)
    rows = (await db.execute(select(ScrapingBlueprint).order_by(ScrapingBlueprint.version))).scalars().all()
    assert rows[0].status == ScrapingBlueprintStatus.SUPERSEDED
    assert rows[1].status == ScrapingBlueprintStatus.APPROVED
    assert (await db.get(ScrapingMission, mission_id)).status == ScrapingMissionStatus.APPROVED


@pytest.mark.asyncio
async def test_reject_draft_blueprint_succeeds_and_requires_reason(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    blueprint = await blueprint_service.generate_blueprint(db, auth, mission_id)
    with pytest.raises(Exception, match="Rejection reason is required"):
        await blueprint_service.reject_blueprint(db, auth, blueprint.id, ScrapingBlueprintRejectRequest(reason=" "))
    rejected = await blueprint_service.reject_blueprint(db, auth, blueprint.id, ScrapingBlueprintRejectRequest(reason="Bad scope"))
    assert rejected.status == "rejected"


@pytest.mark.asyncio
async def test_request_changes_creates_new_version_preserves_previous_and_does_not_replace_active(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    original = await blueprint_service.generate_blueprint(db, auth, mission_id)
    await blueprint_service.approve_blueprint(db, auth, original.id)
    created = await blueprint_service.request_changes(
        db,
        auth,
        original.id,
        ScrapingBlueprintChangeRequest(change_instructions="Tighten scope"),
    )
    mission = await mission_service.get_mission(db, auth, mission_id)
    assert created.version == 2
    assert created.status == "draft"
    assert mission.active_blueprint_id == original.id
    previous = await blueprint_service.get_blueprint(db, auth, original.id)
    assert previous.status == "approved"


@pytest.mark.asyncio
async def test_another_organization_cannot_approve_a_blueprint(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    blueprint = await blueprint_service.generate_blueprint(db, auth, mission_id)
    other = await create_other_auth(db)
    with pytest.raises(Exception, match="ScrapingBlueprint not found"):
        await blueprint_service.approve_blueprint(db, other, blueprint.id)


@pytest.mark.asyncio
async def test_approved_blueprint_cannot_be_modified(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    blueprint = await blueprint_service.generate_blueprint(db, auth, mission_id)
    approved = await blueprint_service.approve_blueprint(db, auth, blueprint.id)
    with pytest.raises(Exception, match="Only draft blueprints can be rejected"):
        await blueprint_service.reject_blueprint(db, auth, approved.id, ScrapingBlueprintRejectRequest(reason="No"))


@pytest.mark.asyncio
async def test_mission_deletion_deletes_blueprint_history(db: AsyncSession, auth: AuthContext, monkeypatch):
    mission_id = await create_mission(db, auth)
    monkeypatch.setattr("app.services.scraping.blueprint_service.get_blueprint_orchestrator", lambda: FakeOrchestrator())
    await blueprint_service.generate_blueprint(db, auth, mission_id)
    await mission_service.delete_mission(db, auth, mission_id)
    rows = (await db.execute(select(ScrapingBlueprint))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_rename_draft_blueprint_succeeds(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(db, auth)
    renamed = await blueprint_service.rename_blueprint(
        db, auth, blueprint.id, ScrapingBlueprintRenameRequest(name="Final Lebanon Strategy")
    )
    assert renamed.display_name == "Final Lebanon Strategy"
    assert renamed.version == 1
    assert renamed.status == "draft"


@pytest.mark.asyncio
async def test_rename_approved_blueprint_succeeds_without_changing_blueprint_json(
    db: AsyncSession, auth: AuthContext
):
    blueprint = await create_blueprint_version(
        db, auth, status=ScrapingBlueprintStatus.APPROVED, active=True
    )
    before = dict(blueprint.blueprint_json)
    renamed = await blueprint_service.rename_blueprint(
        db, auth, blueprint.id, ScrapingBlueprintRenameRequest(name="Approved Strategy")
    )
    row = await db.get(ScrapingBlueprint, blueprint.id)
    assert renamed.display_name == "Approved Strategy"
    assert row is not None
    assert row.blueprint_json == before
    assert row.status == ScrapingBlueprintStatus.APPROVED


@pytest.mark.asyncio
async def test_rename_rejected_blueprint_succeeds(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(
        db, auth, status=ScrapingBlueprintStatus.REJECTED
    )
    renamed = await blueprint_service.rename_blueprint(
        db, auth, blueprint.id, ScrapingBlueprintRenameRequest(name="Rejected Strategy")
    )
    assert renamed.display_name == "Rejected Strategy"
    assert renamed.status == "rejected"


@pytest.mark.asyncio
async def test_rename_trims_whitespace(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(db, auth)
    renamed = await blueprint_service.rename_blueprint(
        db, auth, blueprint.id, ScrapingBlueprintRenameRequest(name="  Trimmed Name  ")
    )
    assert renamed.display_name == "Trimmed Name"


def test_rename_rejects_empty_name():
    with pytest.raises(Exception):
        ScrapingBlueprintRenameRequest(name="")


def test_rename_rejects_whitespace_only_name():
    with pytest.raises(Exception):
        ScrapingBlueprintRenameRequest(name="   ")


def test_rename_rejects_names_longer_than_160_characters():
    with pytest.raises(Exception):
        ScrapingBlueprintRenameRequest(name="x" * 161)


@pytest.mark.asyncio
async def test_rename_generating_blueprint_fails(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(
        db, auth, status=ScrapingBlueprintStatus.GENERATING
    )
    with pytest.raises(Exception, match="currently generating cannot be renamed"):
        await blueprint_service.rename_blueprint(
            db, auth, blueprint.id, ScrapingBlueprintRenameRequest(name="Generating")
        )


@pytest.mark.asyncio
async def test_another_organization_cannot_rename_a_blueprint(
    db: AsyncSession, auth: AuthContext
):
    blueprint = await create_blueprint_version(db, auth)
    other = await create_other_auth(db)
    with pytest.raises(Exception, match="ScrapingBlueprint not found"):
        await blueprint_service.rename_blueprint(
            db, other, blueprint.id, ScrapingBlueprintRenameRequest(name="No Access")
        )


@pytest.mark.asyncio
async def test_delete_draft_blueprint_succeeds(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(db, auth)
    await blueprint_service.delete_blueprint(db, auth, blueprint.id)
    assert await db.get(ScrapingBlueprint, blueprint.id) is None


@pytest.mark.asyncio
async def test_delete_rejected_blueprint_succeeds(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(
        db, auth, status=ScrapingBlueprintStatus.REJECTED
    )
    await blueprint_service.delete_blueprint(db, auth, blueprint.id)
    assert await db.get(ScrapingBlueprint, blueprint.id) is None


@pytest.mark.asyncio
async def test_delete_superseded_blueprint_succeeds(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(
        db, auth, status=ScrapingBlueprintStatus.SUPERSEDED
    )
    await blueprint_service.delete_blueprint(db, auth, blueprint.id)
    assert await db.get(ScrapingBlueprint, blueprint.id) is None


@pytest.mark.asyncio
async def test_delete_failed_blueprint_succeeds(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(db, auth, status=ScrapingBlueprintStatus.FAILED)
    await blueprint_service.delete_blueprint(db, auth, blueprint.id)
    assert await db.get(ScrapingBlueprint, blueprint.id) is None


@pytest.mark.asyncio
async def test_delete_approved_blueprint_fails(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(
        db, auth, status=ScrapingBlueprintStatus.APPROVED
    )
    with pytest.raises(Exception, match="active approved blueprint cannot be deleted"):
        await blueprint_service.delete_blueprint(db, auth, blueprint.id)


@pytest.mark.asyncio
async def test_delete_active_blueprint_fails(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(db, auth, active=True)
    with pytest.raises(Exception, match="active approved blueprint cannot be deleted"):
        await blueprint_service.delete_blueprint(db, auth, blueprint.id)


@pytest.mark.asyncio
async def test_delete_generating_blueprint_fails(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(
        db, auth, status=ScrapingBlueprintStatus.GENERATING
    )
    with pytest.raises(Exception, match="currently generating cannot be deleted"):
        await blueprint_service.delete_blueprint(db, auth, blueprint.id)


@pytest.mark.asyncio
async def test_another_organization_cannot_delete_a_blueprint(
    db: AsyncSession, auth: AuthContext
):
    blueprint = await create_blueprint_version(db, auth)
    other = await create_other_auth(db)
    with pytest.raises(Exception, match="ScrapingBlueprint not found"):
        await blueprint_service.delete_blueprint(db, other, blueprint.id)


@pytest.mark.asyncio
async def test_deleting_one_version_preserves_other_versions(db: AsyncSession, auth: AuthContext):
    mission_id = await create_mission(db, auth)
    first = await create_blueprint_version(db, auth, mission_id=mission_id, version=1)
    second = await create_blueprint_version(db, auth, mission_id=mission_id, version=2)
    third = await create_blueprint_version(db, auth, mission_id=mission_id, version=3)
    await blueprint_service.delete_blueprint(db, auth, second.id)
    rows = (
        (await db.execute(select(ScrapingBlueprint).order_by(ScrapingBlueprint.version)))
        .scalars()
        .all()
    )
    assert [row.id for row in rows] == [first.id, third.id]


@pytest.mark.asyncio
async def test_deleting_a_version_does_not_renumber_remaining_versions(
    db: AsyncSession, auth: AuthContext
):
    mission_id = await create_mission(db, auth)
    await create_blueprint_version(db, auth, mission_id=mission_id, version=1)
    second = await create_blueprint_version(db, auth, mission_id=mission_id, version=2)
    await create_blueprint_version(db, auth, mission_id=mission_id, version=3)
    await blueprint_service.delete_blueprint(db, auth, second.id)
    rows = (
        (await db.execute(select(ScrapingBlueprint).order_by(ScrapingBlueprint.version)))
        .scalars()
        .all()
    )
    assert [row.version for row in rows] == [1, 3]


@pytest.mark.asyncio
async def test_deleting_the_final_blueprint_sets_mission_status_draft(
    db: AsyncSession, auth: AuthContext
):
    blueprint = await create_blueprint_version(db, auth)
    mission_id = blueprint.mission_id
    await blueprint_service.delete_blueprint(db, auth, blueprint.id)
    mission = await db.get(ScrapingMission, mission_id)
    assert mission is not None
    assert mission.status == ScrapingMissionStatus.DRAFT


@pytest.mark.asyncio
async def test_deleting_a_draft_while_an_active_blueprint_exists_keeps_mission_status_approved(
    db: AsyncSession, auth: AuthContext
):
    mission_id = await create_mission(db, auth)
    await create_blueprint_version(
        db,
        auth,
        mission_id=mission_id,
        version=1,
        status=ScrapingBlueprintStatus.APPROVED,
        active=True,
    )
    draft = await create_blueprint_version(db, auth, mission_id=mission_id, version=2)
    await blueprint_service.delete_blueprint(db, auth, draft.id)
    mission = await db.get(ScrapingMission, mission_id)
    assert mission is not None
    assert mission.status == ScrapingMissionStatus.APPROVED


@pytest.mark.asyncio
async def test_deleting_the_selected_draft_does_not_clear_an_unrelated_active_blueprint_id(
    db: AsyncSession, auth: AuthContext
):
    mission_id = await create_mission(db, auth)
    active = await create_blueprint_version(
        db,
        auth,
        mission_id=mission_id,
        version=1,
        status=ScrapingBlueprintStatus.APPROVED,
        active=True,
    )
    draft = await create_blueprint_version(db, auth, mission_id=mission_id, version=2)
    await blueprint_service.delete_blueprint(db, auth, draft.id)
    mission = await db.get(ScrapingMission, mission_id)
    assert mission is not None
    assert mission.active_blueprint_id == active.id


@pytest.mark.asyncio
async def test_explicit_project_id_null_removes_the_mission_from_its_project(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    updated = await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=None)
    )
    assert updated.project_id is None


@pytest.mark.asyncio
async def test_assigning_a_mission_to_a_project_succeeds(db: AsyncSession, auth: AuthContext):
    project = await create_project(db, auth)
    mission_id = await create_mission(db, auth)
    updated = await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    assert updated.project_id == project.id
    assert updated.project_name == project.name


@pytest.mark.asyncio
async def test_project_assignment_persists_after_reloading_the_mission(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    reloaded = await mission_service.get_mission(db, auth, mission_id)
    assert reloaded.project_id == project.id


@pytest.mark.asyncio
async def test_moving_from_one_project_to_another_succeeds(
    db: AsyncSession, auth: AuthContext
):
    old_project = await create_project(db, auth, name="Old Project")
    new_project = await create_project(db, auth, name="New Project")
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=old_project.id)
    )
    moved = await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=new_project.id)
    )
    assert moved.project_id == new_project.id


@pytest.mark.asyncio
async def test_assigning_a_project_does_not_change_blueprint_statuses(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    blueprint = await create_blueprint_version(db, auth)
    await mission_service.update_mission(
        db, auth, blueprint.mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    row = await db.get(ScrapingBlueprint, blueprint.id)
    assert row is not None
    assert row.status == ScrapingBlueprintStatus.DRAFT


@pytest.mark.asyncio
async def test_assigning_a_project_does_not_create_a_new_blueprint(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    blueprint = await create_blueprint_version(db, auth)
    await mission_service.update_mission(
        db, auth, blueprint.mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    rows = (
        (
            await db.execute(
                select(ScrapingBlueprint).where(
                    ScrapingBlueprint.mission_id == blueprint.mission_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_omitted_project_id_preserves_the_current_project(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    updated = await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(title="Renamed Mission")
    )
    assert updated.project_id == project.id


@pytest.mark.asyncio
async def test_assigning_another_organizations_project_fails(
    db: AsyncSession, auth: AuthContext
):
    other = await create_other_auth(db)
    other_project = await create_project(db, other)
    mission_id = await create_mission(db, auth)
    with pytest.raises(Exception, match="Project not found"):
        await mission_service.update_mission(
            db, auth, mission_id, ScrapingMissionUpdate(project_id=other_project.id)
        )


@pytest.mark.asyncio
async def test_project_detail_returns_assigned_scraping_missions(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    detail = await project_service.get_detail(db, auth, project.id)
    assert [mission.id for mission in detail.scraping_missions] == [mission_id]
    assert not hasattr(detail.scraping_missions[0], "blueprint_json")


@pytest.mark.asyncio
async def test_project_detail_does_not_return_missions_from_other_projects(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth, name="Project A")
    other_project = await create_project(db, auth, name="Project B")
    mission_id = await create_mission(db, auth)
    other_mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    await mission_service.update_mission(
        db, auth, other_mission_id, ScrapingMissionUpdate(project_id=other_project.id)
    )
    detail = await project_service.get_detail(db, auth, project.id)
    assert [mission.id for mission in detail.scraping_missions] == [mission_id]


@pytest.mark.asyncio
async def test_moving_a_mission_removes_it_from_the_old_project_response(
    db: AsyncSession, auth: AuthContext
):
    old_project = await create_project(db, auth, name="Old Project")
    new_project = await create_project(db, auth, name="New Project")
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=old_project.id)
    )
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=new_project.id)
    )
    old_detail = await project_service.get_detail(db, auth, old_project.id)
    new_detail = await project_service.get_detail(db, auth, new_project.id)
    assert old_detail.scraping_missions == []
    assert [mission.id for mission in new_detail.scraping_missions] == [mission_id]


@pytest.mark.asyncio
async def test_removing_project_id_removes_mission_from_project_detail(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=None)
    )
    detail = await project_service.get_detail(db, auth, project.id)
    assert detail.scraping_missions == []


@pytest.mark.asyncio
async def test_cross_organization_missions_are_not_exposed_in_project_detail(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    other = await create_other_auth(db)
    other_project = await create_project(db, other)
    other_mission_id = await create_mission(db, other)
    await mission_service.update_mission(
        db, other, other_mission_id, ScrapingMissionUpdate(project_id=other_project.id)
    )
    detail = await project_service.get_detail(db, auth, project.id)
    assert detail.scraping_missions == []


@pytest.mark.asyncio
async def test_mission_rename_succeeds(db: AsyncSession, auth: AuthContext):
    mission_id = await create_mission(db, auth)
    updated = await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(title="New Mission Name")
    )
    assert updated.title == "New Mission Name"


@pytest.mark.asyncio
async def test_mission_rename_trims_whitespace(db: AsyncSession, auth: AuthContext):
    mission_id = await create_mission(db, auth)
    updated = await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(title="  Trimmed Mission  ")
    )
    assert updated.title == "Trimmed Mission"


@pytest.mark.asyncio
async def test_mission_rename_rejects_empty_title(db: AsyncSession, auth: AuthContext):
    mission_id = await create_mission(db, auth)
    with pytest.raises(Exception, match="Mission title is required"):
        await mission_service.update_mission(
            db, auth, mission_id, ScrapingMissionUpdate(title="   ")
        )


@pytest.mark.asyncio
async def test_mission_rename_is_reflected_in_project_detail(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(title="Project Visible Name")
    )
    detail = await project_service.get_detail(db, auth, project.id)
    assert detail.scraping_missions[0].title == "Project Visible Name"


@pytest.mark.asyncio
async def test_cross_organization_mission_rename_fails(db: AsyncSession, auth: AuthContext):
    mission_id = await create_mission(db, auth)
    other = await create_other_auth(db)
    with pytest.raises(Exception, match="ScrapingMission not found"):
        await mission_service.update_mission(
            db, other, mission_id, ScrapingMissionUpdate(title="No Access")
        )


@pytest.mark.asyncio
async def test_delete_draft_mission_succeeds(db: AsyncSession, auth: AuthContext):
    mission_id = await create_mission(db, auth)
    await mission_service.delete_mission(db, auth, mission_id)
    assert await db.get(ScrapingMission, mission_id) is None


@pytest.mark.asyncio
async def test_delete_approved_mission_succeeds(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(
        db, auth, status=ScrapingBlueprintStatus.APPROVED, active=True
    )
    await mission_service.delete_mission(db, auth, blueprint.mission_id)
    assert await db.get(ScrapingMission, blueprint.mission_id) is None


@pytest.mark.asyncio
async def test_delete_rejected_mission_succeeds(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(
        db, auth, status=ScrapingBlueprintStatus.REJECTED
    )
    await mission_service.delete_mission(db, auth, blueprint.mission_id)
    assert await db.get(ScrapingMission, blueprint.mission_id) is None


@pytest.mark.asyncio
async def test_delete_failed_mission_succeeds(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(db, auth, status=ScrapingBlueprintStatus.FAILED)
    await mission_service.delete_mission(db, auth, blueprint.mission_id)
    assert await db.get(ScrapingMission, blueprint.mission_id) is None


@pytest.mark.asyncio
async def test_deleting_a_mission_removes_its_blueprints(db: AsyncSession, auth: AuthContext):
    blueprint = await create_blueprint_version(db, auth)
    await mission_service.delete_mission(db, auth, blueprint.mission_id)
    rows = (await db.execute(select(ScrapingBlueprint))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_deleting_a_mission_removes_it_from_project_detail(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    await mission_service.delete_mission(db, auth, mission_id)
    detail = await project_service.get_detail(db, auth, project.id)
    assert detail.scraping_missions == []


@pytest.mark.asyncio
async def test_deleting_a_mission_does_not_delete_the_project(
    db: AsyncSession, auth: AuthContext
):
    project = await create_project(db, auth)
    mission_id = await create_mission(db, auth)
    await mission_service.update_mission(
        db, auth, mission_id, ScrapingMissionUpdate(project_id=project.id)
    )
    await mission_service.delete_mission(db, auth, mission_id)
    assert await db.get(type(project), project.id) is not None


@pytest.mark.asyncio
async def test_deleting_a_mission_does_not_remove_other_missions(
    db: AsyncSession, auth: AuthContext
):
    first = await create_mission(db, auth)
    second = await create_mission(db, auth)
    await mission_service.delete_mission(db, auth, first)
    assert await db.get(ScrapingMission, second) is not None


@pytest.mark.asyncio
async def test_deleting_a_generating_mission_fails(db: AsyncSession, auth: AuthContext):
    mission_id = await create_mission(db, auth)
    mission = await db.get(ScrapingMission, mission_id)
    assert mission is not None
    mission.status = ScrapingMissionStatus.BLUEPRINT_GENERATING
    await db.flush()
    with pytest.raises(Exception, match="cannot be deleted while its blueprint is generating"):
        await mission_service.delete_mission(db, auth, mission_id)


@pytest.mark.asyncio
async def test_cross_organization_mission_deletion_fails(db: AsyncSession, auth: AuthContext):
    mission_id = await create_mission(db, auth)
    other = await create_other_auth(db)
    with pytest.raises(Exception, match="ScrapingMission not found"):
        await mission_service.delete_mission(db, other, mission_id)
