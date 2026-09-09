"""Regression coverage: PostgreSQL usagekind must include VERDICT_EXPLAIN."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import Enum as SAEnum

from app.db.models import CostRecord, UsageKind

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_055 = VERSIONS / "055_verdict_simple_explanations.py"
MIGRATION_056 = VERSIONS / "056_usagekind_verdict_explain.py"


def _load_migration(path: Path, name: str):
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_usagekind_and_cost_record_kind_match():
    assert UsageKind.VERDICT_EXPLAIN.name == "VERDICT_EXPLAIN"
    assert UsageKind.VERDICT_EXPLAIN.value == "verdict_explain"
    kind_type = CostRecord.__table__.c.kind.type
    assert isinstance(kind_type, SAEnum)
    assert kind_type.enum_class is UsageKind
    assert "VERDICT_EXPLAIN" in list(kind_type.enums)


def test_migration_055_created_explanations_without_usagekind():
    source = MIGRATION_055.read_text(encoding="utf-8")
    assert "verdict_simple_explanations" in source
    assert "usagekind" not in source.lower()
    assert "VERDICT_EXPLAIN" not in source
    assert "ALTER TYPE" not in source


def test_migration_056_adds_verdict_explain_to_usagekind():
    source = MIGRATION_056.read_text(encoding="utf-8")
    assert "down_revision = \"055\"" in source or "down_revision = '055'" in source
    assert "ALTER TYPE usagekind ADD VALUE IF NOT EXISTS 'VERDICT_EXPLAIN'" in source
    module = _load_migration(MIGRATION_056, "migration_056_usagekind_verdict_explain")
    assert module.revision == "056"
    assert module.down_revision == "055"
    assert module.ENUM_TYPE == "usagekind"
    assert module.ENUM_LABEL == "VERDICT_EXPLAIN"


def test_migration_056_is_noop_on_sqlite(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'usagekind.db'}")
    module = _load_migration(MIGRATION_056, "migration_056_sqlite_noop")
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(module, "op", operations)
        module.upgrade()
        module.downgrade()
    engine.dispose()
