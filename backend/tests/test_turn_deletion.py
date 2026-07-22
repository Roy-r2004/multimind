import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.base import Base
from app.db.models import (
    Chat,
    CostRecord,
    DecisionInsurance,
    ModelAnswer,
    ModelAnswerStatus,
    ModelSet,
    OrgMembership,
    OrgRole,
    Organization,
    SavedVerdict,
    Strategy,
    Turn,
    TurnStatus,
    UsageKind,
    User,
    Verdict,
)
from app.llm.orchestrator import TurnContext, TurnOrchestrator
from app.llm.providers import LLMResponse
from app.schemas.api import TurnCreateRequest
from app.services import chat_service as chat_service_module
from app.services.chat_service import chat_service


class FakeProvider:
    def __init__(self) -> None:
        self.started: dict[str, asyncio.Event] = {}
        self.release: dict[str, asyncio.Event] = {}
        self.blocking_models: set[str] = set()
        self.verdict_calls = 0

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096):
        self.started.setdefault(model, asyncio.Event()).set()
        if user == "Produce the verdict JSON now.":
            self.verdict_calls += 1
            return LLMResponse(
                text='{"text":"Final verdict","reason":"Because."}',
                tokens_input=10,
                tokens_output=5,
            )
        if model in self.blocking_models:
            await self.release.setdefault(model, asyncio.Event()).wait()
        return LLMResponse(
            text=f"Answer from {model}",
            tokens_input=10,
            tokens_output=5,
            confidence=90,
        )

    def parse_json_response(self, text: str):
        return {"text": "Final verdict", "reason": "Because."}


class FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    def get_provider(self, _provider_name: str) -> FakeProvider:
        return self.provider


@pytest.fixture
async def db_setup(tmp_path):
    db_path = tmp_path / "turn-delete.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        org = Organization(name="Org", slug="org")
        other_org = Organization(name="Other", slug="other")
        user = User(email="u@example.com", hashed_password="x", full_name="User")
        db.add_all([org, other_org, user])
        await db.flush()
        db.add_all(
            [
                OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER),
                OrgMembership(org_id=other_org.id, user_id=user.id, role=OrgRole.MEMBER),
            ]
        )
        model_set = ModelSet(
            org_id=org.id,
            slug="test-set",
            name="Test Set",
            description="",
            models=["gpt-4.1", "claude"],
            verdict_model="gemini",
            strategy=Strategy.SYNTHESIZE,
            best_for="",
        )
        chat = Chat(org_id=org.id, created_by=user.id, title="New chat")
        other_chat = Chat(org_id=other_org.id, created_by=user.id, title="Other")
        db.add_all([model_set, chat, other_chat])
        await db.commit()

    provider = FakeProvider()
    try:
        yield SimpleNamespace(
            engine=engine,
            Session=Session,
            provider=provider,
            registry=FakeRegistry(provider),
            auth=AuthContext(user=user, org_id=org.id, role=OrgRole.MEMBER),
            other_auth=AuthContext(user=user, org_id=other_org.id, role=OrgRole.MEMBER),
            chat_id=chat.id,
            other_chat_id=other_chat.id,
        )
    finally:
        chat_service_module._orchestration_tasks.clear()
        await engine.dispose()


async def create_turn(Session, auth, chat_id: str):
    async with Session() as db:
        turn = await chat_service.start_turn(
            db,
            auth,
            chat_id,
            TurnCreateRequest(user_message="Delete this prompt", model_set_id="test-set"),
        )
        await db.commit()
        return turn


async def run_turn(Session, registry, auth, turn_id: str, chat_id: str):
    async with Session() as db:
        orchestrator = TurnOrchestrator()
        orchestrator._providers = registry
        await orchestrator.run(
            db,
            TurnContext(
                turn_id=turn_id,
                chat_id=chat_id,
                org_id=auth.org_id,
                project_id=None,
                user_message="Delete this prompt",
                model_ids=["gpt-4.1", "claude"],
                verdict_model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                model_set_name="Test Set",
                skip_answer_seed=True,
            ),
        )
        await db.commit()


async def count_rows(Session, model, **filters):
    async with Session() as db:
        statement = select(model)
        for field, value in filters.items():
            statement = statement.where(getattr(model, field) == value)
        return len((await db.execute(statement)).scalars().all())


@pytest.mark.asyncio
async def test_deleting_completed_middle_turn_removes_only_selected_related_rows(db_setup):
    async with db_setup.Session() as db:
        turns: list[Turn] = []
        verdicts: list[Verdict] = []
        for index in range(1, 4):
            turn = Turn(
                chat_id=db_setup.chat_id,
                user_message=f"Prompt {index}",
                model_set_id="test-set",
                strategy=Strategy.SYNTHESIZE,
                verdict_model="gemini",
                status=TurnStatus.COMPLETED,
            )
            db.add(turn)
            await db.flush()
            db.add_all(
                [
                    ModelAnswer(
                        turn_id=turn.id,
                        model_id="gpt-4.1",
                        text=f"Answer {index}A",
                        status=ModelAnswerStatus.COMPLETED,
                    ),
                    ModelAnswer(
                        turn_id=turn.id,
                        model_id="claude",
                        text=f"Answer {index}B",
                        status=ModelAnswerStatus.COMPLETED,
                    ),
                ]
            )
            verdict = Verdict(
                turn_id=turn.id,
                model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                text=f"Verdict {index}",
                reason=f"Reason {index}",
            )
            db.add(verdict)
            db.add(
                CostRecord(
                    org_id=db_setup.auth.org_id,
                    chat_id=db_setup.chat_id,
                    turn_id=turn.id,
                    model_id="gemini",
                    kind=UsageKind.VERDICT,
                )
            )
            turns.append(turn)
            verdicts.append(verdict)

        middle = turns[1]
        middle_verdict = verdicts[1]
        db.add(
            DecisionInsurance(
                turn_id=middle.id,
                best_case="Best",
                worst_case="Worst",
                risk_level="medium",
                potential_loss="Loss",
                mitigation_plan="Plan",
            )
        )
        await db.flush()
        db.add(
            SavedVerdict(
                org_id=db_setup.auth.org_id,
                user_id=db_setup.auth.user.id,
                source_verdict_id=middle_verdict.id,
                source_turn_id=middle.id,
                source_chat_id=db_setup.chat_id,
                source_chat_title="Snapshot chat",
                source_user_message=middle.user_message,
                verdict_text=middle_verdict.text,
                verdict_reason=middle_verdict.reason,
                verdict_model_id=middle_verdict.model_id,
                strategy=middle_verdict.strategy,
            )
        )
        await db.commit()

    async with db_setup.Session() as db:
        response = await chat_service.delete_turn(
            db, db_setup.auth, db_setup.chat_id, middle.id
        )
        context = await chat_service._latest_previous_verdict_context(
            db, db_setup.chat_id, turns[2].id, None
        )
        await db.commit()

    assert response.deleted is True
    assert await count_rows(db_setup.Session, Turn, id=turns[0].id) == 1
    assert await count_rows(db_setup.Session, Turn, id=middle.id) == 0
    assert await count_rows(db_setup.Session, Turn, id=turns[2].id) == 1
    assert await count_rows(db_setup.Session, ModelAnswer, turn_id=middle.id) == 0
    assert await count_rows(db_setup.Session, Verdict, turn_id=middle.id) == 0
    assert await count_rows(db_setup.Session, CostRecord, turn_id=middle.id) == 0
    assert await count_rows(db_setup.Session, DecisionInsurance, turn_id=middle.id) == 0
    assert await count_rows(db_setup.Session, SavedVerdict, source_turn_id=middle.id) == 1
    assert context is not None
    assert "Prompt 2" not in context
    assert "Verdict 2" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TurnStatus.PARTIAL, TurnStatus.FAILED])
async def test_deleting_historical_partial_or_failed_turn(db_setup, status):
    async with db_setup.Session() as db:
        turn = Turn(
            chat_id=db_setup.chat_id,
            user_message=f"Historical {status.value}",
            model_set_id="test-set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=status,
        )
        db.add(turn)
        await db.flush()
        db.add(
            ModelAnswer(
                turn_id=turn.id,
                model_id="gpt-4.1",
                text="Partial answer" if status is TurnStatus.PARTIAL else "",
                status=(
                    ModelAnswerStatus.COMPLETED
                    if status is TurnStatus.PARTIAL
                    else ModelAnswerStatus.FAILED
                ),
            )
        )
        await db.commit()

    async with db_setup.Session() as db:
        response = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)
        await db.commit()

    assert response.deleted is True
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
    assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0


@pytest.mark.asyncio
async def test_authorized_user_deletes_active_turn_and_all_related_rows(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    async with db_setup.Session() as db:
        db.add(
            Verdict(
                turn_id=turn.id,
                model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                text="Temporary verdict",
                reason="Temporary",
            )
        )
        answer = (
            await db.execute(select(ModelAnswer).where(ModelAnswer.turn_id == turn.id).limit(1))
        ).scalar_one()
        answer.status = ModelAnswerStatus.COMPLETED
        answer.text = "Completed answer"
        db.add(
            CostRecord(
                org_id=db_setup.auth.org_id,
                chat_id=db_setup.chat_id,
                turn_id=turn.id,
                model_id="gpt-4.1",
                kind=UsageKind.ANSWER,
            )
        )
        await db.commit()

    async with db_setup.Session() as db:
        response = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)
        await db.commit()

    assert response.deleted is True
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
    assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0
    assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
    assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0


@pytest.mark.asyncio
async def test_cross_org_and_mismatched_chat_deletion_are_rejected(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async with db_setup.Session() as db:
        with pytest.raises(NotFoundError):
            await chat_service.delete_turn(db, db_setup.other_auth, db_setup.chat_id, turn.id)
        with pytest.raises(NotFoundError):
            await chat_service.delete_turn(db, db_setup.auth, db_setup.other_chat_id, turn.id)


@pytest.mark.asyncio
async def test_repeated_deletion_is_safe_and_list_turns_omits_deleted_turn(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async with db_setup.Session() as db:
        first = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)
        second = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)
        turns = await chat_service.list_turns(db, db_setup.auth, db_setup.chat_id)
        await db.commit()

    assert first.deleted is True
    assert second.deleted is True
    assert all(saved.id != turn.id for saved in turns)


@pytest.mark.asyncio
async def test_registered_orchestration_task_is_cancelled_on_delete(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper())
    chat_service_module._orchestration_tasks[turn.id] = task

    async with db_setup.Session() as db:
        await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)
        await db.commit()

    assert task.done()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_late_provider_result_after_deletion_does_not_recreate_rows_or_verdict(db_setup):
    model_id = "anthropic/claude-sonnet-4"
    db_setup.provider.blocking_models.add(model_id)
    started_event = db_setup.provider.started.setdefault(model_id, asyncio.Event())
    release_event = db_setup.provider.release.setdefault(model_id, asyncio.Event())
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    task = asyncio.create_task(
        run_turn(db_setup.Session, db_setup.registry, db_setup.auth, turn.id, db_setup.chat_id)
    )
    try:
        await asyncio.wait_for(started_event.wait(), timeout=5)

        async with db_setup.Session() as db:
            await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)
            await db.commit()

        release_event.set()
        await task

        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert db_setup.provider.verdict_calls == 0
    finally:
        if not task.done():
            release_event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_later_prompt_runs_normally_after_deletion(db_setup):
    first = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    async with db_setup.Session() as db:
        await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, first.id)
        await db.commit()

    second = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    await run_turn(db_setup.Session, db_setup.registry, db_setup.auth, second.id, db_setup.chat_id)

    async with db_setup.Session() as db:
        saved = await db.get(Turn, second.id)
        verdict = (
            await db.execute(select(Verdict).where(Verdict.turn_id == second.id))
        ).scalar_one_or_none()

    assert saved.status == TurnStatus.COMPLETED
    assert verdict is not None
