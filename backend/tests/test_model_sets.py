"""Model set create/update failure mapping and validation."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, DataError, IntegrityError

from app.core.dependencies import AuthContext
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    InternalServerError,
    NotFoundError,
    ValidationError,
)
from app.db.models import ModelSet, OrgRole, Strategy
from app.schemas.api import ModelSetCreateRequest, ModelSetUpdateRequest, StrategyEnum
from app.services.domain_service import BEST_FOR_MAX_LEN, MODEL_ID_MAX_LEN, model_set_service
from tests.conftest import create_model_set, create_other_auth


def _create_request(**overrides) -> ModelSetCreateRequest:
    payload = {
        "name": "My Set",
        "description": "Short",
        "models": ["gpt-4.1", "claude"],
        "verdict_model": "gpt-4.1",
        "strategy": StrategyEnum.SYNTHESIZE,
        "best_for": "Short",
    }
    payload.update(overrides)
    return ModelSetCreateRequest(**payload)


@pytest.mark.asyncio
async def test_create_model_set_success(db, auth: AuthContext) -> None:
    created = await model_set_service.create(db, auth, _create_request())
    assert created.id.startswith("set-")
    assert created.is_system is False
    assert created.models == ["gpt-4.1", "claude"]


@pytest.mark.asyncio
async def test_create_rejects_unknown_model_id(db, auth: AuthContext) -> None:
    with pytest.raises(ValidationError, match="unknown model id"):
        await model_set_service.create(
            db,
            auth,
            _create_request(models=["not-a-real-model"], verdict_model="gpt-4.1"),
        )


@pytest.mark.asyncio
async def test_create_rejects_empty_model_id(db, auth: AuthContext) -> None:
    with pytest.raises(ValidationError, match="cannot be empty"):
        await model_set_service.create(
            db,
            auth,
            _create_request(models=["  "], verdict_model="gpt-4.1"),
        )


@pytest.mark.asyncio
async def test_create_rejects_overlong_model_id(db, auth: AuthContext) -> None:
    huge = "or:" + ("x" * MODEL_ID_MAX_LEN)
    with pytest.raises(ValidationError, match="exceeds"):
        model_set_service._validate_one_model_id(huge, field="verdict_model")


def test_schema_rejects_overlong_verdict_model() -> None:
    huge = "or:" + ("x" * MODEL_ID_MAX_LEN)
    with pytest.raises(Exception, match="at most 128"):
        _create_request(models=["gpt-4.1"], verdict_model=huge)


@pytest.mark.asyncio
async def test_create_truncates_best_for_from_long_description(db, auth: AuthContext) -> None:
    long_desc = "D" * (BEST_FOR_MAX_LEN + 80)
    created = await model_set_service.create(
        db,
        auth,
        _create_request(description=long_desc, best_for=""),
    )
    assert len(created.best_for) == BEST_FOR_MAX_LEN
    assert created.description == long_desc


@pytest.mark.asyncio
async def test_create_accepts_openrouter_verdict_id(db, auth: AuthContext) -> None:
    mid = "or:openai--gpt-5.5-pro"
    created = await model_set_service.create(
        db,
        auth,
        _create_request(models=["gpt-4.1", mid], verdict_model=mid),
    )
    assert created.verdict_model == mid


@pytest.mark.asyncio
async def test_update_missing_slug_returns_not_found(db, auth: AuthContext) -> None:
    with pytest.raises(NotFoundError):
        await model_set_service.update(
            db,
            auth,
            "missing-slug",
            ModelSetUpdateRequest(name="Nope"),
        )


@pytest.mark.asyncio
async def test_update_system_set_forbidden(db, auth: AuthContext) -> None:
    system = ModelSet(
        org_id=None,
        slug="referee",
        name="System",
        description="",
        models=["gpt-4.1"],
        verdict_model="gpt-4.1",
        strategy=Strategy.REFEREE,
        best_for="",
        is_system=True,
    )
    db.add(system)
    await db.flush()

    with pytest.raises(ForbiddenError, match="System model sets"):
        await model_set_service.update(
            db,
            auth,
            "referee",
            ModelSetUpdateRequest(name="Hijack"),
        )


@pytest.mark.asyncio
async def test_update_other_org_forbidden(db, auth: AuthContext) -> None:
    other = await create_other_auth(db)
    foreign = await create_model_set(db, other, slug="foreign-set")

    with pytest.raises(ForbiddenError, match="another organization"):
        await model_set_service.update(
            db,
            auth,
            foreign.slug,
            ModelSetUpdateRequest(name="Steal"),
        )


@pytest.mark.asyncio
async def test_update_success(db, auth: AuthContext) -> None:
    row = await create_model_set(db, auth, slug="editable")
    updated = await model_set_service.update(
        db,
        auth,
        row.slug,
        ModelSetUpdateRequest(name="Renamed", models=["claude"], verdict_model="claude"),
    )
    assert updated.name == "Renamed"
    assert updated.models == ["claude"]


@pytest.mark.asyncio
async def test_update_chafic_ultimate_system_set_in_place(db, auth: AuthContext) -> None:
    ultimate = ModelSet(
        org_id=None,
        slug="set-7edaefc8",
        name="Chafic ultimate model set",
        description="Custom model set.",
        models=["gpt-4.1", "or:anthropic--claude-fable-5", "gemini", "or:x-ai--grok-4"],
        verdict_model="gpt-4.1",
        strategy=Strategy.REFEREE,
        best_for="Custom model set.",
        template_name="Chafiq Referee",
        custom_instructions="keep me",
        is_system=True,
    )
    db.add(ultimate)
    await db.flush()
    original_id = ultimate.id

    updated = await model_set_service.update(
        db,
        auth,
        "set-7edaefc8",
        ModelSetUpdateRequest(
            description="Temporary description for in-place edit",
            template_name="Chafiq Referee",
        ),
    )

    row = (
        await db.execute(select(ModelSet).where(ModelSet.slug == "set-7edaefc8"))
    ).scalar_one()
    assert updated.id == "set-7edaefc8"
    assert row.id == original_id
    assert row.slug == "set-7edaefc8"
    assert row.is_system is True
    assert row.org_id is None
    assert row.description == "Temporary description for in-place edit"
    assert row.template_name == "Chafiq Referee"
    assert row.models == ["gpt-4.1", "or:anthropic--claude-fable-5", "gemini", "or:x-ai--grok-4"]
    assert row.verdict_model == "gpt-4.1"
    assert row.strategy == Strategy.REFEREE
    assert row.custom_instructions == "keep me"
    assert updated.is_system is True


@pytest.mark.asyncio
async def test_delete_chafic_ultimate_system_set_forbidden(db, auth: AuthContext) -> None:
    db.add(
        ModelSet(
            org_id=None,
            slug="set-7edaefc8",
            name="Chafic ultimate model set",
            description="",
            models=["gpt-4.1"],
            verdict_model="gpt-4.1",
            strategy=Strategy.REFEREE,
            best_for="",
            is_system=True,
        )
    )
    await db.flush()

    with pytest.raises(ForbiddenError, match="System model sets"):
        await model_set_service.delete(db, auth, "set-7edaefc8")

    remaining = (
        await db.execute(select(ModelSet).where(ModelSet.slug == "set-7edaefc8"))
    ).scalar_one()
    assert remaining.is_system is True


def test_map_db_exception_integrity() -> None:
    err = IntegrityError("stmt", {}, Exception("foreign key constraint"))
    mapped = model_set_service._map_db_exception(err)
    assert isinstance(mapped, ConflictError)


def test_map_db_exception_data_error() -> None:
    err = DataError("stmt", {}, Exception("value too long for type character varying(64)"))
    mapped = model_set_service._map_db_exception(err)
    assert isinstance(mapped, ValidationError)


def test_map_db_exception_permission() -> None:
    orig = Exception("permission denied for table model_sets")
    err = DBAPIError("stmt", {}, orig)
    mapped = model_set_service._map_db_exception(err)
    assert isinstance(mapped, InternalServerError)


def test_sanitize_redacts_secrets() -> None:
    raw = Exception("dsn=postgresql://user:password=secret@host/db Authorization: Bearer tok123")
    cleaned = model_set_service._sanitize_exc_message(raw)
    assert "tok123" not in cleaned
    assert "secret@host" not in cleaned or "[REDACTED" in cleaned


def test_submitted_field_names_omit_unset_on_update() -> None:
    data = ModelSetUpdateRequest(name="Only")
    names = model_set_service._submitted_field_names(data, exclude_unset=True)
    assert names == ["name"]


@pytest.mark.asyncio
async def test_create_without_org_forbidden(db, auth: AuthContext) -> None:
    bare = AuthContext(user=auth.user, org_id="", role=OrgRole.OWNER)
    with pytest.raises(ForbiddenError, match="Organization context"):
        await model_set_service.create(db, bare, _create_request())
