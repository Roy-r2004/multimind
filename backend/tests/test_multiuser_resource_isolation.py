"""Member-private chats and projects with organization-admin visibility."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.models import Chat, OrgRole, Project, ScrapingBlueprint, Strategy
from app.schemas.api import ChatCreateRequest, ChatUpdateRequest, ProjectCreateRequest, ProjectUpdateRequest
from app.services.chat_service import chat_service
from app.services.domain_service import project_service
from app.services.scraping.blueprint_service import blueprint_service
from app.services.scraping.mission_service import mission_service
async def _same_org_auths(db: AsyncSession, first: AuthContext):
    fred_user = type(first.user)(
        email="fred@example.com", hashed_password="x", full_name="Fred"
    )
    admin_user = type(first.user)(
        email="admin2@example.com", hashed_password="x", full_name="Admin"
    )
    db.add_all([fred_user, admin_user])
    await db.flush()
    return (
        AuthContext(user=fred_user, org_id=first.org_id, role=OrgRole.MEMBER),
        AuthContext(user=admin_user, org_id=first.org_id, role=OrgRole.ADMIN),
    )


@pytest.mark.asyncio
async def test_members_cannot_list_read_or_modify_each_others_chats(db, auth):
    fred, admin = await _same_org_auths(db, auth)
    owner_auth = auth
    auth = AuthContext(user=auth.user, org_id=auth.org_id, role=OrgRole.MEMBER)
    chafic_chat = await chat_service.create_chat(db, auth, ChatCreateRequest(title="Chafic private"))
    fred_chat = await chat_service.create_chat(db, fred, ChatCreateRequest(title="Fred private"))

    assert {chat.id for chat in await chat_service.list_chats(db, auth)} == {chafic_chat.id}
    assert {chat.id for chat in await chat_service.list_chats(db, fred)} == {fred_chat.id}
    assert {chat.id for chat in await chat_service.list_chats(db, admin)} == {
        chafic_chat.id,
        fred_chat.id,
    }
    with pytest.raises(NotFoundError):
        await chat_service.get_chat(db, auth, fred_chat.id)
    with pytest.raises(NotFoundError):
        await chat_service.get_chat(db, fred, chafic_chat.id)
    with pytest.raises(NotFoundError):
        await chat_service.update_chat(db, fred, chafic_chat.id, ChatUpdateRequest(title="stolen"))
    with pytest.raises(NotFoundError):
        await chat_service.delete_chat(db, auth, fred_chat.id)
    await chat_service.delete_chat(db, owner_auth, fred_chat.id)


@pytest.mark.asyncio
async def test_members_cannot_list_read_modify_or_delete_each_others_projects(db, auth):
    fred, admin = await _same_org_auths(db, auth)
    auth = AuthContext(user=auth.user, org_id=auth.org_id, role=OrgRole.MEMBER)
    chafic_project = await project_service.create(db, auth, ProjectCreateRequest(name="Chafic private"))
    fred_project = await project_service.create(db, fred, ProjectCreateRequest(name="Fred private"))

    assert [project.id for project in await project_service.list(db, auth)] == [chafic_project.id]
    assert [project.id for project in await project_service.list(db, fred)] == [fred_project.id]
    assert {project.id for project in await project_service.list(db, admin)} == {
        chafic_project.id,
        fred_project.id,
    }
    with pytest.raises(NotFoundError):
        await project_service.get(db, auth, fred_project.id)
    with pytest.raises(NotFoundError):
        await project_service.update(db, fred, chafic_project.id, ProjectUpdateRequest(name="stolen"))
    with pytest.raises(NotFoundError):
        await project_service.delete(db, auth, fred_project.id)


@pytest.mark.asyncio
async def test_members_cannot_attach_chats_to_another_members_project(db, auth):
    fred, _admin = await _same_org_auths(db, auth)
    project = await project_service.create(db, auth, ProjectCreateRequest(name="Private"))
    with pytest.raises(NotFoundError):
        await chat_service.create_chat(
            db,
            fred,
            ChatCreateRequest(title="Invalid link", project_id=project.id),
        )


@pytest.mark.asyncio
async def test_member_project_delete_preserves_other_members_chat_reference(db, auth):
    fred, _admin = await _same_org_auths(db, auth)
    project = await project_service.create(db, auth, ProjectCreateRequest(name="Shared legacy"))
    foreign_chat = Chat(
        org_id=auth.org_id, created_by=fred.user.id, title="Fred chat", project_id=project.id
    )
    db.add(foreign_chat)
    await db.flush()

    await project_service.delete(db, auth, project.id)
    await db.flush()

    assert await db.get(Project, project.id) is None
    persisted_chat = await db.get(Chat, foreign_chat.id)
    assert persisted_chat is not None
    assert persisted_chat.project_id is None


@pytest.mark.asyncio
async def test_legacy_project_without_inferred_owner_stays_admin_only(db, auth):
    member = AuthContext(auth.user, auth.org_id, OrgRole.MEMBER)
    legacy = Project(org_id=auth.org_id, name="Ambiguous legacy")
    db.add(legacy)
    await db.flush()
    with pytest.raises(NotFoundError):
        await project_service.get(db, member, legacy.id)
    assert (await project_service.get(db, AuthContext(auth.user, auth.org_id, OrgRole.ADMIN), legacy.id)).id == legacy.id


@pytest.mark.asyncio
async def test_chat_owner_column_is_the_existing_user_assignment(db, auth):
    chat = await chat_service.create_chat(db, auth, ChatCreateRequest(title="Existing field"))
    row = (await db.execute(select(Chat).where(Chat.id == chat.id))).scalar_one()
    assert row.created_by == auth.user.id


@pytest.mark.asyncio
async def test_member_cannot_read_another_members_blueprint_or_mission(db, auth):
    from app.db.models import ModelSet, ScrapingMission, ScrapingBlueprintStatus

    fred, _admin = await _same_org_auths(db, auth)
    model_set = ModelSet(
        org_id=auth.org_id, slug="isolation-set", name="Isolation", description="",
        models=["gpt-4.1"], verdict_model="gpt-4.1", strategy=Strategy.SYNTHESIZE, best_for="",
    )
    db.add(model_set)
    await db.flush()
    mission = ScrapingMission(
        org_id=auth.org_id, created_by=fred.user.id, model_set_id=model_set.slug,
        title="Fred mission", original_prompt="prompt", country_code="LB", country_name="Lebanon",
    )
    db.add(mission)
    await db.flush()
    blueprint = ScrapingBlueprint(
        mission_id=mission.id, version=1, status=ScrapingBlueprintStatus.DRAFT,
        model_set_id=model_set.slug,
    )
    db.add(blueprint)
    await db.flush()

    member = AuthContext(user=auth.user, org_id=auth.org_id, role=OrgRole.MEMBER)
    with pytest.raises(NotFoundError):
        await mission_service.get_mission_row(db, member, mission.id)
    with pytest.raises(NotFoundError):
        await blueprint_service.get_blueprint_row(db, member, blueprint.id)
