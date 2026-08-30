"""Verdict-phase resilience: a turn must not lose its verdict to a transient fault.

Regression cover for "the 4 answers came back but the verdict did not, and
resending the same message produced one" — a non-strict-JSON or transiently
failing verdict call used to fail the whole turn.
"""

import json
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.dml import Update

from app.core.dependencies import AuthContext
from app.db.base import Base
from app.db.models import (
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
)
from app.llm.orchestrator import (
    VERDICT_DEFAULT_REASON,
    TurnContext,
    TurnOrchestrator,
    _answer_score_update_statement,
    _validated_answer_scores,
)
from app.llm.providers import LLMProvider, LLMResponse
from app.services.chat_service import chat_service

VERDICT_USER_PROMPT = "Produce the verdict JSON now."


class ScriptedProvider:
    """Answers always succeed; the verdict call follows a script."""

    def __init__(self, verdict_script: list[object]) -> None:
        self._verdict_script = list(verdict_script)
        self.verdict_calls = 0
        self.answer_calls = 0

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
            if callable(step):
                step = step(system)
            return LLMResponse(text=step, tokens_input=10, tokens_output=5)
        self.answer_calls += 1
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


async def _run(
    Session,
    ids,
    provider: ScriptedProvider,
    events: list | None = None,
    model_ids: list[str] | None = None,
):
    org_id, _user_id, chat_id, turn_id = ids
    orchestrator = TurnOrchestrator()
    orchestrator._providers = ScriptedRegistry(provider)
    async with Session() as db:
        async def on_event(event, data):
            if events is not None:
                events.append((event, data))

        await orchestrator.run(
            db,
            TurnContext(
                turn_id=turn_id,
                chat_id=chat_id,
                org_id=org_id,
                project_id=None,
                user_message="Should we ship it?",
                model_ids=model_ids or ["gpt-4.1", "claude"],
                verdict_model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                model_set_name="Test Set",
            ),
            on_event=on_event,
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


@pytest.mark.asyncio
async def test_single_model_completes_without_referee_verdict_or_verdict_cost(env):
    Session, ids, _auth = env
    events: list[tuple[str, dict]] = []
    provider = ScriptedProvider([])

    turn, verdict, answers = await _run(
        Session,
        ids,
        provider,
        events,
        model_ids=["gpt-4.1"],
    )

    assert provider.answer_calls == 1
    assert provider.verdict_calls == 0
    assert turn.status == TurnStatus.COMPLETED
    assert len(answers) == 1
    assert answers[0].status == ModelAnswerStatus.COMPLETED
    assert verdict is None
    assert "verdict_started" not in [event for event, _ in events]
    assert "verdict_completed" not in [event for event, _ in events]
    assert [event for event, _ in events].count("turn_completed") == 1

    async with Session() as db:
        verdict_costs = (
            await db.execute(
                select(CostRecord).where(
                    CostRecord.turn_id == ids[3],
                    CostRecord.kind == UsageKind.VERDICT,
                )
            )
        ).scalars().all()
    assert verdict_costs == []


@pytest.mark.asyncio
async def test_multi_model_still_calls_referee_and_persists_verdict_cost(env):
    Session, ids, _auth = env
    provider = ScriptedProvider(['{"text":"Use the plan","reason":"Agreement."}'])

    turn, verdict, answers = await _run(Session, ids, provider)

    assert provider.answer_calls == 2
    assert provider.verdict_calls == 1
    assert turn.status == TurnStatus.COMPLETED
    assert len(answers) == 2
    assert verdict is not None
    async with Session() as db:
        verdict_costs = (
            await db.execute(
                select(CostRecord).where(
                    CostRecord.turn_id == ids[3],
                    CostRecord.kind == UsageKind.VERDICT,
                )
            )
        ).scalars().all()
    assert len(verdict_costs) == 1


@pytest.mark.asyncio
async def test_same_chat_can_switch_single_multi_single_without_rewriting_turns(env):
    Session, ids, _auth = env
    org_id, user_id, chat_id, first_turn_id = ids
    first_provider = ScriptedProvider([])
    first, first_verdict, _ = await _run(
        Session, ids, first_provider, model_ids=["gpt-4.1"]
    )

    async def add_pending(message: str) -> str:
        async with Session() as db:
            turn = Turn(
                chat_id=chat_id,
                user_message=message,
                status=TurnStatus.PENDING,
                strategy=Strategy.SYNTHESIZE,
                model_set_id="test-set",
                verdict_model="gemini",
            )
            db.add(turn)
            await db.commit()
            return turn.id

    second_turn_id = await add_pending("Compare options")
    second_provider = ScriptedProvider(['{"text":"Choose B","reason":"Stronger."}'])
    second, second_verdict, _ = await _run(
        Session,
        (org_id, user_id, chat_id, second_turn_id),
        second_provider,
        model_ids=["gpt-4.1", "claude"],
    )

    third_turn_id = await add_pending("Follow up")
    third_provider = ScriptedProvider([])
    third, third_verdict, _ = await _run(
        Session,
        (org_id, user_id, chat_id, third_turn_id),
        third_provider,
        model_ids=["claude"],
    )

    assert [first.status, second.status, third.status] == [
        TurnStatus.COMPLETED,
        TurnStatus.COMPLETED,
        TurnStatus.COMPLETED,
    ]
    assert [first_verdict is not None, second_verdict is not None, third_verdict is not None] == [
        False,
        True,
        False,
    ]
    async with Session() as db:
        persisted_first = await db.get(Turn, first_turn_id)
        persisted_first_verdict = await db.scalar(
            select(Verdict).where(Verdict.turn_id == first_turn_id)
        )
    assert persisted_first.status == TurnStatus.COMPLETED
    assert persisted_first_verdict is None


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


def _row(answer_id, model_id="gpt-4.1", status=ModelAnswerStatus.COMPLETED):
    return SimpleNamespace(id=answer_id, model_id=model_id, status=status)


def test_referee_score_validation_accepts_integer_boundaries_and_partial_results():
    rows = [_row("a", "gpt-4.1"), _row("b", "claude"), _row("failed", status=ModelAnswerStatus.FAILED)]
    parsed = {
        "evaluations": [
            {"answer_id": "a", "score": 0},
            {"answer_id": "b", "score": 100},
            {"answer_id": "failed", "score": 80},
        ]
    }

    assert _validated_answer_scores(parsed, rows) == [
        {"answer_id": "a", "model_id": "gpt-4.1", "score": 0},
        {"answer_id": "b", "model_id": "claude", "score": 100},
    ]


def test_referee_score_validation_rejects_invalid_duplicate_and_unknown_entries():
    rows = [_row("a"), _row("b", "claude")]
    parsed = {
        "evaluations": [
            {"answer_id": "a", "score": 90},
            {"answer_id": "a", "score": 80},
            {"answer_id": "b", "score": 50.5},
            {"answer_id": "b", "score": -1},
            {"answer_id": "unknown", "score": 75},
            {"answer_id": "missing-score"},
            "malformed",
        ]
    }

    assert _validated_answer_scores(parsed, rows) == []


def test_referee_score_validation_rejects_boolean_and_out_of_range_scores():
    rows = [_row("a"), _row("b", "claude"), _row("c", "gemini")]
    parsed = {
        "evaluations": [
            {"answer_id": "a", "score": True},
            {"answer_id": "b", "score": 101},
            {"answer_id": "c", "score": -10},
        ]
    }

    assert _validated_answer_scores(parsed, rows) == []


# --- orchestrator verdict path -------------------------------------------


def test_answer_score_update_uses_varchar_status_comparison_for_postgresql():
    statement = _answer_score_update_statement(
        "turn-id",
        {"answer_id": "answer-id", "model_id": "gpt-4.1", "score": 98},
    )

    compiled = str(statement.compile(dialect=asyncpg.dialect()))

    assert "lower(CAST(model_answers.status AS VARCHAR))" in compiled
    assert "::VARCHAR" in compiled
    assert "::modelanswerstatus" not in compiled.lower()


@pytest.mark.asyncio
async def test_referee_scores_overwrite_null_confidence_and_stream_live_updates(env):
    Session, ids, auth = env

    def scored_verdict(system: str) -> str:
        answer_ids = re.findall(r"\*\*Answer ID\*\*: ([^\r\n]+)", system)
        assert len(answer_ids) == 2
        assert "**Confidence**" not in system
        return json.dumps(
            {
                "text": "Ship it in stages.",
                "reason": "Combined both answers.",
                "evaluations": [
                    {"answer_id": answer_ids[0], "score": 98},
                    {"answer_id": answer_ids[1], "score": 100},
                ],
            }
        )

    provider = ScriptedProvider([scored_verdict])
    events = []
    turn, verdict, answers = await _run(Session, ids, provider, events)
    answers_by_model = {answer.model_id: answer for answer in answers}

    assert turn.status == TurnStatus.COMPLETED
    assert verdict is not None
    assert answers_by_model["gpt-4.1"].confidence == 98
    assert answers_by_model["claude"].confidence == 100
    completed_events = [data for event, data in events if event == "model_answer_completed"]
    assert len(completed_events) == 2
    assert all(data["confidence"] is None for data in completed_events)
    verdict_event = next(data for event, data in events if event == "verdict_completed")
    assert {item["score"] for item in verdict_event["answer_scores"]} == {98, 100}

    # A fresh API read must return the persisted Referee scores, not transient stream state.
    async with Session() as db:
        refreshed = await chat_service.get_turn(db, auth, ids[3])
    refreshed_by_model = {answer.model_id: answer for answer in refreshed.model_answers}
    assert refreshed_by_model["gpt-4.1"].confidence == 98
    assert refreshed_by_model["claude"].confidence == 100


@pytest.mark.asyncio
async def test_valid_verdict_with_bad_or_missing_scores_keeps_confidence_null(env):
    Session, ids, _auth = env

    def malformed_scores(system: str) -> str:
        answer_ids = re.findall(r"\*\*Answer ID\*\*: ([^\r\n]+)", system)
        return json.dumps(
            {
                "text": "Usable verdict",
                "reason": "Scores were malformed.",
                "evaluations": [
                    {"answer_id": answer_ids[0], "score": 90.5},
                    {"answer_id": "not-from-this-turn", "score": 85},
                ],
            }
        )

    turn, verdict, answers = await _run(Session, ids, ScriptedProvider([malformed_scores]))

    assert turn.status == TurnStatus.COMPLETED
    assert verdict is not None
    assert verdict.text == "Usable verdict"
    assert all(answer.confidence is None for answer in answers)


@pytest.mark.asyncio
async def test_score_database_failure_rolls_back_savepoint_and_preserves_verdict(
    env, monkeypatch
):
    Session, ids, _auth = env
    original_execute = AsyncSession.execute

    async def fail_one_score_update(self, statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "model_answers":
            confidence_values = [
                getattr(value, "value", None)
                for column, value in statement._values.items()
                if getattr(column, "name", None) == "confidence"
            ]
            if confidence_values == [100]:
                raise DBAPIError(
                    "UPDATE model_answers SET confidence = :score",
                    {"score": 100},
                    Exception("forced score write failure"),
                )
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", fail_one_score_update)

    def scored_verdict(system: str) -> str:
        answer_ids = re.findall(r"\*\*Answer ID\*\*: ([^\r\n]+)", system)
        return json.dumps(
            {
                "text": "Verdict survives optional score failure.",
                "reason": "The score write is isolated.",
                "evaluations": [
                    {"answer_id": answer_ids[0], "score": 0},
                    {"answer_id": answer_ids[1], "score": 100},
                ],
            }
        )

    events = []
    turn, verdict, answers = await _run(
        Session, ids, ScriptedProvider([scored_verdict]), events
    )
    answers_by_model = {answer.model_id: answer for answer in answers}

    assert turn.status == TurnStatus.COMPLETED
    assert verdict is not None
    assert verdict.text == "Verdict survives optional score failure."
    assert answers_by_model["gpt-4.1"].confidence == 0
    assert answers_by_model["claude"].confidence is None
    verdict_event = next(data for event, data in events if event == "verdict_completed")
    assert verdict_event["answer_scores"] == [
        {
            "answer_id": str(answers_by_model["gpt-4.1"].id),
            "model_id": "gpt-4.1",
            "score": 0,
        }
    ]


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
    assert all(answer.confidence is None for answer in answers)
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
    assert all(a.confidence is None for a in answers)


@pytest.mark.asyncio
async def test_empty_verdict_response_fails_the_turn(env):
    """An empty body is a real failure — do not persist a blank verdict card."""
    Session, ids, _auth = env
    provider = ScriptedProvider(["   ", "  "])

    turn, verdict, _answers = await _run(Session, ids, provider)

    assert verdict is None
    assert turn.status == TurnStatus.FAILED
