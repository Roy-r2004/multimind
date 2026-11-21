"""Model set create/update failure mapping and validation."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError

from app.core.dependencies import AuthContext
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    InternalServerError,
    NotFoundError,
    ValidationError,
)
from app.db.models import ModelSet, OrgMembership, OrgRole, Strategy, User
from app.llm.prompt_engine import STRICT_REFEREE_BEHAVIOR
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
    assert created.effective_referee_prompt is None


@pytest.mark.asyncio
async def test_user_owned_sets_are_isolated_within_same_organization(db, auth: AuthContext) -> None:
    chafic = User(email="chafic@example.com", hashed_password="x", full_name="Chafic")
    fred = User(email="fred@example.com", hashed_password="x", full_name="Fred")
    db.add_all([chafic, fred])
    await db.flush()
    db.add_all(
        [
            OrgMembership(org_id=auth.org_id, user_id=chafic.id, role=OrgRole.MEMBER),
            OrgMembership(org_id=auth.org_id, user_id=fred.id, role=OrgRole.MEMBER),
        ]
    )
    await db.flush()
    chafic_auth = AuthContext(user=chafic, org_id=auth.org_id, role=OrgRole.MEMBER)
    fred_auth = AuthContext(user=fred, org_id=auth.org_id, role=OrgRole.MEMBER)

    chafic_set = await model_set_service.create(
        db, chafic_auth, _create_request(name="Chafic set")
    )
    fred_set = await model_set_service.create(db, fred_auth, _create_request(name="Fred set"))

    chafic_list = {item.id for item in await model_set_service.list(db, chafic_auth)}
    fred_list = {item.id for item in await model_set_service.list(db, fred_auth)}
    assert chafic_set.id in chafic_list and fred_set.id not in chafic_list
    assert fred_set.id in fred_list and chafic_set.id not in fred_list

    with pytest.raises(ForbiddenError):
        await model_set_service.update(db, fred_auth, chafic_set.id, ModelSetUpdateRequest(name="Tampered"))
    with pytest.raises(ForbiddenError):
        await model_set_service.delete(db, fred_auth, chafic_set.id)
    with pytest.raises(ForbiddenError):
        await model_set_service.update(
            db, chafic_auth, fred_set.id, ModelSetUpdateRequest(name="Tampered")
        )
    with pytest.raises(ForbiddenError):
        await model_set_service.delete(db, chafic_auth, fred_set.id)

    await model_set_service.update(
        db, fred_auth, fred_set.id, ModelSetUpdateRequest(name="Fred updated")
    )
    row = (await db.execute(select(ModelSet).where(ModelSet.slug == chafic_set.id))).scalar_one()
    assert row.name == "Chafic set"
    fred_row = (
        await db.execute(select(ModelSet).where(ModelSet.slug == fred_set.id))
    ).scalar_one()
    assert fred_row.name == "Fred updated"


@pytest.mark.asyncio
async def test_admin_can_manage_other_members_model_sets(db, auth: AuthContext) -> None:
    member = User(email="member@example.com", hashed_password="x", full_name="Member")
    db.add(member)
    await db.flush()
    member_set = await create_model_set(db, auth, slug="member-owned", owner_user_id=member.id)
    updated = await model_set_service.update(
        db, auth, member_set.slug, ModelSetUpdateRequest(name="Admin edited")
    )
    assert updated.name == "Admin edited"


@pytest.mark.asyncio
async def test_model_set_selection_is_persisted_per_user(db, auth: AuthContext) -> None:
    fred = User(email="fred@example.com", hashed_password="x", full_name="Fred")
    db.add(fred)
    await db.flush()
    fred_auth = AuthContext(user=fred, org_id=auth.org_id, role=OrgRole.MEMBER)
    chafic_set = await model_set_service.create(db, auth, _create_request(name="Chafic"))
    fred_set = await model_set_service.create(db, fred_auth, _create_request(name="Fred"))

    await model_set_service.set_selection(db, auth, chafic_set.id)
    await model_set_service.set_selection(db, fred_auth, fred_set.id)

    assert await model_set_service.get_selection(db, auth) == chafic_set.id
    assert await model_set_service.get_selection(db, fred_auth) == fred_set.id
    with pytest.raises(NotFoundError):
        await model_set_service.set_selection(db, fred_auth, chafic_set.id)


@pytest.mark.asyncio
async def test_legacy_ultimate_is_visible_selectable_but_admin_only_to_edit(
    db, auth: AuthContext
) -> None:
    chafic = User(email="chafic@example.com", hashed_password="x", full_name="Chafic")
    fred = User(email="fred@example.com", hashed_password="x", full_name="Fred")
    db.add_all([chafic, fred])
    await db.flush()
    chafic_auth = AuthContext(user=chafic, org_id=auth.org_id, role=OrgRole.MEMBER)
    fred_auth = AuthContext(user=fred, org_id=auth.org_id, role=OrgRole.MEMBER)
    ultimate = ModelSet(
        org_id=None,
        owner_user_id=None,
        slug="set-7edaefc8",
        name="Chafic ultimate model set",
        description="Existing configuration",
        models=["gpt-4.1", "claude"],
        verdict_model="gpt-4.1",
        strategy=Strategy.REFEREE,
        best_for="",
        is_system=True,
    )
    db.add(ultimate)
    await db.flush()

    chafic_sets = {item.id for item in await model_set_service.list(db, chafic_auth)}
    fred_sets = {item.id for item in await model_set_service.list(db, fred_auth)}
    assert ultimate.slug in chafic_sets and ultimate.slug in fred_sets
    assert await model_set_service.set_selection(db, chafic_auth, ultimate.slug) == ultimate.slug
    assert await model_set_service.set_selection(db, fred_auth, ultimate.slug) == ultimate.slug

    from app.services.chat_service import chat_service

    assert (await chat_service._resolve_model_set(db, chafic_auth, ultimate.slug)).slug == ultimate.slug
    assert (await chat_service._resolve_model_set(db, fred_auth, ultimate.slug)).slug == ultimate.slug
    with pytest.raises(ForbiddenError, match="organization admins"):
        await model_set_service.update(
            db, chafic_auth, ultimate.slug, ModelSetUpdateRequest(name="Changed")
        )


@pytest.mark.asyncio
async def test_unassigned_legacy_org_set_is_read_only_to_members(db, auth: AuthContext) -> None:
    chafic = User(email="chafic@example.com", hashed_password="x", full_name="Chafic")
    fred = User(email="fred@example.com", hashed_password="x", full_name="Fred")
    db.add_all([chafic, fred])
    await db.flush()
    chafic_auth = AuthContext(user=chafic, org_id=auth.org_id, role=OrgRole.MEMBER)
    fred_auth = AuthContext(user=fred, org_id=auth.org_id, role=OrgRole.MEMBER)
    legacy = await create_model_set(db, auth, slug="legacy-unassigned")

    assert legacy.slug in {s.id for s in await model_set_service.list(db, chafic_auth)}
    assert await model_set_service.set_selection(db, chafic_auth, legacy.slug) == legacy.slug
    with pytest.raises(ForbiddenError):
        await model_set_service.update(
            db, chafic_auth, legacy.slug, ModelSetUpdateRequest(name="Chafic changed")
        )
    with pytest.raises(ForbiddenError):
        await model_set_service.delete(db, fred_auth, legacy.slug)


@pytest.mark.asyncio
async def test_referee_response_falls_back_to_strict_behavior_when_override_is_null(
    db, auth: AuthContext
) -> None:
    created = await model_set_service.create(
        db,
        auth,
        _create_request(strategy=StrategyEnum.REFEREE),
    )

    assert created.referee_system_prompt is None
    assert created.custom_instructions is None
    assert created.effective_referee_prompt == STRICT_REFEREE_BEHAVIOR


@pytest.mark.asyncio
async def test_create_referee_promotes_custom_instructions_to_system_prompt(
    db, auth: AuthContext
) -> None:
    created = await model_set_service.create(
        db,
        auth,
        _create_request(
            strategy=StrategyEnum.REFEREE,
            custom_instructions="MY NEW PROMPT",
        ),
    )

    assert created.referee_system_prompt == "MY NEW PROMPT"
    assert created.custom_instructions is None
    assert created.effective_referee_prompt == "MY NEW PROMPT"
    assert created.effective_referee_prompt != STRICT_REFEREE_BEHAVIOR


@pytest.mark.asyncio
async def test_create_referee_uses_explicit_referee_system_prompt(
    db, auth: AuthContext
) -> None:
    created = await model_set_service.create(
        db,
        auth,
        _create_request(
            strategy=StrategyEnum.REFEREE,
            custom_instructions="ignored draft",
            referee_system_prompt="MY NEW PROMPT",
        ),
    )

    assert created.referee_system_prompt == "MY NEW PROMPT"
    assert created.custom_instructions is None
    assert created.effective_referee_prompt == "MY NEW PROMPT"


@pytest.mark.asyncio
async def test_update_referee_promotes_draft_and_clears_custom_instructions(
    db, auth: AuthContext
) -> None:
    created = await model_set_service.create(
        db,
        auth,
        _create_request(strategy=StrategyEnum.REFEREE, custom_instructions="OLD PROMPT"),
    )
    updated = await model_set_service.update(
        db,
        auth,
        created.id,
        ModelSetUpdateRequest(custom_instructions="MY NEW PROMPT"),
    )

    row = (await db.execute(select(ModelSet).where(ModelSet.slug == created.id))).scalar_one()
    assert updated.referee_system_prompt == "MY NEW PROMPT"
    assert updated.custom_instructions is None
    assert updated.effective_referee_prompt == "MY NEW PROMPT"
    assert row.referee_system_prompt == "MY NEW PROMPT"
    assert row.custom_instructions is None


@pytest.mark.asyncio
async def test_update_referee_with_empty_draft_preserves_system_prompt(
    db, auth: AuthContext
) -> None:
    created = await model_set_service.create(
        db,
        auth,
        _create_request(
            strategy=StrategyEnum.REFEREE,
            referee_system_prompt="MY NEW PROMPT",
        ),
    )
    updated = await model_set_service.update(
        db,
        auth,
        created.id,
        ModelSetUpdateRequest(custom_instructions=None, name="Still Referee"),
    )

    row = (await db.execute(select(ModelSet).where(ModelSet.slug == created.id))).scalar_one()
    assert updated.name == "Still Referee"
    assert updated.referee_system_prompt == "MY NEW PROMPT"
    assert updated.effective_referee_prompt == "MY NEW PROMPT"
    assert row.referee_system_prompt == "MY NEW PROMPT"


@pytest.mark.asyncio
async def test_update_referee_empty_string_does_not_erase_system_prompt(
    db, auth: AuthContext
) -> None:
    created = await model_set_service.create(
        db,
        auth,
        _create_request(
            strategy=StrategyEnum.REFEREE,
            referee_system_prompt="MY NEW PROMPT",
        ),
    )
    updated = await model_set_service.update(
        db,
        auth,
        created.id,
        ModelSetUpdateRequest(custom_instructions="   ", referee_system_prompt=""),
    )

    assert updated.referee_system_prompt == "MY NEW PROMPT"
    assert updated.effective_referee_prompt == "MY NEW PROMPT"


@pytest.mark.asyncio
async def test_non_referee_custom_instructions_are_unchanged(db, auth: AuthContext) -> None:
    created = await model_set_service.create(
        db,
        auth,
        _create_request(
            strategy=StrategyEnum.SYNTHESIZE,
            custom_instructions="legacy council instructions",
        ),
    )
    updated = await model_set_service.update(
        db,
        auth,
        created.id,
        ModelSetUpdateRequest(custom_instructions="updated council instructions"),
    )

    assert created.effective_referee_prompt is None
    assert created.referee_system_prompt is None
    assert created.custom_instructions == "legacy council instructions"
    assert updated.custom_instructions == "updated council instructions"
    assert updated.referee_system_prompt is None
    assert updated.effective_referee_prompt is None


@pytest.mark.asyncio
async def test_model_set_list_only_exposes_effective_prompt_for_referee_sets(
    db, auth: AuthContext
) -> None:
    referee = await create_model_set(db, auth, slug="referee-set")
    referee.strategy = Strategy.REFEREE
    await create_model_set(db, auth, slug="council-set")

    responses = {item.id: item for item in await model_set_service.list(db, auth)}

    assert responses["referee-set"].effective_referee_prompt == STRICT_REFEREE_BEHAVIOR
    assert responses["council-set"].effective_referee_prompt is None


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
