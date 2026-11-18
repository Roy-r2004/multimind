from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.db.base import Base
from app.db.models import ModelSet, OrgMembership, OrgRole, Organization, Project, Strategy, User


@pytest.fixture(autouse=True)
def disable_facility_pipeline_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep discovery/retrieval tests isolated; opt in per-test for extract/publish."""
    settings = get_settings()
    monkeypatch.setattr(settings, "facility_extraction_enabled", False)
    monkeypatch.setattr(settings, "facility_publication_enabled", False)


@pytest.fixture(autouse=True)
def no_real_chat_memory_rebuild_enqueue_globally(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project-wide: never let any test enqueue a real ARQ/Redis chat-memory
    rebuild job.

    ``chat_service.delete_turn``/``restore_turn``/``regenerate_turn`` queue a
    durable rebuild job (app.chat_memory.worker) instead of rebuilding
    inline. With no Redis reachable here, the real
    ``enqueue_chat_memory_rebuild`` would retry a connection for several
    seconds on every call (arq's own ``RedisSettings.conn_retries``), which
    is slow and unrelated to what most tests are checking. A test that
    specifically needs the rebuild to actually run calls
    ``process_chat_memory_rebuild_job`` directly against its own session
    (see the ``drain_chat_memory_rebuild`` helper in
    tests/test_chat_continuation_memory.py) instead of relying on a real
    enqueue.
    """
    from app.services import chat_memory_service as chat_memory_module
    from app.services import chat_service as chat_service_module

    async def noop_enqueue(*, chat_id: str, org_id: str, project_id: str | None) -> None:
        return None

    monkeypatch.setattr(chat_service_module, "enqueue_chat_memory_rebuild", noop_enqueue)
    monkeypatch.setattr(chat_memory_module, "enqueue_chat_memory_rebuild", noop_enqueue)


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def auth(db: AsyncSession) -> AuthContext:
    org = Organization(name="Org One", slug="org-one")
    user = User(email="one@example.com", hashed_password="x", full_name="One")
    db.add_all([org, user])
    await db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))
    await db.flush()
    return AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)


async def create_other_auth(db: AsyncSession) -> AuthContext:
    org = Organization(name="Org Two", slug="org-two")
    user = User(email="two@example.com", hashed_password="x", full_name="Two")
    db.add_all([org, user])
    await db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))
    await db.flush()
    return AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)


async def create_model_set(
    db: AsyncSession,
    auth: AuthContext,
    *,
    slug: str = "research-set",
    models: list[str] | None = None,
    verdict_model: str = "gpt-4.1",
) -> ModelSet:
    model_set = ModelSet(
        org_id=auth.org_id,
        slug=slug,
        name=slug,
        description="",
        models=models or ["gpt-4.1", "claude"],
        verdict_model=verdict_model,
        strategy=Strategy.SYNTHESIZE,
        best_for="",
        is_system=False,
    )
    db.add(model_set)
    await db.flush()
    return model_set


async def create_project(db: AsyncSession, auth: AuthContext, *, name: str = "Project") -> Project:
    project = Project(org_id=auth.org_id, name=name)
    db.add(project)
    await db.flush()
    return project


def valid_blueprint() -> dict:
    return {
        "mission_summary": {
            "goal": "Find target entities",
            "target_entities": ["entity"],
            "deliverables": ["dataset"],
        },
        "scope": {
            "included": ["public listings"],
            "excluded": ["private data"],
            "countries": ["unknown"],
            "regions": ["unknown"],
        },
        "languages": ["English"],
        "search_terms": [{"language": "English", "term": "entity directory", "purpose": "discovery"}],
        "source_strategy": [
            {
                "source_type": "official directory",
                "priority": 1,
                "trust_tier": "high",
                "purpose": "seed sources",
                "required": True,
            }
        ],
        "data_schema": [{"field_name": "name", "description": "Entity name", "required": True}],
        "classification_rules": ["Classify only after evidence is found."],
        "verification_rules": ["Require corroboration."],
        "deduplication_rules": ["Deduplicate by normalized name."],
        "compliance_rules": ["Planning only; verify legal basis before scraping."],
        "task_plan": [{"order": 1, "task": "Prepare source list", "assigned_role": "Mission Interpreter"}],
        "stop_conditions": ["Stop if sources prohibit collection."],
        "estimated_workload": {
            "expected_queries": None,
            "expected_pages": None,
            "expected_ai_calls": None,
            "estimated_cost_usd": None,
            "notes": ["Unknown until source review."],
        },
        "agent_assignments": [
            {"role": "Mission Interpreter", "responsibility": "Define scope", "model_id": "gpt-4.1"}
        ],
    }
