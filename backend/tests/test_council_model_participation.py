"""Dynamic OpenRouter models participate in every ordinary Council flow."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
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
    ModelSet,
    Organization,
    OrgMembership,
    OrgRole,
    Strategy,
    Turn,
    TurnStatus,
    UsageKind,
    User,
    Verdict,
)
from app.llm.catalog import get_model
from app.llm.orchestrator import TurnContext, TurnOrchestrator
from app.llm.providers import LLMProvider, LLMResponse
from app.schemas.api import PromptBuilderRefineMessage, TurnRegenerateRequest
from app.scraping.blueprint_orchestrator import BlueprintOrchestrator
from app.services.brain_knowledge_service import (
    SOURCE_CHAT_TURN,
    brain_knowledge_service,
    format_council_digest,
)
from app.services.chat_memory_service import ChatMemoryService
from app.services.chat_service import chat_service
from app.services.lesson_service import lesson_service
from app.services.playbook_extraction_service import PlaybookExtractionService
from app.services.playbook_source_service import (
    _is_usable_council_answer,
    playbook_source_service,
)
from app.services.prompt_builder_service import prompt_builder_service
from app.services.saved_document_service import saved_document_service
from app.services.scraping.team_planner_service import TeamPlannerService
from tests.conftest import create_model_set, valid_blueprint

MODEL_SLUGS = (
    "nvidia/nemotron-3-ultra-550b-a55b",
    "qwen/qwen3.8-max-0902",
    "deepseek/deepseek-v4.1-flash",
)
MODEL_IDS = (
    "or:nvidia--nemotron-3-ultra-550b-a55b",
    "or:qwen--qwen3.8-max-0902",
    "or:deepseek--deepseek-v4.1-flash",
)
NEMOTRON = MODEL_IDS[0]
QWEN = MODEL_IDS[1]
DEEPSEEK = MODEL_IDS[2]
NEMOTRON_ANSWER = "NEMOTRON_ANSWER_COUNCIL_ANSWER"
QWEN_ANSWER = "QWEN_ANSWER_ANSWER"
DEEPSEEK_ANSWER = "DEEPSEEK_ANSWER_ANSWER"
GPT_ANSWER = "GPT_ANSWER_ANSWER_UNIQUE"
CLAUDE_ANSWER = "CLAUDE_ANSWER_ANSWER_UNIQUE"
VERDICT_USER = "Produce the verdict JSON now."

ANSWER_BY_SLUG = {
    "openai/gpt-4.1": GPT_ANSWER,
    "anthropic/claude-sonnet-4": CLAUDE_ANSWER,
    "nvidia/nemotron-3-ultra-550b-a55b": NEMOTRON_ANSWER,
    "qwen/qwen3.8-max-0902": QWEN_ANSWER,
    "deepseek/deepseek-v4.1-flash": DEEPSEEK_ANSWER,
}


class ScriptedRegistry:
    def __init__(self, provider: object) -> None:
        self._provider = provider

    def get_provider(self, _provider_name: str) -> object:
        return self._provider


class CouncilProvider:
    def __init__(self, *, fail_slugs: frozenset[str] = frozenset()) -> None:
        self.fail_slugs = fail_slugs
        self.verdict_systems: list[str] = []
        self.answer_slugs: list[str] = []
        self.verdict_calls = 0
        self.answer_calls = 0

    async def complete(
        self, *, system: str, user: str, model: str, max_tokens: int = 4096, **_kwargs
    ):
        if user == VERDICT_USER:
            self.verdict_calls += 1
            self.verdict_systems.append(system)
            return LLMResponse(
                text=json.dumps(
                    {
                        "text": "COUNCIL_VERDICT",
                        "reason": "All Council answers.",
                        "evaluations": [
                            {"model_name": get_model(model_id).name, "score": 80 + i}
                            for i, model_id in enumerate(MODEL_IDS)
                        ],
                    }
                ),
                tokens_input=3,
                tokens_output=4,
                cost_usd=0.01,
            )
        self.answer_calls += 1
        self.answer_slugs.append(model)
        if model in self.fail_slugs:
            raise RuntimeError(f"COUNCIL_PROVIDER_FAIL:{model}")
        text = ANSWER_BY_SLUG.get(model, f"Answer from {model}")
        return LLMResponse(
            text=text,
            tokens_input=11,
            tokens_output=7,
            cost_usd=0.42,
        )

    def parse_json_response(self, text: str):
        return LLMProvider.parse_json_response(text)

    def parse_json_object_lenient(self, text: str):
        return LLMProvider.parse_json_object_lenient(text)


@pytest.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'member.db'}", poolclass=NullPool
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", Session)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as db:
        org = Organization(name="Org", slug="member-org")
        user = User(email="member@example.com", hashed_password="x", full_name="User")
        db.add_all([org, user])
        await db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER))
        chat = Chat(org_id=org.id, created_by=user.id, title="Council chat")
        db.add(chat)
        await db.flush()
        turn = Turn(
            chat_id=chat.id,
            user_message="Should we ship it?",
            status=TurnStatus.PENDING,
            strategy=Strategy.SYNTHESIZE,
            model_set_id="member-set",
            verdict_model="gemini",
        )
        db.add(turn)
        await db.commit()
        ids = (org.id, user.id, chat.id, turn.id)
        auth = AuthContext(user=user, org_id=org.id, role=OrgRole.MEMBER)
    try:
        yield Session, ids, auth
    finally:
        await engine.dispose()


async def _run(Session, ids, provider, model_ids: list[str]):
    org_id, _user_id, chat_id, turn_id = ids
    events: list[tuple[str, dict]] = []
    orchestrator = TurnOrchestrator()
    orchestrator._providers = ScriptedRegistry(provider)

    async def on_event(event, data):
        events.append((event, data))

    async with Session() as db:
        await orchestrator.run(
            db,
            TurnContext(
                turn_id=turn_id,
                chat_id=chat_id,
                org_id=org_id,
                project_id=None,
                user_message="Should we ship it?",
                model_ids=model_ids,
                verdict_model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                model_set_name="Council Mix",
            ),
            on_event=on_event,
        )
        await db.commit()
    async with Session() as db:
        turn = (
            await db.execute(
                select(Turn)
                .where(Turn.id == turn_id)
                .options(selectinload(Turn.model_answers), selectinload(Turn.verdict))
            )
        ).scalar_one()
        answers = list(turn.model_answers)
        costs = (
            (await db.execute(select(CostRecord).where(CostRecord.turn_id == turn_id)))
            .scalars()
            .all()
        )
        return turn, turn.verdict, answers, costs, events


@pytest.mark.asyncio
async def test_mixed_council_persists_all_five_and_scores_members(env):
    Session, ids, _auth = env
    provider = CouncilProvider()
    model_ids = ["gpt-4.1", "claude", *MODEL_IDS]
    turn, verdict, answers, costs, events = await _run(Session, ids, provider, model_ids)

    assert turn.status == TurnStatus.COMPLETED
    assert len(answers) == 5
    assert {a.model_id for a in answers} == set(model_ids)
    assert all(a.status == ModelAnswerStatus.COMPLETED for a in answers)
    assert {a.model_id: a.confidence for a in answers if a.model_id in MODEL_IDS} == dict(
        zip(MODEL_IDS, [80, 81, 82])
    )
    assert verdict is not None
    assert verdict.text == "COUNCIL_VERDICT"
    assert provider.verdict_calls == 1
    prompt = provider.verdict_systems[0]
    assert GPT_ANSWER in prompt
    assert CLAUDE_ANSWER in prompt
    assert NEMOTRON_ANSWER in prompt
    assert prompt.index(GPT_ANSWER) < prompt.index(CLAUDE_ANSWER)
    assert QWEN_ANSWER in prompt
    assert DEEPSEEK_ANSWER in prompt

    event_names = [name for name, _ in events]
    assert event_names.count("model_answer_started") == 5
    assert event_names.count("model_answer_completed") == 5
    for member_id in MODEL_IDS:
        assert any(
            n == "model_answer_started" and d.get("model_id") == member_id for n, d in events
        )
        assert any(
            n == "model_answer_completed" and d.get("model_id") == member_id for n, d in events
        )

    assert {a.model_id for a in answers} == set(model_ids)

    answer_costs = [c for c in costs if c.kind == UsageKind.ANSWER]
    assert len(answer_costs) == 5
    member_costs = [c for c in answer_costs if c.model_id in MODEL_IDS]
    assert len(member_costs) == 3
    assert all(c.tokens_input == 11 and c.tokens_output == 7 for c in member_costs)
    assert all(c.cost_usd == 0.42 for c in member_costs)


@pytest.mark.asyncio
async def test_member_failure_marks_turn_partial_and_omits_failed_answer(env):
    Session, ids, _auth = env
    provider = CouncilProvider(fail_slugs=frozenset({"nvidia/nemotron-3-ultra-550b-a55b"}))
    model_ids = ["gpt-4.1", "claude", *MODEL_IDS]
    turn, verdict, answers, _costs, events = await _run(Session, ids, provider, model_ids)

    assert turn.status == TurnStatus.PARTIAL
    assert verdict is not None
    by_id = {a.model_id: a for a in answers}
    assert by_id[NEMOTRON].status == ModelAnswerStatus.FAILED
    assert by_id["gpt-4.1"].status == ModelAnswerStatus.COMPLETED
    prompt = provider.verdict_systems[0]
    assert NEMOTRON_ANSWER not in prompt
    assert "COUNCIL_PROVIDER_FAIL" not in prompt
    assert GPT_ANSWER in prompt
    assert QWEN_ANSWER in prompt
    assert DEEPSEEK_ANSWER in prompt
    assert any(n == "model_answer_failed" and d.get("model_id") == NEMOTRON for n, d in events)


@pytest.mark.asyncio
async def test_other_model_failures_still_use_successful_members(env):
    Session, ids, _auth = env
    provider = CouncilProvider(
        fail_slugs=frozenset({"openai/gpt-4.1", "anthropic/claude-sonnet-4"})
    )
    model_ids = ["gpt-4.1", "claude", *MODEL_IDS]
    turn, verdict, answers, costs, _events = await _run(Session, ids, provider, model_ids)

    assert turn.status == TurnStatus.PARTIAL
    assert verdict is not None
    assert provider.verdict_calls == 1
    by_id = {a.model_id: a for a in answers}
    assert by_id[NEMOTRON].status == ModelAnswerStatus.COMPLETED
    assert by_id[NEMOTRON].text == NEMOTRON_ANSWER
    member_costs = [c for c in costs if c.kind == UsageKind.ANSWER and c.model_id in MODEL_IDS]
    assert len(member_costs) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_ids", [[m] for m in MODEL_IDS] + [list(MODEL_IDS[:2]), list(MODEL_IDS)]
)
async def test_model_membership_controls_verdict(env, model_ids):
    Session, ids, _auth = env
    provider = CouncilProvider()
    turn, verdict, answers, costs, events = await _run(Session, ids, provider, model_ids)
    multi = len(model_ids) > 1
    assert turn.status == TurnStatus.COMPLETED
    assert (verdict is not None) == multi
    assert provider.verdict_calls == int(multi)
    assert ("verdict_started" in [n for n, _ in events]) == multi
    assert len(answers) == len(model_ids)
    assert all(a.status == ModelAnswerStatus.COMPLETED for a in answers)
    assert len([c for c in costs if c.kind == UsageKind.ANSWER]) == len(model_ids)
    if multi:
        assert all(
            ANSWER_BY_SLUG[get_model(m).provider_model] in provider.verdict_systems[0]
            for m in model_ids
        )


def test_format_council_digest_includes_member_text():
    answers = [
        SimpleNamespace(model_id="gpt-4.1", text=GPT_ANSWER),
        SimpleNamespace(model_id=NEMOTRON, text=NEMOTRON_ANSWER),
        SimpleNamespace(model_id=QWEN, text=QWEN_ANSWER),
    ]
    digest = format_council_digest(answers)
    assert digest is not None
    assert GPT_ANSWER in digest
    assert NEMOTRON_ANSWER in digest
    assert QWEN_ANSWER in digest


@pytest.mark.asyncio
async def test_brain_ingest_turn_includes_member_council_digest(
    db: AsyncSession, auth: AuthContext
):
    digest = format_council_digest(
        [
            SimpleNamespace(model_id="gpt-4.1", text=GPT_ANSWER),
            SimpleNamespace(model_id=NEMOTRON, text=NEMOTRON_ANSWER),
        ]
    )
    await brain_knowledge_service.ingest_turn(
        db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        project_id=None,
        turn_id="turn-member-brain",
        chat_title="Chat",
        user_message="Question",
        verdict_text="COUNCIL_VERDICT",
        council_digest=digest,
    )
    rows = (
        (
            await db.execute(
                select(BrainKnowledgeItem).where(
                    BrainKnowledgeItem.source_type == SOURCE_CHAT_TURN,
                    BrainKnowledgeItem.source_id == "turn-member-brain",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert GPT_ANSWER in rows[0].content
    assert NEMOTRON_ANSWER in rows[0].content


def test_saved_document_brain_snapshot_includes_member_excerpts():
    snapshot = {
        "user_message": "Question",
        "verdict": {"text": "COUNCIL_VERDICT"},
        "council_answers": [
            {"model_id": "gpt-4.1", "text": GPT_ANSWER, "status": "completed"},
            {"model_id": NEMOTRON, "text": NEMOTRON_ANSWER, "status": "completed"},
        ],
    }
    text = saved_document_service._snapshot_text(snapshot)
    assert GPT_ANSWER in text
    assert "COUNCIL_VERDICT" in text
    assert NEMOTRON_ANSWER in text


def test_assistant_result_uses_single_member_text():
    service = ChatMemoryService()
    member_only = SimpleNamespace(
        status=TurnStatus.COMPLETED,
        verdict=None,
        model_answers=[
            SimpleNamespace(
                model_id=NEMOTRON,
                text=NEMOTRON_ANSWER,
                status=ModelAnswerStatus.COMPLETED,
            )
        ],
    )
    result = service._assistant_result(member_only)
    assert result.text == NEMOTRON_ANSWER
    assert result.is_verdict is False

    builtin_single = SimpleNamespace(
        status=TurnStatus.COMPLETED,
        verdict=None,
        model_answers=[
            SimpleNamespace(
                model_id="gpt-4.1",
                text=GPT_ANSWER,
                status=ModelAnswerStatus.COMPLETED,
            )
        ],
    )
    result = service._assistant_result(builtin_single)
    assert result is not None
    assert result.text == GPT_ANSWER
    assert result.is_verdict is False

    mixed = SimpleNamespace(
        status=TurnStatus.COMPLETED,
        verdict=SimpleNamespace(text="COUNCIL_VERDICT", reason="ok"),
        model_answers=[
            SimpleNamespace(
                model_id="gpt-4.1",
                text=GPT_ANSWER,
                status=ModelAnswerStatus.COMPLETED,
            ),
            SimpleNamespace(
                model_id=NEMOTRON,
                text=NEMOTRON_ANSWER,
                status=ModelAnswerStatus.COMPLETED,
            ),
        ],
    )
    mixed_result = service._assistant_result(mixed)
    assert mixed_result is not None
    assert mixed_result.text == "COUNCIL_VERDICT"
    assert mixed_result.is_verdict is True


def test_playbook_usable_council_answer_accepts_members():
    member = SimpleNamespace(
        model_id=NEMOTRON,
        text=NEMOTRON_ANSWER,
        status=ModelAnswerStatus.COMPLETED,
    )
    builtin = SimpleNamespace(
        model_id="gpt-4.1",
        text=GPT_ANSWER,
        status=ModelAnswerStatus.COMPLETED,
    )
    assert _is_usable_council_answer(member) is True
    assert _is_usable_council_answer(builtin) is True


@pytest.mark.asyncio
async def test_playbook_reconstruction_and_extraction_include_member_answers(
    db: AsyncSession, auth: AuthContext
):
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Playbook chat")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="Question",
        model_set_id="research-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.COMPLETED,
    )
    db.add(turn)
    await db.flush()
    db.add_all(
        [
            ModelAnswer(
                turn_id=turn.id,
                model_id="gpt-4.1",
                text=GPT_ANSWER,
                status=ModelAnswerStatus.COMPLETED,
            ),
            ModelAnswer(
                turn_id=turn.id,
                model_id=NEMOTRON,
                text=NEMOTRON_ANSWER,
                status=ModelAnswerStatus.COMPLETED,
            ),
        ]
    )
    db.add(
        Verdict(
            turn_id=turn.id,
            model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            text="COUNCIL_VERDICT",
            reason="ok",
        )
    )
    await db.flush()

    assembled = await playbook_source_service.assemble_all_transcripts(db, auth)
    assert len(assembled.chats) == 1
    council = assembled.chats[0].turns[0].council_answers
    assert {answer.model_id for answer in council} == {"gpt-4.1", NEMOTRON}
    rendered = PlaybookExtractionService()._render_turn(chat.id, assembled.chats[0].turns[0])
    assert GPT_ANSWER in rendered
    assert NEMOTRON_ANSWER in rendered
    assert f"model_id={NEMOTRON}" in rendered


@pytest.mark.asyncio
async def test_lesson_context_includes_member_answers(db: AsyncSession, auth: AuthContext):
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Lesson chat")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="Question",
        model_set_id="research-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.COMPLETED,
    )
    db.add(turn)
    await db.flush()
    db.add_all(
        [
            ModelAnswer(
                turn_id=turn.id,
                model_id="claude",
                text=CLAUDE_ANSWER,
                status=ModelAnswerStatus.COMPLETED,
            ),
            ModelAnswer(
                turn_id=turn.id,
                model_id=QWEN,
                text=QWEN_ANSWER,
                status=ModelAnswerStatus.COMPLETED,
            ),
        ]
    )
    db.add(
        Verdict(
            turn_id=turn.id,
            model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            text="COUNCIL_VERDICT",
            reason="ok",
        )
    )
    await db.flush()

    _turn, _chat, _verdict_model, answer_context = await lesson_service._load_turn_context(
        db, auth, turn.id
    )
    assert {row["model_id"] for row in answer_context} == {"claude", QWEN}
    joined = "\n".join(row["text"] for row in answer_context)
    assert QWEN_ANSWER in joined


@pytest.mark.asyncio
@pytest.mark.parametrize("model_ids", [["gpt-4.1", *MODEL_IDS], list(MODEL_IDS)])
async def test_prompt_builder_calls_all_council_members(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch, model_ids
):
    model_set = await create_model_set(
        db,
        auth,
        slug="member-builder",
        models=model_ids,
    )
    calls: list[str] = []

    class Provider:
        async def complete(self, **kwargs):
            calls.append(kwargs["model"])
            return LLMResponse(text="improved prompt", tokens_input=2, tokens_output=2)

    registry = SimpleNamespace(
        get_provider=lambda _name: Provider(),
        validate_configured=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.prompt_builder_service.get_provider_registry",
        lambda: registry,
    )
    pricing = SimpleNamespace(
        ensure_loaded=AsyncMock(return_value=None),
        get_slug_metadata=lambda _slug: {
            "context_length": 200_000,
            "top_provider": {"max_completion_tokens": 4000},
        },
    )
    monkeypatch.setattr(
        "app.services.prompt_builder_service.get_pricing_service",
        lambda: pricing,
    )

    result = await prompt_builder_service.refine(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="Improve this.")],
        model_set_id=model_set.slug,
    )
    assert result.improved_prompt == "improved prompt"
    assert set(MODEL_SLUGS).issubset(calls)
    assert len(calls) == len(model_ids) + 1


@pytest.mark.asyncio
async def test_blueprint_orchestrator_calls_all_models(monkeypatch: pytest.MonkeyPatch):
    called_models: list[str] = []

    class Provider:
        async def complete(self, **kwargs):
            called_models.append(kwargs["model"])
            user = kwargs.get("user") or ""
            if "Return only the final JSON" in user:
                return LLMResponse(
                    text=json.dumps(valid_blueprint()),
                    tokens_input=1,
                    tokens_output=1,
                )
            return LLMResponse(text="analysis", tokens_input=1, tokens_output=1)

    orchestrator = BlueprintOrchestrator()
    orchestrator._providers = ScriptedRegistry(Provider())
    mission = SimpleNamespace(title="Mission", original_prompt="Find facilities")
    model_set = SimpleNamespace(
        models=[*MODEL_IDS, "gpt-4.1"],
        verdict_model="gpt-4.1",
    )
    await orchestrator.generate(mission, model_set)
    assert set(MODEL_SLUGS).issubset(called_models)
    assert len(called_models) == 6


@pytest.mark.asyncio
async def test_regenerate_reseeds_member_model_answers(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'member-regen.db'}", poolclass=NullPool
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", Session)
    monkeypatch.setattr("app.services.chat_service.AsyncSessionLocal", Session)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as db:
        org = Organization(name="Org", slug="member-regen")
        user = User(email="regen-member@example.com", hashed_password="x", full_name="User")
        db.add_all([org, user])
        await db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER))
        model_set = ModelSet(
            org_id=org.id,
            slug="member-regen-set",
            name="Council Regen",
            description="",
            models=["gpt-4.1", "claude", *MODEL_IDS],
            verdict_model="gemini",
            strategy=Strategy.SYNTHESIZE,
            best_for="",
            is_system=False,
        )
        chat = Chat(org_id=org.id, created_by=user.id, title="Regen", model_set_id=model_set.slug)
        db.add_all([model_set, chat])
        await db.flush()
        turn = Turn(
            chat_id=chat.id,
            user_message="Original",
            model_set_id=model_set.slug,
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.COMPLETED,
        )
        db.add(turn)
        await db.flush()
        for model_id in model_set.models:
            db.add(
                ModelAnswer(
                    turn_id=turn.id,
                    model_id=model_id,
                    text="old",
                    status=ModelAnswerStatus.COMPLETED,
                )
            )
        db.add(
            Verdict(
                turn_id=turn.id,
                model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                text="old verdict",
                reason="ok",
            )
        )
        await db.commit()
        auth = AuthContext(user=user, org_id=org.id, role=OrgRole.MEMBER)
        result = await chat_service.regenerate_turn(
            db,
            auth,
            chat.id,
            turn.id,
            TurnRegenerateRequest(prompt="Edited"),
        )
    assert {a.model_id for a in result.new_turn.model_answers} == {
        "gpt-4.1",
        "claude",
        *MODEL_IDS,
    }
    provider = CouncilProvider()
    orchestrator = TurnOrchestrator()
    orchestrator._providers = ScriptedRegistry(provider)
    async with Session() as db:
        regenerated = await db.get(Turn, result.new_turn.id)
        regenerated.status = TurnStatus.RUNNING
        await db.commit()
        await orchestrator.run(
            db,
            TurnContext(
                turn_id=result.new_turn.id,
                chat_id=chat.id,
                org_id=org.id,
                project_id=None,
                user_message="Edited",
                model_ids=list(model_set.models),
                verdict_model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                model_set_name=model_set.name,
                skip_answer_seed=True,
            ),
        )
    async with Session() as db:
        answers = (
            (await db.execute(select(ModelAnswer).where(ModelAnswer.turn_id == result.new_turn.id)))
            .scalars()
            .all()
        )
        assert len(answers) == 5
        assert all(a.status == ModelAnswerStatus.COMPLETED and a.text != "old" for a in answers)
    assert set(MODEL_SLUGS).issubset(provider.answer_slugs)
    assert all(ANSWER_BY_SLUG[slug] in provider.verdict_systems[0] for slug in MODEL_SLUGS)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_slug", ["openai/gpt-4.1", *MODEL_SLUGS])
async def test_each_member_failure_has_same_partial_status(env, fail_slug):
    Session, ids, _auth = env
    provider = CouncilProvider(fail_slugs=frozenset({fail_slug}))
    turn, verdict, answers, _, _ = await _run(Session, ids, provider, ["gpt-4.1", *MODEL_IDS])
    assert turn.status == TurnStatus.PARTIAL
    assert verdict is not None
    assert sum(a.status == ModelAnswerStatus.FAILED for a in answers) == 1
    assert ANSWER_BY_SLUG[fail_slug] not in provider.verdict_systems[0]


@pytest.mark.asyncio
async def test_all_members_fail_without_verdict(env):
    Session, ids, _auth = env
    provider = CouncilProvider(fail_slugs=frozenset(MODEL_SLUGS))
    turn, verdict, answers, costs, _ = await _run(Session, ids, provider, list(MODEL_IDS))
    assert turn.status == TurnStatus.FAILED
    assert verdict is None
    assert all(a.status == ModelAnswerStatus.FAILED for a in answers)
    assert not costs


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_delayed", [False, True])
async def test_verdict_waits_for_every_member(env, fail_delayed):
    Session, ids, _auth = env

    class GatedProvider(CouncilProvider):
        def __init__(self):
            super().__init__(
                fail_slugs=frozenset({MODEL_SLUGS[2]}) if fail_delayed else frozenset()
            )
            self.started = set()
            self.all_started = asyncio.Event()
            self.release = asyncio.Event()

        async def complete(self, **kwargs):
            if kwargs["user"] == VERDICT_USER:
                assert self.release.is_set()
            else:
                self.started.add(kwargs["model"])
                if self.started == set(MODEL_SLUGS):
                    self.all_started.set()
                if kwargs["model"] == MODEL_SLUGS[2]:
                    await self.release.wait()
            return await super().complete(**kwargs)

    provider = GatedProvider()
    task = asyncio.create_task(_run(Session, ids, provider, list(MODEL_IDS)))
    try:
        await asyncio.wait_for(provider.all_started.wait(), 5)
        assert provider.verdict_calls == 0
        assert not task.done()
    finally:
        provider.release.set()
        turn, verdict, _answers, costs, events = await asyncio.wait_for(task, 5)
    assert turn.status == (TurnStatus.PARTIAL if fail_delayed else TurnStatus.COMPLETED)
    assert verdict is not None
    names = [name for name, _ in events]
    answer_indices = [
        i
        for i, name in enumerate(names)
        if name in {"model_answer_completed", "model_answer_failed"}
    ]
    assert len(answer_indices) == 3
    assert max(answer_indices) < names.index("verdict_started") < names.index("turn_completed")
    assert len([c for c in costs if c.kind == UsageKind.ANSWER]) == (2 if fail_delayed else 3)


@pytest.mark.asyncio
async def test_challenge_council_calls_all_members(env, monkeypatch):
    Session, ids, auth = env
    provider = CouncilProvider()
    orchestrator = TurnOrchestrator()
    orchestrator._providers = ScriptedRegistry(provider)
    monkeypatch.setattr("app.services.lesson_service.get_orchestrator", lambda: orchestrator)
    async with Session() as db:
        await create_model_set(db, auth, slug="member-set", models=list(MODEL_IDS))
        turn = await db.get(Turn, ids[3])
        turn.verdict = Verdict(
            turn_id=turn.id,
            model_id="gemini",
            strategy=turn.strategy,
            text="Original verdict",
            reason="ok",
        )
        await db.flush()
        chat = await db.get(Chat, ids[2])
        replies = await lesson_service._council_challenge_reply(
            db=db,
            auth=auth,
            turn=turn,
            chat=chat,
            answer_context=[],
            messages=[],
            challenge="Reconsider the evidence.",
        )
    council = [r for r in replies if r["kind"] == "challenge_model_answer"]
    assert {r["model_id"] for r in council} == set(MODEL_IDS)
    assert {r["content"] for r in council} == {ANSWER_BY_SLUG[s] for s in MODEL_SLUGS}
    assert set(provider.answer_slugs) == set(MODEL_SLUGS)


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict_model", ["gpt-4.1", None])
async def test_team_planner_allows_all_members(monkeypatch, verdict_model):
    payload = {
        "recommended_agent_count": 3,
        "rationale": "Discovery, extraction, verification.",
        "agents": [
            {
                "sequence": i + 1,
                "name": f"Agent {i}",
                "role": "verification",
                "purpose": "Verify sources",
                "instructions": "Check evidence and deduplicate.",
                "model_id": model_id,
                "assigned_scope": {},
                "depends_on": [],
            }
            for i, model_id in enumerate(MODEL_IDS)
        ],
    }
    calls = []

    class Provider:
        async def complete(self, **kwargs):
            calls.append(kwargs)
            return LLMResponse(text=json.dumps(payload), tokens_input=1, tokens_output=1)

    monkeypatch.setattr(
        "app.services.scraping.team_planner_service.get_provider_registry",
        lambda: ScriptedRegistry(Provider()),
    )
    model_set = SimpleNamespace(
        models=list(MODEL_IDS), verdict_model=verdict_model, name="Council", slug="council"
    )
    plan, planner = await TeamPlannerService().plan_team(
        SimpleNamespace(title="Mission", original_prompt="Find facilities"),
        SimpleNamespace(version=1, blueprint_json=valid_blueprint()),
        model_set,
    )
    assert planner == (verdict_model or NEMOTRON)
    assert {a.model_id for a in plan.agents} == set(MODEL_IDS)
    assert len(calls) == 1
    assert all(model_id in calls[0]["user"] for model_id in MODEL_IDS)
    assert calls[0]["model"] == get_model(planner).provider_model


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", MODEL_IDS)
async def test_single_member_answer_enters_rolling_memory(env, monkeypatch, model_id):
    Session, ids, _auth = env
    await _run(Session, ids, CouncilProvider(), [model_id])
    text = ANSWER_BY_SLUG[get_model(model_id).provider_model]

    class MemoryProvider:
        async def complete(self, **kwargs):
            assert text in kwargs["system"]
            return LLMResponse(text="Remembered: " + text, tokens_input=1, tokens_output=1)

    monkeypatch.setattr(
        "app.services.chat_memory_service.get_provider_registry",
        lambda: ScriptedRegistry(MemoryProvider()),
    )
    service = ChatMemoryService()
    async with Session() as db:
        eligible = await service.list_eligible_turns_oldest_first(db, ids[2])
        assert len(eligible) == 1
        turn, assistant = eligible[0]
        assert assistant.text == text and not assistant.is_verdict
        chat = await db.get(Chat, ids[2])
        assert (
            await service._merge_one_turn(
                db, chat=chat, turn=turn, assistant_result=assistant, org_id=ids[0], project_id=None
            )
            is True
        )
        await db.commit()
    async with Session() as db:
        chat = await db.get(Chat, ids[2])
        assert text in chat.rolling_memory
        assert chat.rolling_memory_through_turn_id == ids[3]
