"""Verdict Simple Explanation: generation, isolation, and API surface."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.core.dependencies import AuthContext
from app.db.base import Base
from app.db.models import (
    BrainKnowledgeItem,
    Chat,
    CostRecord,
    ModelAnswer,
    ModelAnswerStatus,
    OrgMembership,
    OrgRole,
    Organization,
    Strategy,
    Turn,
    TurnStatus,
    UsageKind,
    User,
    Verdict,
    VerdictSimpleExplanation,
    VerdictSimpleExplanationStatus,
)
from app.llm.prompt_engine import get_prompt_engine
from app.services.brain_knowledge_service import SOURCE_VERDICT, brain_knowledge_service
from app.services.chat_memory_service import ChatMemoryService, chat_memory_service
from app.services.chat_service import chat_service
from app.services import verdict_simplifier_service as simplifier_module
from app.services.verdict_simplifier_service import (
    SIMPLIFIER_USER_PROMPT,
    VerdictSimplifierService,
    verdict_simplifier_service,
)

SENTINEL = "SIMPLE_EXPLANATION_SENTINEL_DO_NOT_LEAK"


@pytest.fixture
async def env(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'simplifier.db'}", poolclass=NullPool
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as db:
        org = Organization(name="Org", slug="org-simp")
        user = User(email="s@example.com", hashed_password="x", full_name="User")
        db.add_all([org, user])
        await db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER))
        chat = Chat(org_id=org.id, created_by=user.id, title="Chat")
        db.add(chat)
        await db.flush()
        turn = Turn(
            chat_id=chat.id,
            user_message="Should we pick Option A or B?",
            status=TurnStatus.COMPLETED,
            strategy=Strategy.SYNTHESIZE,
            model_set_id="test-set",
            verdict_model="gemini",
        )
        db.add(turn)
        await db.flush()
        db.add(
            ModelAnswer(
                turn_id=turn.id,
                model_id="gpt-4.1",
                text="Council prefers A",
                status=ModelAnswerStatus.COMPLETED,
            )
        )
        verdict = Verdict(
            turn_id=turn.id,
            model_id="gemini",
            strategy=Strategy.SYNTHESIZE,
            text="The Verdict recommends Option B for isolation.",
            reason="It keeps the feature separate.",
        )
        db.add(verdict)
        await db.commit()
        ids = {
            "org_id": org.id,
            "user_id": user.id,
            "chat_id": chat.id,
            "turn_id": turn.id,
            "verdict_id": verdict.id,
        }
        auth = AuthContext(user=user, org_id=org.id, role=OrgRole.MEMBER)
    try:
        yield Session, ids, auth
    finally:
        await engine.dispose()


def _patch_provider(monkeypatch, script: list[object], calls: list[dict] | None = None):
    class FakeProvider:
        async def complete(self, *, system, user, model, max_tokens=4096, **kwargs):
            if calls is not None:
                calls.append({"system": system, "user": user, "model": model})
            step = script.pop(0) if script else SENTINEL
            if isinstance(step, Exception):
                raise step
            return SimpleNamespace(
                text=step,
                tokens_input=11,
                tokens_output=7,
                cost_usd=0.002,
            )

    monkeypatch.setattr(
        simplifier_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _p: FakeProvider()),
    )
    monkeypatch.setattr(
        simplifier_module,
        "get_model",
        lambda _mid: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1-mini"),
    )
    monkeypatch.setattr(simplifier_module, "resolve_llm_cost", lambda *a, **k: 0.002)


async def _explain(Session, ids):
    async with Session() as db:
        return await verdict_simplifier_service.explain_verdict(
            db,
            verdict_id=ids["verdict_id"],
            org_id=ids["org_id"],
            chat_id=ids["chat_id"],
            turn_id=ids["turn_id"],
        )


@pytest.mark.asyncio
async def test_completed_verdict_generates_explanation(env, monkeypatch):
    Session, ids, _auth = env
    calls: list[dict] = []
    _patch_provider(monkeypatch, [SENTINEL], calls)
    row = await _explain(Session, ids)
    assert row is not None
    assert row.status == VerdictSimpleExplanationStatus.SUCCEEDED
    assert row.content == SENTINEL
    assert calls and SIMPLIFIER_USER_PROMPT in calls[0]["user"]
    assert "Should we pick Option A or B?" not in calls[0]["user"]
    assert "Council prefers A" not in calls[0]["user"]
    assert "The Verdict recommends Option B" in calls[0]["user"]
    async with Session() as db:
        costs = (
            (
                await db.execute(
                    select(CostRecord).where(
                        CostRecord.turn_id == ids["turn_id"],
                        CostRecord.kind == UsageKind.VERDICT_EXPLAIN,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(costs) == 1


@pytest.mark.asyncio
async def test_simplifier_failure_does_not_fail_turn(env, monkeypatch):
    Session, ids, _auth = env
    _patch_provider(monkeypatch, [RuntimeError("provider down")])
    row = await _explain(Session, ids)
    assert row is None or row.status == VerdictSimpleExplanationStatus.FAILED
    async with Session() as db:
        turn = await db.get(Turn, ids["turn_id"])
        verdict = await db.get(Verdict, ids["verdict_id"])
        assert turn.status == TurnStatus.COMPLETED
        assert verdict.text == "The Verdict recommends Option B for isolation."
        expl = (
            await db.execute(
                select(VerdictSimpleExplanation).where(
                    VerdictSimpleExplanation.verdict_id == ids["verdict_id"]
                )
            )
        ).scalar_one_or_none()
        assert expl is None or expl.status == VerdictSimpleExplanationStatus.FAILED
        assert expl is None or not (expl.content or "").strip()


@pytest.mark.asyncio
async def test_get_turn_returns_explanation(env, monkeypatch):
    Session, ids, auth = env
    _patch_provider(monkeypatch, ["Main point:\nUse Option B."])
    await _explain(Session, ids)
    async with Session() as db:
        response = await chat_service.get_turn(db, auth, ids["turn_id"])
    assert response.verdict is not None
    assert response.verdict.text == "The Verdict recommends Option B for isolation."
    assert response.verdict.simple_explanation is not None
    assert response.verdict.simple_explanation.status == "succeeded"
    assert "Use Option B" in (response.verdict.simple_explanation.content or "")


@pytest.mark.asyncio
async def test_get_turn_without_explanation_loads(env):
    Session, ids, auth = env
    async with Session() as db:
        response = await chat_service.get_turn(db, auth, ids["turn_id"])
    assert response.verdict is not None
    assert response.verdict.simple_explanation is None
    assert response.status == TurnStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_idempotent_does_not_regenerate(env, monkeypatch):
    Session, ids, _auth = env
    calls: list[dict] = []
    _patch_provider(monkeypatch, ["first", "second"], calls)
    await _explain(Session, ids)
    await _explain(Session, ids)
    assert len(calls) == 1
    async with Session() as db:
        rows = (
            (
                await db.execute(
                    select(VerdictSimpleExplanation).where(
                        VerdictSimpleExplanation.verdict_id == ids["verdict_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].content == "first"


@pytest.mark.asyncio
async def test_assistant_result_and_recent_context_exclude_explanation(env, monkeypatch):
    Session, ids, _auth = env
    _patch_provider(monkeypatch, [SENTINEL])
    await _explain(Session, ids)
    async with Session() as db:
        turn = (
            await db.execute(
                select(Turn)
                .where(Turn.id == ids["turn_id"])
                .options(
                    selectinload(Turn.verdict).selectinload(Verdict.simple_explanation),
                    selectinload(Turn.model_answers),
                )
            )
        ).scalar_one()
        result = ChatMemoryService._assistant_result(turn)
        assert result is not None
        assert result.text == turn.verdict.text
        assert result.reason == turn.verdict.reason
        assert SENTINEL not in result.text
        assert SENTINEL not in (result.reason or "")

        current = Turn(
            chat_id=ids["chat_id"],
            user_message="Follow-up",
            status=TurnStatus.PENDING,
            strategy=Strategy.SYNTHESIZE,
            model_set_id="test-set",
            verdict_model="gemini",
        )
        db.add(current)
        await db.commit()
        context = await chat_memory_service.build_recent_conversation_context(
            db, ids["chat_id"], current.id, current.created_at
        )
    assert context is not None
    assert "The Verdict recommends Option B" in context
    assert "It keeps the feature separate." in context
    assert SENTINEL not in context


@pytest.mark.asyncio
async def test_brain_ingest_does_not_include_explanation(env, monkeypatch):
    Session, ids, _auth = env
    _patch_provider(monkeypatch, [SENTINEL])
    await _explain(Session, ids)
    async with Session() as db:
        await brain_knowledge_service.ingest_turn(
            db,
            org_id=ids["org_id"],
            user_id=ids["user_id"],
            project_id=None,
            turn_id=ids["turn_id"],
            chat_title="Chat",
            user_message="Should we pick Option A or B?",
            verdict_text="The Verdict recommends Option B for isolation.",
            council_digest="gpt-4.1: Council prefers A",
        )
        await db.commit()
        items = (await db.execute(select(BrainKnowledgeItem))).scalars().all()
        blobs = "\n".join(f"{item.title}\n{item.content}" for item in items)
        assert SENTINEL not in blobs
        verdict_items = [item for item in items if item.source_type == SOURCE_VERDICT]
        assert verdict_items
        assert "Option B" in verdict_items[0].content


def test_chat_service_ingest_uses_canonical_verdict_text_only():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "app" / "services" / "chat_service.py"
    text = path.read_text(encoding="utf-8")
    assert "verdict_text=turn_row.verdict.text if turn_row.verdict else None" in text
    ingest_block = text.split("await brain_knowledge_service.ingest_turn(", 1)[1].split(")", 1)[0]
    assert "simple_explanation" not in ingest_block


@pytest.mark.asyncio
async def test_deleting_verdict_cascades_explanation(env, monkeypatch):
    Session, ids, _auth = env
    _patch_provider(monkeypatch, [SENTINEL])
    await _explain(Session, ids)
    async with Session() as db:
        verdict = await db.get(
            Verdict,
            ids["verdict_id"],
            options=[selectinload(Verdict.simple_explanation)],
        )
        assert verdict.simple_explanation is not None
        await db.delete(verdict)
        await db.commit()
        leftover = (await db.execute(select(VerdictSimpleExplanation))).scalars().all()
        assert leftover == []
        turn = await db.get(Turn, ids["turn_id"])
        assert turn is not None
        assert turn.status == TurnStatus.COMPLETED


def test_simplifier_prompt_is_isolated():
    prompt = get_prompt_engine().verdict_simple_explanation_prompt()
    assert "Verdict Simplifier" in prompt
    assert "Do not disagree" in prompt
    user = VerdictSimplifierService._user_payload("Body of verdict", "Because isolation")
    assert "Body of verdict" in user
    assert "Because isolation" in user
    assert "user question" not in user.lower()
