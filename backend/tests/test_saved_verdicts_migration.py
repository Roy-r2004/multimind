import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect


def load_migration_017():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "017_saved_verdicts.py"
    )
    spec = importlib.util.spec_from_file_location("migration_017", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_with_ops(module, conn, fn_name: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    ctx = MigrationContext.configure(conn)
    ops = Operations(ctx)
    original_op = module.op
    module.op = ops
    try:
        getattr(module, fn_name)()
    finally:
        module.op = original_op


def create_dependencies(conn) -> None:
    conn.exec_driver_sql("CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)")
    conn.exec_driver_sql("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)")
    conn.exec_driver_sql("INSERT INTO organizations (id) VALUES ('org-1')")
    conn.exec_driver_sql("INSERT INTO users (id) VALUES ('user-1')")


def create_correct_saved_verdicts_without_index(conn) -> None:
    conn.exec_driver_sql(
        """
        CREATE TABLE saved_verdicts (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL REFERENCES organizations(id),
            user_id VARCHAR(36) NOT NULL REFERENCES users(id),
            source_verdict_id VARCHAR(36) NOT NULL,
            source_turn_id VARCHAR(36),
            source_chat_id VARCHAR(36),
            source_chat_title VARCHAR(512) NOT NULL,
            source_user_message TEXT NOT NULL,
            verdict_text TEXT NOT NULL,
            verdict_reason TEXT NOT NULL,
            verdict_model_id VARCHAR(64) NOT NULL,
            strategy VARCHAR(32) NOT NULL,
            saved_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT uq_saved_verdict_user_source
                UNIQUE (org_id, user_id, source_verdict_id)
        )
        """
    )


def create_expected_saved_verdicts_for_helper(conn, module) -> None:
    module.op.create_table(
        module.EXPECTED_TABLE,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_verdict_id", sa.String(36), nullable=False),
        sa.Column("source_turn_id", sa.String(36), nullable=True),
        sa.Column("source_chat_id", sa.String(36), nullable=True),
        sa.Column("source_chat_title", sa.String(512), nullable=False),
        sa.Column("source_user_message", sa.Text(), nullable=False),
        sa.Column("verdict_text", sa.Text(), nullable=False),
        sa.Column("verdict_reason", sa.Text(), nullable=False),
        sa.Column("verdict_model_id", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "user_id",
            "source_verdict_id",
            name=module.EXPECTED_UNIQUE,
        ),
    )


def test_migration_017_clean_upgrade_creates_complete_schema_and_downgrades():
    module = load_migration_017()
    assert module.revision == "017"
    assert module.down_revision == "016"

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        run_with_ops(module, conn, "upgrade")

        inspector = inspect(conn)
        assert "saved_verdicts" in inspector.get_table_names()
        columns = {column["name"]: column for column in inspector.get_columns("saved_verdicts")}
        assert set(module.EXPECTED_COLUMNS).issubset(columns)
        assert columns["id"]["nullable"] is False
        assert columns["source_turn_id"]["nullable"] is True
        assert columns["source_chat_id"]["nullable"] is True
        assert columns["saved_at"]["nullable"] is False
        assert inspector.get_pk_constraint("saved_verdicts")["constrained_columns"] == ["id"]
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("saved_verdicts")
        } == {"uq_saved_verdict_user_source"}
        assert {
            index["name"] for index in inspector.get_indexes("saved_verdicts")
        } == {"ix_saved_verdicts_org_user_saved_at"}
        fks = inspector.get_foreign_keys("saved_verdicts")
        assert {
            (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in fks
        } == {
            (("org_id",), "organizations", ("id",)),
            (("user_id",), "users", ("id",)),
        }

        run_with_ops(module, conn, "downgrade")
        assert "saved_verdicts" not in inspect(conn).get_table_names()


def test_migration_017_existing_correct_schema_noops_without_duplicate_indexes():
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        ctx = MigrationContext.configure(conn)
        ops = Operations(ctx)
        original_op = module.op
        module.op = ops
        try:
            create_expected_saved_verdicts_for_helper(conn, module)
            module.op.create_index(
                module.EXPECTED_INDEX,
                module.EXPECTED_TABLE,
                ["org_id", "user_id", "saved_at"],
            )
            module.upgrade()
            module.upgrade()
        finally:
            module.op = original_op

        inspector = inspect(conn)
        assert [index["name"] for index in inspector.get_indexes("saved_verdicts")].count(
            "ix_saved_verdicts_org_user_saved_at"
        ) == 1


def test_migration_017_repairs_missing_index_on_existing_table():
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        create_correct_saved_verdicts_without_index(conn)
        run_with_ops(module, conn, "upgrade")

        assert "ix_saved_verdicts_org_user_saved_at" in {
            index["name"] for index in inspect(conn).get_indexes("saved_verdicts")
        }


def test_migration_017_missing_unique_with_duplicates_fails_loudly():
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        conn.exec_driver_sql(
            """
            CREATE TABLE saved_verdicts (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                org_id VARCHAR(36) NOT NULL REFERENCES organizations(id),
                user_id VARCHAR(36) NOT NULL REFERENCES users(id),
                source_verdict_id VARCHAR(36) NOT NULL,
                source_turn_id VARCHAR(36),
                source_chat_id VARCHAR(36),
                source_chat_title VARCHAR(512) NOT NULL,
                source_user_message TEXT NOT NULL,
                verdict_text TEXT NOT NULL,
                verdict_reason TEXT NOT NULL,
                verdict_model_id VARCHAR(64) NOT NULL,
                strategy VARCHAR(32) NOT NULL,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO saved_verdicts (
                id, org_id, user_id, source_verdict_id, source_chat_title,
                source_user_message, verdict_text, verdict_reason, verdict_model_id, strategy
            )
            VALUES
                (
                    'saved-1', 'org-1', 'user-1', 'verdict-1', 'Chat',
                    'Prompt', 'Text', 'Reason', 'gemini', 'SYNTHESIZE'
                ),
                (
                    'saved-2', 'org-1', 'user-1', 'verdict-1', 'Chat',
                    'Prompt', 'Text', 'Reason', 'gemini', 'SYNTHESIZE'
                )
            """
        )

        with pytest.raises(RuntimeError, match="duplicate"):
            run_with_ops(module, conn, "upgrade")

        assert conn.exec_driver_sql("SELECT COUNT(*) FROM saved_verdicts").scalar_one() == 2


def test_migration_017_missing_required_column_fails_loudly():
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        conn.exec_driver_sql(
            """
            CREATE TABLE saved_verdicts (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                org_id VARCHAR(36) NOT NULL REFERENCES organizations(id),
                user_id VARCHAR(36) NOT NULL REFERENCES users(id),
                source_verdict_id VARCHAR(36) NOT NULL,
                source_turn_id VARCHAR(36),
                source_chat_id VARCHAR(36),
                source_chat_title VARCHAR(512) NOT NULL,
                verdict_text TEXT NOT NULL,
                verdict_reason TEXT NOT NULL,
                verdict_model_id VARCHAR(64) NOT NULL,
                strategy VARCHAR(32) NOT NULL,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT uq_saved_verdict_user_source
                    UNIQUE (org_id, user_id, source_verdict_id)
            )
            """
        )

        with pytest.raises(RuntimeError, match="missing required column source_user_message"):
            run_with_ops(module, conn, "upgrade")


def test_migration_017_wrong_column_type_fails_loudly():
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        create_correct_saved_verdicts_without_index(conn)
        with pytest.raises(RuntimeError, match="incompatible type"):
            conn.exec_driver_sql("DROP TABLE saved_verdicts")
            conn.exec_driver_sql(
                """
                CREATE TABLE saved_verdicts (
                    id INTEGER NOT NULL PRIMARY KEY,
                    org_id VARCHAR(36) NOT NULL REFERENCES organizations(id),
                    user_id VARCHAR(36) NOT NULL REFERENCES users(id),
                    source_verdict_id VARCHAR(36) NOT NULL,
                    source_turn_id VARCHAR(36),
                    source_chat_id VARCHAR(36),
                    source_chat_title VARCHAR(512) NOT NULL,
                    source_user_message TEXT NOT NULL,
                    verdict_text TEXT NOT NULL,
                    verdict_reason TEXT NOT NULL,
                    verdict_model_id VARCHAR(64) NOT NULL,
                    strategy VARCHAR(32) NOT NULL,
                    saved_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    CONSTRAINT uq_saved_verdict_user_source
                        UNIQUE (org_id, user_id, source_verdict_id)
                )
                """
            )
            run_with_ops(module, conn, "upgrade")


def test_migration_017_unexpected_source_turn_foreign_key_fails_loudly():
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        conn.exec_driver_sql("CREATE TABLE turns (id VARCHAR(36) PRIMARY KEY)")
        conn.exec_driver_sql(
            """
            CREATE TABLE saved_verdicts (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                org_id VARCHAR(36) NOT NULL REFERENCES organizations(id),
                user_id VARCHAR(36) NOT NULL REFERENCES users(id),
                source_verdict_id VARCHAR(36) NOT NULL,
                source_turn_id VARCHAR(36) REFERENCES turns(id) ON DELETE CASCADE,
                source_chat_id VARCHAR(36),
                source_chat_title VARCHAR(512) NOT NULL,
                source_user_message TEXT NOT NULL,
                verdict_text TEXT NOT NULL,
                verdict_reason TEXT NOT NULL,
                verdict_model_id VARCHAR(64) NOT NULL,
                strategy VARCHAR(32) NOT NULL,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT uq_saved_verdict_user_source
                    UNIQUE (org_id, user_id, source_verdict_id)
            )
            """
        )

        with pytest.raises(RuntimeError, match="unexpected foreign keys"):
            run_with_ops(module, conn, "upgrade")


def test_migration_017_fatal_drift_prevents_partial_column_repair():
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        conn.exec_driver_sql(
            """
            CREATE TABLE saved_verdicts (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                org_id VARCHAR(36) NOT NULL REFERENCES organizations(id),
                user_id VARCHAR(36) NOT NULL REFERENCES users(id),
                source_verdict_id VARCHAR(36) NOT NULL,
                source_turn_id VARCHAR(36),
                source_chat_title VARCHAR(512) NOT NULL,
                source_user_message TEXT NOT NULL,
                verdict_text TEXT NOT NULL,
                verdict_reason TEXT NOT NULL,
                verdict_model_id VARCHAR(64) NOT NULL,
                strategy VARCHAR(32) NOT NULL,
                saved_at DATETIME DEFAULT '2026-01-01' NOT NULL,
                CONSTRAINT uq_saved_verdict_user_source
                    UNIQUE (org_id, user_id, source_verdict_id)
            )
            """
        )

        with pytest.raises(RuntimeError, match="saved_at"):
            run_with_ops(module, conn, "upgrade")

        columns = {column["name"] for column in inspect(conn).get_columns("saved_verdicts")}
        assert "source_chat_id" not in columns


@pytest.mark.parametrize(
    "saved_at_definition",
    [
        "saved_at DATETIME NOT NULL",
        "saved_at DATETIME DEFAULT '2026-01-01' NOT NULL",
    ],
)
def test_migration_017_missing_or_incompatible_saved_at_default_fails_loudly(
    saved_at_definition,
):
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        conn.exec_driver_sql(
            f"""
            CREATE TABLE saved_verdicts (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                org_id VARCHAR(36) NOT NULL REFERENCES organizations(id),
                user_id VARCHAR(36) NOT NULL REFERENCES users(id),
                source_verdict_id VARCHAR(36) NOT NULL,
                source_turn_id VARCHAR(36),
                source_chat_id VARCHAR(36),
                source_chat_title VARCHAR(512) NOT NULL,
                source_user_message TEXT NOT NULL,
                verdict_text TEXT NOT NULL,
                verdict_reason TEXT NOT NULL,
                verdict_model_id VARCHAR(64) NOT NULL,
                strategy VARCHAR(32) NOT NULL,
                {saved_at_definition},
                CONSTRAINT uq_saved_verdict_user_source
                    UNIQUE (org_id, user_id, source_verdict_id)
            )
            """
        )

        with pytest.raises(RuntimeError, match="CURRENT_TIMESTAMP"):
            run_with_ops(module, conn, "upgrade")


def test_migration_017_accepts_equivalent_unique_and_index_with_different_names():
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        conn.exec_driver_sql(
            """
            CREATE TABLE saved_verdicts (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                org_id VARCHAR(36) NOT NULL REFERENCES organizations(id),
                user_id VARCHAR(36) NOT NULL REFERENCES users(id),
                source_verdict_id VARCHAR(36) NOT NULL,
                source_turn_id VARCHAR(36),
                source_chat_id VARCHAR(36),
                source_chat_title VARCHAR(512) NOT NULL,
                source_user_message TEXT NOT NULL,
                verdict_text TEXT NOT NULL,
                verdict_reason TEXT NOT NULL,
                verdict_model_id VARCHAR(64) NOT NULL,
                strategy VARCHAR(32) NOT NULL,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT uq_saved_verdict_equivalent
                    UNIQUE (org_id, user_id, source_verdict_id)
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE INDEX ix_saved_verdicts_equivalent_lookup
            ON saved_verdicts (org_id, user_id, saved_at)
            """
        )

        run_with_ops(module, conn, "upgrade")

        inspector = inspect(conn)
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("saved_verdicts")
        } == {("org_id", "user_id", "source_verdict_id")}
        assert {
            tuple(index["column_names"]) for index in inspector.get_indexes("saved_verdicts")
        } == {("org_id", "user_id", "saved_at")}


def test_migration_017_sqlite_missing_fk_fails_before_partial_index_repair():
    module = load_migration_017()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_dependencies(conn)
        conn.exec_driver_sql(
            """
            CREATE TABLE saved_verdicts (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                org_id VARCHAR(36) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                source_verdict_id VARCHAR(36) NOT NULL,
                source_turn_id VARCHAR(36),
                source_chat_id VARCHAR(36),
                source_chat_title VARCHAR(512) NOT NULL,
                source_user_message TEXT NOT NULL,
                verdict_text TEXT NOT NULL,
                verdict_reason TEXT NOT NULL,
                verdict_model_id VARCHAR(64) NOT NULL,
                strategy VARCHAR(32) NOT NULL,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT uq_saved_verdict_user_source
                    UNIQUE (org_id, user_id, source_verdict_id)
            )
            """
        )

        with pytest.raises(RuntimeError, match="SQLite cannot safely add it"):
            run_with_ops(module, conn, "upgrade")

        assert inspect(conn).get_indexes("saved_verdicts") == []
