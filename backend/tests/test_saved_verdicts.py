from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dependencies import AuthContext
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.base import Base
from app.db.models import (
    Chat,
    OrgMembership,
    OrgRole,
    Organization,
    SavedVerdict,
    Strategy,
    Turn,
    TurnStatus,
    User,
    Verdict,
)
from app.api.v1 import saved_verdicts as saved_verdicts_api_module
from app.services.chat_service import chat_service
from app.services.saved_verdict_service import savable_verdict_statement, saved_verdict_service


@pytest.fixture
async def db_setup(tmp_path):
    db_path = tmp_path / "saved-verdicts.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        org = Organization(name="Org", slug="org")
        other_org = Organization(name="Other", slug="other")
        user = User(email="u@example.com", hashed_password="x", full_name="User")
        other_user = User(email="other@example.com", hashed_password="x", full_name="Other")
        admin = User(email="admin@example.com", hashed_password="x", full_name="Admin")
        owner = User(email="owner@example.com", hashed_password="x", full_name="Owner")
        viewer = User(email="viewer@example.com", hashed_password="x", full_name="Viewer")
        db.add_all([org, other_org, user, other_user, admin, owner, viewer])
        await db.flush()
        db.add_all(
            [
                OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER),
                OrgMembership(org_id=org.id, user_id=other_user.id, role=OrgRole.MEMBER),
                OrgMembership(org_id=org.id, user_id=admin.id, role=OrgRole.ADMIN),
                OrgMembership(org_id=org.id, user_id=owner.id, role=OrgRole.OWNER),
                OrgMembership(org_id=org.id, user_id=viewer.id, role=OrgRole.VIEWER),
                OrgMembership(org_id=other_org.id, user_id=user.id, role=OrgRole.MEMBER),
            ]
        )
        chat = Chat(org_id=org.id, created_by=user.id, title="Snapshot chat")
        other_chat = Chat(org_id=other_org.id, created_by=user.id, title="Other org chat")
        db.add_all([chat, other_chat])
        await db.flush()
        turn = Turn(
            chat_id=chat.id,
            user_message="Backend prompt",
            model_set_id="set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.COMPLETED,
        )
        other_turn = Turn(
            chat_id=other_chat.id,
            user_message="Other prompt",
            model_set_id="set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.COMPLETED,
        )
        db.add_all([turn, other_turn])
        await db.flush()
        verdict = Verdict(
            turn_id=turn.id,
            model_id="gemini",
            strategy=Strategy.SYNTHESIZE,
            text="Backend verdict",
            reason="Backend reason",
        )
        other_verdict = Verdict(
            turn_id=other_turn.id,
            model_id="gemini",
            strategy=Strategy.SYNTHESIZE,
            text="Other verdict",
            reason="Other reason",
        )
        db.add_all([verdict, other_verdict])
        await db.commit()

    try:
        yield SimpleNamespace(
            engine=engine,
            Session=Session,
            auth=AuthContext(user=user, org_id=org.id, role=OrgRole.MEMBER),
            other_user_auth=AuthContext(user=other_user, org_id=org.id, role=OrgRole.MEMBER),
            admin_auth=AuthContext(user=admin, org_id=org.id, role=OrgRole.ADMIN),
            owner_auth=AuthContext(user=owner, org_id=org.id, role=OrgRole.OWNER),
            viewer_auth=AuthContext(user=viewer, org_id=org.id, role=OrgRole.VIEWER),
            other_org_auth=AuthContext(user=user, org_id=other_org.id, role=OrgRole.MEMBER),
            chat_id=chat.id,
            turn_id=turn.id,
            verdict_id=verdict.id,
            other_verdict_id=other_verdict.id,
        )
    finally:
        await engine.dispose()


async def saved_rows(Session, **filters):
    async with Session() as db:
        statement = select(SavedVerdict)
        for field, value in filters.items():
            statement = statement.where(getattr(SavedVerdict, field) == value)
        return list((await db.execute(statement)).scalars().all())


@pytest.mark.asyncio
async def test_completed_and_partial_turns_can_be_saved(db_setup):
    async with db_setup.Session() as db:
        partial_turn = Turn(
            chat_id=db_setup.chat_id,
            user_message="Partial prompt",
            model_set_id="set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.PARTIAL.name,
        )
        db.add(partial_turn)
        await db.flush()
        partial_verdict = Verdict(
            turn_id=partial_turn.id,
            model_id="gemini",
            strategy=Strategy.SYNTHESIZE,
            text="Partial verdict",
            reason="Partial reason",
        )
        db.add(partial_verdict)
        await db.flush()

        completed = await saved_verdict_service.save_verdict(
            db, db_setup.auth, db_setup.verdict_id
        )
        partial = await saved_verdict_service.save_verdict(db, db_setup.auth, partial_verdict.id)
        await db.commit()

    assert completed.saved is True
    assert partial.saved is True
    assert partial.source_user_message == "Partial prompt"
    assert partial.verdict_text == "Partial verdict"


def test_savable_verdict_query_casts_turn_status_to_varchar_for_postgresql():
    compiled = str(
        savable_verdict_statement("verdict-id", "org-id").compile(
            dialect=asyncpg.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "CAST(turns.status AS VARCHAR)" in compiled
    assert "::turnstatus" not in compiled
    assert "COMPLETED" in compiled
    assert "PARTIAL" in compiled


@pytest.mark.asyncio
async def test_authorized_user_saves_verdict_and_snapshot_uses_backend_values(db_setup):
    async with db_setup.Session() as db:
        response = await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    assert response.saved is True
    rows = await saved_rows(db_setup.Session, source_verdict_id=db_setup.verdict_id)
    assert len(rows) == 1
    saved = rows[0]
    assert saved.org_id == db_setup.auth.org_id
    assert saved.user_id == db_setup.auth.user.id
    assert saved.source_turn_id == db_setup.turn_id
    assert saved.source_chat_id == db_setup.chat_id
    assert saved.source_chat_title == "Snapshot chat"
    assert saved.source_user_message == "Backend prompt"
    assert saved.verdict_text == "Backend verdict"
    assert saved.verdict_reason == "Backend reason"
    assert saved.verdict_model_id == "gemini"
    assert saved.strategy == Strategy.SYNTHESIZE


@pytest.mark.asyncio
async def test_frontend_supplied_snapshot_content_cannot_be_injected(db_setup):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    saved = (await saved_rows(db_setup.Session, source_verdict_id=db_setup.verdict_id))[0]
    assert saved.source_user_message != "Injected prompt"
    assert saved.verdict_text != "Injected verdict"
    assert saved.source_chat_title == "Snapshot chat"


@pytest.mark.asyncio
async def test_repeated_save_is_idempotent(db_setup):
    async with db_setup.Session() as db:
        first = await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        second = await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    assert first.saved is True
    assert second.saved is True
    rows = await saved_rows(db_setup.Session, source_verdict_id=db_setup.verdict_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_save_returns_existing_row(db_setup, monkeypatch):
    async with db_setup.Session() as db:
        first = await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    async with db_setup.Session() as db:
        chat = await db.get(Chat, db_setup.chat_id)
        chat.title = "Pending title survives duplicate save race"
        original_get_saved = saved_verdict_service._get_saved_by_source
        lookup_calls = 0

        async def miss_existing_once(db_arg, auth_arg, verdict_id_arg):
            nonlocal lookup_calls
            lookup_calls += 1
            if lookup_calls == 1:
                return None
            return await original_get_saved(db_arg, auth_arg, verdict_id_arg)

        monkeypatch.setattr(saved_verdict_service, "_get_saved_by_source", miss_existing_once)
        response = await saved_verdict_service.save_verdict(
            db, db_setup.auth, db_setup.verdict_id
        )
        await db.commit()

    rows = await saved_rows(db_setup.Session, source_verdict_id=db_setup.verdict_id)
    assert response.saved is True
    assert response.id == first.id
    assert response.id == rows[0].id
    assert response.source_verdict_id == db_setup.verdict_id
    assert len(rows) == 1
    async with db_setup.Session() as db:
        chat = await db.get(Chat, db_setup.chat_id)
    assert chat.title == "Pending title survives duplicate save race"


@pytest.mark.asyncio
async def test_duplicate_save_race_with_independent_sessions_returns_winner_row(
    db_setup, monkeypatch
):
    async with db_setup.Session() as first_lookup_db, db_setup.Session() as second_lookup_db:
        assert (
            await saved_verdict_service._get_saved_by_source(
                first_lookup_db, db_setup.auth, db_setup.verdict_id
            )
        ) is None
        assert (
            await saved_verdict_service._get_saved_by_source(
                second_lookup_db, db_setup.auth, db_setup.verdict_id
            )
        ) is None

    async with db_setup.Session() as winner_db:
        winner = await saved_verdict_service.save_verdict(
            winner_db, db_setup.auth, db_setup.verdict_id
        )
        await winner_db.commit()

    async with db_setup.Session() as loser_db:
        chat = await loser_db.get(Chat, db_setup.chat_id)
        chat.title = "Independent loser transaction survives"
        original_get_saved = saved_verdict_service._get_saved_by_source
        lookup_calls = 0

        async def stale_lookup_once(db_arg, auth_arg, verdict_id_arg):
            nonlocal lookup_calls
            lookup_calls += 1
            if lookup_calls == 1:
                return None
            return await original_get_saved(db_arg, auth_arg, verdict_id_arg)

        monkeypatch.setattr(saved_verdict_service, "_get_saved_by_source", stale_lookup_once)
        loser = await saved_verdict_service.save_verdict(
            loser_db, db_setup.auth, db_setup.verdict_id
        )
        await loser_db.commit()

    rows = await saved_rows(
        db_setup.Session,
        org_id=db_setup.auth.org_id,
        user_id=db_setup.auth.user.id,
        source_verdict_id=db_setup.verdict_id,
    )
    assert winner.id == loser.id
    assert loser.id == rows[0].id
    assert len(rows) == 1
    async with db_setup.Session() as db:
        chat = await db.get(Chat, db_setup.chat_id)
    assert chat.title == "Independent loser transaction survives"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_message",
    [
        "FOREIGN KEY constraint failed",
        "NOT NULL constraint failed: saved_verdicts.verdict_text",
    ],
)
async def test_save_does_not_swallow_fk_or_not_null_integrity_error(
    db_setup, monkeypatch, error_message
):
    async with db_setup.Session() as db:
        async def fail_flush_with_unrelated_integrity_error(*args, **kwargs):
            raise IntegrityError(
                "INSERT INTO saved_verdicts",
                None,
                Exception(error_message),
            )

        monkeypatch.setattr(db, "flush", fail_flush_with_unrelated_integrity_error)
        with pytest.raises(IntegrityError):
            await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)

    rows = await saved_rows(db_setup.Session, source_verdict_id=db_setup.verdict_id)
    assert rows == []


@pytest.mark.asyncio
async def test_save_does_not_swallow_another_unique_constraint(db_setup, monkeypatch):
    async with db_setup.Session() as db:
        async def fail_flush_with_other_unique_error(*args, **kwargs):
            raise IntegrityError(
                "INSERT INTO saved_verdicts",
                None,
                Exception("UNIQUE constraint failed: saved_verdicts.id"),
            )

        monkeypatch.setattr(db, "flush", fail_flush_with_other_unique_error)
        with pytest.raises(IntegrityError):
            await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)

    rows = await saved_rows(db_setup.Session, source_verdict_id=db_setup.verdict_id)
    assert rows == []


@pytest.mark.asyncio
async def test_save_unique_race_does_not_return_another_users_row(db_setup, monkeypatch):
    async with db_setup.Session() as other_db:
        await saved_verdict_service.save_verdict(
            other_db, db_setup.other_user_auth, db_setup.verdict_id
        )
        await other_db.commit()

    async with db_setup.Session() as db:
        async def fail_flush_with_expected_unique_error(*args, **kwargs):
            raise IntegrityError(
                "INSERT INTO saved_verdicts",
                None,
                Exception(
                    "UNIQUE constraint failed: saved_verdicts.org_id, "
                    "saved_verdicts.user_id, saved_verdicts.source_verdict_id"
                ),
            )

        monkeypatch.setattr(db, "flush", fail_flush_with_expected_unique_error)
        with pytest.raises(IntegrityError):
            await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)

    current_user_rows = await saved_rows(
        db_setup.Session,
        user_id=db_setup.auth.user.id,
        source_verdict_id=db_setup.verdict_id,
    )
    other_user_rows = await saved_rows(
        db_setup.Session,
        user_id=db_setup.other_user_auth.user.id,
        source_verdict_id=db_setup.verdict_id,
    )
    assert current_user_rows == []
    assert len(other_user_rows) == 1


@pytest.mark.asyncio
async def test_duplicate_saved_verdict_rows_are_prevented(db_setup):
    async with db_setup.Session() as db:
        saved = SavedVerdict(
            org_id=db_setup.auth.org_id,
            user_id=db_setup.auth.user.id,
            source_verdict_id=db_setup.verdict_id,
            source_turn_id=db_setup.turn_id,
            source_chat_id=db_setup.chat_id,
            source_chat_title="Snapshot chat",
            source_user_message="Backend prompt",
            verdict_text="Backend verdict",
            verdict_reason="Backend reason",
            verdict_model_id="gemini",
            strategy=Strategy.SYNTHESIZE,
        )
        duplicate = SavedVerdict(
            org_id=db_setup.auth.org_id,
            user_id=db_setup.auth.user.id,
            source_verdict_id=db_setup.verdict_id,
            source_turn_id=db_setup.turn_id,
            source_chat_id=db_setup.chat_id,
            source_chat_title="Snapshot chat",
            source_user_message="Backend prompt",
            verdict_text="Backend verdict",
            verdict_reason="Backend reason",
            verdict_model_id="gemini",
            strategy=Strategy.SYNTHESIZE,
        )
        db.add_all([saved, duplicate])
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_another_organizations_verdict_cannot_be_saved(db_setup):
    async with db_setup.Session() as db:
        with pytest.raises(NotFoundError):
            await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.other_verdict_id)


@pytest.mark.asyncio
async def test_another_user_cannot_see_saved_item(db_setup):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        visible = await saved_verdict_service.list_saved_verdicts(db, db_setup.auth)
        hidden = await saved_verdict_service.list_saved_verdicts(db, db_setup.other_user_auth)
        await db.commit()

    assert len(visible) == 1
    assert hidden == []


@pytest.mark.asyncio
async def test_list_returns_only_current_user_saved_verdicts(db_setup):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await saved_verdict_service.save_verdict(db, db_setup.other_user_auth, db_setup.verdict_id)
        user_items = await saved_verdict_service.list_saved_verdicts(db, db_setup.auth)
        await db.commit()

    assert [item.source_verdict_id for item in user_items] == [db_setup.verdict_id]


@pytest.mark.asyncio
async def test_list_sorts_newest_first(db_setup):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        first = (await db.execute(select(SavedVerdict))).scalar_one()
        first.saved_at = datetime.now(UTC) - timedelta(days=1)
        second = SavedVerdict(
            org_id=db_setup.auth.org_id,
            user_id=db_setup.auth.user.id,
            source_verdict_id="source-newer",
            source_turn_id="turn-newer",
            source_chat_id=db_setup.chat_id,
            source_chat_title="Newer",
            source_user_message="New prompt",
            verdict_text="New verdict",
            verdict_reason="New reason",
            verdict_model_id="gemini",
            strategy=Strategy.SYNTHESIZE,
            saved_at=datetime.now(UTC),
        )
        db.add(second)
        await db.flush()
        items = await saved_verdict_service.list_saved_verdicts(db, db_setup.auth)
        await db.commit()

    assert [item.source_verdict_id for item in items] == ["source-newer", db_setup.verdict_id]


@pytest.mark.asyncio
async def test_unsave_removes_only_current_users_saved_item(db_setup):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await saved_verdict_service.save_verdict(db, db_setup.other_user_auth, db_setup.verdict_id)
        response = await saved_verdict_service.unsave_verdict(db, db_setup.auth, db_setup.verdict_id)
        current = await saved_verdict_service.list_saved_verdicts(db, db_setup.auth)
        other = await saved_verdict_service.list_saved_verdicts(db, db_setup.other_user_auth)
        await db.commit()

    assert response.saved is False
    assert current == []
    assert len(other) == 1


@pytest.mark.asyncio
async def test_repeated_unsave_is_safe(db_setup):
    async with db_setup.Session() as db:
        first = await saved_verdict_service.unsave_verdict(db, db_setup.auth, db_setup.verdict_id)
        second = await saved_verdict_service.unsave_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    assert first.saved is False
    assert second.saved is False


@pytest.mark.asyncio
async def test_user_deletes_own_saved_verdict_without_deleting_source_rows(db_setup):
    async with db_setup.Session() as db:
        current = await saved_verdict_service.save_verdict(
            db, db_setup.auth, db_setup.verdict_id
        )
        other = await saved_verdict_service.save_verdict(
            db, db_setup.other_user_auth, db_setup.verdict_id
        )
        await db.commit()

    async with db_setup.Session() as db:
        response = await saved_verdict_service.delete_saved_verdict(
            db, db_setup.auth, current.id
        )
        await db.commit()

    assert response.deleted is True
    assert response.id == current.id
    assert await saved_rows(db_setup.Session, id=current.id) == []
    assert len(await saved_rows(db_setup.Session, id=other.id)) == 1
    async with db_setup.Session() as db:
        assert await db.get(Chat, db_setup.chat_id) is not None
        assert await db.get(Turn, db_setup.turn_id) is not None
        assert await db.get(Verdict, db_setup.verdict_id) is not None


@pytest.mark.asyncio
async def test_same_organization_user_cannot_delete_another_users_saved_verdict(db_setup):
    async with db_setup.Session() as db:
        saved = await saved_verdict_service.save_verdict(
            db, db_setup.other_user_auth, db_setup.verdict_id
        )
        await db.commit()

    async with db_setup.Session() as db:
        with pytest.raises(NotFoundError):
            await saved_verdict_service.delete_saved_verdict(db, db_setup.auth, saved.id)
        await db.rollback()

    assert len(await saved_rows(db_setup.Session, id=saved.id)) == 1


@pytest.mark.asyncio
async def test_cross_organization_user_cannot_delete_saved_verdict(db_setup):
    async with db_setup.Session() as db:
        saved = await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    async with db_setup.Session() as db:
        with pytest.raises(NotFoundError):
            await saved_verdict_service.delete_saved_verdict(db, db_setup.other_org_auth, saved.id)
        await db.rollback()

    assert len(await saved_rows(db_setup.Session, id=saved.id)) == 1


@pytest.mark.asyncio
async def test_saved_verdict_delete_route_scopes_to_current_user(db_setup):
    async with db_setup.Session() as db:
        saved = await saved_verdict_service.save_verdict(
            db, db_setup.other_user_auth, db_setup.verdict_id
        )
        await db.commit()

    async with db_setup.Session() as db:
        with pytest.raises(NotFoundError):
            await saved_verdicts_api_module.delete_saved_verdict(
                saved.id,
                auth=db_setup.auth,
                db=db,
            )
        await db.rollback()

    assert len(await saved_rows(db_setup.Session, id=saved.id)) == 1


@pytest.mark.asyncio
async def test_saved_verdict_delete_route_commit_failure_leaves_row(db_setup, monkeypatch):
    async with db_setup.Session() as db:
        saved = await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    async with db_setup.Session() as db:
        rollback_called = False

        async def fail_commit():
            raise RuntimeError("commit failed")

        original_rollback = db.rollback

        async def track_rollback():
            nonlocal rollback_called
            rollback_called = True
            await original_rollback()

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(db, "rollback", track_rollback)
        with pytest.raises(RuntimeError):
            await saved_verdicts_api_module.delete_saved_verdict(
                saved.id,
                auth=db_setup.auth,
                db=db,
            )

    assert rollback_called is True
    assert len(await saved_rows(db_setup.Session, id=saved.id)) == 1


@pytest.mark.asyncio
async def test_viewer_can_delete_own_saved_verdict_but_cannot_purge(db_setup):
    async with db_setup.Session() as db:
        saved = await saved_verdict_service.save_verdict(
            db, db_setup.viewer_auth, db_setup.verdict_id
        )
        await db.commit()

    async with db_setup.Session() as db:
        response = await saved_verdict_service.delete_saved_verdict(
            db, db_setup.viewer_auth, saved.id
        )
        await db.commit()

    assert response.deleted is True
    assert await saved_rows(db_setup.Session, id=saved.id) == []

    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    async with db_setup.Session() as db:
        with pytest.raises(ForbiddenError):
            await saved_verdict_service.purge_organization_saved_verdicts(
                db, db_setup.viewer_auth
            )
        await db.rollback()

    assert len(await saved_rows(db_setup.Session, org_id=db_setup.auth.org_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_name", ["admin_auth", "owner_auth"])
async def test_admin_and_owner_can_purge_organization_saved_verdicts(db_setup, auth_name):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await saved_verdict_service.save_verdict(
            db, db_setup.other_user_auth, db_setup.verdict_id
        )
        await db.commit()

    async with db_setup.Session() as db:
        response = await saved_verdict_service.purge_organization_saved_verdicts(
            db, getattr(db_setup, auth_name)
        )
        await db.commit()

    assert response.deleted_count == 2
    assert await saved_rows(db_setup.Session, org_id=db_setup.auth.org_id) == []
    async with db_setup.Session() as db:
        assert await db.get(Chat, db_setup.chat_id) is not None
        assert await db.get(Turn, db_setup.turn_id) is not None
        assert await db.get(Verdict, db_setup.verdict_id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_name", ["auth", "viewer_auth"])
async def test_member_and_viewer_cannot_purge_organization_saved_verdicts(db_setup, auth_name):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    async with db_setup.Session() as db:
        with pytest.raises(ForbiddenError):
            await saved_verdict_service.purge_organization_saved_verdicts(
                db, getattr(db_setup, auth_name)
            )
        await db.rollback()

    assert len(await saved_rows(db_setup.Session, org_id=db_setup.auth.org_id)) == 1


@pytest.mark.asyncio
async def test_organization_purge_does_not_cross_tenants(db_setup):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        other_org_saved = await saved_verdict_service.save_verdict(
            db, db_setup.other_org_auth, db_setup.other_verdict_id
        )
        await db.commit()

    async with db_setup.Session() as db:
        response = await saved_verdict_service.purge_organization_saved_verdicts(
            db, db_setup.owner_auth
        )
        await db.commit()

    assert response.deleted_count == 1
    assert await saved_rows(db_setup.Session, org_id=db_setup.auth.org_id) == []
    assert len(await saved_rows(db_setup.Session, id=other_org_saved.id)) == 1


@pytest.mark.asyncio
async def test_purge_flush_failure_rolls_back_without_success(db_setup, monkeypatch):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    async with db_setup.Session() as db:
        async def fail_flush():
            raise RuntimeError("flush failed")

        monkeypatch.setattr(db, "flush", fail_flush)
        with pytest.raises(RuntimeError):
            await saved_verdict_service.purge_organization_saved_verdicts(
                db, db_setup.owner_auth
            )
        await db.rollback()

    assert len(await saved_rows(db_setup.Session, org_id=db_setup.auth.org_id)) == 1


@pytest.mark.asyncio
async def test_purge_route_commit_failure_leaves_rows(db_setup, monkeypatch):
    async with db_setup.Session() as db:
        first = await saved_verdict_service.save_verdict(
            db, db_setup.auth, db_setup.verdict_id
        )
        second = await saved_verdict_service.save_verdict(
            db, db_setup.other_user_auth, db_setup.verdict_id
        )
        await db.commit()

    async with db_setup.Session() as db:
        rollback_called = False

        async def fail_commit():
            raise RuntimeError("commit failed")

        original_rollback = db.rollback

        async def track_rollback():
            nonlocal rollback_called
            rollback_called = True
            await original_rollback()

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(db, "rollback", track_rollback)
        with pytest.raises(RuntimeError):
            await saved_verdicts_api_module.purge_organization_saved_verdicts(
                auth=db_setup.owner_auth,
                db=db,
            )

    assert rollback_called is True
    assert len(await saved_rows(db_setup.Session, id=first.id)) == 1
    assert len(await saved_rows(db_setup.Session, id=second.id)) == 1


@pytest.mark.asyncio
async def test_repeated_saved_verdict_delete_returns_not_found_without_500(db_setup):
    async with db_setup.Session() as db:
        saved = await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.commit()

    async with db_setup.Session() as db:
        first = await saved_verdict_service.delete_saved_verdict(db, db_setup.auth, saved.id)
        await db.commit()

    async with db_setup.Session() as db:
        with pytest.raises(NotFoundError):
            await saved_verdict_service.delete_saved_verdict(db, db_setup.auth, saved.id)
        await db.rollback()

    assert first.deleted is True
    assert await saved_rows(db_setup.Session, id=saved.id) == []


@pytest.mark.asyncio
async def test_deleting_original_verdict_does_not_delete_snapshot(db_setup):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await db.execute(delete(Verdict).where(Verdict.id == db_setup.verdict_id))
        items = await saved_verdict_service.list_saved_verdicts(db, db_setup.auth)
        await db.commit()

    assert len(items) == 1
    assert items[0].source_verdict_id == db_setup.verdict_id
    assert items[0].verdict_text == "Backend verdict"


@pytest.mark.asyncio
async def test_deleting_original_turn_does_not_delete_snapshot(db_setup):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, db_setup.turn_id)
        items = await saved_verdict_service.list_saved_verdicts(db, db_setup.auth)
        await db.commit()

    assert len(items) == 1
    assert items[0].source_turn_id == db_setup.turn_id
    assert items[0].verdict_text == "Backend verdict"


@pytest.mark.asyncio
async def test_deleting_original_chat_does_not_delete_snapshot_and_reports_unavailable(db_setup):
    async with db_setup.Session() as db:
        await saved_verdict_service.save_verdict(db, db_setup.auth, db_setup.verdict_id)
        await chat_service.delete_chat(db, db_setup.auth, db_setup.chat_id)
        items = await saved_verdict_service.list_saved_verdicts(db, db_setup.auth)
        await db.commit()

    assert len(items) == 1
    assert items[0].source_chat_id is None
    assert items[0].original_chat_exists is False
    assert items[0].original_chat_route is None
    assert items[0].source_chat_title == "Snapshot chat"
