from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.base import Base
from app.db.models import (
    Chat,
    OrgMembership,
    OrgRole,
    Organization,
    Strategy,
    Turn,
    TurnStatus,
    User,
    Verdict,
)
from app.services.brain_knowledge_service import (
    SOURCE_CHAT_TURN,
    brain_knowledge_service,
)
from app.services.brain_service import brain_service
from app.services.chat_service import chat_service
from app.services.embedding_utils import cosine_similarity, local_embed
from app.services.saved_document_service import saved_document_service


@pytest.fixture
async def db_setup(tmp_path):
    db_path = tmp_path / "pin-docs.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        org = Organization(name="Org", slug="org")
        other_org = Organization(name="Other", slug="other")
        user = User(email="u@example.com", hashed_password="x", full_name="User")
        other = User(email="o@example.com", hashed_password="x", full_name="Other")
        db.add_all([org, other_org, user, other])
        await db.flush()
        db.add_all(
            [
                OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER),
                OrgMembership(org_id=other_org.id, user_id=other.id, role=OrgRole.MEMBER),
            ]
        )
        chat = Chat(org_id=org.id, created_by=user.id, title="Pin chat")
        other_chat = Chat(org_id=other_org.id, created_by=other.id, title="Other")
        db.add_all([chat, other_chat])
        await db.flush()
        turn = Turn(
            chat_id=chat.id,
            user_message="How does alcohol affect sleep?",
            model_set_id="balanced",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gpt-4.1",
            status=TurnStatus.COMPLETED,
        )
        other_turn = Turn(
            chat_id=other_chat.id,
            user_message="Secret",
            model_set_id="balanced",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gpt-4.1",
            status=TurnStatus.COMPLETED,
        )
        db.add_all([turn, other_turn])
        await db.flush()
        verdict = Verdict(
            turn_id=turn.id,
            model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            text="Alcohol disrupts REM sleep.",
            reason="Council consensus",
        )
        other_verdict = Verdict(
            turn_id=other_turn.id,
            model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            text="Other org verdict",
            reason="n/a",
        )
        db.add_all([verdict, other_verdict])
        await db.commit()

    try:
        yield SimpleNamespace(
            Session=Session,
            org_id=org.id,
            other_org_id=other_org.id,
            user_id=user.id,
            other_user_id=other.id,
            chat_id=chat.id,
            other_chat_id=other_chat.id,
            turn_id=turn.id,
            verdict_id=verdict.id,
            other_verdict_id=other_verdict.id,
        )
    finally:
        await engine.dispose()


async def auth_for(db, setup, *, other: bool = False) -> AuthContext:
    from sqlalchemy import select

    user_id = setup.other_user_id if other else setup.user_id
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    return AuthContext(
        user=user,
        org_id=setup.other_org_id if other else setup.org_id,
        role=OrgRole.MEMBER,
    )


@pytest.mark.asyncio
async def test_pin_replace_and_unpin(db_setup):
    async with db_setup.Session() as db:
        auth = await auth_for(db, db_setup)
        pinned = await chat_service.pin_verdict(db, auth, db_setup.chat_id, db_setup.verdict_id)
        assert pinned.pinned_verdict_id == db_setup.verdict_id
        assert pinned.pinned_turn_id == db_setup.turn_id

        # second pin replaces
        turn2 = Turn(
            chat_id=db_setup.chat_id,
            user_message="Follow up",
            model_set_id="balanced",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gpt-4.1",
            status=TurnStatus.COMPLETED,
        )
        db.add(turn2)
        await db.flush()
        verdict2 = Verdict(
            turn_id=turn2.id,
            model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            text="Second verdict",
            reason="r",
        )
        db.add(verdict2)
        await db.flush()
        replaced = await chat_service.pin_verdict(db, auth, db_setup.chat_id, verdict2.id)
        assert replaced.pinned_verdict_id == verdict2.id

        unpinned = await chat_service.unpin_verdict(db, auth, db_setup.chat_id)
        assert unpinned.pinned_verdict_id is None
        await db.commit()


@pytest.mark.asyncio
async def test_pin_rejects_foreign_verdict(db_setup):
    async with db_setup.Session() as db:
        auth = await auth_for(db, db_setup)
        with pytest.raises(NotFoundError):
            await chat_service.pin_verdict(
                db, auth, db_setup.chat_id, db_setup.other_verdict_id
            )


@pytest.mark.asyncio
async def test_saved_document_labels_and_search(db_setup):
    async with db_setup.Session() as db:
        auth = await auth_for(db, db_setup)
        label = await saved_document_service.create_label(db, auth, "Alcohol")
        doc = await saved_document_service.create_from_turn(
            db,
            auth,
            turn_id=db_setup.turn_id,
            name="Alcohol Consumption Verdict",
            label_ids=[label.id],
            label_names=[],
        )
        assert doc.name == "Alcohol Consumption Verdict"
        assert [item.name for item in doc.labels] == ["Alcohol"]

        found = await saved_document_service.search(db, auth, q="alcohol")
        assert len(found) == 1
        by_label = await saved_document_service.search(db, auth, label_id=label.id)
        assert len(by_label) == 1

        other_auth = await auth_for(db, db_setup, other=True)
        isolated = await saved_document_service.search(db, other_auth, q="alcohol")
        assert isolated == []
        await db.commit()


@pytest.mark.asyncio
async def test_brain_retrieval_is_permissioned(db_setup):
    assert cosine_similarity(local_embed("alcohol sleep"), local_embed("alcohol sleep")) > 0.9

    async with db_setup.Session() as db:
        await brain_knowledge_service.ingest_turn(
            db,
            org_id=db_setup.org_id,
            user_id=db_setup.user_id,
            project_id=None,
            turn_id=db_setup.turn_id,
            chat_title="Pin chat",
            user_message="How does alcohol affect sleep?",
            verdict_text="Alcohol disrupts REM sleep.",
        )
        await db.flush()

        context = await brain_service.get_context_for_user(
            db,
            db_setup.user_id,
            db_setup.org_id,
            "User",
            query="alcohol and sleep quality",
        )
        assert "Relevant Brain knowledge" in context
        assert "Alcohol disrupts REM sleep" in context

        other_context = await brain_service.get_context_for_user(
            db,
            db_setup.other_user_id,
            db_setup.other_org_id,
            "Other",
            query="alcohol and sleep quality",
        )
        assert "Alcohol disrupts REM sleep" not in other_context

        items = await brain_knowledge_service.retrieve(
            db,
            org_id=db_setup.org_id,
            user_id=db_setup.user_id,
            query="alcohol sleep",
        )
        assert any(item.source_type == SOURCE_CHAT_TURN for item in items)
        await db.commit()
