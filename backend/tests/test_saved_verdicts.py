from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
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
        db.add_all([org, other_org, user, other_user])
        await db.flush()
        db.add_all(
            [
                OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER),
                OrgMembership(org_id=org.id, user_id=other_user.id, role=OrgRole.MEMBER),
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
