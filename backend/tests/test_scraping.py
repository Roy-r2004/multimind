import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.models import (
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingCoverageCell,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingExecutionAgent,
    ScrapingExecutionStatus,
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
    ScrapingExecutionCreate,
    ScrapingTeamPlanOutput,
)
from app.scraping.blueprint_orchestrator import BlueprintOrchestrator
from app.services.domain_service import project_service
from app.services.scraping.blueprint_service import blueprint_service
from app.services.scraping.countries import COUNTRIES, resolve_country
from app.services.scraping.execution_orchestrator import MockExecutionOrchestrator
from app.services.scraping.execution_service import execution_service
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


def logged_execution_reason(caplog, reason: str) -> bool:
    return any(getattr(record, "reason", None) == reason for record in caplog.records)


async def create_mission(db: AsyncSession, auth: AuthContext) -> str:
    return await create_country_mission(db, auth, country_code="LB")


async def create_country_mission(
    db: AsyncSession, auth: AuthContext, *, country_code: str
) -> str:
    await create_model_set(db, auth)
    mission = await mission_service.create_mission(
        db,
        auth,
        ScrapingMissionCreate(
            title="Mission",
            country_code=country_code,
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
        ScrapingMissionCreate(
            title=" Mission ",
            country_code="LB",
            original_prompt=" Prompt ",
            model_set_id="research-set",
        ),
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
            ScrapingMissionCreate(
                title=" ",
                country_code="LB",
                original_prompt="Prompt",
                model_set_id="research-set",
            ),
        )


@pytest.mark.asyncio
async def test_mission_creation_rejects_empty_prompt(db: AsyncSession, auth: AuthContext):
    await create_model_set(db, auth)
    with pytest.raises(Exception, match="Mission prompt is required"):
        await mission_service.create_mission(
            db,
            auth,
            ScrapingMissionCreate(
                title="Mission",
                country_code="LB",
                original_prompt=" ",
                model_set_id="research-set",
            ),
        )


@pytest.mark.asyncio
async def test_mission_creation_rejects_another_organizations_model_set(db: AsyncSession, auth: AuthContext):
    other = await create_other_auth(db)
    await create_model_set(db, other, slug="other-set")
    with pytest.raises(Exception, match="ModelSet not found"):
        await mission_service.create_mission(
            db,
            auth,
            ScrapingMissionCreate(
                title="Mission",
                country_code="LB",
                original_prompt="Prompt",
                model_set_id="other-set",
            ),
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
                country_code="LB",
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


def test_offline_country_catalog_has_complete_valid_iso_codes():
    assert len(COUNTRIES) == 249
    assert len(set(COUNTRIES)) == 249
    assert all(len(code) == 2 for code in COUNTRIES)
    assert all(code.isalpha() and code.isupper() for code in COUNTRIES)
    assert all(country.code == code for code, country in COUNTRIES.items())
    assert all(country.name.strip() for country in COUNTRIES.values())


def test_country_resolution_normalizes_and_rejects_unsupported_codes():
    lebanon = resolve_country("lb")
    assert lebanon.code == "LB"
    assert lebanon.name == "Lebanon"

    turkiye = resolve_country(" tr ")
    assert turkiye.code == "TR"
    assert turkiye.name == "Türkiye"

    aland = resolve_country("ax")
    assert aland.code == "AX"
    assert aland.name == "Åland Islands"

    with pytest.raises(Exception, match="Unsupported country code"):
        resolve_country("XX")
    with pytest.raises(Exception, match="Unsupported country code"):
        resolve_country("XK")


@pytest.mark.asyncio
async def test_mission_creation_requires_valid_country(db: AsyncSession, auth: AuthContext):
    await create_model_set(db, auth)
    with pytest.raises(Exception, match="Unsupported country code"):
        await mission_service.create_mission(
            db,
            auth,
            ScrapingMissionCreate(
                title="Mission",
                country_code="XX",
                original_prompt="Prompt",
                model_set_id="research-set",
            ),
        )
    mission = await mission_service.create_mission(
        db,
        auth,
        ScrapingMissionCreate(
            title="Mission",
            country_code="lb",
            original_prompt="Prompt",
            model_set_id="research-set",
        ),
    )
    assert mission.country_code == "LB"
    assert mission.country_name == "Lebanon"


@pytest.mark.asyncio
async def test_legacy_mission_can_set_country_once_and_locks_after_blueprint(
    db: AsyncSession, auth: AuthContext
):
    await create_model_set(db, auth)
    mission = ScrapingMission(
        org_id=auth.org_id,
        created_by=auth.user.id,
        model_set_id="research-set",
        title="Legacy",
        original_prompt="Prompt",
    )
    db.add(mission)
    await db.flush()
    updated = await mission_service.update_mission(
        db, auth, mission.id, ScrapingMissionUpdate(country_code="JO")
    )
    assert updated.country_code == "JO"
    await create_blueprint_version(
        db,
        auth,
        mission_id=mission.id,
        status=ScrapingBlueprintStatus.APPROVED,
        active=True,
    )
    with pytest.raises(Exception, match="Country cannot be changed"):
        await mission_service.update_mission(
            db, auth, mission.id, ScrapingMissionUpdate(country_code="LB")
        )


async def create_planned_team_plan(db: AsyncSession, auth: AuthContext, monkeypatch) -> ScrapingRun:
    blueprint = await create_active_approved_blueprint(db, auth)
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)
    return await run_service.get_run_row(db, auth, run.id)


@pytest.mark.asyncio
async def test_legacy_mission_without_country_cannot_start_execution(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    await create_model_set(db, auth)
    mission = ScrapingMission(
        org_id=auth.org_id,
        created_by=auth.user.id,
        model_set_id="research-set",
        title="Legacy",
        original_prompt="Prompt",
        status=ScrapingMissionStatus.APPROVED,
    )
    db.add(mission)
    await db.flush()
    blueprint = await create_blueprint_version(
        db,
        auth,
        mission_id=mission.id,
        status=ScrapingBlueprintStatus.APPROVED,
        active=True,
    )
    mission.active_blueprint_id = blueprint.id
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, mission.id)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    with pytest.raises(Exception, match="Set a mission country"):
        await execution_service.create_execution(
            db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
        )


@pytest.mark.asyncio
async def test_first_mock_execution_snapshots_country_and_agents(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    run = await create_planned_team_plan(db, auth, monkeypatch)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    assert execution.status == "queued"
    assert execution.country_code == "LB"
    agents = await db.execute(
        select(ScrapingExecutionAgent).where(ScrapingExecutionAgent.execution_id == execution.id)
    )
    assert len(agents.scalars().all()) == len(run.agents)


@pytest.mark.asyncio
async def test_second_active_execution_for_team_plan_returns_conflict(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    run = await create_planned_team_plan(db, auth, monkeypatch)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    first = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    with pytest.raises(Exception, match="active mock execution"):
        await execution_service.create_execution(
            db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
        )
    row = await db.get(ScrapingExecution, first.id)
    assert row is not None
    row.status = ScrapingExecutionStatus.COMPLETED
    await db.flush()
    second = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    assert second.id != first.id


@pytest.mark.asyncio
async def test_mock_worker_persists_profile_cells_tasks_events_and_metrics(
    db: AsyncSession, auth: AuthContext, monkeypatch, caplog
):
    run = await create_planned_team_plan(db, auth, monkeypatch)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)

    async def no_delay():
        return None

    monkeypatch.setattr("app.services.scraping.execution_orchestrator.sleep_mock_delay", no_delay)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    assert execution.status == "queued"
    caplog.set_level("INFO", logger="app.services.scraping.execution_orchestrator")
    await MockExecutionOrchestrator(db).run(execution.id)
    detail = await execution_service.get_detail(db, auth, execution.id)
    assert detail.execution.status == "completed"
    assert detail.execution.started_at is not None
    assert detail.country_profile is not None
    assert detail.coverage_summary_counts
    assert detail.task_summary_counts["completed"] > 0
    assert detail.recent_events[0].sequence_number == 1
    assert detail.execution.sources_discovered >= 0
    events = await db.execute(
        select(ScrapingEvent).where(ScrapingEvent.execution_id == execution.id)
    )
    sequences = [event.sequence_number for event in events.scalars().all()]
    assert sequences == sorted(set(sequences))
    assert "scraping_execution_execution_claim_succeeded" in caplog.text
    assert "scraping_execution_orchestrator_completed" in caplog.text


@pytest.mark.asyncio
async def test_mock_worker_generic_country_profile_creates_cells_and_tasks(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    mission_id = await create_country_mission(db, auth, country_code="BR")
    blueprint = await create_blueprint_version(
        db,
        auth,
        mission_id=mission_id,
        status=ScrapingBlueprintStatus.APPROVED,
        active=True,
    )
    blueprint.blueprint_json = {
        **valid_blueprint(),
        "scope": {
            "included": ["public listings"],
            "excluded": ["private data"],
            "countries": ["Brazil"],
            "regions": [],
        },
        "languages": [],
        "source_strategy": [],
    }
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)

    async def fake_enqueue(execution_id):
        return None

    async def no_delay():
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    monkeypatch.setattr("app.services.scraping.execution_orchestrator.sleep_mock_delay", no_delay)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    await MockExecutionOrchestrator(db).run(execution.id)
    detail = await execution_service.get_detail(db, auth, execution.id)
    assert detail.execution.status == "completed"
    assert detail.country_profile is not None
    assert detail.country_profile["country_code"] == "BR"
    assert detail.country_profile["country_name"] == "Brazil"
    assert detail.country_profile["administrative_regions"] == [
        {"code": "brazil", "name": "Brazil"}
    ]
    assert detail.country_profile["languages"] == [{"code": "en", "name": "English"}]
    assert detail.coverage_summary_counts
    assert detail.task_summary_counts["completed"] > 0
    cells = (
        await db.execute(
            select(ScrapingCoverageCell)
            .where(ScrapingCoverageCell.execution_id == execution.id)
            .order_by(ScrapingCoverageCell.region_name)
        )
    ).scalars().all()
    assert cells
    assert all(cell.region_code and len(cell.region_code) <= 32 for cell in cells)
    identities = {
        (cell.region_name, cell.language_name, cell.source_category) for cell in cells
    }
    assert len(identities) == len(cells)


@pytest.mark.asyncio
async def test_mock_worker_deduplicates_coverage_dimensions(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    blueprint = await create_active_approved_blueprint(db, auth)
    blueprint.blueprint_json = {
        **valid_blueprint(),
        "scope": {
            "included": ["public listings"],
            "excluded": ["private data"],
            "countries": ["Lebanon"],
            "regions": [" Beirut ", "Beirut", " BEIRUT ", "Mount Lebanon"],
        },
        "languages": [" English ", "English"],
        "source_strategy": [
            {"source_type": " official directory ", "priority": 1},
            {"source_type": "Official Directory", "priority": 2},
        ],
    }
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)

    async def fake_enqueue(execution_id):
        return None

    async def no_delay():
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    monkeypatch.setattr("app.services.scraping.execution_orchestrator.sleep_mock_delay", no_delay)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    await MockExecutionOrchestrator(db).run(execution.id)
    cells = (
        await db.execute(
            select(ScrapingCoverageCell)
            .where(ScrapingCoverageCell.execution_id == execution.id)
            .order_by(ScrapingCoverageCell.region_name)
        )
    ).scalars().all()
    identities = [
        (cell.region_name, cell.language_name, cell.source_category) for cell in cells
    ]
    assert len(cells) == 2
    assert len(set(identities)) == len(identities)
    assert [cell.region_name for cell in cells] == ["Beirut", "Mount Lebanon"]


def test_coverage_dimensions_keep_region_codes_inside_schema_limit(db: AsyncSession):
    orchestrator = MockExecutionOrchestrator(db)
    long_name = "A Very Long Administrative Region Name That Exceeds The Database Limit"
    regions, languages, categories = orchestrator._coverage_dimensions(
        {
            "administrative_regions": [{"code": long_name, "name": long_name}],
            "languages": [{"code": " en ", "name": " English "}],
            "source_categories": [" general web "],
        },
        "ZZ",
        "Fallback Country",
    )
    assert regions == [{"code": regions[0]["code"], "name": long_name}]
    assert regions[0]["code"]
    assert len(regions[0]["code"]) <= 32
    assert languages == [{"code": "en", "name": "English"}]
    assert categories == ["general web"]


@pytest.mark.asyncio
async def test_mock_worker_missing_execution_logs_skip_reason(db: AsyncSession, caplog):
    caplog.set_level("INFO", logger="app.services.scraping.execution_orchestrator")
    await MockExecutionOrchestrator(db).run("missing-execution-id")
    assert logged_execution_reason(caplog, "execution_not_found")


@pytest.mark.asyncio
async def test_mock_worker_terminal_execution_logs_skip_reason(
    db: AsyncSession, auth: AuthContext, monkeypatch, caplog
):
    run = await create_planned_team_plan(db, auth, monkeypatch)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    row = await db.get(ScrapingExecution, execution.id)
    assert row is not None
    row.status = ScrapingExecutionStatus.COMPLETED
    await db.flush()
    caplog.set_level("INFO", logger="app.services.scraping.execution_orchestrator")
    await MockExecutionOrchestrator(db).run(execution.id)
    assert logged_execution_reason(caplog, "execution_already_terminal")


@pytest.mark.asyncio
async def test_mock_worker_missing_execution_agents_fails_deterministically(
    db: AsyncSession, auth: AuthContext, monkeypatch, caplog
):
    run = await create_planned_team_plan(db, auth, monkeypatch)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    agents = (
        await db.execute(
            select(ScrapingExecutionAgent).where(
                ScrapingExecutionAgent.execution_id == execution.id
            )
        )
    ).scalars().all()
    for agent in agents:
        await db.delete(agent)
    await db.flush()
    caplog.set_level("INFO", logger="app.services.scraping.execution_orchestrator")
    await MockExecutionOrchestrator(db).run(execution.id)
    failed = await db.get(ScrapingExecution, execution.id)
    assert failed is not None
    assert failed.status == ScrapingExecutionStatus.FAILED
    assert failed.error_message == "Mock execution failed."
    events = await db.execute(
        select(ScrapingEvent).where(
            ScrapingEvent.execution_id == execution.id,
            ScrapingEvent.event_type == "execution_failed",
        )
    )
    assert len(events.scalars().all()) == 1
    assert logged_execution_reason(caplog, "no_execution_agents")


@pytest.mark.asyncio
async def test_mock_worker_accepts_long_descriptive_source_category(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    long_category = (
        "Government Ministry/Registry (Ministry of Public Health, Ministry of Social Affairs)"
    )
    blueprint = await create_active_approved_blueprint(db, auth)
    blueprint.blueprint_json = {
        **valid_blueprint(),
        "source_strategy": [
            {
                "source_type": long_category,
                "priority": 1,
                "trust_tier": "high",
                "purpose": "seed sources",
                "required": True,
            }
        ],
    }
    mock_planner(monkeypatch, 3)
    run = await run_service.plan_team(db, auth, blueprint.mission_id)

    async def fake_enqueue(execution_id):
        return None

    async def no_delay():
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    monkeypatch.setattr("app.services.scraping.execution_orchestrator.sleep_mock_delay", no_delay)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    await MockExecutionOrchestrator(db).run(execution.id)
    cells = await db.execute(
        select(ScrapingCoverageCell).where(ScrapingCoverageCell.execution_id == execution.id)
    )
    categories = {cell.source_category for cell in cells.scalars().all()}
    assert long_category in categories


@pytest.mark.asyncio
async def test_orchestrator_failure_log_renders_stage_and_exception_type(
    db: AsyncSession, auth: AuthContext, monkeypatch, caplog
):
    run = await create_planned_team_plan(db, auth, monkeypatch)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    orchestrator = MockExecutionOrchestrator(db)

    async def fail_after_profile(execution_row, execution_agents):
        orchestrator.current_stage = "flush_coverage_cells"
        orchestrator.coverage_region_count = 2
        orchestrator.coverage_language_count = 1
        orchestrator.coverage_source_category_count = 3
        orchestrator.attempted_coverage_cell_count = 6
        raise RuntimeError("database detail that must not be persisted")

    monkeypatch.setattr(
        orchestrator, "_ensure_profile_matrix_and_tasks", fail_after_profile
    )
    caplog.set_level("ERROR", logger="app.services.scraping.execution_orchestrator")
    await orchestrator.run(execution.id)
    failed = await db.get(ScrapingExecution, execution.id)
    assert failed is not None
    assert failed.status == ScrapingExecutionStatus.FAILED
    assert failed.error_message == "Mock execution failed."
    assert "database detail that must not be persisted" not in failed.error_message
    events = await db.execute(
        select(ScrapingEvent).where(
            ScrapingEvent.execution_id == execution.id,
            ScrapingEvent.event_type == "execution_failed",
        )
    )
    failed_events = events.scalars().all()
    assert len(failed_events) == 1
    assert failed_events[0].message == "Mock execution failed."
    assert "database detail that must not be persisted" not in failed_events[0].message
    rendered_log = caplog.text
    assert f"scraping_execution_failed execution_id={execution.id}" in rendered_log
    assert "stage=flush_coverage_cells" in rendered_log
    assert "exception_type=RuntimeError" in rendered_log
    assert "region_count=2" in rendered_log
    assert "language_count=1" in rendered_log
    assert "source_category_count=3" in rendered_log
    assert "attempted_coverage_cell_count=6" in rendered_log


@pytest.mark.asyncio
async def test_orchestrator_failed_flush_rolls_back_and_stores_failed_event(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    run = await create_planned_team_plan(db, auth, monkeypatch)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    orchestrator = MockExecutionOrchestrator(db)

    async def fail_with_pending_rollback(execution_row, execution_agents):
        db.add(
            ScrapingEvent(
                execution_id=execution_row.id,
                sequence_number=1,
                event_type="duplicate_sequence_for_test",
                message="This duplicate event intentionally fails.",
                metadata_json={"mock": True},
            )
        )
        await db.flush()

    monkeypatch.setattr(
        orchestrator, "_ensure_profile_matrix_and_tasks", fail_with_pending_rollback
    )
    await orchestrator.run(execution.id)
    failed = await db.get(ScrapingExecution, execution.id)
    assert failed is not None
    assert failed.status == ScrapingExecutionStatus.FAILED
    assert failed.error_message == "Mock execution failed."
    events = await db.execute(
        select(ScrapingEvent).where(
            ScrapingEvent.execution_id == execution.id,
            ScrapingEvent.event_type == "execution_failed",
        )
    )
    assert len(events.scalars().all()) == 1


@pytest.mark.asyncio
async def test_execution_delete_and_team_plan_active_execution_rules(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    run = await create_planned_team_plan(db, auth, monkeypatch)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    execution = await execution_service.create_execution(
        db, auth, run.id, ScrapingExecutionCreate(execution_type="initial_full_country")
    )
    with pytest.raises(Exception, match="child execution campaign is active"):
        await run_service.delete_run(db, auth, run.id)
    with pytest.raises(Exception, match="Active mock executions cannot be deleted"):
        await execution_service.delete_execution(db, auth, execution.id)
    row = await db.get(ScrapingExecution, execution.id)
    assert row is not None
    row.status = ScrapingExecutionStatus.CANCELLED
    await db.flush()
    await execution_service.delete_execution(db, auth, execution.id)
    assert await db.get(ScrapingExecution, execution.id) is None
    agents = await db.execute(
        select(ScrapingExecutionAgent).where(ScrapingExecutionAgent.execution_id == execution.id)
    )
    cells = await db.execute(
        select(ScrapingCoverageCell).where(ScrapingCoverageCell.execution_id == execution.id)
    )
    assert agents.scalars().all() == []
    assert cells.scalars().all() == []
