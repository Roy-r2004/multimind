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
    ScrapingMissionCreate,
)
from app.scraping.blueprint_orchestrator import BlueprintOrchestrator
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
