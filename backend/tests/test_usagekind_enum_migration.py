"""Focused checks for UsageKind PostgreSQL enum migration 030."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.db.models import UsageKind


def load_migration_030():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "030_usagekind_enum_values.py"
    )
    spec = importlib.util.spec_from_file_location("migration_030", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migration_030_covers_every_python_usagekind_member_name():
    module = load_migration_030()
    python_names = {member.name for member in UsageKind}
    required = set(module.REQUIRED_USAGEKIND_LABELS)
    assert python_names == required
    # New kinds that were missing from the live PG enum before 030.
    for label in (
        "EMBEDDING",
        "SCRAPING",
        "BLUEPRINT",
        "EXTRACTION",
        "CLASSIFICATION",
        "PLANNER",
        "DOCUMENT",
        "HELPER",
        "OTHER",
    ):
        assert label in required
    # Historical labels must remain listed (never deleted/renamed by 030).
    for label in ("ANSWER", "VERDICT", "INSURANCE", "LESSON", "BRAIN"):
        assert label in required


def test_migration_030_uses_idempotent_add_value_sql():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "030_usagekind_enum_values.py"
    ).read_text(encoding="utf-8")
    assert "ALTER TYPE usagekind ADD VALUE IF NOT EXISTS" in source
    assert "down_revision = \"029\"" in source or "down_revision = '029'" in source
    for label in (
        "EMBEDDING",
        "SCRAPING",
        "BLUEPRINT",
        "EXTRACTION",
        "CLASSIFICATION",
        "PLANNER",
        "DOCUMENT",
        "HELPER",
        "OTHER",
    ):
        assert f"'{label}'" in source or f'"{label}"' in source


@pytest.mark.asyncio
async def test_sqlalchemy_usagekind_emits_member_names_not_values():
    """Regression: PG enum must accept EMBEDDING, not embedding."""
    assert UsageKind.EMBEDDING.name == "EMBEDDING"
    assert UsageKind.EMBEDDING.value == "embedding"
    from sqlalchemy import Enum

    enum_type = Enum(UsageKind)
    assert "EMBEDDING" in enum_type.enums
    assert "embedding" not in enum_type.enums
