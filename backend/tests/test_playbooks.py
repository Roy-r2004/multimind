"""My Playbooks Phase 1: ownership, serialization, cascade, and read APIs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.dependencies import AuthContext, get_auth_context
from app.db.base import Base
from app.db.models import (
    Chat,
    OrgMembership,
    OrgRole,
    Organization,
    Playbook,
    PlaybookExcludedSource,
    PlaybookObservation,
    PlaybookObservationSource,
    PlaybookRun,
    PlaybookSourceState,
    Strategy,
    Turn,
    TurnStatus,
    User,
)
from app.db.session import get_db
from app.main import create_app
from app.services.playbook_service import playbook_service
from tests.conftest import create_other_auth


async def _client_for(db: AsyncSession, auth: AuthContext) -> AsyncClient:
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = lambda: auth
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _same_org_other_user(db: AsyncSession, auth: AuthContext) -> AuthContext:
    user = User(email="playbook-peer@example.com", hashed_password="x", full_name="Peer")
    db.add(user)
    await db.flush()
    db.add(OrgMembership(org_id=auth.org_id, user_id=user.id, role=OrgRole.MEMBER))
    await db.flush()
    return AuthContext(user=user, org_id=auth.org_id, role=OrgRole.MEMBER)


async def _same_user_other_org(db: AsyncSession, auth: AuthContext) -> AuthContext:
    org = Organization(name="Playbook Org Two", slug="playbook-org-two")
    db.add(org)
    await db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=auth.user.id, role=OrgRole.MEMBER))
    await db.flush()
    return AuthContext(user=auth.user, org_id=org.id, role=OrgRole.MEMBER)


@pytest.mark.asyncio
async def test_get_me_creates_empty_playbook(db: AsyncSession, auth: AuthContext):
    async with await _client_for(db, auth) as client:
        response = await client.get("/api/v1/playbooks/me")
    assert response.status_code == 200
    body = response.json()
    assert body["org_id"] == auth.org_id
    assert body["user_id"] == auth.user.id
    assert body["status"] == "not_generated"
    assert body["injection_enabled"] is True
    assert body["core_summary"] is None
    assert body["extraction_version"] == 1
    assert body["playbook_version"] == 0
    assert body["last_success_run_id"] is None
    assert body["last_success_at"] is None
    assert body["created_at"]
    assert body["updated_at"]

    rows = list((await db.execute(select(Playbook))).scalars().all())
    assert len(rows) == 1
    assert rows[0].id == body["id"]


@pytest.mark.asyncio
async def test_repeated_get_me_does_not_duplicate(db: AsyncSession, auth: AuthContext):
    async with await _client_for(db, auth) as client:
        first = await client.get("/api/v1/playbooks/me")
        second = await client.get("/api/v1/playbooks/me")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    count = len(list((await db.execute(select(Playbook))).scalars().all()))
    assert count == 1


@pytest.mark.asyncio
async def test_same_org_users_get_separate_playbooks(db: AsyncSession, auth: AuthContext):
    peer = await _same_org_other_user(db, auth)
    async with await _client_for(db, auth) as client:
        mine = await client.get("/api/v1/playbooks/me")
    async with await _client_for(db, peer) as client:
        theirs = await client.get("/api/v1/playbooks/me")
    assert mine.json()["id"] != theirs.json()["id"]
    assert mine.json()["org_id"] == theirs.json()["org_id"] == auth.org_id
    assert mine.json()["user_id"] == auth.user.id
    assert theirs.json()["user_id"] == peer.user.id


@pytest.mark.asyncio
async def test_same_user_different_orgs_get_separate_playbooks(
    db: AsyncSession, auth: AuthContext
):
    other_org_auth = await _same_user_other_org(db, auth)
    async with await _client_for(db, auth) as client:
        first_org = await client.get("/api/v1/playbooks/me")
    async with await _client_for(db, other_org_auth) as client:
        second_org = await client.get("/api/v1/playbooks/me")
    assert first_org.json()["id"] != second_org.json()["id"]
    assert first_org.json()["user_id"] == second_org.json()["user_id"] == auth.user.id
    assert first_org.json()["org_id"] == auth.org_id
    assert second_org.json()["org_id"] == other_org_auth.org_id


@pytest.mark.asyncio
async def test_user_cannot_see_another_users_observations(
    db: AsyncSession, auth: AuthContext
):
    peer = await _same_org_other_user(db, auth)
    async with await _client_for(db, auth) as client:
        mine = (await client.get("/api/v1/playbooks/me")).json()
    db.add(
        PlaybookObservation(
            playbook_id=mine["id"],
            category="preference",
            subject="Length",
            observation="Prefers concise answers",
            status="confirmed",
        )
    )
    await db.flush()

    async with await _client_for(db, peer) as client:
        listed = await client.get("/api/v1/playbooks/me/observations")
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.asyncio
async def test_observation_filters_and_excluded_default_hidden(
    db: AsyncSession, auth: AuthContext
):
    async with await _client_for(db, auth) as client:
        playbook = (await client.get("/api/v1/playbooks/me")).json()

    visible = PlaybookObservation(
        playbook_id=playbook["id"],
        category="preference",
        subject="Tone",
        observation="Direct and technical",
        status="active",
    )
    matching_status = PlaybookObservation(
        playbook_id=playbook["id"],
        category="project",
        subject="MultiMind",
        observation="Building Playbooks",
        status="planned",
    )
    excluded = PlaybookObservation(
        playbook_id=playbook["id"],
        category="preference",
        subject="Secret",
        observation="Should stay hidden",
        status="active",
        user_excluded=True,
    )
    db.add_all([visible, matching_status, excluded])
    await db.flush()

    async with await _client_for(db, auth) as client:
        default_list = await client.get("/api/v1/playbooks/me/observations")
        by_category = await client.get(
            "/api/v1/playbooks/me/observations",
            params={"category": "preference"},
        )
        by_status = await client.get(
            "/api/v1/playbooks/me/observations",
            params={"status": "planned"},
        )
        with_excluded = await client.get(
            "/api/v1/playbooks/me/observations",
            params={"include_excluded": True, "category": "preference"},
        )

    default_ids = {row["id"] for row in default_list.json()}
    assert visible.id in default_ids
    assert matching_status.id in default_ids
    assert excluded.id not in default_ids

    category_ids = {row["id"] for row in by_category.json()}
    assert category_ids == {visible.id}

    status_rows = by_status.json()
    assert len(status_rows) == 1
    assert status_rows[0]["id"] == matching_status.id
    assert status_rows[0]["observation"] == "Building Playbooks"
    assert status_rows[0]["user_excluded"] is False

    included_ids = {row["id"] for row in with_excluded.json()}
    assert included_ids == {visible.id, excluded.id}


@pytest.mark.asyncio
async def test_latest_run_is_current_users_newest_only(
    db: AsyncSession, auth: AuthContext
):
    peer = await _same_org_other_user(db, auth)
    async with await _client_for(db, auth) as client:
        empty = await client.get("/api/v1/playbooks/me/runs/latest")
        mine = (await client.get("/api/v1/playbooks/me")).json()
    assert empty.status_code == 200
    assert empty.json() is None

    async with await _client_for(db, peer) as client:
        theirs = (await client.get("/api/v1/playbooks/me")).json()

    older = PlaybookRun(
        playbook_id=mine["id"],
        kind="full",
        status="failed",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    newer = PlaybookRun(
        playbook_id=mine["id"],
        kind="incremental",
        status="completed",
        processed_count=4,
        total_count=4,
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    peer_run = PlaybookRun(
        playbook_id=theirs["id"],
        kind="full",
        status="completed",
        created_at=datetime.now(UTC),
    )
    db.add_all([older, newer, peer_run])
    await db.flush()

    async with await _client_for(db, auth) as client:
        latest = await client.get("/api/v1/playbooks/me/runs/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert body["id"] == newer.id
    assert body["kind"] == "incremental"
    assert body["status"] == "completed"
    assert body["playbook_id"] == mine["id"]
    assert body["processed_count"] == 4


@pytest.mark.asyncio
async def test_playbook_cascade_deletes_children_not_chats_or_turns(
    db: AsyncSession, auth: AuthContext
):
    playbook = Playbook(org_id=auth.org_id, user_id=auth.user.id)
    db.add(playbook)
    await db.flush()

    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Keep me")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="Keep this turn",
        model_set_id="set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.COMPLETED,
    )
    db.add(turn)
    await db.flush()

    observation = PlaybookObservation(
        playbook_id=playbook.id,
        category="decision",
        observation="Use PostgreSQL",
    )
    run = PlaybookRun(playbook_id=playbook.id, kind="full")
    db.add_all([observation, run])
    await db.flush()

    db.add_all(
        [
            PlaybookObservationSource(
                observation_id=observation.id,
                chat_id=chat.id,
                turn_id=turn.id,
                source_kind="verdict",
                epistemic_role="user_confirmed",
                quote="PostgreSQL was selected",
            ),
            PlaybookSourceState(
                playbook_id=playbook.id,
                source_type="chat_turn",
                source_id=turn.id[:36] if len(turn.id) >= 36 else turn.id,
                content_hash="abc123",
                processed_run_id=run.id,
            ),
            PlaybookExcludedSource(
                playbook_id=playbook.id,
                chat_id=chat.id,
            ),
        ]
    )
    await db.flush()

    await db.delete(playbook)
    await db.flush()

    assert (await db.execute(select(Playbook))).scalar_one_or_none() is None
    assert list((await db.execute(select(PlaybookObservation))).scalars().all()) == []
    assert list((await db.execute(select(PlaybookObservationSource))).scalars().all()) == []
    assert list((await db.execute(select(PlaybookRun))).scalars().all()) == []
    assert list((await db.execute(select(PlaybookSourceState))).scalars().all()) == []
    assert list((await db.execute(select(PlaybookExcludedSource))).scalars().all()) == []
    assert (await db.get(Chat, chat.id)) is not None
    assert (await db.get(Turn, turn.id)) is not None


@pytest.mark.asyncio
async def test_playbook_unique_per_org_user(db: AsyncSession, auth: AuthContext):
    db.add(Playbook(org_id=auth.org_id, user_id=auth.user.id))
    await db.flush()
    db.add(Playbook(org_id=auth.org_id, user_id=auth.user.id))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_excluded_source_requires_chat_or_turn(db: AsyncSession, auth: AuthContext):
    playbook = Playbook(org_id=auth.org_id, user_id=auth.user.id)
    db.add(playbook)
    await db.flush()
    db.add(PlaybookExcludedSource(playbook_id=playbook.id))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_get_or_create_recovers_from_org_user_unique_race(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    first = await playbook_service.get_or_create_for_current_user(db, auth)
    await db.flush()
    calls = {"n": 0}
    real_load = playbook_service._load_for_current_user

    async def skip_first_load(session: AsyncSession, auth_ctx: AuthContext):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_load(session, auth_ctx)

    monkeypatch.setattr(playbook_service, "_load_for_current_user", skip_first_load)
    second = await playbook_service.get_or_create_for_current_user(db, auth)
    assert second.id == first.id
    rows = list((await db.execute(select(Playbook))).scalars().all())
    assert len(rows) == 1
    assert rows[0].id == first.id


@pytest.mark.asyncio
async def test_get_or_create_reraises_unrelated_integrity_error(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    async def none_load(session: AsyncSession, auth_ctx: AuthContext):
        return None

    async def flush_fk(*args, **kwargs):
        raise IntegrityError(
            "INSERT",
            {},
            Exception("FOREIGN KEY constraint failed: playbooks.org_id"),
        )

    monkeypatch.setattr(playbook_service, "_load_for_current_user", none_load)
    monkeypatch.setattr(db, "flush", flush_fk)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        await playbook_service.get_or_create_for_current_user(db, auth)
    assert (await db.execute(select(Playbook))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_concurrent_get_or_create_creates_one_playbook(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'playbooks-race.db'}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as setup:
        org = Organization(name="Race Org", slug="race-org")
        user = User(email="race@example.com", hashed_password="x", full_name="Race")
        setup.add_all([org, user])
        await setup.flush()
        setup.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))
        await setup.commit()
        auth = AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)

    async def create_one() -> str:
        async with session_factory() as session:
            playbook = await playbook_service.get_or_create_for_current_user(session, auth)
            await session.commit()
            return playbook.id

    ids = await asyncio.gather(*(create_one() for _ in range(8)))
    assert len(set(ids)) == 1
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Playbook))
        assert count == 1
        playbook = (await session.execute(select(Playbook))).scalar_one()
        assert playbook.id == ids[0]
        assert playbook.org_id == auth.org_id
        assert playbook.user_id == auth.user.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_get_me_does_not_raise_duplicate_key(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'playbooks-api-race.db'}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as setup:
        org = Organization(name="API Race Org", slug="api-race-org")
        user = User(email="api-race@example.com", hashed_password="x", full_name="API Race")
        setup.add_all([org, user])
        await setup.flush()
        setup.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))
        await setup.commit()
        auth = AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)

    app = create_app()

    async def override_db():
        async with session_factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = lambda: auth

    async def fetch_me() -> dict:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/playbooks/me")
        assert response.status_code == 200, response.text
        return response.json()

    bodies = await asyncio.gather(*(fetch_me() for _ in range(6)))
    ids = {body["id"] for body in bodies}
    assert len(ids) == 1
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Playbook))
        assert count == 1
    await engine.dispose()
