import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.models import (
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingMission,
    ScrapingMissionStatus,
    ScrapingRun,
    ScrapingRunAgent,
    ScrapingRunStatus,
)
from app.llm.providers import LLMResponse
from app.main import create_app
from app.schemas.api import (
    ScrapingBlueprintChangeRequest,
    ScrapingBlueprintContent,
    ScrapingBlueprintRejectRequest,
    ScrapingBlueprintRenameRequest,
    ScrapingMissionCreate,
    ScrapingMissionUpdate,
    ScrapingTeamPlanOutput,
)
from app.scraping.blueprint_orchestrator import BlueprintOrchestrator
from app.services.domain_service import project_service
from app.services.scraping.blueprint_service import blueprint_service
from app.services.scraping.mission_service import mission_service
from app.services.scraping.run_service import run_service
from app.services.scraping.team_planner_service import TeamPlannerService, team_planner_service
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


def team_plan_payload(count: int, *, model_id: str = "gpt-4.1") -> dict:
    agents = []
    for index in range(1, count + 1):
        agents.append(
            {
                "sequence": index,
                "name": f"Agent {index}",
                "role": "verification" if index == count else f"role_{index}",
                "purpose": f"Purpose {index}",
                "instructions": f"Instructions {index}",
                "assigned_scope": {
                    "regions": [f"Region {index}"],
                    "languages": ["en"],
                    "source_categories": ["official"],
                },
                "model_id": model_id,
                "depends_on": [index - 1] if index > 1 else [],
            }
        )
    return {
        "recommended_agent_count": count,
        "rationale": f"{count} agents are appropriate for the approved blueprint.",
        "agents": agents,
    }


async def create_active_approved_blueprint(
    db: AsyncSession, auth: AuthContext
) -> ScrapingBlueprint:
    return await create_blueprint_version(
        db,
        auth,
        status=ScrapingBlueprintStatus.APPROVED,
        active=True,
    )


def mock_planner(monkeypatch, count: int = 3):
    async def fake_plan_team(mission, blueprint, model_set):
        return (
            ScrapingTeamPlanOutput.model_validate(team_plan_payload(count)),
            model_set.verdict_model,
        )

    monkeypatch.setattr(team_planner_service, "plan_team", fake_plan_team)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [3, 7])
async def test_dynamic_planning_persists_ai_selected_agent_count(
    db: AsyncSession, auth: AuthContext, monkeypatch, count: int
):
    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch, count)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    assert run.status == "planned"
    assert run.recommended_agent_count == count
    assert len(run.agents) == count
    assert count != 5 or len(run.agents) == 5
    assert run.blueprint_id == blueprint.id
    assert run.blueprint_version == blueprint.version
    assert run.planner_model_id == "gpt-4.1"
    assert run.planner_rationale == f"{count} agents are appropriate for the approved blueprint."
    assert [agent.sequence for agent in run.agents] == list(range(1, count + 1))
    assert run.agents[1].dependency_agent_ids == [run.agents[0].id]
    assert run.agents[0].assigned_scope["regions"] == ["Region 1"]


@pytest.mark.asyncio
async def test_planning_rejects_mission_without_active_approved_blueprint(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_blueprint_version(db, auth, status=ScrapingBlueprintStatus.DRAFT)
    mock_planner(monkeypatch)
    with pytest.raises(Exception, match="active approved blueprint"):
        await run_service.plan_team(db, auth, blueprint.mission_id)


@pytest.mark.asyncio
async def test_planning_rejects_generating_mission(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_blueprint_version(
        db,
        auth,
        status=ScrapingBlueprintStatus.GENERATING,
    )
    mock_planner(monkeypatch)
    with pytest.raises(Exception, match="active approved blueprint"):
        await run_service.plan_team(db, auth, blueprint.mission_id)


@pytest.mark.asyncio
async def test_planning_does_not_use_non_active_approved_blueprint(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    mission_id = await create_mission(db, auth)
    approved = await create_blueprint_version(
        db,
        auth,
        mission_id=mission_id,
        status=ScrapingBlueprintStatus.APPROVED,
    )
    draft = await create_blueprint_version(
        db,
        auth,
        mission_id=mission_id,
        version=2,
        status=ScrapingBlueprintStatus.DRAFT,
        active=True,
    )
    mission = await db.get(ScrapingMission, mission_id)
    assert mission is not None
    mission.active_blueprint_id = draft.id
    await db.flush()
    mock_planner(monkeypatch)
    with pytest.raises(Exception, match="active approved blueprint"):
        await run_service.plan_team(db, auth, approved.mission_id)


@pytest.mark.asyncio
async def test_cross_organization_mission_cannot_plan_run(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_active_approved_blueprint(db, auth)
    other = await create_other_auth(db)
    mock_planner(monkeypatch)
    with pytest.raises(Exception, match="ScrapingMission not found"):
        await run_service.plan_team(db, other, blueprint.mission_id)


@pytest.mark.asyncio
async def test_duplicate_planning_run_is_rejected(db: AsyncSession, auth: AuthContext, monkeypatch):
    blueprint = await create_active_approved_blueprint(db, auth)
    db.add(
        ScrapingRun(
            organization_id=auth.org_id,
            mission_id=blueprint.mission_id,
            blueprint_id=blueprint.id,
            model_set_id="research-set",
            status=ScrapingRunStatus.PLANNING,
        )
    )
    await db.flush()
    mock_planner(monkeypatch)
    with pytest.raises(Exception, match="already exists"):
        await run_service.plan_team(db, auth, blueprint.mission_id)


@pytest.mark.asyncio
async def test_second_plan_for_exact_blueprint_returns_409(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    from app.db.session import get_db

    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch)
    app = create_app()

    async def override_db():
        yield db

    async def override_auth():
        return auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(f"/api/v1/scraping/missions/{blueprint.mission_id}/runs/plan")
        second = await client.post(f"/api/v1/scraping/missions/{blueprint.mission_id}/runs/plan")

    assert first.status_code == 200
    assert second.status_code == 409
    body = second.json()
    assert body["message"] == "An AI scraping team plan already exists for this blueprint version."
    assert body["details"] == {
        "message": "An AI scraping team plan already exists for this blueprint version.",
        "existing_run_id": first.json()["id"],
        "existing_run_status": "planned",
    }


@pytest.mark.asyncio
async def test_different_approved_blueprint_version_can_receive_own_run(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    first_blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch)
    first_run = await run_service.plan_team(db, auth, first_blueprint.mission_id)
    second_blueprint = await create_blueprint_version(
        db,
        auth,
        mission_id=first_blueprint.mission_id,
        version=2,
        status=ScrapingBlueprintStatus.APPROVED,
        active=True,
    )
    second_run = await run_service.plan_team(db, auth, first_blueprint.mission_id)
    assert first_run.blueprint_id == first_blueprint.id
    assert second_run.blueprint_id == second_blueprint.id
    assert first_run.id != second_run.id


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (team_plan_payload(0), "too few|number of agents|at least 1"),
        (team_plan_payload(1), "too few"),
        (team_plan_payload(13), "too many"),
        ({**team_plan_payload(3), "recommended_agent_count": 2}, "recommended_agent_count"),
        (
            {
                **team_plan_payload(3),
                "agents": [
                    {**team_plan_payload(3)["agents"][0], "sequence": 1},
                    {**team_plan_payload(3)["agents"][1], "sequence": 1},
                    team_plan_payload(3)["agents"][2],
                ],
            },
            "duplicate",
        ),
        (
            {
                **team_plan_payload(3),
                "agents": [
                    {**agent, "model_id": "invented"}
                    for agent in team_plan_payload(3)["agents"]
                ],
            },
            "outside",
        ),
        (
            {
                **team_plan_payload(3),
                "agents": [
                    {
                        key: value
                        for key, value in team_plan_payload(3)["agents"][0].items()
                        if key != "role"
                    },
                    team_plan_payload(3)["agents"][1],
                    team_plan_payload(3)["agents"][2],
                ],
            },
            "schema",
        ),
        (
            {
                **team_plan_payload(3),
                "agents": [
                    {**team_plan_payload(3)["agents"][0], "depends_on": [1]},
                    team_plan_payload(3)["agents"][1],
                    team_plan_payload(3)["agents"][2],
                ],
            },
            "self-dependency",
        ),
        (
            {
                **team_plan_payload(3),
                "agents": [
                    {**team_plan_payload(3)["agents"][0], "depends_on": [99]},
                    team_plan_payload(3)["agents"][1],
                    team_plan_payload(3)["agents"][2],
                ],
            },
            "unknown dependency",
        ),
        (
            {
                **team_plan_payload(3),
                "agents": [
                    {**team_plan_payload(3)["agents"][0], "depends_on": [3]},
                    team_plan_payload(3)["agents"][1],
                    {**team_plan_payload(3)["agents"][2], "depends_on": [1]},
                ],
            },
            "cycle",
        ),
        ({**team_plan_payload(3), "unexpected": "field"}, "schema"),
    ],
)
def test_team_plan_validation_rejects_invalid_structures(payload, message):
    with pytest.raises(Exception, match=message):
        TeamPlannerService().validate_plan_data(payload, allowed_model_ids=["gpt-4.1", "claude"])


@pytest.mark.asyncio
async def test_malformed_planner_json_triggers_one_repair_call():
    class Provider:
        def __init__(self):
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            return LLMResponse(
                text=__import__("json").dumps(team_plan_payload(3)),
                tokens_input=1,
                tokens_output=1,
            )

    provider = Provider()
    plan = await TeamPlannerService().parse_validate_or_repair(
        provider=provider,
        provider_model="openai/gpt-4.1",
        raw_text="{not-json",
        allowed_model_ids=["gpt-4.1", "claude"],
    )
    assert provider.calls == 1
    assert plan.recommended_agent_count == 3


@pytest.mark.asyncio
async def test_invalid_repaired_planner_output_leaves_run_failed(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_active_approved_blueprint(db, auth)

    async def fake_plan_team(mission, blueprint, model_set):
        raise RuntimeError("OpenRouter secret sk-test should not leak")

    monkeypatch.setattr(team_planner_service, "plan_team", fake_plan_team)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    assert run.status == "failed"
    assert run.agents == []
    assert "sk-test" not in (run.error_message or "")


@pytest.mark.asyncio
async def test_list_runs_is_mission_and_organization_scoped(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    first = await create_active_approved_blueprint(db, auth)
    second_mission = ScrapingMission(
        org_id=auth.org_id,
        created_by=auth.user.id,
        model_set_id="research-set",
        title="Second mission",
        original_prompt="Second prompt",
        status=ScrapingMissionStatus.APPROVED,
    )
    db.add(second_mission)
    await db.flush()
    second = ScrapingBlueprint(
        mission_id=second_mission.id,
        version=1,
        status=ScrapingBlueprintStatus.APPROVED,
        blueprint_json=valid_blueprint(),
        model_set_id="research-set",
        judge_model_id="gpt-4.1",
    )
    db.add(second)
    await db.flush()
    second_mission.active_blueprint_id = second.id
    await db.flush()
    mock_planner(monkeypatch, 3)
    first_run = await run_service.plan_team(db, auth, first.mission_id)
    await run_service.plan_team(db, auth, second.mission_id)
    runs = await run_service.list_runs(db, auth, first.mission_id)
    assert [run.id for run in runs] == [first_run.id]
    other = await create_other_auth(db)
    with pytest.raises(Exception, match="ScrapingMission not found"):
        await run_service.list_runs(db, other, first.mission_id)


@pytest.mark.asyncio
async def test_run_detail_returns_agents_and_rejects_other_organization(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    detail = await run_service.get_run(db, auth, run.id)
    assert len(detail.agents) == 3
    other = await create_other_auth(db)
    with pytest.raises(Exception, match="ScrapingRun not found"):
        await run_service.get_run(db, other, run.id)


@pytest.mark.asyncio
async def test_cancel_planned_run_preserves_agents_and_history(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    cancelled = await run_service.cancel_run(db, auth, run.id)
    assert cancelled.status == "cancelled"
    assert len(cancelled.agents) == 3
    assert {agent.status for agent in cancelled.agents} == {"cancelled"}
    visible = await run_service.list_runs(db, auth, blueprint.mission_id)
    assert visible[0].id == run.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        ScrapingRunStatus.PLANNED,
        ScrapingRunStatus.COMPLETED,
        ScrapingRunStatus.FAILED,
        ScrapingRunStatus.CANCELLED,
    ],
)
async def test_delete_safe_run_statuses_succeeds(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch,
    status: ScrapingRunStatus,
):
    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    row = await run_service.get_run_row(db, auth, run.id)
    row.status = status
    await db.flush()
    await run_service.delete_run(db, auth, run.id)
    assert await db.get(ScrapingRun, run.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ScrapingRunStatus.PLANNING, ScrapingRunStatus.RUNNING])
async def test_delete_active_run_statuses_returns_409(
    db: AsyncSession,
    auth: AuthContext,
    status: ScrapingRunStatus,
):
    blueprint = await create_active_approved_blueprint(db, auth)
    run = ScrapingRun(
        organization_id=auth.org_id,
        mission_id=blueprint.mission_id,
        blueprint_id=blueprint.id,
        model_set_id="research-set",
        status=status,
    )
    db.add(run)
    await db.flush()
    with pytest.raises(Exception, match="cannot be deleted while planning or executing"):
        await run_service.delete_run(db, auth, run.id)
    assert await db.get(ScrapingRun, run.id) is not None


@pytest.mark.asyncio
async def test_after_deleting_run_same_blueprint_can_be_planned_again(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch, 3)
    first = await run_service.plan_team(db, auth, blueprint.mission_id)
    await run_service.delete_run(db, auth, first.id)
    second = await run_service.plan_team(db, auth, blueprint.mission_id)
    assert second.blueprint_id == blueprint.id
    assert second.id != first.id


@pytest.mark.asyncio
async def test_deleting_run_removes_agents(db: AsyncSession, auth: AuthContext, monkeypatch):
    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    await run_service.delete_run(db, auth, run.id)
    rows = await db.execute(select(ScrapingRunAgent).where(ScrapingRunAgent.run_id == run.id))
    assert rows.scalars().all() == []


@pytest.mark.asyncio
async def test_cross_organization_run_deletion_fails(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    other = await create_other_auth(db)
    with pytest.raises(Exception, match="ScrapingRun not found"):
        await run_service.delete_run(db, other, run.id)
    assert await db.get(ScrapingRun, run.id) is not None


@pytest.mark.asyncio
async def test_failed_and_completed_cancellation_rules(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_active_approved_blueprint(db, auth)

    async def fake_plan_team(mission, blueprint, model_set):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(team_planner_service, "plan_team", fake_plan_team)
    failed = await run_service.plan_team(db, auth, blueprint.mission_id)
    same = await run_service.cancel_run(db, auth, failed.id)
    assert same.status == "failed"
    row = await run_service.get_run_row(db, auth, failed.id)
    row.status = ScrapingRunStatus.COMPLETED
    await db.flush()
    with pytest.raises(Exception, match="completed"):
        await run_service.cancel_run(db, auth, failed.id)


@pytest.mark.asyncio
async def test_historical_integrity_and_no_blueprint_or_project_mutation(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    project = await create_project(db, auth)
    blueprint = await create_active_approved_blueprint(db, auth)
    await mission_service.update_mission(
        db,
        auth,
        blueprint.mission_id,
        ScrapingMissionUpdate(project_id=project.id),
    )
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    await create_blueprint_version(
        db,
        auth,
        mission_id=blueprint.mission_id,
        version=2,
        status=ScrapingBlueprintStatus.DRAFT,
    )
    reloaded_blueprint = await db.get(ScrapingBlueprint, blueprint.id)
    assert reloaded_blueprint is not None
    assert run.blueprint_id == blueprint.id
    assert reloaded_blueprint.status == ScrapingBlueprintStatus.APPROVED
    mission = await db.get(ScrapingMission, blueprint.mission_id)
    assert mission is not None
    assert mission.project_id == project.id
    count = await db.execute(
        select(ScrapingBlueprint).where(ScrapingBlueprint.mission_id == blueprint.mission_id)
    )
    assert len(count.scalars().all()) == 2


@pytest.mark.asyncio
async def test_planning_persists_allowed_models_only(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    assert {agent.model_id for agent in run.agents} == {"gpt-4.1"}
    rows = await db.execute(select(ScrapingRunAgent).where(ScrapingRunAgent.run_id == run.id))
    assert len(rows.scalars().all()) == 3
