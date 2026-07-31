"""Focused tests for prompt edit / turn regeneration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.db.base import Base
from app.db.models import (
    Chat,
    ModelAnswer,
    ModelAnswerStatus,
    ModelSet,
    Organization,
    OrgMembership,
    OrgRole,
    Strategy,
    Turn,
    TurnStatus,
    User,
    Verdict,
)
from app.schemas.api import TurnCreateRequest, TurnRegenerateRequest
from app.services import chat_service as chat_service_module
from app.services.chat_service import chat_service


@pytest.fixture
async def db_setup(tmp_path, monkeypatch):
    db_path = tmp_path / "turn-regenerate.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(chat_service_module, "AsyncSessionLocal", Session)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        org = Organization(name="Org", slug="org-regen")
        other_org = Organization(name="Other", slug="other-regen")
        user = User(email="regen@example.com", hashed_password="x", full_name="User")
        viewer = User(email="regen-viewer@example.com", hashed_password="x", full_name="Viewer")
        outsider = User(email="regen-out@example.com", hashed_password="x", full_name="Out")
        db.add_all([org, other_org, user, viewer, outsider])
        await db.flush()
        db.add_all(
            [
                OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER),
                OrgMembership(org_id=org.id, user_id=viewer.id, role=OrgRole.VIEWER),
                OrgMembership(org_id=other_org.id, user_id=outsider.id, role=OrgRole.OWNER),
            ]
        )
        model_set = ModelSet(
            org_id=org.id,
            slug="regen-set",
            name="Regen Set",
            description="",
            models=["gpt-4.1", "claude"],
            verdict_model="gemini",
            strategy=Strategy.SYNTHESIZE,
            best_for="",
            is_system=False,
        )
        referee = ModelSet(
            org_id=None,
            slug="referee",
            name="Chafiq Referee",
            description="",
            models=["gpt-4.1", "claude", "gemini"],
            verdict_model="gpt-4.1",
            strategy=Strategy.REFEREE,
            best_for="",
            is_system=True,
        )
        chat = Chat(org_id=org.id, created_by=user.id, title="Regen chat")
        other_chat = Chat(org_id=other_org.id, created_by=outsider.id, title="Other")
        db.add_all([model_set, referee, chat, other_chat])
        await db.commit()
        ids = SimpleNamespace(
            org_id=org.id,
            other_org_id=other_org.id,
            user_id=user.id,
            viewer_id=viewer.id,
            outsider_id=outsider.id,
            chat_id=chat.id,
            other_chat_id=other_chat.id,
        )

    yield SimpleNamespace(
        Session=Session,
        engine=engine,
        auth=AuthContext(user=user, org_id=ids.org_id, role=OrgRole.MEMBER),
        viewer_auth=AuthContext(user=viewer, org_id=ids.org_id, role=OrgRole.VIEWER),
        outsider_auth=AuthContext(
            user=outsider, org_id=ids.other_org_id, role=OrgRole.OWNER
        ),
        chat_id=ids.chat_id,
        other_chat_id=ids.other_chat_id,
    )
    await engine.dispose()


async def _complete_turn(
    Session,
    *,
    chat_id: str,
    user_message: str,
    model_set_id: str = "regen-set",
    created_at: datetime | None = None,
) -> Turn:
    async with Session() as db:
        turn = Turn(
            chat_id=chat_id,
            user_message=user_message,
            model_set_id=model_set_id,
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.COMPLETED,
            created_at=created_at or datetime.now(UTC),
        )
        db.add(turn)
        await db.flush()
        db.add(
            ModelAnswer(
                turn_id=turn.id,
                model_id="gpt-4.1",
                text=f"Answer for {user_message}",
                confidence=80,
                status=ModelAnswerStatus.COMPLETED,
            )
        )
        db.add(
            Verdict(
                turn_id=turn.id,
                model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                text=f"Verdict for {user_message}",
                reason="Because.",
            )
        )
        await db.commit()
        return turn


@pytest.mark.asyncio
async def test_owner_can_regenerate_and_reuse_model_set(db_setup):
    Session = db_setup.Session
    t1 = await _complete_turn(Session, chat_id=db_setup.chat_id, user_message="First")
    async with Session() as db:
        result = await chat_service.regenerate_turn(
            db,
            db_setup.auth,
            db_setup.chat_id,
            t1.id,
            TurnRegenerateRequest(prompt="Edited first"),
        )
    assert result.old_turn_id == t1.id
    assert result.new_turn.user_message == "Edited first"
    assert result.new_turn.model_set_id == "regen-set"
    assert result.new_turn.status == "pending"
    assert result.superseded_turn_ids == [t1.id]
    assert result.model_set_fallback is False

    async with Session() as db:
        old = (
            await db.execute(select(Turn).where(Turn.id == t1.id))
        ).scalar_one()
        assert old.deleted_at is not None
        listed = await chat_service.list_turns(db, db_setup.auth, db_setup.chat_id)
    assert [turn.id for turn in listed] == [result.new_turn.id]
    assert listed[0].user_message == "Edited first"
    assert listed[0].verdict is None


@pytest.mark.asyncio
async def test_later_turns_are_superseded(db_setup):
    Session = db_setup.Session
    base = datetime.now(UTC)
    t1 = await _complete_turn(
        Session, chat_id=db_setup.chat_id, user_message="One", created_at=base
    )
    t2 = await _complete_turn(
        Session,
        chat_id=db_setup.chat_id,
        user_message="Two",
        created_at=base + timedelta(seconds=1),
    )
    t3 = await _complete_turn(
        Session,
        chat_id=db_setup.chat_id,
        user_message="Three",
        created_at=base + timedelta(seconds=2),
    )

    async with Session() as db:
        result = await chat_service.regenerate_turn(
            db,
            db_setup.auth,
            db_setup.chat_id,
            t1.id,
            TurnRegenerateRequest(prompt="One edited"),
        )

    assert set(result.superseded_turn_ids) == {t1.id, t2.id, t3.id}
    async with Session() as db:
        listed = await chat_service.list_turns(db, db_setup.auth, db_setup.chat_id)
        assert len(listed) == 1
        assert listed[0].id == result.new_turn.id


@pytest.mark.asyncio
async def test_empty_prompt_rejected(db_setup):
    Session = db_setup.Session
    t1 = await _complete_turn(Session, chat_id=db_setup.chat_id, user_message="Keep")
    async with Session() as db:
        with pytest.raises(ValidationError, match="empty"):
            await chat_service.regenerate_turn(
                db,
                db_setup.auth,
                db_setup.chat_id,
                t1.id,
                TurnRegenerateRequest(prompt="   "),
            )
        listed = await chat_service.list_turns(db, db_setup.auth, db_setup.chat_id)
    assert len(listed) == 1
    assert listed[0].id == t1.id


@pytest.mark.asyncio
async def test_viewer_and_wrong_org_cannot_regenerate(db_setup):
    Session = db_setup.Session
    t1 = await _complete_turn(Session, chat_id=db_setup.chat_id, user_message="Secret")

    async with Session() as db:
        with pytest.raises(ForbiddenError):
            await chat_service.regenerate_turn(
                db,
                db_setup.viewer_auth,
                db_setup.chat_id,
                t1.id,
                TurnRegenerateRequest(prompt="Nope"),
            )

    async with Session() as db:
        with pytest.raises(NotFoundError):
            await chat_service.regenerate_turn(
                db,
                db_setup.outsider_auth,
                db_setup.chat_id,
                t1.id,
                TurnRegenerateRequest(prompt="Nope"),
            )


@pytest.mark.asyncio
async def test_missing_turn_and_active_conflict(db_setup):
    Session = db_setup.Session
    async with Session() as db:
        with pytest.raises(NotFoundError):
            await chat_service.regenerate_turn(
                db,
                db_setup.auth,
                db_setup.chat_id,
                str(uuid4()),
                TurnRegenerateRequest(prompt="Missing"),
            )

    async with Session() as db:
        pending = await chat_service.start_turn(
            db,
            db_setup.auth,
            db_setup.chat_id,
            TurnCreateRequest(user_message="Running", model_set_id="regen-set"),
        )
        await db.commit()

    async with Session() as db:
        with pytest.raises(ConflictError, match="still generating"):
            await chat_service.regenerate_turn(
                db,
                db_setup.auth,
                db_setup.chat_id,
                pending.id,
                TurnRegenerateRequest(prompt="Edit running"),
            )


@pytest.mark.asyncio
async def test_missing_model_set_falls_back_to_referee(db_setup):
    Session = db_setup.Session
    t1 = await _complete_turn(
        Session,
        chat_id=db_setup.chat_id,
        user_message="Old set",
        model_set_id="missing-set",
    )
    async with Session() as db:
        result = await chat_service.regenerate_turn(
            db,
            db_setup.auth,
            db_setup.chat_id,
            t1.id,
            TurnRegenerateRequest(prompt="Recovered"),
        )
    assert result.model_set_fallback is True
    assert result.new_turn.model_set_id == "referee"


@pytest.mark.asyncio
async def test_unrelated_user_set_and_normal_create_still_work(db_setup):
    Session = db_setup.Session
    async with Session() as db:
        created = await chat_service.start_turn(
            db,
            db_setup.auth,
            db_setup.chat_id,
            TurnCreateRequest(user_message="Brand new", model_set_id="regen-set"),
        )
        await db.commit()
    assert created.user_message == "Brand new"
    assert created.model_set_id == "regen-set"
