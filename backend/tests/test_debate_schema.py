"""Phase 1 coverage: isolated AI Debate Mode persistence schema.

These tests only exercise the new debate_sessions/debate_challenges/
debate_responses tables and the additive UsageKind.DEBATE value. No debate
service/orchestrator/API exists yet (later phases) — this file proves the
schema itself is sound and that it cannot disturb Turn/ModelAnswer/Verdict
or the unrelated Challenge/Lessons tables.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.dependencies import AuthContext
from app.db.base import Base
from app.db.models import (
    Chat,
    CostRecord,
    DebateChallenge,
    DebateChallengeStatus,
    DebateResponse,
    DebateResponseStatus,
    DebateSession,
    DebateSessionStatus,
    ModelAnswer,
    ModelAnswerStatus,
    OrgRole,
    Organization,
    Strategy,
    Turn,
    TurnStatus,
    UsageKind,
    User,
    Verdict,
    VerdictLesson,
    LessonStatus,
)

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "061_debate_mode_schema.py"


def _load_migration():
    spec = spec_from_file_location("migration_061_debate_mode_schema", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def fk_db():
    """A dedicated engine with sqlite FK enforcement turned on at connect time.

    The shared conftest `db` fixture doesn't enable this pragma, and sqlite
    refuses to toggle it mid-transaction, so FK/ON-DELETE-CASCADE behavior is
    only observable here. (Already independently verified against a real
    disposable PostgreSQL instance in Phase 1's manual migration check.)
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


async def _make_turn(db: AsyncSession, auth: AuthContext, *, with_verdict: bool = True) -> Turn:
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Debate test chat")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="What should we do?",
        model_set_id="set-x",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-5.1",
        status=TurnStatus.COMPLETED,
    )
    db.add(turn)
    await db.flush()
    db.add_all(
        [
            ModelAnswer(
                turn_id=turn.id, model_id="model-a", text="Answer A", status=ModelAnswerStatus.COMPLETED
            ),
            ModelAnswer(
                turn_id=turn.id, model_id="model-b", text="Answer B", status=ModelAnswerStatus.COMPLETED
            ),
        ]
    )
    if with_verdict:
        db.add(Verdict(turn_id=turn.id, model_id="gpt-5.1", strategy=Strategy.SYNTHESIZE, text="Verdict text", reason="Because"))
    await db.flush()
    return turn


# --- 1. DebateSession creation + FK integrity -------------------------------


async def test_debate_session_creation_and_fk_integrity(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth)
    session = DebateSession(
        org_id=auth.org_id,
        turn_id=turn.id,
        status=DebateSessionStatus.PENDING,
        model_ids=["model-a", "model-b"],
    )
    db.add(session)
    await db.flush()

    fetched = (await db.execute(select(DebateSession).where(DebateSession.id == session.id))).scalar_one()
    assert fetched.turn_id == turn.id
    assert fetched.org_id == auth.org_id
    assert fetched.status == DebateSessionStatus.PENDING
    assert fetched.model_ids == ["model-a", "model-b"]
    assert fetched.max_rounds == 2  # default


async def test_debate_session_rejects_unknown_turn(fk_db: AsyncSession):
    org = Organization(name="Org", slug="org-fk")
    fk_db.add(org)
    await fk_db.flush()
    session = DebateSession(
        org_id=org.id,
        turn_id="not-a-real-turn",
        status=DebateSessionStatus.PENDING,
        model_ids=[],
    )
    fk_db.add(session)
    with pytest.raises(IntegrityError):
        await fk_db.flush()


# --- 2. Org isolation (structural) ------------------------------------------


async def test_debate_session_carries_its_own_org_id(db: AsyncSession, auth: AuthContext):
    """org_id is stored directly on the row (not inferred via Turn/Chat join)."""
    turn = await _make_turn(db, auth)
    session = DebateSession(
        org_id=auth.org_id, turn_id=turn.id, status=DebateSessionStatus.PENDING, model_ids=["model-a"]
    )
    db.add(session)
    await db.flush()
    rows = (
        await db.execute(select(DebateSession).where(DebateSession.org_id == auth.org_id))
    ).scalars().all()
    assert any(r.id == session.id for r in rows)
    other_org_rows = (
        await db.execute(select(DebateSession).where(DebateSession.org_id == "some-other-org"))
    ).scalars().all()
    assert other_org_rows == []


# --- 3. Unique active session per turn + retry-compatible schema -----------


async def test_only_one_active_debate_session_per_turn(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth)
    db.add(DebateSession(org_id=auth.org_id, turn_id=turn.id, status=DebateSessionStatus.PENDING, model_ids=[]))
    await db.flush()
    db.add(DebateSession(org_id=auth.org_id, turn_id=turn.id, status=DebateSessionStatus.RUNNING, model_ids=[]))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_failed_debate_session_can_be_retried_with_a_new_session(db: AsyncSession, auth: AuthContext):
    """A terminal (failed/cancelled/completed/partial) session never blocks a retry."""
    turn = await _make_turn(db, auth)
    first = DebateSession(
        org_id=auth.org_id,
        turn_id=turn.id,
        status=DebateSessionStatus.FAILED,
        model_ids=[],
        error_message="provider timeout",
    )
    db.add(first)
    await db.flush()

    retry = DebateSession(org_id=auth.org_id, turn_id=turn.id, status=DebateSessionStatus.PENDING, model_ids=[])
    db.add(retry)
    await db.flush()  # must not raise

    sessions = (
        await db.execute(select(DebateSession).where(DebateSession.turn_id == turn.id))
    ).scalars().all()
    assert {s.status for s in sessions} == {DebateSessionStatus.FAILED, DebateSessionStatus.PENDING}


# --- 4. Challenge / response persistence + allowed statuses ----------------


async def test_challenge_and_response_persistence(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth)
    session = DebateSession(
        org_id=auth.org_id, turn_id=turn.id, status=DebateSessionStatus.RUNNING, model_ids=["model-a", "model-b"]
    )
    db.add(session)
    await db.flush()

    challenge = DebateChallenge(
        org_id=auth.org_id,
        debate_session_id=session.id,
        author_model_id="model-a",
        status=DebateChallengeStatus.COMPLETED,
        challenges=[{"target_model_id": "model-b", "issue": "misses a cost caveat", "severity": "medium"}],
        tokens_input=120,
        tokens_output=80,
        cost_usd=0.002,
    )
    response = DebateResponse(
        org_id=auth.org_id,
        debate_session_id=session.id,
        author_model_id="model-b",
        status=DebateResponseStatus.COMPLETED,
        responses=[{"challenge_author_model_id": "model-a", "stance": "revises", "explanation": "fair point"}],
    )
    db.add_all([challenge, response])
    await db.flush()

    await db.refresh(session, attribute_names=["challenges", "responses"])
    assert [c.author_model_id for c in session.challenges] == ["model-a"]
    assert [r.author_model_id for r in session.responses] == ["model-b"]
    assert session.challenges[0].challenges[0]["severity"] == "medium"
    assert session.responses[0].responses[0]["stance"] == "revises"


async def test_one_challenge_row_per_author_per_session(db: AsyncSession, auth: AuthContext):
    """O(n) peer review: one row per reviewing model per session, never pairwise."""
    turn = await _make_turn(db, auth)
    session = DebateSession(org_id=auth.org_id, turn_id=turn.id, status=DebateSessionStatus.RUNNING, model_ids=[])
    db.add(session)
    await db.flush()
    db.add(DebateChallenge(org_id=auth.org_id, debate_session_id=session.id, author_model_id="model-a", status=DebateChallengeStatus.COMPLETED))
    await db.flush()
    db.add(DebateChallenge(org_id=auth.org_id, debate_session_id=session.id, author_model_id="model-a", status=DebateChallengeStatus.COMPLETED))
    with pytest.raises(IntegrityError):
        await db.flush()


def test_debate_status_enums_are_bounded():
    assert {s.value for s in DebateSessionStatus} == {
        "pending", "running", "completed", "partial", "failed", "cancelled"
    }
    assert {s.value for s in DebateChallengeStatus} == {"pending", "running", "completed", "failed"}
    assert {s.value for s in DebateResponseStatus} == {"pending", "running", "completed", "failed"}


# --- 5. Deletion behavior: cascade within debate, never into Turn/Verdict --


async def test_deleting_debate_session_cascades_children_but_not_turn_or_verdict(fk_db: AsyncSession):
    org = Organization(name="Org", slug="org-cascade")
    user = User(email="cascade@example.com", hashed_password="x", full_name="Cascade Tester")
    fk_db.add_all([org, user])
    await fk_db.flush()
    auth = AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)
    turn = await _make_turn(fk_db, auth)
    session = DebateSession(
        org_id=auth.org_id, turn_id=turn.id, status=DebateSessionStatus.COMPLETED, model_ids=["model-a"]
    )
    fk_db.add(session)
    await fk_db.flush()
    fk_db.add(
        DebateChallenge(
            org_id=auth.org_id,
            debate_session_id=session.id,
            author_model_id="model-a",
            status=DebateChallengeStatus.COMPLETED,
        )
    )
    fk_db.add(
        DebateResponse(
            org_id=auth.org_id,
            debate_session_id=session.id,
            author_model_id="model-a",
            status=DebateResponseStatus.COMPLETED,
        )
    )
    await fk_db.flush()
    session_id = session.id

    await fk_db.delete(session)
    await fk_db.flush()

    assert (
        await fk_db.execute(select(DebateChallenge).where(DebateChallenge.debate_session_id == session_id))
    ).scalars().all() == []
    assert (
        await fk_db.execute(select(DebateResponse).where(DebateResponse.debate_session_id == session_id))
    ).scalars().all() == []

    # The original Turn/ModelAnswer/Verdict rows must survive untouched.
    surviving_turn = (await fk_db.execute(select(Turn).where(Turn.id == turn.id))).scalar_one()
    assert surviving_turn.status == TurnStatus.COMPLETED
    assert surviving_turn.error_message is None
    answers = (await fk_db.execute(select(ModelAnswer).where(ModelAnswer.turn_id == turn.id))).scalars().all()
    assert len(answers) == 2
    verdict = (await fk_db.execute(select(Verdict).where(Verdict.turn_id == turn.id))).scalar_one()
    assert verdict.text == "Verdict text"


# --- 6. Existing Challenge/Lessons tables remain completely independent ----


async def test_debate_session_coexists_with_unrelated_verdict_lesson(db: AsyncSession, auth: AuthContext):
    """AI Debate (models challenge each other) is unrelated to Challenge/Lessons
    (user disagrees with the Verdict) — both can exist for the same Turn without
    interfering with each other, and neither table's shape changed."""
    turn = await _make_turn(db, auth)
    lesson = VerdictLesson(
        turn_id=turn.id,
        chat_id=turn.chat_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
        user_name="Tester",
        user_message="why not approach B?",
        disagreement_reason="I think approach B is cheaper",
        user_position="Prefer approach B",
        verdict_model_id="gpt-5.1",
        verdict_model_name="GPT-5.1",
        verdict_text="Verdict text",
        verdict_reason="Because",
        strategy=Strategy.SYNTHESIZE,
        title="Why not approach B?",
        summary="User prefers approach B on cost grounds.",
        comparison={},
        status=LessonStatus.DISCUSSING,
    )
    debate = DebateSession(org_id=auth.org_id, turn_id=turn.id, status=DebateSessionStatus.PENDING, model_ids=[])
    db.add_all([lesson, debate])
    await db.flush()  # no unique/FK conflict between the two unrelated features

    assert (await db.execute(select(VerdictLesson).where(VerdictLesson.turn_id == turn.id))).scalar_one().disagreement_reason == (
        "I think approach B is cheaper"
    )
    assert (await db.execute(select(DebateSession).where(DebateSession.turn_id == turn.id))).scalar_one().status == (
        DebateSessionStatus.PENDING
    )


# --- 7. Existing chats without any debate stay fully compatible ------------


async def test_turn_without_any_debate_session_is_unaffected(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth)
    rows = (await db.execute(select(DebateSession).where(DebateSession.turn_id == turn.id))).scalars().all()
    assert rows == []  # no debate record is simply "none" -- not an error, not a required join


# --- 8. UsageKind.DEBATE is additive ----------------------------------------


def test_usagekind_debate_is_additive():
    assert UsageKind.DEBATE.value == "debate"
    # Every pre-existing member is still present and unchanged.
    for name, value in (
        ("ANSWER", "answer"),
        ("VERDICT", "verdict"),
        ("INSURANCE", "insurance"),
        ("LESSON", "lesson"),
        ("BRAIN", "brain"),
        ("CHAT_MEMORY", "chat_memory"),
        ("VERDICT_EXPLAIN", "verdict_explain"),
        ("DOCUMENT_PROCESSING", "document_processing"),
    ):
        assert getattr(UsageKind, name).value == value


async def test_cost_record_accepts_debate_kind(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth)
    record = CostRecord(
        org_id=auth.org_id, turn_id=turn.id, model_id="model-a", kind=UsageKind.DEBATE, cost_usd=0.001
    )
    db.add(record)
    await db.flush()
    fetched = (await db.execute(select(CostRecord).where(CostRecord.id == record.id))).scalar_one()
    assert fetched.kind == UsageKind.DEBATE


# --- 9. Migration 061 in isolation (mirrors 056/057/058 migration tests) ---


def test_migration_061_down_revision_is_current_head():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'down_revision = "gosom066_run_discovery_provider"' in source
    module = _load_migration()
    assert module.revision == "061"
    assert module.down_revision == "gosom066_run_discovery_provider"


def test_migration_061_never_touches_protected_tables():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    # The only "turns" reference allowed is the debate_sessions.turn_id FK target;
    # no op.add_column/alter_column/drop_* against turns/model_answers/verdicts/
    # verdict_lessons appears anywhere in this migration.
    for protected_table in ('"model_answers"', '"verdicts"', '"verdict_lessons"'):
        assert protected_table not in source
    assert 'ForeignKeyConstraint(["turn_id"], ["turns.id"])' in source
    assert "add_column" not in source
    assert "alter_column" not in source


def test_migration_061_creates_tables_on_sqlite_and_downgrade_removes_them(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'debate-061.db'}")
    module = _load_migration()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE turns (id VARCHAR(36) PRIMARY KEY)")
        operations = Operations(MigrationContext.configure(connection))
        module.op = operations
        module.upgrade()

        inspector = sa.inspect(connection)
        assert {"debate_sessions", "debate_challenges", "debate_responses"}.issubset(
            set(inspector.get_table_names())
        )
        unique_indexes = [
            idx for idx in inspector.get_indexes("debate_sessions") if idx.get("unique")
        ]
        assert any(idx["name"] == "uq_debate_sessions_active_turn" for idx in unique_indexes)

        module.downgrade()
        remaining = set(sa.inspect(connection).get_table_names())
        assert "debate_sessions" not in remaining
        assert "debate_challenges" not in remaining
        assert "debate_responses" not in remaining
        # protected tables created above for FK purposes are still there
        assert {"organizations", "turns"}.issubset(remaining)
    engine.dispose()


def test_migration_061_usagekind_alter_is_noop_on_sqlite(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'debate-061-enum.db'}")
    module = _load_migration()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE turns (id VARCHAR(36) PRIMARY KEY)")
        operations = Operations(MigrationContext.configure(connection))
        module.op = operations
        module.upgrade()  # must not raise on sqlite (dialect guard short-circuits ALTER TYPE)
    engine.dispose()
