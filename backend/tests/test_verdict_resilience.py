"""Verdict-phase resilience: a turn must not lose its verdict to a transient fault.

Regression cover for "the 4 answers came back but the verdict did not, and
resending the same message produced one" — a non-strict-JSON or transiently
failing verdict call used to fail the whole turn.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.dependencies import AuthContext
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
from app.llm.orchestrator import (
    VERDICT_DEFAULT_REASON,
    TurnContext,
    TurnOrchestrator,
)
from app.llm.providers import LLMProvider, LLMResponse

VERDICT_USER_PROMPT = "Produce the verdict JSON now."


class ScriptedProvider:
    """Answers always succeed; the verdict call follows a script."""

    def __init__(self, verdict_script: list[object]) -> None:
        self._verdict_script = list(verdict_script)
        self.verdict_calls = 0

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096, **_kwargs):
        if user == VERDICT_USER_PROMPT:
            self.verdict_calls += 1
            step = (
                self._verdict_script.pop(0)
                if self._verdict_script
                else '{"text":"Fallback","reason":"ok"}'
            )
            if isinstance(step, Exception):
                raise step
            return LLMResponse(text=step, tokens_input=10, tokens_output=5)
        return LLMResponse(
            text=f"Answer from {model}", tokens_input=10, tokens_output=5, confidence=90
        )

    def parse_json_response(self, text: str):
        return LLMProvider.parse_json_response(text)

    def parse_json_object_lenient(self, text: str):
        return LLMProvider.parse_json_object_lenient(text)

    def tracking_model_id(self, model: str) -> str:
        return model


class ScriptedRegistry:
    def __init__(self, provider: ScriptedProvider) -> None:
        self._provider = provider

    def get_provider(self, _provider_name: str) -> ScriptedProvider:
        return self._provider


@pytest.fixture
async def env(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'verdict.db'}", poolclass=NullPool
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as db:
        org = Organization(name="Org", slug="org")
        user = User(email="v@example.com", hashed_password="x", full_name="User")
        db.add_all([org, user])
        await db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER))
        chat = Chat(org_id=org.id, created_by=user.id, title="Chat")
        db.add(chat)
        await db.flush()
        turn = Turn(
            chat_id=chat.id,
            user_message="Should we ship it?",
            status=TurnStatus.PENDING,
            strategy=Strategy.SYNTHESIZE,
            model_set_id="test-set",
            verdict_model="gemini",
        )
        db.add(turn)
        await db.commit()
        ids = (org.id, user.id, chat.id, turn.id)
    try:
        yield Session, ids, AuthContext(user=user, org_id=org.id, role=OrgRole.MEMBER)
    finally:
        await engine.dispose()


async def _run(Session, ids, provider: ScriptedProvider):
    org_id, _user_id, chat_id, turn_id = ids
    orchestrator = TurnOrchestrator()
    orchestrator._providers = ScriptedRegistry(provider)
    async with Session() as db:
        await orchestrator.run(
            db,
            TurnContext(
                turn_id=turn_id,
                chat_id=chat_id,
                org_id=org_id,
                project_id=None,
                user_message="Should we ship it?",
                model_ids=["gpt-4.1", "claude"],
                verdict_model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                model_set_name="Test Set",
            ),
        )
        await db.commit()
    async with Session() as db:
        turn = await db.get(Turn, turn_id)
        verdict = (
            await db.execute(select(Verdict).where(Verdict.turn_id == turn_id))
        ).scalar_one_or_none()
        answers = (
            (await db.execute(select(ModelAnswer).where(ModelAnswer.turn_id == turn_id)))
            .scalars()
            .all()
        )
        return turn, verdict, answers


# --- lenient parser units -------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_text",
    [
        ('{"text":"Ship it","reason":"Consensus."}', "Ship it"),
        ('```json\n{"text":"Ship it","reason":"Consensus."}\n```', "Ship it"),
        ('Here is the verdict:\n{"text":"Ship it","reason":"Consensus."}\nHope that helps!', "Ship it"),
        ('{"reason":"Consensus.","text":"Ship it"} trailing prose', "Ship it"),
    ],
)
def test_lenient_parser_recovers_wrapped_json(raw, expected_text):
    parsed = LLMProvider.parse_json_object_lenient(raw)
    assert parsed is not None
    assert parsed["text"] == expected_text


def test_lenient_parser_repairs_truncated_object():
    """Token cap cut the response mid-string."""
    parsed = LLMProvider.parse_json_object_lenient(
        '{"reason":"Consensus.","text":"Ship it because the council agree'
    )
    assert parsed is not None
    assert parsed["reason"] == "Consensus."
    assert parsed["text"].startswith("Ship it because")


def test_lenient_parser_returns_none_for_prose():
    assert LLMProvider.parse_json_object_lenient("No JSON here at all.") is None
    assert LLMProvider.parse_json_object_lenient("") is None


def test_lenient_parser_ignores_braces_inside_strings():
    parsed = LLMProvider.parse_json_object_lenient(
        '{"text":"use {curly} braces","reason":"ok"}'
    )
    assert parsed is not None
    assert parsed["text"] == "use {curly} braces"


# --- orchestrator verdict path -------------------------------------------


@pytest.mark.asyncio
async def test_verdict_survives_non_json_response(env):
    """Plain prose from the verdict model becomes the verdict, not a failed turn."""
    Session, ids, _auth = env
    provider = ScriptedProvider(["Ship it — three of four models agree."])

    turn, verdict, answers = await _run(Session, ids, provider)

    assert verdict is not None
    assert verdict.text == "Ship it — three of four models agree."
    assert verdict.reason == VERDICT_DEFAULT_REASON
    assert turn.status == TurnStatus.COMPLETED
    assert len(answers) == 2
    assert provider.verdict_calls == 1


@pytest.mark.asyncio
async def test_verdict_retries_once_after_transient_error(env):
    """The exact reported symptom: first call blows up, a retry succeeds."""
    Session, ids, _auth = env
    provider = ScriptedProvider(
        [RuntimeError("upstream 502"), '{"text":"Ship it","reason":"Consensus."}']
    )

    turn, verdict, _answers = await _run(Session, ids, provider)

    assert provider.verdict_calls == 2
    assert verdict is not None
    assert verdict.text == "Ship it"
    assert verdict.reason == "Consensus."
    assert turn.status == TurnStatus.COMPLETED


@pytest.mark.asyncio
async def test_verdict_recovers_truncated_json(env):
    Session, ids, _auth = env
    provider = ScriptedProvider(['{"reason":"Consensus.","text":"Ship it, but stage the roll'])

    turn, verdict, _answers = await _run(Session, ids, provider)

    assert verdict is not None
    assert verdict.text.startswith("Ship it, but stage the roll")
    assert turn.status == TurnStatus.COMPLETED


@pytest.mark.asyncio
async def test_turn_fails_only_when_every_verdict_attempt_fails(env):
    Session, ids, _auth = env
    provider = ScriptedProvider([RuntimeError("boom"), RuntimeError("boom again")])

    turn, verdict, answers = await _run(Session, ids, provider)

    assert provider.verdict_calls == 2
    assert verdict is None
    assert turn.status == TurnStatus.FAILED
    assert "boom again" in (turn.error_message or "")
    # Answers are preserved so the user still sees the council.
    assert len(answers) == 2
    assert all(a.status == ModelAnswerStatus.COMPLETED for a in answers)


@pytest.mark.asyncio
async def test_empty_verdict_response_fails_the_turn(env):
    """An empty body is a real failure — do not persist a blank verdict card."""
    Session, ids, _auth = env
    provider = ScriptedProvider(["   ", "  "])

    turn, verdict, _answers = await _run(Session, ids, provider)

    assert verdict is None
    assert turn.status == TurnStatus.FAILED
