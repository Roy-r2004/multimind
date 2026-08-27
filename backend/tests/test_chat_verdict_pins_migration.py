"""Focused data-compatibility coverage for migration 050."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_migration_050_preserves_legacy_scalar_pin(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'pin-migration.db'}")
    metadata = sa.MetaData()
    verdicts = sa.Table(
        "verdicts",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    chats = sa.Table(
        "chats",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pinned_verdict_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(
            ["pinned_verdict_id"],
            ["verdicts.id"],
            name="fk_chats_pinned_verdict_id",
            ondelete="SET NULL",
        ),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(verdicts.insert().values(id="verdict-a"))
        connection.execute(
            chats.insert().values(id="chat-a", pinned_verdict_id="verdict-a")
        )
        migration_path = (
            Path(__file__).parents[1] / "alembic" / "versions" / "050_chat_verdict_pins.py"
        )
        spec = spec_from_file_location("migration_050_chat_verdict_pins", migration_path)
        assert spec is not None and spec.loader is not None
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "pinned_verdict_id" not in {
            column["name"] for column in inspector.get_columns("chats")
        }
        pin = connection.execute(
            sa.text(
                "SELECT chat_id, verdict_id FROM chat_verdict_pins WHERE chat_id = :chat_id"
            ),
            {"chat_id": "chat-a"},
        ).one()
        assert pin == ("chat-a", "verdict-a")

        migration.downgrade()

        restored = connection.execute(
            sa.text("SELECT pinned_verdict_id FROM chats WHERE id = :chat_id"),
            {"chat_id": "chat-a"},
        ).scalar_one()
        assert restored == "verdict-a"

    engine.dispose()
