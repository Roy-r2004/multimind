import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, update
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
        self.cancel_attempted: set[str] = set()
        self.ignore_cancellation_models: set[str] = set()
        self.failing_models: set[str] = set()
        self.verdict_calls = 0

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096):
        self.started.setdefault(model, asyncio.Event()).set()
        if model in self.blocking_models:
            try:
                await self.release.setdefault(model, asyncio.Event()).wait()
            except asyncio.CancelledError:
                self.cancel_attempted.add(model)
                if model not in self.ignore_cancellation_models:
                    raise
        if user == "Produce the verdict JSON now.":
            self.verdict_calls += 1
            if model in self.failing_models:
                raise RuntimeError(f"{model} failed")
            return LLMResponse(
                text='{"text":"Final verdict","reason":"Because."}',
                tokens_input=10,
                tokens_output=5,
            )
        if model in self.failing_models:
            raise RuntimeError(f"{model} failed")
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

    await asyncio.gather(task, return_exceptions=True)
    assert task.done()
    assert task.cancelled()
    assert turn.id not in chat_service_module._orchestration_tasks


@pytest.mark.asyncio
async def test_delete_turn_cancels_local_task_only_after_delete_commit(db_setup, monkeypatch):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    sequence: list[str] = []

    class RecordingTask:
        def done(self):
            return False

        def cancel(self):
            sequence.append("cancel")

        def __await__(self):
            async def done():
                return None

            return done().__await__()

    task = RecordingTask()
    chat_service_module._orchestration_tasks[turn.id] = task

    async with db_setup.Session() as db:
        original_flush = db.flush
        original_commit = db.commit

        async def recording_flush(*args, **kwargs):
            sequence.append("flush")
            return await original_flush(*args, **kwargs)

        async def recording_commit():
            sequence.append("commit")
            return await original_commit()

        monkeypatch.setattr(db, "flush", recording_flush)
        monkeypatch.setattr(db, "commit", recording_commit)
        response = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)

    assert response.deleted is True
    assert sequence == ["flush", "commit", "cancel"]
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
    assert turn.id not in chat_service_module._orchestration_tasks


@pytest.mark.asyncio
async def test_delete_turn_does_not_wait_for_task_that_suppresses_cancellation(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    release_task = asyncio.Event()
    cancellation_seen = asyncio.Event()

    async def suppress_cancel_until_released():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancellation_seen.set()
            await release_task.wait()

    task = asyncio.create_task(suppress_cancel_until_released())
    chat_service_module._orchestration_tasks[turn.id] = task

    try:
        async with db_setup.Session() as db:
            response = await asyncio.wait_for(
                chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id),
                timeout=1,
            )

        assert response.deleted is True
        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        await asyncio.wait_for(cancellation_seen.wait(), timeout=1)
        assert not task.done()
        assert chat_service_module._orchestration_tasks[turn.id] is task

        release_task.set()
        await asyncio.wait_for(task, timeout=1)
        assert turn.id not in chat_service_module._orchestration_tasks
    finally:
        release_task.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        chat_service_module._orchestration_tasks.pop(turn.id, None)


@pytest.mark.asyncio
async def test_delete_turn_consumes_already_done_task_once_after_commit(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    class DoneTask:
        def __init__(self):
            self.result_calls = 0

        def done(self):
            return True

        def add_done_callback(self, callback):
            raise AssertionError("callback should not be registered for completed task")

        def cancel(self):
            raise AssertionError("completed task should not be cancelled")

        def result(self):
            self.result_calls += 1
            return None

    task = DoneTask()
    chat_service_module._orchestration_tasks[turn.id] = task

    async with db_setup.Session() as db:
        response = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)

    assert response.deleted is True
    assert task.result_calls == 1
    assert turn.id not in chat_service_module._orchestration_tasks


@pytest.mark.asyncio
async def test_delete_turn_does_not_cancel_or_remove_replacement_task_after_commit(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    class ControlledTask:
        def __init__(self):
            self.cancel_called = False
            self.done_value = False
            self.result_calls = 0
            self.callbacks = []

        def done(self):
            return self.done_value

        def cancel(self):
            self.cancel_called = True

        def add_done_callback(self, callback):
            self.callbacks.append(callback)

        def result(self):
            self.result_calls += 1
            if not self.done_value:
                raise AssertionError("result called before task completed")
            return None

    captured_task = ControlledTask()
    replacement_task = ControlledTask()
    chat_service_module._orchestration_tasks[turn.id] = captured_task

    async with db_setup.Session() as db:
        original_flush = db.flush

        async def replace_registry_during_delete(*args, **kwargs):
            chat_service_module._orchestration_tasks[turn.id] = replacement_task
            return await original_flush(*args, **kwargs)

        monkeypatch.setattr(db, "flush", replace_registry_during_delete)
        response = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)

    try:
        assert response.deleted is True
        assert captured_task.cancel_called is True
        assert replacement_task.cancel_called is False
        assert captured_task.result_calls == 0
        assert chat_service_module._orchestration_tasks[turn.id] is replacement_task

        captured_task.done_value = True
        captured_task.callbacks[0](captured_task)
        assert captured_task.result_calls == 1
        assert chat_service_module._orchestration_tasks[turn.id] is replacement_task
    finally:
        chat_service_module._orchestration_tasks.pop(turn.id, None)


@pytest.mark.asyncio
async def test_cross_worker_cancellation_stops_unregistered_orchestration(db_setup):
    provider_models = ["openai/gpt-4.1", "anthropic/claude-sonnet-4"]
    db_setup.provider.blocking_models.update(provider_models)
    started_events = [
        db_setup.provider.started.setdefault(model_id, asyncio.Event())
        for model_id in provider_models
    ]
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    task = asyncio.create_task(
        run_turn(db_setup.Session, db_setup.registry, db_setup.auth, turn.id, db_setup.chat_id)
    )
    try:
        for event in started_events:
            await asyncio.wait_for(event.wait(), timeout=5)

        assert turn.id not in chat_service_module._orchestration_tasks

        async with db_setup.Session() as db:
            await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)
            await db.commit()

        await asyncio.wait_for(task, timeout=5)

        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert db_setup.provider.cancel_attempted == set(provider_models)
        assert db_setup.provider.verdict_calls == 0
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_late_provider_result_after_cancel_request_does_not_persist(db_setup):
    provider_models = ["openai/gpt-4.1", "anthropic/claude-sonnet-4"]
    db_setup.provider.blocking_models.update(provider_models)
    db_setup.provider.ignore_cancellation_models.update(provider_models)
    started_events = [
        db_setup.provider.started.setdefault(model_id, asyncio.Event())
        for model_id in provider_models
    ]
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    task = asyncio.create_task(
        run_turn(db_setup.Session, db_setup.registry, db_setup.auth, turn.id, db_setup.chat_id)
    )
    try:
        for event in started_events:
            await asyncio.wait_for(event.wait(), timeout=5)

        async with db_setup.Session() as db:
            await db.execute(
                update(Turn)
                .where(Turn.id == turn.id)
                .values(cancel_requested_at=func.now())
            )
            await db.commit()

        await asyncio.wait_for(task, timeout=5)

        async with db_setup.Session() as db:
            saved = await db.get(Turn, turn.id)
            answers = (
                await db.execute(select(ModelAnswer).where(ModelAnswer.turn_id == turn.id))
            ).scalars().all()

        assert saved.status == TurnStatus.RUNNING
        assert saved.cancel_requested_at is not None
        assert db_setup.provider.cancel_attempted == set(provider_models)
        assert all(answer.status == ModelAnswerStatus.RUNNING for answer in answers)
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert db_setup.provider.verdict_calls == 0
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_delete_turn_does_not_cancel_local_task_when_delete_commit_fails(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper())
    chat_service_module._orchestration_tasks[turn.id] = task

    async with db_setup.Session() as db:
        async def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)

    assert not task.cancelled()
    assert chat_service_module._orchestration_tasks[turn.id] is task
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    chat_service_module._orchestration_tasks.pop(turn.id, None)


@pytest.mark.asyncio
async def test_delete_turn_does_not_cancel_local_task_when_delete_flush_fails(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper())
    chat_service_module._orchestration_tasks[turn.id] = task

    async with db_setup.Session() as db:
        async def fail_flush(*args, **kwargs):
            raise RuntimeError("flush failed")

        monkeypatch.setattr(db, "flush", fail_flush)
        with pytest.raises(RuntimeError, match="flush failed"):
            await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)

    assert not task.cancelled()
    assert chat_service_module._orchestration_tasks[turn.id] is task
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    chat_service_module._orchestration_tasks.pop(turn.id, None)


@pytest.mark.asyncio
async def test_delete_turn_success_survives_post_commit_local_cancel_failure(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    class FailingCancelTask:
        def done(self):
            return False

        def cancel(self):
            raise RuntimeError("local cancel failed")

        def __await__(self):
            async def done():
                return None

            return done().__await__()

    task = FailingCancelTask()
    chat_service_module._orchestration_tasks[turn.id] = task

    async with db_setup.Session() as db:
        response = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)

    assert response.deleted is True
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
    assert turn.id not in chat_service_module._orchestration_tasks


@pytest.mark.asyncio
async def test_missing_turn_row_is_treated_as_cancelled_by_orchestrator(db_setup):
    model_id = "openai/gpt-4.1"
    db_setup.provider.blocking_models.add(model_id)
    started_event = db_setup.provider.started.setdefault(model_id, asyncio.Event())
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    task = asyncio.create_task(
        run_turn(
            db_setup.Session,
            db_setup.registry,
            db_setup.auth,
            turn.id,
            db_setup.chat_id,
        )
    )
    try:
        await asyncio.wait_for(started_event.wait(), timeout=5)

        async with db_setup.Session() as db:
            await db.execute(delete(CostRecord).where(CostRecord.turn_id == turn.id))
            await db.execute(delete(ModelAnswer).where(ModelAnswer.turn_id == turn.id))
            await db.execute(delete(Turn).where(Turn.id == turn.id))
            await db.commit()

        await asyncio.wait_for(task, timeout=5)

        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert model_id in db_setup.provider.cancel_attempted
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_request_cannot_be_overwritten_by_verdict_completion(db_setup):
    verdict_model_id = "google/gemini-2.5-pro"
    db_setup.provider.blocking_models.add(verdict_model_id)
    verdict_started = db_setup.provider.started.setdefault(verdict_model_id, asyncio.Event())
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    task = asyncio.create_task(
        run_turn(db_setup.Session, db_setup.registry, db_setup.auth, turn.id, db_setup.chat_id)
    )
    try:
        await asyncio.wait_for(verdict_started.wait(), timeout=5)

        async with db_setup.Session() as db:
            await db.execute(
                update(Turn)
                .where(Turn.id == turn.id)
                .values(cancel_requested_at=func.now())
            )
            await db.commit()

        await asyncio.wait_for(task, timeout=5)

        async with db_setup.Session() as db:
            saved = await db.get(Turn, turn.id)

        assert saved.status == TurnStatus.RUNNING
        assert saved.cancel_requested_at is not None
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert verdict_model_id in db_setup.provider.cancel_attempted
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_provider_failure_after_cancel_request_does_not_mark_turn_failed(db_setup):
    provider_models = ["openai/gpt-4.1", "anthropic/claude-sonnet-4"]
    db_setup.provider.blocking_models.update(provider_models)
    db_setup.provider.ignore_cancellation_models.update(provider_models)
    db_setup.provider.failing_models.update(provider_models)
    started_events = [
        db_setup.provider.started.setdefault(model_id, asyncio.Event())
        for model_id in provider_models
    ]
    events: list[tuple[str, dict]] = []
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async def run_with_events():
        async with db_setup.Session() as db:
            orchestrator = TurnOrchestrator()
            orchestrator._providers = db_setup.registry

            async def on_event(event: str, data: dict):
                events.append((event, data))

            await orchestrator.run(
                db,
                TurnContext(
                    turn_id=turn.id,
                    chat_id=db_setup.chat_id,
                    org_id=db_setup.auth.org_id,
                    project_id=None,
                    user_message="Delete this prompt",
                    model_ids=["gpt-4.1", "claude"],
                    verdict_model_id="gemini",
                    strategy=Strategy.SYNTHESIZE,
                    model_set_name="Test Set",
                    skip_answer_seed=True,
                ),
                on_event=on_event,
            )
            await db.commit()

    task = asyncio.create_task(run_with_events())
    try:
        for event in started_events:
            await asyncio.wait_for(event.wait(), timeout=5)

        async with db_setup.Session() as db:
            await db.execute(
                update(Turn)
                .where(Turn.id == turn.id)
                .values(cancel_requested_at=func.now())
            )
            await db.commit()

        await asyncio.wait_for(task, timeout=5)

        async with db_setup.Session() as db:
            saved = await db.get(Turn, turn.id)

        assert saved.status == TurnStatus.RUNNING
        assert saved.cancel_requested_at is not None
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert all(event_name != "turn_failed" for event_name, _ in events)
        assert db_setup.provider.cancel_attempted == set(provider_models)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_answer_persistence_rechecks_after_cancel_between_precheck_and_write(
    db_setup, monkeypatch
):
    pause_before_lock = asyncio.Event()
    release_lock = asyncio.Event()
    original_lock = TurnOrchestrator._lock_active_turn_for_persistence

    async def delayed_lock(self, db, turn_id):
        if not pause_before_lock.is_set():
            pause_before_lock.set()
            await release_lock.wait()
        return await original_lock(self, db, turn_id)

    monkeypatch.setattr(TurnOrchestrator, "_lock_active_turn_for_persistence", delayed_lock)
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    task = asyncio.create_task(
        run_turn(db_setup.Session, db_setup.registry, db_setup.auth, turn.id, db_setup.chat_id)
    )
    try:
        await asyncio.wait_for(pause_before_lock.wait(), timeout=5)

        async with db_setup.Session() as db:
            await db.execute(
                update(Turn)
                .where(Turn.id == turn.id)
                .values(cancel_requested_at=func.now())
            )
            await db.commit()

        release_lock.set()
        await asyncio.wait_for(task, timeout=5)

        async with db_setup.Session() as db:
            saved = await db.get(Turn, turn.id)
            answers = (
                await db.execute(select(ModelAnswer).where(ModelAnswer.turn_id == turn.id))
            ).scalars().all()

        assert saved.status == TurnStatus.RUNNING
        assert saved.cancel_requested_at is not None
        assert all(answer.status == ModelAnswerStatus.RUNNING for answer in answers)
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
    finally:
        release_lock.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_verdict_persistence_rechecks_after_cancel_between_precheck_and_write(
    db_setup, monkeypatch
):
    pause_before_lock = asyncio.Event()
    release_lock = asyncio.Event()
    original_lock = TurnOrchestrator._lock_active_turn_for_persistence
    lock_calls = 0

    async def delayed_verdict_lock(self, db, turn_id):
        nonlocal lock_calls
        lock_calls += 1
        if lock_calls == 3:
            pause_before_lock.set()
            await release_lock.wait()
        return await original_lock(self, db, turn_id)

    monkeypatch.setattr(
        TurnOrchestrator, "_lock_active_turn_for_persistence", delayed_verdict_lock
    )
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    task = asyncio.create_task(
        run_turn(db_setup.Session, db_setup.registry, db_setup.auth, turn.id, db_setup.chat_id)
    )
    try:
        await asyncio.wait_for(pause_before_lock.wait(), timeout=5)

        async with db_setup.Session() as db:
            await db.execute(
                update(Turn)
                .where(Turn.id == turn.id)
                .values(cancel_requested_at=func.now())
            )
            await db.commit()

        release_lock.set()
        await asyncio.wait_for(task, timeout=5)

        async with db_setup.Session() as db:
            saved = await db.get(Turn, turn.id)

        assert saved.status == TurnStatus.RUNNING
        assert saved.cancel_requested_at is not None
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 2
    finally:
        release_lock.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_turn_failure_rechecks_after_cancel_between_precheck_and_write(
    db_setup, monkeypatch
):
    db_setup.provider.failing_models.update(["openai/gpt-4.1", "anthropic/claude-sonnet-4"])
    pause_before_lock = asyncio.Event()
    release_lock = asyncio.Event()
    original_lock = TurnOrchestrator._lock_active_turn_for_persistence
    lock_calls = 0

    async def delayed_turn_failure_lock(self, db, turn_id):
        nonlocal lock_calls
        lock_calls += 1
        if lock_calls == 3:
            pause_before_lock.set()
            await release_lock.wait()
        return await original_lock(self, db, turn_id)

    monkeypatch.setattr(
        TurnOrchestrator, "_lock_active_turn_for_persistence", delayed_turn_failure_lock
    )
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    task = asyncio.create_task(
        run_turn(db_setup.Session, db_setup.registry, db_setup.auth, turn.id, db_setup.chat_id)
    )
    try:
        await asyncio.wait_for(pause_before_lock.wait(), timeout=5)

        async with db_setup.Session() as db:
            await db.execute(
                update(Turn)
                .where(Turn.id == turn.id)
                .values(cancel_requested_at=func.now())
            )
            await db.commit()

        release_lock.set()
        await asyncio.wait_for(task, timeout=5)

        async with db_setup.Session() as db:
            saved = await db.get(Turn, turn.id)

        assert saved.status == TurnStatus.RUNNING
        assert saved.cancel_requested_at is not None
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
    finally:
        release_lock.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_conditional_completion_update_loses_to_cancel_request(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async with db_setup.Session() as db:
        await db.execute(
            update(Turn)
            .where(Turn.id == turn.id)
            .values(status=TurnStatus.RUNNING, cancel_requested_at=func.now())
        )
        await db.commit()

    async with db_setup.Session() as db:
        completed = await db.execute(
            update(Turn)
            .where(
                Turn.id == turn.id,
                Turn.cancel_requested_at.is_(None),
                Turn.status.in_((TurnStatus.PENDING, TurnStatus.RUNNING)),
            )
            .values(status=TurnStatus.COMPLETED)
        )
        await db.commit()

    async with db_setup.Session() as db:
        saved = await db.get(Turn, turn.id)

    assert completed.rowcount == 0
    assert saved.status == TurnStatus.RUNNING
    assert saved.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_orchestration_task_cleanup_does_not_remove_newer_registered_task(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async def sleeper():
        await asyncio.sleep(60)

    old_task = asyncio.create_task(sleeper())
    newer_task = asyncio.create_task(sleeper())
    chat_service_module._orchestration_tasks[turn.id] = newer_task
    chat_service_module._discard_orchestration_task(turn.id, old_task)

    try:
        assert chat_service_module._orchestration_tasks[turn.id] is newer_task
    finally:
        old_task.cancel()
        newer_task.cancel()
        await asyncio.gather(old_task, newer_task, return_exceptions=True)
        chat_service_module._orchestration_tasks.pop(turn.id, None)


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
