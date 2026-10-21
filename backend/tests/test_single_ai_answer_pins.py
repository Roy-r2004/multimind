"""Pins for single-AI answers, which have no Verdict row."""

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.base import Base
from app.db.models import (
    Chat,
    ModelAnswer,
    ModelAnswerStatus,
    Organization,
    OrgMembership,
    OrgRole,
    Strategy,
    Turn,
    TurnStatus,
    User,
    Verdict,
)
from app.services.chat_service import chat_service


@pytest.fixture
async def db_setup(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'answer-pins.db'}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        org = Organization(name="Org", slug="org-answers")
        other_org = Organization(name="Other", slug="other-answers")
        user = User(email="answer@example.com", hashed_password="x", full_name="User")
        other = User(email="other-answer@example.com", hashed_password="x", full_name="Other")
        db.add_all([org, other_org, user, other])
        await db.flush()
        db.add_all(
            [
                OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER),
                OrgMembership(org_id=other_org.id, user_id=other.id, role=OrgRole.MEMBER),
            ]
        )
        chat = Chat(org_id=org.id, created_by=user.id, title="Single AI")
        db.add(chat)
        await db.flush()
        single = Turn(
            chat_id=chat.id,
            user_message="One model",
            model_set_id="solo",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gpt-4.1",
            status=TurnStatus.COMPLETED,
        )
        council = Turn(
            chat_id=chat.id,
            user_message="Council",
            model_set_id="council",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.COMPLETED,
        )
        db.add_all([single, council])
        await db.flush()
        answer = ModelAnswer(
            turn_id=single.id,
            model_id="gpt-4.1",
            text="Only this model answered.",
            status=ModelAnswerStatus.COMPLETED,
        )
        verdict = Verdict(
            turn_id=council.id,
            model_id="gemini",
            strategy=Strategy.SYNTHESIZE,
            text="Council verdict",
            reason="Because.",
        )
        db.add_all([answer, verdict])
        await db.commit()
        ids = SimpleNamespace(
            Session=Session,
            org_id=org.id,
            other_org_id=other_org.id,
            user_id=user.id,
            other_user_id=other.id,
            chat_id=chat.id,
            turn_id=single.id,
            answer_id=answer.id,
            verdict_id=verdict.id,
            verdict_turn_id=council.id,
        )

    try:
        yield ids
    finally:
        await engine.dispose()


async def _auth(db, setup, *, other: bool = False) -> AuthContext:
    from sqlalchemy import select

    user_id = setup.other_user_id if other else setup.user_id
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    return AuthContext(
        user=user,
        org_id=setup.other_org_id if other else setup.org_id,
        role=OrgRole.MEMBER,
    )


@pytest.mark.asyncio
async def test_single_ai_answer_pin_selection_pins_and_verdict_pins_stay_separate(db_setup):
    async with db_setup.Session() as db:
        auth = await _auth(db, db_setup)
        pinned = await chat_service.pin_answer(db, auth, db_setup.chat_id, db_setup.answer_id)
        assert [(pin.answer_id, pin.turn_id, pin.pin_type) for pin in pinned.pinned_answers] == [
            (db_setup.answer_id, db_setup.turn_id, "answer")
        ]
        assert pinned.pinned_verdicts == []

        duplicate = await chat_service.pin_answer(db, auth, db_setup.chat_id, db_setup.answer_id)
        assert len(duplicate.pinned_answers) == 1

        first = await chat_service.pin_answer(
            db,
            auth,
            db_setup.chat_id,
            db_setup.answer_id,
            pin_type="selection",
            selected_text="Only this",
            selected_html="<p>Only this</p>",
        )
        second = await chat_service.pin_answer(
            db,
            auth,
            db_setup.chat_id,
            db_setup.answer_id,
            pin_type="selection",
            selected_text="model answered",
            selected_html="<p>model answered</p>",
        )
        assert len(second.pinned_answers) == 3
        assert {pin.pin_type for pin in second.pinned_answers} == {"answer", "selection"}

        verdict = await chat_service.pin_verdict(db, auth, db_setup.chat_id, db_setup.verdict_id)
        assert [pin.verdict_id for pin in verdict.pinned_verdicts] == [db_setup.verdict_id]
        assert len(verdict.pinned_answers) == 3

        listed = await chat_service.list_chats(db, auth)
        chat = next(item for item in listed if item.id == db_setup.chat_id)
        assert [pin.verdict_id for pin in chat.pinned_verdicts] == [db_setup.verdict_id]
        assert {pin.turn_id for pin in chat.pinned_answers} == {db_setup.turn_id}
        assert len(chat.pinned_answers) == 3

        removed = await chat_service.unpin_answer_selection(
            db, auth, db_setup.chat_id, first.pinned_answers[-1].id
        )
        assert len(removed.pinned_answers) == 2
        assert [pin.verdict_id for pin in removed.pinned_verdicts] == [db_setup.verdict_id]

        unpinned = await chat_service.unpin_answer(db, auth, db_setup.chat_id, db_setup.answer_id)
        assert [pin.pin_type for pin in unpinned.pinned_answers] == ["selection"]
        assert unpinned.pinned_verdicts[0].verdict_id == db_setup.verdict_id


@pytest.mark.asyncio
async def test_answer_pin_rejects_another_org_and_missing_answer(db_setup):
    async with db_setup.Session() as db:
        other = await _auth(db, db_setup, other=True)
        with pytest.raises(NotFoundError):
            await chat_service.pin_answer(db, other, db_setup.chat_id, db_setup.answer_id)
        auth = await _auth(db, db_setup)
        with pytest.raises(NotFoundError):
            await chat_service.pin_answer(db, auth, db_setup.chat_id, "missing-answer")
