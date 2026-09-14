"""Council fallback integration: original rows, events, costs and Verdict survive."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    Chat,
    CostRecord,
    ModelAnswer,
    ModelAnswerStatus,
    Strategy,
    Turn,
    UsageKind,
)
from app.llm.orchestrator import TurnContext, TurnNoLongerWritable, TurnOrchestrator
from app.llm.providers import (
    LLMProvider,
    LLMResponse,
    OpenRouterError,
    OpenRouterProvider,
    build_openrouter_chat_payload,
    council_fallback_reason,
)

PRO = "openai/gpt-6-astra-pro"
ASTRA = "openai/gpt-6-astra"
GPT = "openai/gpt-5.1"
GEMINI = "google/gemini-3.7-flash"
FLASH = "google/gemini-3.6-flash"


@pytest.fixture
async def db(tmp_path):
    # Cancellation can invalidate a connection; file-backed SQLite retains the schema.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'council.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "primary,failures,set_id,expected,success",
    [
        (PRO, 0, "set-7edaefc8", [PRO], True),
        (PRO, 1, "set-7edaefc8", [PRO, ASTRA], True),
        (PRO, 2, "set-7edaefc8", [PRO, ASTRA, GPT], True),
        (PRO, 3, "set-7edaefc8", [PRO, ASTRA, GPT], False),
        (GEMINI, 1, "set-7edaefc8", [GEMINI, FLASH], True),
        (PRO, 1, "another-set", [PRO], False),
        (PRO, 0, "another-set", [PRO], True),
        (ASTRA, 0, "set-7edaefc8", [ASTRA], True),
        (GPT, 0, "set-7edaefc8", [GPT], True),
        ("qwen/random-dynamic-model", 0, "set-7edaefc8", ["qwen/random-dynamic-model"], True),
        (ASTRA, 1, "set-7edaefc8", [ASTRA], False),
        (GPT, 1, "set-7edaefc8", [GPT], False),
        ("qwen/random-dynamic-model", 1, "set-7edaefc8", ["qwen/random-dynamic-model"], False),
        (PRO, "bug", "set-7edaefc8", [PRO], False),
        (PRO, "cancel", "set-7edaefc8", [PRO], False),
        (PRO, "deleted", "set-7edaefc8", [PRO], False),
        (PRO, "delete-between", "set-7edaefc8", [PRO], False),
    ],
)
async def test_council_slots(db, auth, monkeypatch, primary, failures, set_id, expected, success):
    targeted = set_id == "set-7edaefc8" and primary in (PRO, GEMINI)
    if not targeted:

        def unexpected_fallback(*args, **kwargs):
            pytest.fail("Legacy path entered fallback machinery")

        monkeypatch.setattr("app.llm.orchestrator.council_fallback_reason", unexpected_fallback)
    slot_id = "or:" + primary.replace("/", "--")
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Fallback test")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="Question",
        strategy=Strategy.SYNTHESIZE,
        model_set_id=set_id,
        verdict_model="or:openai--gpt-5.1",
    )
    db.add(turn)
    await db.commit()
    turn_id = turn.id
    calls, verdict_calls, events = [], [], []
    deleted = False

    async def complete(**kwargs):
        nonlocal deleted
        model = kwargs["model"]
        if kwargs["max_tokens"] != 20000:
            assert "allow_provider_fallbacks" not in kwargs
            verdict_calls.append(model)
            return LLMResponse(
                text='{"text":"Verdict","reason":"ok"}',
                tokens_input=4,
                tokens_output=2,
                cost_usd=0.01,
            )
        if model == "openai/gpt-4.1":
            assert "allow_provider_fallbacks" not in kwargs
            return LLMResponse(text="Other Council answer", tokens_input=1, tokens_output=1)
        calls.append(model)
        if targeted:
            assert kwargs["allow_provider_fallbacks"] is True
        else:
            assert "allow_provider_fallbacks" not in kwargs
        if failures == "cancel":
            raise asyncio.CancelledError
        if failures == "deleted":
            raise TurnNoLongerWritable
        if failures == "delete-between":
            deleted = True
            raise OpenRouterError(503, "Unavailable")
        if failures == "bug":
            raise TypeError("Application bug")
        if len(calls) <= failures:
            raise OpenRouterError(429, "Rate limited")
        return LLMResponse(
            text="Council answer",
            tokens_input=11,
            tokens_output=7,
            cost_usd=0.123,
            raw={"model": model},
        )

    provider = SimpleNamespace(
        complete=complete,
        parse_json_object_lenient=LLMProvider.parse_json_object_lenient,
    )
    orchestrator = TurnOrchestrator()
    orchestrator._providers = SimpleNamespace(get_provider=lambda _: provider)
    original_check = orchestrator._ensure_not_deleted

    async def check(session, tid):
        if deleted:
            raise TurnNoLongerWritable
        await original_check(session, tid)

    orchestrator._ensure_not_deleted = check
    ctx = TurnContext(
        turn_id=turn_id,
        chat_id=chat.id,
        org_id=auth.org_id,
        project_id=None,
        user_message="Question",
        model_ids=[slot_id, "gpt-4.1"],
        verdict_model_id="or:openai--gpt-5.1",
        strategy=Strategy.SYNTHESIZE,
        model_set_name="Renamed Ultimate",
        model_set_id=set_id,
    )

    async def emit(event, data):
        events.append((event, data))

    if failures == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await orchestrator.run(db, ctx, emit)
        await db.rollback()
    else:
        await orchestrator.run(db, ctx, emit)
    assert calls == expected
    rows = (
        (await db.execute(select(ModelAnswer).where(ModelAnswer.turn_id == turn_id)))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {row.model_id for row in rows} == {slot_id, "gpt-4.1"}
    answer = next(row for row in rows if row.model_id == slot_id)
    assert [data["model_id"] for event, data in events if event == "model_answer_started"] == [
        slot_id,
        "gpt-4.1",
    ]
    completed = [
        data
        for event, data in events
        if event == "model_answer_completed" and data["model_id"] == slot_id
    ]
    assert len(completed) == int(success)
    if success:
        assert completed[0]["model_id"] == slot_id
        assert answer.status == ModelAnswerStatus.COMPLETED
        assert verdict_calls == [GPT]
        costs = (
            (
                await db.execute(
                    select(CostRecord).where(
                        CostRecord.turn_id == turn_id,
                        CostRecord.model_id == slot_id,
                        CostRecord.kind == UsageKind.ANSWER,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(costs) == 1
        assert costs[0].cost_usd == pytest.approx(0.123)
        assert costs[0].tokens_input == 11
    elif failures not in ("cancel", "deleted", "delete-between"):
        assert answer.status == ModelAnswerStatus.FAILED
        assert len([e for e, _ in events if e == "model_answer_failed"]) == 1


@pytest.mark.parametrize(
    "error,eligible",
    [
        (OpenRouterError(429, "limited"), True),
        (OpenRouterError(503, "unavailable"), True),
        (OpenRouterError(404, "no model"), True),
        (httpx.ReadTimeout("timeout"), True),
        (httpx.ConnectError("connection"), True),
        (OpenRouterError(400, "invalid"), False),
        (OpenRouterError(401, "auth"), False),
        (OpenRouterError(402, "credits"), False),
        (ValueError("invalid state"), False),
        (RuntimeError("OpenRouter error (429): not a typed provider error"), False),
    ],
)
def test_eligibility(error, eligible):
    assert (council_fallback_reason(error) is not None) == eligible


@pytest.mark.parametrize("model", [PRO, ASTRA, GPT, GEMINI, FLASH, "qwen/random-model"])
@pytest.mark.parametrize("opt_in", [False, True])
def test_provider_failover(model, opt_in):
    payload = build_openrouter_chat_payload(
        model=model,
        system="s",
        user="u",
        max_tokens=20,
        allow_provider_fallbacks=opt_in,
    )
    assert payload["model"] == model
    if opt_in:
        assert payload["provider"] == {"allow_fallbacks": True}
    else:
        assert "provider" not in payload


@pytest.mark.parametrize("opt_in", [False, True])
async def test_provider_preserves_status_and_bounded_retries(monkeypatch, opt_in):
    provider = OpenRouterProvider()
    provider._api_key = "test"
    response = httpx.Response(503, json={"error": {"message": "Unavailable"}})
    post = AsyncMock(return_value=response)
    client = AsyncMock()
    client.__aenter__.return_value.post = post
    monkeypatch.setattr("app.llm.providers.httpx.AsyncClient", lambda **_: client)
    monkeypatch.setattr("app.llm.providers.asyncio.sleep", AsyncMock())
    with pytest.raises(OpenRouterError) as exc:
        await provider.complete(model=PRO, system="s", user="u", allow_provider_fallbacks=opt_in)
    assert exc.value.status_code == 503
    assert post.await_count == 2
    for call in post.await_args_list:
        payload = call.kwargs["json"]
        if opt_in:
            assert payload["provider"] == {"allow_fallbacks": True}
        else:
            assert "provider" not in payload
