import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1 import chats as chats_api_module
from app.core.dependencies import AuthContext
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.base import Base
from app.db import session as db_session_module
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
    MODEL_TRACKING_ALIASES = {
        "gpt-4.1": "openai/gpt-4.1",
        "openai/gpt-4.1": "openai/gpt-4.1",
        "claude": "anthropic/claude-sonnet-4",
        "anthropic/claude-sonnet-4": "anthropic/claude-sonnet-4",
        "gemini": "google/gemini-2.5-pro",
        "google/gemini-2.5-pro": "google/gemini-2.5-pro",
    }

    def __init__(self) -> None:
        self.started: dict[str, asyncio.Event] = {}
        self.release: dict[str, asyncio.Event] = {}
        self.blocking_models: set[str] = set()
        self.cancel_attempted: set[str] = set()
        self.ignore_cancellation_models: set[str] = set()
        self.failing_models: set[str] = set()
        self.failure_messages: dict[str, str] = {}
        self.calls: list[str] = []
        self.verdict_calls = 0

    def tracking_model_id(self, model: str) -> str:
        return self.MODEL_TRACKING_ALIASES.get(model, model)

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096):
        tracking_id = self.tracking_model_id(model)
        self.started.setdefault(tracking_id, asyncio.Event()).set()
        self.calls.append(tracking_id)
        if tracking_id in self.blocking_models:
            try:
                await self.release.setdefault(tracking_id, asyncio.Event()).wait()
            except asyncio.CancelledError:
                self.cancel_attempted.add(tracking_id)
                if tracking_id not in self.ignore_cancellation_models:
                    raise
        if user == "Produce the verdict JSON now.":
            self.verdict_calls += 1
            if tracking_id in self.failing_models:
                raise RuntimeError(
                    self.failure_messages.get(tracking_id, f"{tracking_id} failed")
                )
            return LLMResponse(
                text='{"text":"Final verdict","reason":"Because."}',
                tokens_input=10,
                tokens_output=5,
            )
        if tracking_id in self.failing_models:
            raise RuntimeError(
                self.failure_messages.get(tracking_id, f"{tracking_id} failed")
            )
        return LLMResponse(
            text=f"Answer from {tracking_id}",
            tokens_input=10,
            tokens_output=5,
            confidence=90,
        )

    def parse_json_response(self, text: str):
        return {"text": "Final verdict", "reason": "Because."}


class FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider
        self.provider_names: list[str] = []

    def get_provider(self, _provider_name: str) -> FakeProvider:
        self.provider_names.append(_provider_name)
        return self.provider


def make_test_orchestrator(registry: FakeRegistry) -> TurnOrchestrator:
    orchestrator = TurnOrchestrator()
    orchestrator._providers = registry
    return orchestrator


async def cleanup_orchestration_tasks() -> None:
    tasks = list(chat_service_module._orchestration_tasks.values())
    awaitable_tasks = []
    for task in tasks:
        if hasattr(task, "done") and not task.done() and hasattr(task, "cancel"):
            task.cancel()
        if isinstance(task, asyncio.Task):
            awaitable_tasks.append(task)
    if awaitable_tasks:
        await asyncio.gather(*awaitable_tasks, return_exceptions=True)
    chat_service_module._orchestration_tasks.clear()


@pytest.fixture
async def db_setup(tmp_path, monkeypatch):
    db_path = tmp_path / "turn-delete.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(chat_service_module, "AsyncSessionLocal", Session)
    monkeypatch.setattr(chats_api_module, "AsyncSessionLocal", Session)
    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", Session)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        org = Organization(name="Org", slug="org")
        other_org = Organization(name="Other", slug="other")
        user = User(email="u@example.com", hashed_password="x", full_name="User")
        admin = User(email="admin@example.com", hashed_password="x", full_name="Admin")
        owner = User(email="owner@example.com", hashed_password="x", full_name="Owner")
        viewer = User(email="viewer@example.com", hashed_password="x", full_name="Viewer")
        other_member = User(
            email="other-member@example.com",
            hashed_password="x",
            full_name="Other Member",
        )
        db.add_all([org, other_org, user, admin, owner, viewer, other_member])
        await db.flush()
        db.add_all(
            [
                OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.MEMBER),
                OrgMembership(org_id=org.id, user_id=admin.id, role=OrgRole.ADMIN),
                OrgMembership(org_id=org.id, user_id=owner.id, role=OrgRole.OWNER),
                OrgMembership(org_id=org.id, user_id=viewer.id, role=OrgRole.VIEWER),
                OrgMembership(org_id=org.id, user_id=other_member.id, role=OrgRole.MEMBER),
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
        viewer_chat = Chat(org_id=org.id, created_by=viewer.id, title="Viewer chat")
        other_chat = Chat(org_id=other_org.id, created_by=user.id, title="Other")
        db.add_all([model_set, chat, viewer_chat, other_chat])
        await db.commit()

    provider = FakeProvider()
    registry = FakeRegistry(provider)
    monkeypatch.setattr(
        chat_service_module,
        "get_orchestrator",
        lambda: make_test_orchestrator(registry),
    )
    try:
        yield SimpleNamespace(
            engine=engine,
            Session=Session,
            provider=provider,
            registry=registry,
            auth=AuthContext(user=user, org_id=org.id, role=OrgRole.MEMBER),
            admin_auth=AuthContext(user=admin, org_id=org.id, role=OrgRole.ADMIN),
            owner_auth=AuthContext(user=owner, org_id=org.id, role=OrgRole.OWNER),
            viewer_auth=AuthContext(user=viewer, org_id=org.id, role=OrgRole.VIEWER),
            other_member_auth=AuthContext(
                user=other_member,
                org_id=org.id,
                role=OrgRole.MEMBER,
            ),
            other_auth=AuthContext(user=user, org_id=other_org.id, role=OrgRole.MEMBER),
            chat_id=chat.id,
            viewer_chat_id=viewer_chat.id,
            other_chat_id=other_chat.id,
        )
    finally:
        await cleanup_orchestration_tasks()
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
        await db.execute(
            update(Turn)
            .where(
                Turn.id == turn_id,
                Turn.status == TurnStatus.PENDING,
            )
            .values(status=TurnStatus.RUNNING)
        )
        await db.commit()
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


async def hard_delete_turn_rows(Session, turn_id: str) -> None:
    async with Session() as db:
        await db.execute(delete(CostRecord).where(CostRecord.turn_id == turn_id))
        await db.execute(delete(DecisionInsurance).where(DecisionInsurance.turn_id == turn_id))
        await db.execute(delete(Verdict).where(Verdict.turn_id == turn_id))
        await db.execute(delete(ModelAnswer).where(ModelAnswer.turn_id == turn_id))
        await db.execute(delete(Turn).where(Turn.id == turn_id))
        await db.commit()


async def first_stream_event(Session, auth, turn_id: str):
    async with Session() as db:
        stream = chat_service.execute_turn_stream(db, auth, turn_id)
        try:
            return await stream.__anext__()
        finally:
            await stream.aclose()


async def collect_stream_events(Session, auth, turn_id: str, limit: int = 10):
    events = []
    async with Session() as db:
        stream = chat_service.execute_turn_stream(db, auth, turn_id)
        try:
            async for event in stream:
                events.append(event)
                if len(events) >= limit or event["type"] in {
                    "error",
                    "turn_failed",
                    "turn_deleted",
                    "turn_completed",
                }:
                    break
        finally:
            await stream.aclose()
    return events


async def claim_turn(Session, turn_id: str) -> int:
    async with Session() as db:
        result = await db.execute(
            update(Turn)
            .where(
                Turn.id == turn_id,
                Turn.status == TurnStatus.PENDING,
            )
            .values(status=TurnStatus.RUNNING, error_message=None)
        )
        await db.commit()
        return result.rowcount


def test_fake_provider_tracking_model_id_normalizes_aliases():
    provider = FakeProvider()

    assert provider.tracking_model_id("gpt-4.1") == provider.tracking_model_id(
        "openai/gpt-4.1"
    )
    assert provider.tracking_model_id("claude") == provider.tracking_model_id(
        "anthropic/claude-sonnet-4"
    )


def test_fake_registry_returns_shared_provider_for_all_model_families(db_setup):
    assert db_setup.registry.get_provider("openai") is db_setup.provider
    assert db_setup.registry.get_provider("anthropic") is db_setup.provider
    assert db_setup.registry.get_provider("google") is db_setup.provider


def test_turn_status_mapping_uses_varchar_backed_enum():
    status_type = Turn.__table__.c.status.type

    assert status_type.native_enum is False
    assert list(status_type.enums) == [status.name for status in TurnStatus]


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
async def test_viewer_cannot_delete_or_stop_turn_and_has_no_side_effects(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    async with db_setup.Session() as db:
        await db.execute(
            update(Turn).where(Turn.id == turn.id).values(status=TurnStatus.RUNNING)
        )
        await db.commit()

    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper())
    chat_service_module._orchestration_tasks[turn.id] = task

    try:
        async with db_setup.Session() as db:
            with pytest.raises(ForbiddenError) as exc_info:
                await chat_service.delete_turn(
                    db,
                    db_setup.viewer_auth,
                    db_setup.chat_id,
                    turn.id,
                )
            await db.rollback()

        assert exc_info.value.message == "You do not have permission to delete this turn."
        assert exc_info.value.code == "FORBIDDEN"
        assert chat_service_module._orchestration_tasks.get(turn.id) is task
        assert not task.cancelled()
        async with db_setup.Session() as db:
            saved_turn = await db.get(Turn, turn.id)
            assert saved_turn is not None
            assert saved_turn.status == TurnStatus.RUNNING
    finally:
        chat_service_module._discard_orchestration_task(turn.id, task)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_delete_turn_route_denies_same_org_viewer(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async with db_setup.Session() as db:
        with pytest.raises(ForbiddenError) as exc_info:
            await chats_api_module.delete_turn(
                UUID(db_setup.chat_id),
                UUID(turn.id),
                db_setup.viewer_auth,
                db,
            )
        await db.rollback()

    assert exc_info.value.message == "You do not have permission to delete this turn."
    assert exc_info.value.code == "FORBIDDEN"
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 1


@pytest.mark.asyncio
async def test_viewer_cannot_delete_own_created_chat_turn(db_setup):
    async with db_setup.Session() as db:
        turn = Turn(
            chat_id=db_setup.viewer_chat_id,
            user_message="Viewer-owned prompt",
            model_set_id="test-set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.PENDING,
        )
        db.add(turn)
        await db.commit()

    async with db_setup.Session() as db:
        with pytest.raises(ForbiddenError) as exc_info:
            await chat_service.delete_turn(
                db,
                db_setup.viewer_auth,
                db_setup.viewer_chat_id,
                turn.id,
            )
        await db.rollback()

    assert exc_info.value.message == "You do not have permission to delete this turn."
    assert exc_info.value.code == "FORBIDDEN"
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 1


@pytest.mark.asyncio
async def test_non_creator_member_cannot_delete_another_members_turn(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async with db_setup.Session() as db:
        with pytest.raises(ForbiddenError) as exc_info:
            await chat_service.delete_turn(
                db,
                db_setup.other_member_auth,
                db_setup.chat_id,
                turn.id,
            )
        await db.rollback()

    assert exc_info.value.message == "You do not have permission to delete this turn."
    assert exc_info.value.code == "FORBIDDEN"
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_name", ["admin_auth", "owner_auth"])
async def test_admin_and_owner_can_delete_any_org_turn(db_setup, auth_name):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    auth = getattr(db_setup, auth_name)

    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper())
    chat_service_module._orchestration_tasks[turn.id] = task

    try:
        async with db_setup.Session() as db:
            response = await chat_service.delete_turn(db, auth, db_setup.chat_id, turn.id)
            await db.commit()

        await asyncio.sleep(0)

        assert response.deleted is True
        assert task.cancelled()
        assert chat_service_module._orchestration_tasks.get(turn.id) is not task
        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
    finally:
        chat_service_module._discard_orchestration_task(turn.id, task)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
async def test_concurrent_stream_starts_create_one_orchestration_task_and_provider_run(db_setup):
    provider_models = [
        db_setup.provider.tracking_model_id("gpt-4.1"),
        db_setup.provider.tracking_model_id("claude"),
    ]
    db_setup.provider.blocking_models.update(provider_models)
    started_events = [
        db_setup.provider.started.setdefault(model_id, asyncio.Event())
        for model_id in provider_models
    ]
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    first = asyncio.create_task(first_stream_event(db_setup.Session, db_setup.auth, turn.id))
    second = asyncio.create_task(first_stream_event(db_setup.Session, db_setup.auth, turn.id))
    events = await asyncio.gather(first, second)

    try:
        assert [event["type"] for event in events].count("turn_started") == 1
        duplicate_events = [event for event in events if event["type"] == "error"]
        assert len(duplicate_events) == 1
        assert duplicate_events[0]["data"]["code"] == "TURN_ALREADY_RUNNING"
        assert turn.id in chat_service_module._orchestration_tasks
        winning_task = chat_service_module._orchestration_tasks[turn.id]
        assert not winning_task.done()

        for event in started_events:
            await asyncio.wait_for(event.wait(), timeout=5)
        assert sorted(db_setup.provider.calls) == sorted(provider_models)
    finally:
        async with db_setup.Session() as db:
            await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)


@pytest.mark.asyncio
async def test_cross_worker_atomic_claim_allows_one_winner(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    rowcounts = await asyncio.gather(
        claim_turn(db_setup.Session, turn.id),
        claim_turn(db_setup.Session, turn.id),
    )

    assert sorted(rowcounts) == [0, 1]


@pytest.mark.asyncio
async def test_high_contention_stream_starts_produce_one_provider_run(db_setup):
    provider_models = [
        db_setup.provider.tracking_model_id("gpt-4.1"),
        db_setup.provider.tracking_model_id("claude"),
    ]
    db_setup.provider.blocking_models.update(provider_models)
    started_events = [
        db_setup.provider.started.setdefault(model_id, asyncio.Event())
        for model_id in provider_models
    ]
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    events = await asyncio.gather(
        *(first_stream_event(db_setup.Session, db_setup.auth, turn.id) for _ in range(5))
    )

    try:
        assert [event["type"] for event in events].count("turn_started") == 1
        duplicate_events = [event for event in events if event["type"] == "error"]
        assert len(duplicate_events) == 4
        assert all(event["data"]["code"] == "TURN_ALREADY_RUNNING" for event in duplicate_events)

        for event in started_events:
            await asyncio.wait_for(event.wait(), timeout=5)
        assert sorted(db_setup.provider.calls) == sorted(provider_models)
    finally:
        async with db_setup.Session() as db:
            await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)


@pytest.mark.asyncio
async def test_stream_start_never_overwrites_live_registry_task(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async def sleeper():
        await asyncio.sleep(60)

    live_task = asyncio.create_task(sleeper())
    chat_service_module._orchestration_tasks[turn.id] = live_task

    try:
        event = await first_stream_event(db_setup.Session, db_setup.auth, turn.id)

        assert event["type"] == "error"
        assert event["data"]["code"] == "TURN_ALREADY_RUNNING"
        assert chat_service_module._orchestration_tasks[turn.id] is live_task
        assert not live_task.cancelled()
        assert db_setup.provider.calls == []
        async with db_setup.Session() as db:
            saved = await db.get(Turn, turn.id)
        assert saved.status == TurnStatus.RUNNING
    finally:
        live_task.cancel()
        await asyncio.gather(live_task, return_exceptions=True)
        chat_service_module._orchestration_tasks.pop(turn.id, None)


@pytest.mark.asyncio
async def test_stream_start_removes_stale_completed_registry_task(db_setup):
    db_setup.provider.blocking_models.add(db_setup.provider.tracking_model_id("gpt-4.1"))
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    stale_task = asyncio.create_task(asyncio.sleep(0))
    await stale_task
    chat_service_module._orchestration_tasks[turn.id] = stale_task

    event = await first_stream_event(db_setup.Session, db_setup.auth, turn.id)

    try:
        assert event["type"] == "turn_started"
        assert chat_service_module._orchestration_tasks[turn.id] is not stale_task
        assert not chat_service_module._orchestration_tasks[turn.id].done()
    finally:
        async with db_setup.Session() as db:
            await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)


@pytest.mark.asyncio
async def test_stream_start_after_deletion_returns_deleted_without_provider_call(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    async with db_setup.Session() as db:
        await db.execute(delete(ModelAnswer).where(ModelAnswer.turn_id == turn.id))
        await db.execute(delete(Turn).where(Turn.id == turn.id))
        await db.commit()

    event = await first_stream_event(db_setup.Session, db_setup.auth, turn.id)

    assert event["type"] == "turn_deleted"
    assert turn.id not in chat_service_module._orchestration_tasks
    assert db_setup.provider.calls == []


@pytest.mark.asyncio
async def test_stream_start_recovers_claim_when_task_creation_fails(db_setup, monkeypatch):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    def fail_create_task(coroutine):
        raise RuntimeError("create task failed")

    monkeypatch.setattr(chat_service_module, "_create_orchestration_task", fail_create_task)

    event = await first_stream_event(db_setup.Session, db_setup.auth, turn.id)

    assert event["type"] == "error"
    assert event["data"]["code"] == "TURN_START_FAILED"
    assert turn.id not in chat_service_module._orchestration_tasks
    assert db_setup.provider.calls == []
    async with db_setup.Session() as db:
        saved = await db.get(Turn, turn.id)
    assert saved.status == TurnStatus.FAILED


@pytest.mark.asyncio
async def test_stream_start_recovers_claim_when_request_is_cancelled_after_commit(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async with db_setup.Session() as db:
        original_commit = db.commit
        commit_calls = 0

        async def cancel_after_claim_commit():
            nonlocal commit_calls
            commit_calls += 1
            await original_commit()
            if commit_calls == 1:
                raise asyncio.CancelledError()

        monkeypatch.setattr(db, "commit", cancel_after_claim_commit)
        stream = chat_service.execute_turn_stream(db, db_setup.auth, turn.id)
        with pytest.raises(asyncio.CancelledError):
            await stream.__anext__()
        await stream.aclose()

    assert turn.id not in chat_service_module._orchestration_tasks
    assert db_setup.provider.calls == []
    async with db_setup.Session() as db:
        saved = await db.get(Turn, turn.id)
    assert saved.status == TurnStatus.FAILED


@pytest.mark.asyncio
async def test_stream_start_immediate_orchestration_failure_cleans_registry_and_marks_failed(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    sensitive = "internal filesystem path /srv/app/private/config.py"

    class FailingOrchestrator:
        async def run(self, db, ctx, on_event=None):
            raise RuntimeError(sensitive)

    monkeypatch.setattr(
        chat_service_module, "get_orchestrator", lambda: FailingOrchestrator()
    )

    event = await first_stream_event(db_setup.Session, db_setup.auth, turn.id)

    assert event["type"] == "error"
    assert event["data"]["code"] == "TURN_STREAM_INTERNAL_ERROR"
    assert event["data"]["message"] == "An unexpected error occurred while processing the turn."
    assert sensitive not in json.dumps(event)
    assert turn.id not in chat_service_module._orchestration_tasks
    async with db_setup.Session() as db:
        saved = await db.get(Turn, turn.id)
    assert saved.status == TurnStatus.FAILED
    assert db_setup.provider.calls == []


@pytest.mark.asyncio
async def test_stream_provider_failure_event_does_not_leak_provider_details(db_setup):
    sensitive = (
        "provider body token sk-secret endpoint https://provider.internal/v1 "
        "postgresql://user:secret@internal-db:5432/app"
    )
    provider_models = [
        db_setup.provider.tracking_model_id("gpt-4.1"),
        db_setup.provider.tracking_model_id("claude"),
    ]
    db_setup.provider.failing_models.update(provider_models)
    db_setup.provider.failure_messages.update({model: sensitive for model in provider_models})
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    events = await collect_stream_events(db_setup.Session, db_setup.auth, turn.id, limit=10)
    serialized = json.dumps(events)

    assert any(event["type"] == "model_answer_failed" for event in events)
    assert any(event["type"] == "turn_failed" for event in events)
    assert "MODEL_ANSWER_FAILED" in serialized
    assert "TURN_FAILED" in serialized
    assert sensitive not in serialized
    assert "sk-secret" not in serialized
    assert "internal-db" not in serialized


@pytest.mark.asyncio
async def test_stream_failed_turn_event_does_not_expose_persisted_internal_error(db_setup):
    sensitive = "SELECT * FROM private_table at postgresql://user:secret@internal-db:5432/app"
    async with db_setup.Session() as db:
        turn = Turn(
            chat_id=db_setup.chat_id,
            user_message="Failed sensitive",
            model_set_id="test-set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.FAILED,
            error_message=sensitive,
        )
        db.add(turn)
        await db.commit()

    events = await collect_stream_events(db_setup.Session, db_setup.auth, turn.id, limit=2)
    serialized = json.dumps(events)

    assert events[0]["type"] == "turn_failed"
    assert events[0]["data"]["code"] == "TURN_FAILED"
    assert events[0]["data"]["error"] == "Turn failed."
    assert sensitive not in serialized
    assert "private_table" not in serialized
    assert "internal-db" not in serialized


@pytest.mark.asyncio
async def test_stream_route_generator_error_uses_safe_payload(monkeypatch, db_setup):
    sensitive = (
        "connection refused at internal-db SELECT * FROM private_table "
        "postgresql://user:secret@internal-db:5432/app"
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def commit(self):
            return None

        async def rollback(self):
            return None

    class FakeSessionLocal:
        def __call__(self):
            return FakeSession()

    async def leaking_stream(db, auth, turn_id):
        raise RuntimeError(sensitive)
        yield

    monkeypatch.setattr(chats_api_module, "AsyncSessionLocal", FakeSessionLocal())
    monkeypatch.setattr(chats_api_module.chat_service, "execute_turn_stream", leaking_stream)

    response = await chats_api_module.stream_turn(UUID(db_setup.chat_id), db_setup.auth)
    chunk = await response.body_iterator.__anext__()
    body = chunk.decode() if isinstance(chunk, bytes) else chunk

    assert "event: error" in body
    assert "TURN_STREAM_INTERNAL_ERROR" in body
    assert "An unexpected error occurred while processing the turn." in body
    assert sensitive not in body
    assert "private_table" not in body
    assert "internal-db" not in body


@pytest.mark.asyncio
async def test_stream_cancelled_error_keeps_deleted_event(db_setup, monkeypatch):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    class CancelledOrchestrator:
        async def run(self, db, ctx, on_event=None):
            raise asyncio.CancelledError()

    monkeypatch.setattr(
        chat_service_module, "get_orchestrator", lambda: CancelledOrchestrator()
    )

    event = await first_stream_event(db_setup.Session, db_setup.auth, turn.id)

    assert event["type"] == "turn_deleted"


@pytest.mark.asyncio
async def test_duplicate_start_keeps_stable_public_error(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    async with db_setup.Session() as db:
        await db.execute(
            update(Turn).where(Turn.id == turn.id).values(status=TurnStatus.RUNNING)
        )
        await db.commit()

    event = await first_stream_event(db_setup.Session, db_setup.auth, turn.id)

    assert event == {
        "type": "error",
        "data": {
            "code": "TURN_ALREADY_RUNNING",
            "message": "Turn orchestration is already running.",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [TurnStatus.COMPLETED, TurnStatus.PARTIAL, TurnStatus.FAILED]
)
async def test_stream_start_for_terminal_turn_does_not_create_task_or_provider_call(
    db_setup, status
):
    async with db_setup.Session() as db:
        turn = Turn(
            chat_id=db_setup.chat_id,
            user_message=f"Terminal {status.value}",
            model_set_id="test-set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=status,
        )
        db.add(turn)
        await db.commit()

    event = await first_stream_event(db_setup.Session, db_setup.auth, turn.id)

    assert event["type"] in {"turn_completed", "turn_failed"}
    assert turn.id not in chat_service_module._orchestration_tasks
    assert db_setup.provider.calls == []


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
async def test_delete_chat_cancels_active_turn_task_only_after_delete_commit(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    sequence: list[str] = []

    class RecordingTask:
        def __init__(self):
            self.callbacks = []
            self.done_value = False

        def done(self):
            return self.done_value

        def cancel(self):
            sequence.append("cancel")

        def add_done_callback(self, callback):
            self.callbacks.append(callback)

        def result(self):
            return None

    task = RecordingTask()
    chat_service_module._orchestration_tasks[turn.id] = task

    async with db_setup.Session() as db:
        db.add(
            SavedVerdict(
                org_id=db_setup.auth.org_id,
                user_id=db_setup.auth.user.id,
                source_verdict_id="durable-source-verdict",
                source_turn_id=turn.id,
                source_chat_id=db_setup.chat_id,
                source_chat_title="Snapshot chat",
                source_user_message="Snapshot prompt",
                verdict_text="Snapshot verdict",
                verdict_reason="Snapshot reason",
                verdict_model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
            )
        )
        await db.commit()

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
        await chat_service.delete_chat(db, db_setup.auth, db_setup.chat_id)

    try:
        assert sequence == ["flush", "commit", "cancel"]
        assert await count_rows(db_setup.Session, Chat, id=db_setup.chat_id) == 0
        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, SavedVerdict, source_turn_id=turn.id) == 1

        assert chat_service_module._orchestration_tasks[turn.id] is task
        task.done_value = True
        task.callbacks[0](task)
        assert turn.id not in chat_service_module._orchestration_tasks
    finally:
        chat_service_module._orchestration_tasks.pop(turn.id, None)


@pytest.mark.asyncio
async def test_delete_chat_commit_failure_preserves_active_turn_and_task(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    async with db_setup.Session() as db:
        await db.execute(
            update(Turn).where(Turn.id == turn.id).values(status=TurnStatus.RUNNING)
        )
        await db.commit()

    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper())
    chat_service_module._orchestration_tasks[turn.id] = task

    try:
        async with db_setup.Session() as db:
            async def fail_commit():
                raise RuntimeError("commit failed")

            monkeypatch.setattr(db, "commit", fail_commit)
            with pytest.raises(RuntimeError, match="commit failed"):
                await chat_service.delete_chat(db, db_setup.auth, db_setup.chat_id)

        assert not task.cancelled()
        assert chat_service_module._orchestration_tasks[turn.id] is task
        assert await count_rows(db_setup.Session, Chat, id=db_setup.chat_id) == 1
        async with db_setup.Session() as db:
            saved_turn = await db.get(Turn, turn.id)
            assert saved_turn is not None
            assert saved_turn.status == TurnStatus.RUNNING
    finally:
        chat_service_module._orchestration_tasks.pop(turn.id, None)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_delete_chat_during_unregistered_orchestration_discards_late_provider_result(
    db_setup
):
    provider_models = [
        db_setup.provider.tracking_model_id("gpt-4.1"),
        db_setup.provider.tracking_model_id("claude"),
    ]
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
            db.add(
                SavedVerdict(
                    org_id=db_setup.auth.org_id,
                    user_id=db_setup.auth.user.id,
                    source_verdict_id="saved-before-chat-delete",
                    source_turn_id=turn.id,
                    source_chat_id=db_setup.chat_id,
                    source_chat_title="Snapshot chat",
                    source_user_message="Snapshot prompt",
                    verdict_text="Snapshot verdict",
                    verdict_reason="Snapshot reason",
                    verdict_model_id="gemini",
                    strategy=Strategy.SYNTHESIZE,
                )
            )
            await db.commit()

        async with db_setup.Session() as db:
            await chat_service.delete_chat(db, db_setup.auth, db_setup.chat_id)

        await asyncio.wait_for(task, timeout=5)

        assert await count_rows(db_setup.Session, Chat, id=db_setup.chat_id) == 0
        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, SavedVerdict, source_turn_id=turn.id) == 1
        assert db_setup.provider.cancel_attempted == set(provider_models)
        assert db_setup.provider.verdict_calls == 0
        assert turn.id not in chat_service_module._orchestration_tasks
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cross_worker_cancellation_stops_unregistered_orchestration(db_setup):
    provider_models = [
        db_setup.provider.tracking_model_id("gpt-4.1"),
        db_setup.provider.tracking_model_id("claude"),
    ]
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
async def test_late_provider_result_after_turn_deletion_does_not_persist(db_setup):
    provider_models = [
        db_setup.provider.tracking_model_id("gpt-4.1"),
        db_setup.provider.tracking_model_id("claude"),
    ]
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

        await hard_delete_turn_rows(db_setup.Session, turn.id)

        await asyncio.wait_for(task, timeout=5)

        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0
        assert db_setup.provider.cancel_attempted == set(provider_models)
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
    async with db_setup.Session() as db:
        saved_turn = await db.get(Turn, turn.id)
        assert saved_turn.status == TurnStatus.PENDING
        retry = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)
    assert retry.deleted is True
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
    await asyncio.gather(task, return_exceptions=True)


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
    async with db_setup.Session() as db:
        saved_turn = await db.get(Turn, turn.id)
        assert saved_turn.status == TurnStatus.PENDING
        retry = await chat_service.delete_turn(db, db_setup.auth, db_setup.chat_id, turn.id)
    assert retry.deleted is True
    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
    await asyncio.gather(task, return_exceptions=True)


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
async def test_missing_turn_row_is_treated_as_deleted_by_orchestrator(db_setup):
    model_id = db_setup.provider.tracking_model_id("gpt-4.1")
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
async def test_turn_deletion_cannot_be_overwritten_by_verdict_completion(db_setup):
    verdict_model_id = db_setup.provider.tracking_model_id("gemini")
    db_setup.provider.blocking_models.add(verdict_model_id)
    verdict_started = db_setup.provider.started.setdefault(verdict_model_id, asyncio.Event())
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    task = asyncio.create_task(
        run_turn(db_setup.Session, db_setup.registry, db_setup.auth, turn.id, db_setup.chat_id)
    )
    try:
        await asyncio.wait_for(verdict_started.wait(), timeout=5)

        await hard_delete_turn_rows(db_setup.Session, turn.id)

        await asyncio.wait_for(task, timeout=5)

        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert verdict_model_id in db_setup.provider.cancel_attempted
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_provider_failure_after_turn_deletion_does_not_mark_turn_failed(db_setup):
    provider_models = [
        db_setup.provider.tracking_model_id("gpt-4.1"),
        db_setup.provider.tracking_model_id("claude"),
    ]
    db_setup.provider.blocking_models.update(provider_models)
    db_setup.provider.ignore_cancellation_models.update(provider_models)
    db_setup.provider.failing_models.update(provider_models)
    started_events = [
        db_setup.provider.started.setdefault(model_id, asyncio.Event())
        for model_id in provider_models
    ]
    events: list[tuple[str, dict]] = []
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    assert await claim_turn(db_setup.Session, turn.id) == 1

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

        await hard_delete_turn_rows(db_setup.Session, turn.id)

        await asyncio.wait_for(task, timeout=5)

        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert all(event_name != "turn_failed" for event_name, _ in events)
        assert db_setup.provider.cancel_attempted == set(provider_models)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_answer_persistence_rechecks_after_delete_between_precheck_and_write(
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

        await hard_delete_turn_rows(db_setup.Session, turn.id)

        release_lock.set()
        await asyncio.wait_for(task, timeout=5)

        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
    finally:
        release_lock.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_verdict_persistence_rechecks_after_delete_between_precheck_and_write(
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

        await hard_delete_turn_rows(db_setup.Session, turn.id)

        release_lock.set()
        await asyncio.wait_for(task, timeout=5)

        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
    finally:
        release_lock.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_turn_failure_rechecks_after_delete_between_precheck_and_write(
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

        await hard_delete_turn_rows(db_setup.Session, turn.id)

        release_lock.set()
        await asyncio.wait_for(task, timeout=5)

        assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
        assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0
        assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
    finally:
        release_lock.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_answer_cost_integrity_error_after_delete_is_discarded(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    async with db_setup.Session() as db:
        await db.execute(
            update(Turn).where(Turn.id == turn.id).values(status=TurnStatus.RUNNING)
        )
        await db.commit()

    async def run_with_commit_race():
        async with db_setup.Session() as db:
            original_commit = db.commit
            original_rollback = db.rollback
            commit_calls = 0
            rollback_calls = 0

            async def fail_answer_cost_commit_after_delete():
                nonlocal commit_calls
                commit_calls += 1
                if commit_calls == 2:
                    raise IntegrityError("cost insert raced delete", None, None)
                await original_commit()

            async def rollback_then_delete_turn():
                nonlocal rollback_calls
                rollback_calls += 1
                await original_rollback()
                if rollback_calls == 1:
                    await hard_delete_turn_rows(db_setup.Session, turn.id)

            monkeypatch.setattr(db, "commit", fail_answer_cost_commit_after_delete)
            monkeypatch.setattr(db, "rollback", rollback_then_delete_turn)
            orchestrator = TurnOrchestrator()
            orchestrator._providers = db_setup.registry
            await orchestrator.run(
                db,
                TurnContext(
                    turn_id=turn.id,
                    chat_id=db_setup.chat_id,
                    org_id=db_setup.auth.org_id,
                    project_id=None,
                    user_message="Delete this prompt",
                    model_ids=["gpt-4.1"],
                    verdict_model_id="gemini",
                    strategy=Strategy.SYNTHESIZE,
                    model_set_name="Test Set",
                    skip_answer_seed=True,
                ),
            )

    await run_with_commit_race()

    assert await count_rows(db_setup.Session, Turn, id=turn.id) == 0
    assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 0
    assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0
    assert await count_rows(db_setup.Session, Verdict, turn_id=turn.id) == 0


@pytest.mark.asyncio
async def test_answer_cost_integrity_error_with_active_turn_is_not_swallowed(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    async with db_setup.Session() as setup_db:
        await setup_db.execute(
            update(Turn).where(Turn.id == turn.id).values(status=TurnStatus.RUNNING)
        )
        await setup_db.commit()

    async with db_setup.Session() as db:
        original_commit = db.commit
        commit_calls = 0

        async def fail_answer_cost_commit():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 2:
                raise IntegrityError("unrelated cost constraint", None, None)
            await original_commit()

        monkeypatch.setattr(db, "commit", fail_answer_cost_commit)
        orchestrator = TurnOrchestrator()
        orchestrator._providers = db_setup.registry

        with pytest.raises(IntegrityError):
            await orchestrator.run(
                db,
                TurnContext(
                    turn_id=turn.id,
                    chat_id=db_setup.chat_id,
                    org_id=db_setup.auth.org_id,
                    project_id=None,
                    user_message="Delete this prompt",
                    model_ids=["gpt-4.1"],
                    verdict_model_id="gemini",
                    strategy=Strategy.SYNTHESIZE,
                    model_set_name="Test Set",
                    skip_answer_seed=True,
                ),
            )

    async with db_setup.Session() as db:
        saved = await db.get(Turn, turn.id)
        answer = (
            await db.execute(
                select(ModelAnswer).where(
                    ModelAnswer.turn_id == turn.id,
                    ModelAnswer.model_id == "gpt-4.1",
                )
            )
        ).scalar_one()

    assert saved.status == TurnStatus.RUNNING
    assert answer.status == ModelAnswerStatus.RUNNING
    assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0


@pytest.mark.asyncio
async def test_answer_cost_integrity_error_with_missing_answer_is_not_swallowed(
    db_setup, monkeypatch
):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)
    async with db_setup.Session() as setup_db:
        await setup_db.execute(
            update(Turn).where(Turn.id == turn.id).values(status=TurnStatus.RUNNING)
        )
        await setup_db.commit()

    async with db_setup.Session() as db:
        original_commit = db.commit
        original_rollback = db.rollback
        commit_calls = 0
        rollback_calls = 0

        async def fail_answer_cost_commit():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 2:
                raise IntegrityError("unrelated cost constraint", None, None)
            await original_commit()

        async def rollback_then_remove_answer():
            nonlocal rollback_calls
            rollback_calls += 1
            await original_rollback()
            if rollback_calls == 1:
                async with db_setup.Session() as drift_db:
                    await drift_db.execute(
                        delete(ModelAnswer).where(
                            ModelAnswer.turn_id == turn.id,
                            ModelAnswer.model_id == "gpt-4.1",
                        )
                    )
                    await drift_db.commit()

        monkeypatch.setattr(db, "commit", fail_answer_cost_commit)
        monkeypatch.setattr(db, "rollback", rollback_then_remove_answer)
        orchestrator = TurnOrchestrator()
        orchestrator._providers = db_setup.registry

        with pytest.raises(IntegrityError):
            await orchestrator.run(
                db,
                TurnContext(
                    turn_id=turn.id,
                    chat_id=db_setup.chat_id,
                    org_id=db_setup.auth.org_id,
                    project_id=None,
                    user_message="Delete this prompt",
                    model_ids=["gpt-4.1"],
                    verdict_model_id="gemini",
                    strategy=Strategy.SYNTHESIZE,
                    model_set_name="Test Set",
                    skip_answer_seed=True,
                ),
            )

    async with db_setup.Session() as db:
        saved = await db.get(Turn, turn.id)

    assert saved.status == TurnStatus.RUNNING
    assert (
        await count_rows(
            db_setup.Session,
            ModelAnswer,
            turn_id=turn.id,
            model_id="gpt-4.1",
        )
        == 0
    )
    assert await count_rows(db_setup.Session, ModelAnswer, turn_id=turn.id) == 1
    assert await count_rows(db_setup.Session, CostRecord, turn_id=turn.id) == 0


@pytest.mark.asyncio
async def test_conditional_completion_update_loses_to_deleted_turn(db_setup):
    turn = await create_turn(db_setup.Session, db_setup.auth, db_setup.chat_id)

    async with db_setup.Session() as db:
        await db.execute(
            update(Turn)
            .where(Turn.id == turn.id)
            .values(status=TurnStatus.RUNNING)
        )
        await db.commit()

    await hard_delete_turn_rows(db_setup.Session, turn.id)

    async with db_setup.Session() as db:
        completed = await db.execute(
            update(Turn)
            .where(
                Turn.id == turn.id,
                Turn.status.in_((TurnStatus.PENDING, TurnStatus.RUNNING)),
            )
            .values(status=TurnStatus.COMPLETED)
        )
        await db.commit()

    async with db_setup.Session() as db:
        saved = await db.get(Turn, turn.id)

    assert completed.rowcount == 0
    assert saved is None


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
