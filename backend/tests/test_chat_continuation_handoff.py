"""One-time fresh-chat continuation handoff (Chat A → Chat B)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.base import Base
from app.db.models import (
    Chat,
    ModelAnswer,
    ModelAnswerStatus,
    OrgMembership,
    OrgRole,
    Organization,
    Strategy,
    Turn,
    TurnStatus,
    User,
    Verdict,
)
from app.schemas.api import ChatUpdateRequest, TurnCreateRequest, TurnRegenerateRequest
from app.services.chat_memory_service import (
    CONTINUATION_HANDOFF_HEADER,
    CONTINUATION_HANDOFF_MAX_CHARS,
    CONTINUATION_HANDOFF_MAX_RECENT_TURNS,
    CONTINUATION_SEED_PREFIX,
    TurnHistoryEntry,
    build_continuation_handoff_text,
    chat_memory_service,
    extract_continuation_handoff,
    format_continuation_seed_memory,
)
from app.services.chat_service import (
    _run_chat_memory_update_best_effort,
    _seed_continuation_memory_after_success,
    chat_service,
)
from app.services.multi_reference_context_service import (
    MULTI_REFERENCE_HEADER,
    multi_reference_context_service,
)
from tests.conftest import create_model_set, create_other_auth


@pytest.fixture
async def handoff_env(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'handoff.db'}", poolclass=NullPool
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        org = Organization(name="Handoff Org", slug="handoff-org")
        user = User(email="handoff@example.com", hashed_password="x", full_name="Handoff User")
        db.add_all([org, user])
        await db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))
        chat_a = Chat(org_id=org.id, created_by=user.id, title="Capital of Lebanon")
        chat_b = Chat(org_id=org.id, created_by=user.id, title="New chat")
        db.add_all([chat_a, chat_b])
        await db.flush()
        await create_model_set(db, AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER))
        await db.commit()
        auth = AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)
        ids = SimpleNamespace(
            Session=Session,
            auth=auth,
            org_id=org.id,
            user_id=user.id,
            chat_a_id=chat_a.id,
            chat_b_id=chat_b.id,
        )

    try:
        yield ids
    finally:
        await engine.dispose()


async def _add_completed_turn(
    db: AsyncSession,
    *,
    chat_id: str,
    index: int,
    created_at: datetime | None = None,
    council_text: str | None = "SECRET_COUNCIL_SHOULD_NOT_APPEAR",
) -> Turn:
    turn = Turn(
        chat_id=chat_id,
        user_message=f"User question {index}",
        model_set_id="research-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.COMPLETED,
    )
    if created_at is not None:
        turn.created_at = created_at
    db.add(turn)
    await db.flush()
    if council_text is not None:
        db.add(
            ModelAnswer(
                turn_id=turn.id,
                model_id="gpt-4.1",
                text=council_text,
                status=ModelAnswerStatus.COMPLETED,
            )
        )
    db.add(
        Verdict(
            turn_id=turn.id,
            text=f"Verdict answer {index}",
            reason=f"Reason {index}",
            model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
        )
    )
    await db.flush()
    return turn


@pytest.mark.asyncio
async def test_one_reference_dispatches_only_to_legacy_builder(handoff_env, monkeypatch):
    calls = {"legacy": 0, "multi": 0}
    original = chat_memory_service.build_continuation_handoff

    async def legacy(*args, **kwargs):
        calls["legacy"] += 1
        return await original(*args, **kwargs)

    async def multi(*args, **kwargs):
        calls["multi"] += 1
        raise AssertionError("single reference reached multi-reference service")

    monkeypatch.setattr(chat_memory_service, "build_continuation_handoff", legacy)
    monkeypatch.setattr(multi_reference_context_service, "build", multi)
    async with handoff_env.Session() as db:
        await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Continue",
                model_set_id="research-set",
                referenced_chat_ids=[handoff_env.chat_a_id],
            ),
        )
    assert calls == {"legacy": 1, "multi": 0}


@pytest.mark.asyncio
async def test_single_reference_persists_and_rebuilds_fresh_on_every_turn(
    handoff_env, monkeypatch
):
    calls: list[str] = []

    async def handoff(_db, *, source_chat):
        calls.append(source_chat.id)
        return f"{CONTINUATION_HANDOFF_HEADER}\n\nfresh-{len(calls)}-{source_chat.title}"

    monkeypatch.setattr(chat_memory_service, "build_continuation_handoff", handoff)
    async with handoff_env.Session() as db:
        first = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Explicit",
                model_set_id="research-set",
                referenced_chat_id=handoff_env.chat_a_id,
            ),
        )
        for index in range(2, 6):
            turn = await chat_service.start_turn(
                db,
                handoff_env.auth,
                handoff_env.chat_b_id,
                TurnCreateRequest(user_message=f"Follow-up {index}", model_set_id="research-set"),
            )
            stored = await db.get(Turn, turn.id)
            assert f"fresh-{index}-Capital of Lebanon" in (stored.custom_instructions or "")

        chat = await db.get(Chat, handoff_env.chat_b_id)
        first_row = await db.get(Turn, first.id)
        assert chat.active_referenced_chat_id == handoff_env.chat_a_id
        assert "fresh-1-Capital of Lebanon" in (first_row.custom_instructions or "")

    assert calls == [handoff_env.chat_a_id] * 5


@pytest.mark.asyncio
async def test_explicit_persisted_reference_is_injected_exactly_once(handoff_env, monkeypatch):
    calls = 0

    async def handoff(_db, *, source_chat):
        nonlocal calls
        calls += 1
        return f"{CONTINUATION_HANDOFF_HEADER}\n\n{source_chat.title}"

    monkeypatch.setattr(chat_memory_service, "build_continuation_handoff", handoff)
    async with handoff_env.Session() as db:
        chat = await db.get(Chat, handoff_env.chat_b_id)
        chat.active_referenced_chat_id = handoff_env.chat_a_id
        turn = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Same again",
                model_set_id="research-set",
                referenced_chat_id=handoff_env.chat_a_id,
            ),
        )
        stored = await db.get(Turn, turn.id)
        assert (stored.custom_instructions or "").count(CONTINUATION_HANDOFF_HEADER) == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_persistent_reference_replace_and_remove(handoff_env, monkeypatch):
    seen: list[str] = []

    async def handoff(_db, *, source_chat):
        seen.append(source_chat.id)
        return f"{CONTINUATION_HANDOFF_HEADER}\n\n{source_chat.title}"

    monkeypatch.setattr(chat_memory_service, "build_continuation_handoff", handoff)
    async with handoff_env.Session() as db:
        chat_c = Chat(
            org_id=handoff_env.org_id, created_by=handoff_env.user_id, title="Replacement"
        )
        db.add(chat_c)
        await db.flush()
        target = await db.get(Chat, handoff_env.chat_b_id)
        target.active_referenced_chat_id = handoff_env.chat_a_id

        await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Replace",
                model_set_id="research-set",
                referenced_chat_id=chat_c.id,
            ),
        )
        follow_up = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(user_message="Uses replacement", model_set_id="research-set"),
        )
        assert "Replacement" in ((await db.get(Turn, follow_up.id)).custom_instructions or "")
        assert target.active_referenced_chat_id == chat_c.id

        response = await chat_service.update_chat(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            ChatUpdateRequest(active_referenced_chat_id=None),
        )
        assert response.active_referenced_chat is None
        no_reference = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(user_message="No reference", model_set_id="research-set"),
        )
        assert CONTINUATION_HANDOFF_HEADER not in (
            (await db.get(Turn, no_reference.id)).custom_instructions or ""
        )

    assert seen == [chat_c.id, chat_c.id]


@pytest.mark.asyncio
async def test_deleting_referenced_chat_clears_reference_and_target_remains_usable(handoff_env):
    async with handoff_env.Session() as db:
        target = await db.get(Chat, handoff_env.chat_b_id)
        target.active_referenced_chat_id = handoff_env.chat_a_id
        await db.commit()

        await chat_service.delete_chat(db, handoff_env.auth, handoff_env.chat_a_id)

    async with handoff_env.Session() as db:
        target = await db.get(Chat, handoff_env.chat_b_id)
        assert target is not None
        assert target.active_referenced_chat_id is None
        turn = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(user_message="Still works", model_set_id="research-set"),
        )
        stored = await db.get(Turn, turn.id)
        assert CONTINUATION_HANDOFF_HEADER not in (stored.custom_instructions or "")


@pytest.mark.asyncio
async def test_two_references_dispatch_to_multi_and_persist_snapshot(handoff_env, monkeypatch):
    async with handoff_env.Session() as db:
        chat_c = Chat(
            org_id=handoff_env.org_id,
            created_by=handoff_env.user_id,
            title="Second source",
        )
        db.add(chat_c)
        await db.commit()
        chat_c_id = chat_c.id

    calls = 0

    async def multi(_db, *, source_chats, question):
        nonlocal calls
        calls += 1
        assert [chat.id for chat in source_chats] == [handoff_env.chat_a_id, chat_c_id]
        assert question == "Combine"
        return f"{MULTI_REFERENCE_HEADER}\n\ncombined snapshot"

    monkeypatch.setattr(multi_reference_context_service, "build", multi)
    async with handoff_env.Session() as db:
        created = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Combine",
                model_set_id="research-set",
                referenced_chat_ids=[handoff_env.chat_a_id, chat_c_id],
            ),
        )
        stored = await db.get(Turn, created.id)
        assert MULTI_REFERENCE_HEADER in (stored.custom_instructions or "")
        snapshot = stored.custom_instructions
        stored.status = TurnStatus.COMPLETED
        await db.flush()
        assert await _seed_continuation_memory_after_success(
            db, chat_id=handoff_env.chat_b_id, turn=stored
        ) is True
        destination = await db.get(Chat, handoff_env.chat_b_id)
        assert MULTI_REFERENCE_HEADER in (destination.rolling_memory or "")
        assert len(destination.rolling_memory or "") <= 16_000
        await db.commit()

        regenerated = await chat_service.regenerate_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            created.id,
            TurnRegenerateRequest(prompt="Combine edited"),
        )
        regenerated_row = await db.get(Turn, regenerated.new_turn.id)
        assert regenerated_row.custom_instructions == snapshot
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference_ids",
    [
        ["  "],
        ["same", "same"],
    ],
)
async def test_multi_reference_rejects_blank_and_duplicate_ids(
    handoff_env, reference_ids
):
    with pytest.raises(ValidationError):
        async with handoff_env.Session() as db:
            await chat_service.start_turn(
                db,
                handoff_env.auth,
                handoff_env.chat_b_id,
                TurnCreateRequest(
                    user_message="Invalid",
                    model_set_id="research-set",
                    referenced_chat_ids=reference_ids,
                ),
            )


@pytest.mark.asyncio
async def test_reference_fields_are_mutually_exclusive(handoff_env):
    with pytest.raises(ValidationError, match="either"):
        async with handoff_env.Session() as db:
            await chat_service.start_turn(
                db,
                handoff_env.auth,
                handoff_env.chat_b_id,
                TurnCreateRequest(
                    user_message="Invalid",
                    model_set_id="research-set",
                    referenced_chat_id=handoff_env.chat_a_id,
                    referenced_chat_ids=[handoff_env.chat_a_id],
                ),
            )


def test_handoff_text_budget_and_contents():
    memory = "MEMORY_FACT " * 2000
    entries = [
        TurnHistoryEntry(
            turn_id=f"t{i}",
            user_message=f"Q{i} " + ("x" * 500),
            verdict_text=f"V{i} " + ("y" * 500),
            verdict_reason=f"R{i}",
            created_at=datetime.now(UTC),
        )
        for i in range(6)
    ]
    handoff = build_continuation_handoff_text(
        source_title="Prior Chat",
        rolling_memory=memory,
        recent_entries_oldest_first=entries[-CONTINUATION_HANDOFF_MAX_RECENT_TURNS:],
    )
    assert CONTINUATION_HANDOFF_HEADER in handoff
    assert "Prior Chat" in handoff
    assert "MEMORY_FACT" in handoff
    assert "User question:" in handoff or "Q" in handoff
    assert len(handoff) <= CONTINUATION_HANDOFF_MAX_CHARS
    assert "SECRET_COUNCIL" not in handoff


def test_extract_and_seed_format_helpers():
    handoff = (
        f"{CONTINUATION_HANDOFF_HEADER}\n"
        "The user is continuing a previous conversation from chat 'A'. "
        "Treat the following as prior conversation context.\n\n"
        "### Older memory from previous chat\nKeep Beirut as capital."
    )
    merged = f"Model set rules\n\n{handoff}\n\nAttached file: notes.txt\nContent:\n```text\nsecret\n```"
    extracted = extract_continuation_handoff(merged)
    assert extracted is not None
    assert CONTINUATION_HANDOFF_HEADER in extracted
    assert "Attached file" not in extracted
    assert "secret" not in extracted
    seeded = format_continuation_seed_memory(extracted)
    assert seeded.startswith(CONTINUATION_SEED_PREFIX)
    assert "Beirut" in seeded
    assert CONTINUATION_HANDOFF_HEADER not in seeded


@pytest.mark.asyncio
async def test_same_org_reference_builds_handoff_and_seeds_once(handoff_env):
    async with handoff_env.Session() as db:
        chat_a = await db.get(Chat, handoff_env.chat_a_id)
        assert chat_a is not None
        chat_a.rolling_memory = "Lebanon capital is Beirut. Population discussion ongoing."
        chat_a.rolling_memory_through_turn_id = None
        base = datetime.now(UTC) - timedelta(hours=1)
        for i in range(5):
            await _add_completed_turn(
                db, chat_id=chat_a.id, index=i, created_at=base + timedelta(minutes=i)
            )
        # Force watermark on Chat A to prove it is not copied.
        latest = (
            await chat_memory_service.list_eligible_turns_oldest_first(db, chat_a.id)
        )[0][0]
        chat_a.rolling_memory_through_turn_id = latest.id
        source_memory = chat_a.rolling_memory
        source_through = chat_a.rolling_memory_through_turn_id
        await db.commit()

    async with handoff_env.Session() as db:
        turn = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="How many people live there?",
                model_set_id="research-set",
                referenced_chat_id=handoff_env.chat_a_id,
            ),
        )
        await db.commit()
        stored = await db.get(Turn, turn.id)
        assert stored is not None
        instructions = stored.custom_instructions or ""
        assert CONTINUATION_HANDOFF_HEADER in instructions
        assert "Beirut" in instructions
        assert "User question 4" in instructions or "Verdict answer 4" in instructions
        assert "SECRET_COUNCIL" not in instructions
        assert "Attached file" not in instructions
        # Chat B still empty until post-completion seed.
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        assert chat_b is not None
        assert not (chat_b.rolling_memory or "").strip()

        chat_a = await db.get(Chat, handoff_env.chat_a_id)
        assert chat_a is not None
        assert chat_a.rolling_memory == source_memory
        assert chat_a.rolling_memory_through_turn_id == source_through

        # Synchronous post-success seed (same helper used before turn_completed).
        turn_row = await db.get(Turn, turn.id)
        turn_row.status = TurnStatus.COMPLETED
        await db.flush()
        seeded = await _seed_continuation_memory_after_success(
            db,
            chat_id=handoff_env.chat_b_id,
            turn=turn_row,
        )
        assert seeded is True
        await db.commit()

    async with handoff_env.Session() as db:
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        chat_a = await db.get(Chat, handoff_env.chat_a_id)
        assert chat_b is not None and chat_a is not None
        assert (chat_b.rolling_memory or "").startswith(CONTINUATION_SEED_PREFIX)
        assert "Beirut" in (chat_b.rolling_memory or "")
        assert chat_b.rolling_memory_through_turn_id is None
        assert chat_a.rolling_memory == source_memory
        assert chat_a.rolling_memory_through_turn_id == source_through
        seeded_once = chat_b.rolling_memory

        # Second memory pass must not re-seed / grow from the same handoff.
        again = await chat_memory_service.seed_continuation_memory_if_empty(
            db,
            chat_id=chat_b.id,
            custom_instructions=(await db.get(Turn, turn.id)).custom_instructions,
        )
        assert again is False
        await db.commit()
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        assert chat_b.rolling_memory == seeded_once


@pytest.mark.asyncio
async def test_sync_seed_after_success_before_turn_two_sees_memory(handoff_env):
    """After COMPLETED seed helper returns, Chat B memory is already seeded for Turn 2."""
    async with handoff_env.Session() as db:
        chat_a = await db.get(Chat, handoff_env.chat_a_id)
        chat_a.rolling_memory = "Inherited capital Beirut"
        await _add_completed_turn(db, chat_id=chat_a.id, index=1)
        turn = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="How many people live there?",
                model_set_id="research-set",
                referenced_chat_id=handoff_env.chat_a_id,
            ),
        )
        turn_row = await db.get(Turn, turn.id)
        assert turn_row is not None
        assert not ((await db.get(Chat, handoff_env.chat_b_id)).rolling_memory or "").strip()

        # Simulate successful orchestration status before turn_completed returns.
        turn_row.status = TurnStatus.COMPLETED
        db.add(
            Verdict(
                turn_id=turn_row.id,
                text="About 2.4 million.",
                reason="from handoff",
                model_id="gpt-4.1",
                strategy=Strategy.SYNTHESIZE,
            )
        )
        await db.flush()

        seeded = await _seed_continuation_memory_after_success(
            db, chat_id=handoff_env.chat_b_id, turn=turn_row
        )
        assert seeded is True
        await db.commit()

        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        assert (chat_b.rolling_memory or "").startswith(CONTINUATION_SEED_PREFIX)
        assert "Beirut" in (chat_b.rolling_memory or "")
        assert chat_b.rolling_memory_through_turn_id is None
        seeded_memory = chat_b.rolling_memory

        # Turn 2 gets both normal Chat B memory and a freshly rebuilt Chat A handoff.
        second = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Second turn immediately",
                model_set_id="research-set",
            ),
        )
        await db.commit()
        second_row = await db.get(Turn, second.id)
        assert CONTINUATION_HANDOFF_HEADER in (second_row.custom_instructions or "")
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        assert chat_b.rolling_memory == seeded_memory


@pytest.mark.asyncio
async def test_failed_turn_does_not_seed(handoff_env):
    async with handoff_env.Session() as db:
        chat_a = await db.get(Chat, handoff_env.chat_a_id)
        chat_a.rolling_memory = "Should not seed on failure"
        await _add_completed_turn(db, chat_id=chat_a.id, index=1)
        turn = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Go",
                model_set_id="research-set",
                referenced_chat_id=handoff_env.chat_a_id,
            ),
        )
        turn_row = await db.get(Turn, turn.id)
        turn_row.status = TurnStatus.FAILED
        turn_row.error_message = "boom"
        await db.flush()

        seeded = await _seed_continuation_memory_after_success(
            db, chat_id=handoff_env.chat_b_id, turn=turn_row
        )
        assert seeded is False
        await db.commit()
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        assert not (chat_b.rolling_memory or "").strip()


@pytest.mark.asyncio
async def test_best_effort_helper_does_not_seed(handoff_env, monkeypatch):
    """merge_expired_turns stays background; it must not own continuation seeding."""
    async with handoff_env.Session() as db:
        chat_a = await db.get(Chat, handoff_env.chat_a_id)
        chat_a.rolling_memory = "Must not appear via background helper"
        await _add_completed_turn(db, chat_id=chat_a.id, index=1)
        turn = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Go",
                model_set_id="research-set",
                referenced_chat_id=handoff_env.chat_a_id,
            ),
        )
        await db.commit()
        turn_id = turn.id
        assert CONTINUATION_HANDOFF_HEADER in ((await db.get(Turn, turn_id)).custom_instructions or "")

    monkeypatch.setattr(
        "app.services.chat_service.AsyncSessionLocal",
        handoff_env.Session,
    )
    await _run_chat_memory_update_best_effort(
        chat_id=handoff_env.chat_b_id,
        org_id=handoff_env.org_id,
        project_id=None,
        turn_id=turn_id,
    )

    async with handoff_env.Session() as db:
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        assert not (chat_b.rolling_memory or "").strip()


@pytest.mark.asyncio
async def test_cross_org_reference_not_found(handoff_env):
    async with handoff_env.Session() as db:
        other = await create_other_auth(db)
        other_chat = Chat(org_id=other.org_id, created_by=other.user.id, title="Other org")
        db.add(other_chat)
        await db.commit()
        other_chat_id = other_chat.id

    async with handoff_env.Session() as db:
        with pytest.raises(NotFoundError):
            await chat_service.start_turn(
                db,
                handoff_env.auth,
                handoff_env.chat_b_id,
                TurnCreateRequest(
                    user_message="Hello",
                    model_set_id="research-set",
                    referenced_chat_id=other_chat_id,
                ),
            )


@pytest.mark.asyncio
async def test_cannot_reference_current_chat(handoff_env):
    async with handoff_env.Session() as db:
        with pytest.raises(ValidationError, match="current chat"):
            await chat_service.start_turn(
                db,
                handoff_env.auth,
                handoff_env.chat_b_id,
                TurnCreateRequest(
                    user_message="Hello",
                    model_set_id="research-set",
                    referenced_chat_id=handoff_env.chat_b_id,
                ),
            )


@pytest.mark.asyncio
async def test_nonempty_chat_b_memory_not_overwritten(handoff_env):
    async with handoff_env.Session() as db:
        chat_a = await db.get(Chat, handoff_env.chat_a_id)
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        assert chat_a is not None and chat_b is not None
        chat_a.rolling_memory = "Source memory fact"
        chat_b.rolling_memory = "Existing Chat B memory — keep me"
        await _add_completed_turn(db, chat_id=chat_a.id, index=1)
        await db.commit()

        turn = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Continue please",
                model_set_id="research-set",
                referenced_chat_id=handoff_env.chat_a_id,
            ),
        )
        await db.commit()
        turn_row = await db.get(Turn, turn.id)
        turn_row.status = TurnStatus.COMPLETED
        await db.flush()
        seeded = await _seed_continuation_memory_after_success(
            db,
            chat_id=handoff_env.chat_b_id,
            turn=turn_row,
        )
        assert seeded is False
        await db.commit()

    async with handoff_env.Session() as db:
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        assert chat_b.rolling_memory == "Existing Chat B memory — keep me"


@pytest.mark.asyncio
async def test_turn_two_without_explicit_reference_reloads_active_chat_a(handoff_env):
    async with handoff_env.Session() as db:
        chat_a = await db.get(Chat, handoff_env.chat_a_id)
        chat_a.rolling_memory = "Only in Chat A"
        await _add_completed_turn(db, chat_id=chat_a.id, index=1)
        await db.commit()

        first = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="First",
                model_set_id="research-set",
                referenced_chat_id=handoff_env.chat_a_id,
            ),
        )
        first_row = await db.get(Turn, first.id)
        first_row.status = TurnStatus.COMPLETED
        db.add(
            Verdict(
                turn_id=first_row.id,
                text="First verdict",
                reason="r",
                model_id="gpt-4.1",
                strategy=Strategy.SYNTHESIZE,
            )
        )
        await db.flush()
        await _seed_continuation_memory_after_success(
            db,
            chat_id=handoff_env.chat_b_id,
            turn=first_row,
        )
        await db.commit()

    async with handoff_env.Session() as db:
        second = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Second turn no reference",
                model_set_id="research-set",
            ),
        )
        await db.commit()
        second_row = await db.get(Turn, second.id)
        instructions = second_row.custom_instructions or ""
        assert CONTINUATION_HANDOFF_HEADER in instructions
        assert "Only in Chat A" in instructions


@pytest.mark.asyncio
async def test_regenerate_does_not_reseed_memory(handoff_env, monkeypatch):
    async with handoff_env.Session() as db:
        chat_a = await db.get(Chat, handoff_env.chat_a_id)
        chat_a.rolling_memory = "Inherited capital Beirut"
        await _add_completed_turn(db, chat_id=chat_a.id, index=1)
        await db.commit()

        turn = await chat_service.start_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            TurnCreateRequest(
                user_message="Original",
                model_set_id="research-set",
                referenced_chat_id=handoff_env.chat_a_id,
            ),
        )
        turn_row = await db.get(Turn, turn.id)
        turn_row.status = TurnStatus.COMPLETED
        db.add(
            Verdict(
                turn_id=turn_row.id,
                text="Original verdict",
                reason="r",
                model_id="gpt-4.1",
                strategy=Strategy.SYNTHESIZE,
            )
        )
        await db.flush()
        await _seed_continuation_memory_after_success(
            db,
            chat_id=handoff_env.chat_b_id,
            turn=turn_row,
        )
        await db.commit()

    async with handoff_env.Session() as db:
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        seeded = chat_b.rolling_memory
        assert seeded and seeded.startswith(CONTINUATION_SEED_PREFIX)

        # Avoid cancelling live orchestration tasks in unit test.
        async def _noop_cancel(turn_id, task):  # noqa: ANN001
            return None

        monkeypatch.setattr(
            "app.services.chat_service._cancel_orchestration_task_after_commit",
            _noop_cancel,
        )

        regen = await chat_service.regenerate_turn(
            db,
            handoff_env.auth,
            handoff_env.chat_b_id,
            turn.id,
            TurnRegenerateRequest(prompt="Edited original"),
        )
        await db.commit()
        regen_row = await db.get(Turn, regen.new_turn.id)
        assert CONTINUATION_HANDOFF_HEADER in (regen_row.custom_instructions or "")
        regen_row.status = TurnStatus.COMPLETED
        await db.flush()
        reseeded = await _seed_continuation_memory_after_success(
            db,
            chat_id=handoff_env.chat_b_id,
            turn=regen_row,
        )
        assert reseeded is False
        await db.commit()

    async with handoff_env.Session() as db:
        chat_b = await db.get(Chat, handoff_env.chat_b_id)
        assert chat_b.rolling_memory == seeded
        assert chat_b.rolling_memory_through_turn_id is None
