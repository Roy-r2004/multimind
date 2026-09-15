"""Shadow/test Council models: display + cost yes, intelligence no."""

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
from app.core.exceptions import ValidationError
from app.db.base import Base
from app.db.models import (
    BrainKnowledgeItem,
    Chat,
    CostRecord,
    ModelAnswer,
    ModelAnswerStatus,
    ModelSet,
    OrgMembership,
    OrgRole,
    Organization,
    Strategy,
    Turn,
    TurnStatus,
    UsageKind,
    User,
    Verdict,
)
from app.llm.catalog import (
    is_intelligence_eligible_model,
    is_shadow_model,
)
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

SHADOW_SLUGS = (
    "nvidia/nemotron-3-ultra-550b-a55b",
    "qwen/qwen3.8-max-0902",
    "deepseek/deepseek-v4.1-flash",
)
SHADOW_OR_IDS = (
    "or:nvidia--nemotron-3-ultra-550b-a55b",
    "or:qwen--qwen3.8-max-0902",
    "or:deepseek--deepseek-v4.1-flash",
)
NEMOTRON = SHADOW_OR_IDS[0]
QWEN_SHADOW = SHADOW_OR_IDS[1]
DEEPSEEK_SHADOW = SHADOW_OR_IDS[2]
SECRET_NEMOTRON = "SECRET_NEMOTRON_SHADOW_ANSWER"
SECRET_QWEN = "SECRET_QWEN_SHADOW_ANSWER"
SECRET_DEEPSEEK = "SECRET_DEEPSEEK_SHADOW_ANSWER"
PROD_GPT = "PROD_GPT_ANSWER_UNIQUE"
PROD_CLAUDE = "PROD_CLAUDE_ANSWER_UNIQUE"
VERDICT_USER = "Produce the verdict JSON now."

ANSWER_BY_SLUG = {
    "openai/gpt-4.1": PROD_GPT,
    "anthropic/claude-sonnet-4": PROD_CLAUDE,
    "nvidia/nemotron-3-ultra-550b-a55b": SECRET_NEMOTRON,
    "qwen/qwen3.8-max-0902": SECRET_QWEN,
    "deepseek/deepseek-v4.1-flash": SECRET_DEEPSEEK,
}


class ScriptedRegistry:
    def __init__(self, provider: object) -> None:
        self._provider = provider

    def get_provider(self, _provider_name: str) -> object:
        return self._provider


class ShadowCouncilProvider:
    def __init__(self, *, fail_slugs: frozenset[str] = frozenset()) -> None:
        self.fail_slugs = fail_slugs
        self.verdict_systems: list[str] = []
        self.answer_slugs: list[str] = []
        self.verdict_calls = 0
        self.answer_calls = 0

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096, **_kwargs):
        if user == VERDICT_USER:
            self.verdict_calls += 1
            self.verdict_systems.append(system)
            return LLMResponse(
                text='{"text":"FILTERED_PRODUCTION_VERDICT","reason":"Production only."}',
                tokens_input=3,
                tokens_output=4,
                cost_usd=0.01,
            )
        self.answer_calls += 1
        self.answer_slugs.append(model)
        if model in self.fail_slugs:
            raise RuntimeError(f"SHADOW_PROVIDER_FAIL:{model}")
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
async def env(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'shadow.db'}", poolclass=NullPool
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as db:
        org = Organization(name="Org", slug="shadow-org")
        user = User(email="shadow@example.com", hashed_password="x", full_name="User")
        db.add_all([org, user])
        await db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER))
        chat = Chat(org_id=org.id, created_by=user.id, title="Shadow chat")
        db.add(chat)
        await db.flush()
        turn = Turn(
            chat_id=chat.id,
            user_message="Should we ship it?",
            status=TurnStatus.PENDING,
            strategy=Strategy.SYNTHESIZE,
            model_set_id="shadow-set",
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
                model_set_name="Shadow Mix",
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
            await db.execute(select(CostRecord).where(CostRecord.turn_id == turn_id))
        ).scalars().all()
        return turn, turn.verdict, answers, costs, events


def _assert_no_shadow_secrets(text: str) -> None:
    assert SECRET_NEMOTRON not in text
    assert SECRET_QWEN not in text
    assert SECRET_DEEPSEEK not in text
    assert "SHADOW_PROVIDER_FAIL" not in text
    assert "nemotron-3-ultra-550b-a55b" not in text.lower()
    assert "qwen3.8-max-0902" not in text.lower()
    assert "deepseek-v4.1-flash" not in text.lower()


def test_is_shadow_model_recognizes_exact_slugs_and_or_ids():
    for slug, or_id in zip(SHADOW_SLUGS, SHADOW_OR_IDS, strict=True):
        assert is_shadow_model(slug) is True
        assert is_shadow_model(or_id) is True
        assert is_intelligence_eligible_model(slug) is False
        assert is_intelligence_eligible_model(or_id) is False


def test_is_shadow_model_rejects_production_and_builtin_cousins():
    assert is_shadow_model("gpt-4.1") is False
    assert is_shadow_model("deepseek") is False
    assert is_shadow_model("qwen") is False
    assert is_shadow_model("or:deepseek--deepseek-chat-v3-0324") is False
    assert is_shadow_model("qwen/qwen-2.5-72b-instruct") is False
    assert is_intelligence_eligible_model("gpt-4.1") is True
    assert is_intelligence_eligible_model("deepseek") is True
    assert is_intelligence_eligible_model("qwen") is True


@pytest.mark.asyncio
async def test_mixed_council_persists_all_five_and_filters_verdict(env):
    Session, ids, _auth = env
    provider = ShadowCouncilProvider()
    model_ids = ["gpt-4.1", "claude", *SHADOW_OR_IDS]
    turn, verdict, answers, costs, events = await _run(Session, ids, provider, model_ids)

    assert turn.status == TurnStatus.COMPLETED
    assert len(answers) == 5
    assert {a.model_id for a in answers} == set(model_ids)
    assert all(a.status == ModelAnswerStatus.COMPLETED for a in answers)
    assert verdict is not None
    assert verdict.text == "FILTERED_PRODUCTION_VERDICT"
    assert provider.verdict_calls == 1
    prompt = provider.verdict_systems[0]
    assert PROD_GPT in prompt
    assert PROD_CLAUDE in prompt
    assert prompt.index(PROD_GPT) < prompt.index(PROD_CLAUDE)
    _assert_no_shadow_secrets(prompt)

    event_names = [name for name, _ in events]
    assert event_names.count("model_answer_started") == 5
    assert event_names.count("model_answer_completed") == 5
    for shadow_id in SHADOW_OR_IDS:
        assert any(
            n == "model_answer_started" and d.get("model_id") == shadow_id for n, d in events
        )
        assert any(
            n == "model_answer_completed" and d.get("model_id") == shadow_id for n, d in events
        )

    assert {a.model_id for a in answers} == set(model_ids)

    answer_costs = [c for c in costs if c.kind == UsageKind.ANSWER]
    assert len(answer_costs) == 5
    shadow_costs = [c for c in answer_costs if c.model_id in SHADOW_OR_IDS]
    assert len(shadow_costs) == 3
    assert all(c.tokens_input == 11 and c.tokens_output == 7 for c in shadow_costs)
    assert all(c.cost_usd == 0.42 for c in shadow_costs)


@pytest.mark.asyncio
async def test_shadow_failure_does_not_partial_or_enter_verdict(env):
    Session, ids, _auth = env
    provider = ShadowCouncilProvider(fail_slugs=frozenset({"nvidia/nemotron-3-ultra-550b-a55b"}))
    model_ids = ["gpt-4.1", "claude", *SHADOW_OR_IDS]
    turn, verdict, answers, _costs, events = await _run(Session, ids, provider, model_ids)

    assert turn.status == TurnStatus.COMPLETED
    assert verdict is not None
    by_id = {a.model_id: a for a in answers}
    assert by_id[NEMOTRON].status == ModelAnswerStatus.FAILED
    assert by_id["gpt-4.1"].status == ModelAnswerStatus.COMPLETED
    prompt = provider.verdict_systems[0]
    assert PROD_GPT in prompt
    _assert_no_shadow_secrets(prompt)
    assert any(n == "model_answer_failed" and d.get("model_id") == NEMOTRON for n, d in events)


@pytest.mark.asyncio
async def test_production_failure_is_fail_closed_even_when_shadows_succeed(env):
    Session, ids, _auth = env
    provider = ShadowCouncilProvider(
        fail_slugs=frozenset({"openai/gpt-4.1", "anthropic/claude-sonnet-4"})
    )
    model_ids = ["gpt-4.1", "claude", *SHADOW_OR_IDS]
    turn, verdict, answers, costs, _events = await _run(Session, ids, provider, model_ids)

    assert turn.status == TurnStatus.FAILED
    assert verdict is None
    assert provider.verdict_calls == 0
    by_id = {a.model_id: a for a in answers}
    assert by_id[NEMOTRON].status == ModelAnswerStatus.COMPLETED
    assert by_id[NEMOTRON].text == SECRET_NEMOTRON
    shadow_costs = [
        c for c in costs if c.kind == UsageKind.ANSWER and c.model_id in SHADOW_OR_IDS
    ]
    assert len(shadow_costs) == 3


@pytest.mark.asyncio
async def test_shadow_only_turn_has_no_verdict(env):
    Session, ids, _auth = env
    provider = ShadowCouncilProvider()
    turn, verdict, answers, costs, events = await _run(
        Session, ids, provider, list(SHADOW_OR_IDS)
    )

    assert turn.status == TurnStatus.COMPLETED
    assert verdict is None
    assert provider.verdict_calls == 0
    assert "verdict_started" not in [n for n, _ in events]
    assert len(answers) == 3
    assert all(a.status == ModelAnswerStatus.COMPLETED for a in answers)
    assert len([c for c in costs if c.kind == UsageKind.ANSWER]) == 3


def test_format_council_digest_excludes_shadow_text():
    answers = [
        SimpleNamespace(model_id="gpt-4.1", text=PROD_GPT),
        SimpleNamespace(model_id=NEMOTRON, text=SECRET_NEMOTRON),
        SimpleNamespace(model_id=QWEN_SHADOW, text=SECRET_QWEN),
    ]
    digest = format_council_digest(answers)
    assert digest is not None
    assert PROD_GPT in digest
    _assert_no_shadow_secrets(digest)


@pytest.mark.asyncio
async def test_brain_ingest_turn_omits_shadow_council_digest(db: AsyncSession, auth: AuthContext):
    digest = format_council_digest(
        [
            SimpleNamespace(model_id="gpt-4.1", text=PROD_GPT),
            SimpleNamespace(model_id=NEMOTRON, text=SECRET_NEMOTRON),
        ]
    )
    await brain_knowledge_service.ingest_turn(
        db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        project_id=None,
        turn_id="turn-shadow-brain",
        chat_title="Chat",
        user_message="Question",
        verdict_text="FILTERED_PRODUCTION_VERDICT",
        council_digest=digest,
    )
    rows = (
        await db.execute(
            select(BrainKnowledgeItem).where(
                BrainKnowledgeItem.source_type == SOURCE_CHAT_TURN,
                BrainKnowledgeItem.source_id == "turn-shadow-brain",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert PROD_GPT in rows[0].content
    _assert_no_shadow_secrets(rows[0].content)


def test_saved_document_brain_snapshot_excludes_shadow_excerpts():
    snapshot = {
        "user_message": "Question",
        "verdict": {"text": "FILTERED_PRODUCTION_VERDICT"},
        "council_answers": [
            {"model_id": "gpt-4.1", "text": PROD_GPT, "status": "completed"},
            {"model_id": NEMOTRON, "text": SECRET_NEMOTRON, "status": "completed"},
        ],
    }
    text = saved_document_service._snapshot_text(snapshot)
    assert PROD_GPT in text
    assert "FILTERED_PRODUCTION_VERDICT" in text
    _assert_no_shadow_secrets(text)


def test_assistant_result_never_uses_shadow_text():
    service = ChatMemoryService()
    shadow_only = SimpleNamespace(
        status=TurnStatus.COMPLETED,
        verdict=None,
        model_answers=[
            SimpleNamespace(
                model_id=NEMOTRON,
                text=SECRET_NEMOTRON,
                status=ModelAnswerStatus.COMPLETED,
            )
        ],
    )
    assert service._assistant_result(shadow_only) is None

    production_single = SimpleNamespace(
        status=TurnStatus.COMPLETED,
        verdict=None,
        model_answers=[
            SimpleNamespace(
                model_id="gpt-4.1",
                text=PROD_GPT,
                status=ModelAnswerStatus.COMPLETED,
            )
        ],
    )
    result = service._assistant_result(production_single)
    assert result is not None
    assert result.text == PROD_GPT
    assert result.is_verdict is False

    mixed = SimpleNamespace(
        status=TurnStatus.COMPLETED,
        verdict=SimpleNamespace(text="FILTERED_PRODUCTION_VERDICT", reason="ok"),
        model_answers=[
            SimpleNamespace(
                model_id="gpt-4.1",
                text=PROD_GPT,
                status=ModelAnswerStatus.COMPLETED,
            ),
            SimpleNamespace(
                model_id=NEMOTRON,
                text=SECRET_NEMOTRON,
                status=ModelAnswerStatus.COMPLETED,
            ),
        ],
    )
    mixed_result = service._assistant_result(mixed)
    assert mixed_result is not None
    assert mixed_result.text == "FILTERED_PRODUCTION_VERDICT"
    _assert_no_shadow_secrets(mixed_result.text)


def test_playbook_usable_council_answer_rejects_shadows():
    shadow = SimpleNamespace(
        model_id=NEMOTRON,
        text=SECRET_NEMOTRON,
        status=ModelAnswerStatus.COMPLETED,
    )
    production = SimpleNamespace(
        model_id="gpt-4.1",
        text=PROD_GPT,
        status=ModelAnswerStatus.COMPLETED,
    )
    assert _is_usable_council_answer(shadow) is False
    assert _is_usable_council_answer(production) is True


@pytest.mark.asyncio
async def test_playbook_reconstruction_and_extraction_omit_shadow_answers(
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
                text=PROD_GPT,
                status=ModelAnswerStatus.COMPLETED,
            ),
            ModelAnswer(
                turn_id=turn.id,
                model_id=NEMOTRON,
                text=SECRET_NEMOTRON,
                status=ModelAnswerStatus.COMPLETED,
            ),
        ]
    )
    db.add(
        Verdict(
            turn_id=turn.id,
            model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            text="FILTERED_PRODUCTION_VERDICT",
            reason="ok",
        )
    )
    await db.flush()

    assembled = await playbook_source_service.assemble_all_transcripts(db, auth)
    assert len(assembled.chats) == 1
    council = assembled.chats[0].turns[0].council_answers
    assert [answer.model_id for answer in council] == ["gpt-4.1"]
    rendered = PlaybookExtractionService()._render_turn(
        chat.id, assembled.chats[0].turns[0]
    )
    assert PROD_GPT in rendered
    _assert_no_shadow_secrets(rendered)
    assert f"model_id={NEMOTRON}" not in rendered


@pytest.mark.asyncio
async def test_lesson_context_excludes_shadow_answers(db: AsyncSession, auth: AuthContext):
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
                text=PROD_CLAUDE,
                status=ModelAnswerStatus.COMPLETED,
            ),
            ModelAnswer(
                turn_id=turn.id,
                model_id=QWEN_SHADOW,
                text=SECRET_QWEN,
                status=ModelAnswerStatus.COMPLETED,
            ),
        ]
    )
    db.add(
        Verdict(
            turn_id=turn.id,
            model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            text="FILTERED_PRODUCTION_VERDICT",
            reason="ok",
        )
    )
    await db.flush()

    _turn, _chat, _verdict_model, answer_context = await lesson_service._load_turn_context(
        db, auth, turn.id
    )
    assert [row["model_id"] for row in answer_context] == ["claude"]
    joined = "\n".join(row["text"] for row in answer_context)
    _assert_no_shadow_secrets(joined)


@pytest.mark.asyncio
async def test_prompt_builder_skips_shadow_council_members(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(
        db,
        auth,
        slug="shadow-builder",
        models=["gpt-4.1", NEMOTRON, QWEN_SHADOW],
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
    assert "nvidia/nemotron-3-ultra-550b-a55b" not in calls
    assert "qwen/qwen3.8-max-0902" not in calls
    assert calls[0] == "openai/gpt-4.1"


@pytest.mark.asyncio
async def test_prompt_builder_rejects_shadow_only_model_set(
    db: AsyncSession, auth: AuthContext
):
    model_set = await create_model_set(
        db, auth, slug="shadow-only-builder", models=list(SHADOW_OR_IDS)
    )
    with pytest.raises(ValidationError, match="no council models"):
        await prompt_builder_service.refine(
            db,
            auth,
            messages=[PromptBuilderRefineMessage(role="user", content="Improve this.")],
            model_set_id=model_set.slug,
        )


@pytest.mark.asyncio
async def test_blueprint_orchestrator_skips_shadow_models(monkeypatch: pytest.MonkeyPatch):
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
        models=[NEMOTRON, "gpt-4.1"],
        verdict_model="gpt-4.1",
    )
    await orchestrator.generate(mission, model_set)
    assert "nvidia/nemotron-3-ultra-550b-a55b" not in called_models
    assert called_models
    assert all(model == "openai/gpt-4.1" for model in called_models)


def test_team_planner_allowed_models_exclude_shadows():
    model_set = SimpleNamespace(
        models=["gpt-4.1", NEMOTRON, "claude"],
        verdict_model="gpt-4.1",
    )
    from app.llm.catalog import intelligence_eligible_model_ids

    assert intelligence_eligible_model_ids(model_set.models) == ["gpt-4.1", "claude"]
    assert TeamPlannerService().planner_model_id(model_set) == "gpt-4.1"


@pytest.mark.asyncio
async def test_regenerate_reseeds_shadow_model_answers(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'shadow-regen.db'}", poolclass=NullPool
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.services.chat_service.AsyncSessionLocal", Session)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as db:
        org = Organization(name="Org", slug="shadow-regen")
        user = User(email="regen-shadow@example.com", hashed_password="x", full_name="User")
        db.add_all([org, user])
        await db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER))
        model_set = ModelSet(
            org_id=org.id,
            slug="shadow-regen-set",
            name="Shadow Regen",
            description="",
            models=["gpt-4.1", "claude", *SHADOW_OR_IDS],
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
        *SHADOW_OR_IDS,
    }
    await engine.dispose()


class GatedShadowProvider(ShadowCouncilProvider):
    """Hold selected answer slugs until `release` is set; record Verdict entry."""

    def __init__(
        self,
        *,
        hold_slugs: frozenset[str],
        fail_slugs: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(fail_slugs=fail_slugs)
        self.hold_slugs = hold_slugs
        self.release = asyncio.Event()
        self.verdict_entered = asyncio.Event()
        self.turn_failed_entered = asyncio.Event()

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096, **kwargs):
        if user == VERDICT_USER:
            assert not self.release.is_set(), "Verdict ran after the shadow gate was released"
            self.verdict_entered.set()
            return await super().complete(
                system=system, user=user, model=model, max_tokens=max_tokens, **kwargs
            )
        if model in self.hold_slugs:
            await self.release.wait()
        return await super().complete(
            system=system, user=user, model=model, max_tokens=max_tokens, **kwargs
        )


async def _run_gated(Session, ids, provider, model_ids: list[str], events: list):
    org_id, _user_id, chat_id, turn_id = ids
    orchestrator = TurnOrchestrator()
    orchestrator._providers = ScriptedRegistry(provider)

    async def on_event(event, data):
        events.append((event, data))
        if event == "turn_failed":
            provider.turn_failed_entered.set()

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
                model_set_name="Shadow Mix",
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
        costs = (
            await db.execute(select(CostRecord).where(CostRecord.turn_id == turn_id))
        ).scalars().all()
        return turn, turn.verdict, list(turn.model_answers), costs


@pytest.mark.asyncio
async def test_slow_shadow_does_not_delay_verdict(env):
    Session, ids, _auth = env
    provider = GatedShadowProvider(hold_slugs=frozenset(SHADOW_SLUGS))
    model_ids = ["gpt-4.1", "claude", *SHADOW_OR_IDS]
    events: list[tuple[str, dict]] = []
    task = asyncio.create_task(_run_gated(Session, ids, provider, model_ids, events))

    await asyncio.wait_for(provider.verdict_entered.wait(), timeout=5)
    assert provider.verdict_calls == 1
    assert not provider.release.is_set()

    provider.release.set()
    turn, verdict, answers, costs = await asyncio.wait_for(task, timeout=5)

    assert turn.status == TurnStatus.COMPLETED
    assert verdict is not None
    assert verdict.text == "FILTERED_PRODUCTION_VERDICT"
    assert provider.verdict_calls == 1
    event_names = [name for name, _ in events]
    assert event_names.index("verdict_completed") < event_names.index("turn_completed")
    delayed_completed = [
        i
        for i, (name, data) in enumerate(events)
        if name == "model_answer_completed" and data.get("model_id") in SHADOW_OR_IDS
    ]
    assert delayed_completed
    assert all(i > event_names.index("verdict_completed") for i in delayed_completed)
    assert {a.model_id for a in answers} == set(model_ids)
    assert all(a.status == ModelAnswerStatus.COMPLETED for a in answers)
    assert len([c for c in costs if c.kind == UsageKind.ANSWER and c.model_id in SHADOW_OR_IDS]) == 3


@pytest.mark.asyncio
async def test_shadow_failure_after_verdict_keeps_completed_turn(env):
    Session, ids, _auth = env
    provider = GatedShadowProvider(
        hold_slugs=frozenset({"deepseek/deepseek-v4.1-flash"}),
        fail_slugs=frozenset({"deepseek/deepseek-v4.1-flash"}),
    )
    model_ids = ["gpt-4.1", "claude", *SHADOW_OR_IDS]
    events: list[tuple[str, dict]] = []
    task = asyncio.create_task(_run_gated(Session, ids, provider, model_ids, events))

    await asyncio.wait_for(provider.verdict_entered.wait(), timeout=5)
    assert provider.verdict_calls == 1
    provider.release.set()
    turn, verdict, answers, _costs = await asyncio.wait_for(task, timeout=5)

    assert provider.verdict_calls == 1
    assert turn.status == TurnStatus.COMPLETED
    assert verdict is not None
    assert verdict.text == "FILTERED_PRODUCTION_VERDICT"
    by_id = {a.model_id: a for a in answers}
    assert by_id[DEEPSEEK_SHADOW].status == ModelAnswerStatus.FAILED
    assert by_id[DEEPSEEK_SHADOW].error_message is not None
    assert "SHADOW_PROVIDER_FAIL" in by_id[DEEPSEEK_SHADOW].error_message
    assert any(
        n == "model_answer_failed" and d.get("model_id") == DEEPSEEK_SHADOW for n, d in events
    )
    verdict_idx = [n for n, _ in events].index("verdict_completed")
    fail_idx = next(
        i
        for i, (n, d) in enumerate(events)
        if n == "model_answer_failed" and d.get("model_id") == DEEPSEEK_SHADOW
    )
    assert verdict_idx < fail_idx


@pytest.mark.asyncio
async def test_shadow_success_after_verdict_persists_answer_and_cost(env):
    Session, ids, _auth = env
    provider = GatedShadowProvider(hold_slugs=frozenset({"deepseek/deepseek-v4.1-flash"}))
    model_ids = ["gpt-4.1", "claude", *SHADOW_OR_IDS]
    events: list[tuple[str, dict]] = []
    task = asyncio.create_task(_run_gated(Session, ids, provider, model_ids, events))

    await asyncio.wait_for(provider.verdict_entered.wait(), timeout=5)
    verdict_text_at_invoke = provider.verdict_systems[0]
    provider.release.set()
    turn, verdict, answers, costs = await asyncio.wait_for(task, timeout=5)

    assert provider.verdict_calls == 1
    assert turn.status == TurnStatus.COMPLETED
    assert verdict is not None
    assert verdict.text == "FILTERED_PRODUCTION_VERDICT"
    assert provider.verdict_systems == [verdict_text_at_invoke]
    by_id = {a.model_id: a for a in answers}
    assert by_id[DEEPSEEK_SHADOW].status == ModelAnswerStatus.COMPLETED
    assert by_id[DEEPSEEK_SHADOW].text == SECRET_DEEPSEEK
    shadow_cost = next(
        c for c in costs if c.kind == UsageKind.ANSWER and c.model_id == DEEPSEEK_SHADOW
    )
    assert shadow_cost.tokens_input == 11
    assert shadow_cost.tokens_output == 7
    assert shadow_cost.cost_usd == 0.42
    event_names = [n for n, _ in events]
    completed_idx = next(
        i
        for i, (n, d) in enumerate(events)
        if n == "model_answer_completed" and d.get("model_id") == DEEPSEEK_SHADOW
    )
    assert event_names.index("verdict_completed") < completed_idx < event_names.index("turn_completed")


@pytest.mark.asyncio
async def test_production_failure_does_not_wait_on_shadow_success(env):
    Session, ids, _auth = env
    provider = GatedShadowProvider(
        hold_slugs=frozenset(SHADOW_SLUGS),
        fail_slugs=frozenset({"openai/gpt-4.1", "anthropic/claude-sonnet-4"}),
    )
    model_ids = ["gpt-4.1", "claude", *SHADOW_OR_IDS]
    events: list[tuple[str, dict]] = []
    task = asyncio.create_task(_run_gated(Session, ids, provider, model_ids, events))

    await asyncio.wait_for(provider.turn_failed_entered.wait(), timeout=5)
    assert provider.verdict_calls == 0
    assert not provider.release.is_set()
    provider.release.set()
    turn, verdict, answers, costs = await asyncio.wait_for(task, timeout=5)

    assert turn.status == TurnStatus.FAILED
    assert verdict is None
    assert provider.verdict_calls == 0
    by_id = {a.model_id: a for a in answers}
    assert by_id[DEEPSEEK_SHADOW].status == ModelAnswerStatus.COMPLETED
    assert by_id[DEEPSEEK_SHADOW].text == SECRET_DEEPSEEK
    shadow_costs = [
        c for c in costs if c.kind == UsageKind.ANSWER and c.model_id in SHADOW_OR_IDS
    ]
    assert len(shadow_costs) == 3
