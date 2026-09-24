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


def test_migration_057_defaults_constraints_and_cascades():
    import pytest
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE chats (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE verdicts (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE chat_verdict_pins (id VARCHAR(36) PRIMARY KEY, chat_id VARCHAR(36) NOT NULL, verdict_id VARCHAR(36) NOT NULL, FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE, FOREIGN KEY(verdict_id) REFERENCES verdicts(id) ON DELETE CASCADE, CONSTRAINT uq_chat_verdict_pin UNIQUE(chat_id, verdict_id))")
        for column in ("chat_id", "verdict_id"):
            connection.exec_driver_sql(f"CREATE INDEX ix_chat_verdict_pins_{column} ON chat_verdict_pins ({column})")
        connection.exec_driver_sql("INSERT INTO chats VALUES ('c')")
        connection.exec_driver_sql("INSERT INTO verdicts VALUES ('v')")
        connection.exec_driver_sql("INSERT INTO chat_verdict_pins VALUES ('old', 'c', 'v')")
        path = Path(__file__).parents[1] / "alembic/versions/057_verdict_selection_pins.py"
        spec = spec_from_file_location("migration_057", path)
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.exec_driver_sql("SELECT pin_type, selected_text, selected_html FROM chat_verdict_pins").one() == ("verdict", None, None)
        inspector = sa.inspect(connection)
        assert len(inspector.get_indexes("chat_verdict_pins")) == 2
        assert not inspector.get_unique_constraints("chat_verdict_pins")
        for pin_id in ('s1', 's2'):
            connection.execute(sa.text("INSERT INTO chat_verdict_pins (id, chat_id, verdict_id, pin_type, selected_text) VALUES (:id, 'c', 'v', 'selection', 'words')"), {'id': pin_id})
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO chat_verdict_pins (id, chat_id, verdict_id, pin_type) VALUES ('bad', 'c', 'v', 'bad')")
        connection.exec_driver_sql("DELETE FROM verdicts WHERE id='v'")
        assert connection.exec_driver_sql("SELECT count(*) FROM chat_verdict_pins").scalar() == 0
        connection.exec_driver_sql("INSERT INTO verdicts VALUES ('v')")
        connection.exec_driver_sql("INSERT INTO chat_verdict_pins (id, chat_id, verdict_id, pin_type, selected_text) VALUES ('s', 'c', 'v', 'selection', 'words')")
        connection.exec_driver_sql("DELETE FROM chats WHERE id='c'")
        assert connection.exec_driver_sql("SELECT count(*) FROM chat_verdict_pins").scalar() == 0
        migration.downgrade()
        assert sa.inspect(connection).get_unique_constraints('chat_verdict_pins')[0]['name'] == 'uq_chat_verdict_pin'
    engine.dispose()


def test_migration_058_keeps_existing_selection_pins():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE chat_verdict_pins (id TEXT PRIMARY KEY, pin_type TEXT NOT NULL, selected_text TEXT)")
        connection.exec_driver_sql("INSERT INTO chat_verdict_pins VALUES ('old', 'selection', 'same words')")
        path = Path(__file__).parents[1] / "alembic/versions/058_verdict_selection_locator.py"
        spec = spec_from_file_location("migration_058", path)
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.exec_driver_sql("SELECT selection_locator FROM chat_verdict_pins").scalar() is None
        connection.execute(sa.text("UPDATE chat_verdict_pins SET selection_locator = :locator"),
                           {"locator": '{"start": 12, "end": 22, "quote": "same words"}'})
        migration.downgrade()
        assert connection.exec_driver_sql("SELECT id, pin_type, selected_text FROM chat_verdict_pins").one() == ("old", "selection", "same words")
    engine.dispose()
