"""Regression: ModelSetService must import; Strategy persists enum names."""

import pytest
from sqlalchemy import select, text

from app.core.dependencies import AuthContext
from app.db.models import ModelSet, Strategy, StrategyColumn
from app.services.domain_service import ModelSetService, model_set_service
from tests.conftest import create_model_set


def test_model_set_service_module_imports() -> None:
    """Class method ``list`` must not break ``list[str]`` annotations at import time."""
    assert ModelSetService is not None
    assert model_set_service is not None
    assert callable(ModelSetService.list)


def test_app_main_imports() -> None:
    from app.main import app

    assert app.title


def test_strategy_column_writes_enum_names() -> None:
    col = StrategyColumn()
    assert col.process_bind_param(Strategy.REFEREE, None) == "REFEREE"
    assert col.process_bind_param("Referee", None) == "REFEREE"
    assert col.process_bind_param("REFEREE", None) == "REFEREE"


def test_strategy_column_reads_names_and_values() -> None:
    col = StrategyColumn()
    assert col.process_result_value("REFEREE", None) is Strategy.REFEREE
    assert col.process_result_value("Referee", None) is Strategy.REFEREE
    assert col.process_result_value("PICK_BEST", None) is Strategy.PICK_BEST
    assert col.process_result_value("Pick Best", None) is Strategy.PICK_BEST


def test_model_set_uses_strategy_column() -> None:
    assert isinstance(ModelSet.__table__.c.strategy.type, StrategyColumn)


@pytest.mark.asyncio
async def test_load_model_set_with_uppercase_strategy_name(db, auth: AuthContext) -> None:
    row = await create_model_set(db, auth, slug="strat-name-test")
    await db.execute(
        text("UPDATE model_sets SET strategy = :s WHERE id = :id"),
        {"s": "REFEREE", "id": row.id},
    )
    await db.commit()

    db.expire_all()
    loaded = (
        await db.execute(select(ModelSet).where(ModelSet.slug == "strat-name-test"))
    ).scalar_one()
    assert loaded.strategy is Strategy.REFEREE
    assert loaded.strategy.value == "Referee"


@pytest.mark.asyncio
async def test_persist_strategy_as_name(db, auth: AuthContext) -> None:
    row = await create_model_set(db, auth, slug="strat-write-test")
    row.strategy = Strategy.SYNTHESIZE
    await db.commit()

    raw = (
        await db.execute(
            text("SELECT strategy FROM model_sets WHERE slug = :slug"),
            {"slug": "strat-write-test"},
        )
    ).scalar_one()
    assert raw == "SYNTHESIZE"
