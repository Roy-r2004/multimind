"""Add saved verdict snapshots

Revision ID: 017
Revises: 016
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.engine.reflection import Inspector

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


EXPECTED_TABLE = "saved_verdicts"
EXPECTED_INDEX = "ix_saved_verdicts_org_user_saved_at"
EXPECTED_INDEX_COLUMNS = ("org_id", "user_id", "saved_at")
EXPECTED_UNIQUE = "uq_saved_verdict_user_source"
EXPECTED_UNIQUE_COLUMNS = ("org_id", "user_id", "source_verdict_id")
EXPECTED_PRIMARY_KEY = ("id",)
EXPECTED_FOREIGN_KEYS = {
    "org_id": ("organizations", "id"),
    "user_id": ("users", "id"),
}
EXPECTED_COLUMNS = {
    "id": (sa.String, 36, False, False),
    "org_id": (sa.String, 36, False, False),
    "user_id": (sa.String, 36, False, False),
    "source_verdict_id": (sa.String, 36, False, False),
    "source_turn_id": (sa.String, 36, True, True),
    "source_chat_id": (sa.String, 36, True, True),
    "source_chat_title": (sa.String, 512, False, False),
    "source_user_message": (sa.Text, None, False, False),
    "verdict_text": (sa.Text, None, False, False),
    "verdict_reason": (sa.Text, None, False, False),
    "verdict_model_id": (sa.String, 64, False, False),
    "strategy": (sa.String, 32, False, False),
    "saved_at": (sa.DateTime, None, False, False),
}


def _drift_error(message: str) -> RuntimeError:
    return RuntimeError(f"{EXPECTED_TABLE} schema drift: {message}")


def _type_is_compatible(
    actual_type: sa.types.TypeEngine,
    expected_type: type,
    length: int | None,
    dialect_name: str,
) -> bool:
    if expected_type is sa.String:
        if not isinstance(actual_type, sa.String):
            return False
        return actual_type.length is None or length is None or actual_type.length >= length
    if expected_type is sa.Text:
        return isinstance(actual_type, (sa.Text, sa.String))
    if expected_type is sa.DateTime:
        if not isinstance(actual_type, sa.DateTime):
            return False
        return dialect_name == "sqlite" or getattr(actual_type, "timezone", None) is not False
    return isinstance(actual_type, expected_type)


def _column_def(name: str) -> sa.Column:
    column_type, length, nullable, _repairable = EXPECTED_COLUMNS[name]
    if column_type is sa.String:
        type_ = sa.String(length)
    elif column_type is sa.Text:
        type_ = sa.Text()
    elif column_type is sa.DateTime:
        type_ = sa.DateTime(timezone=True)
    else:
        type_ = column_type()
    kwargs = {"nullable": nullable}
    if name == "saved_at":
        kwargs["server_default"] = sa.text("CURRENT_TIMESTAMP")
    return sa.Column(name, type_, **kwargs)


def _is_current_timestamp_default(default: object) -> bool:
    if default is None:
        return False
    normalized = str(default).lower().strip()
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    normalized = normalized.replace("'", "").replace('"', "")
    return "current_timestamp" in normalized or "now()" in normalized


def _find_equivalent_index(inspector: Inspector) -> bool:
    for index in inspector.get_indexes(EXPECTED_TABLE):
        columns = tuple(index.get("column_names") or ())
        if index["name"] == EXPECTED_INDEX and columns != EXPECTED_INDEX_COLUMNS:
            raise _drift_error(
                f"index {EXPECTED_INDEX} has columns {index.get('column_names')}; "
                f"expected {EXPECTED_INDEX_COLUMNS}"
            )
        if columns == EXPECTED_INDEX_COLUMNS and not index.get("unique", False):
            return True
    return False


def _find_equivalent_unique_constraint(inspector: Inspector) -> bool:
    for constraint in inspector.get_unique_constraints(EXPECTED_TABLE):
        columns = tuple(constraint.get("column_names") or ())
        if constraint["name"] == EXPECTED_UNIQUE and columns != EXPECTED_UNIQUE_COLUMNS:
            raise _drift_error(
                f"unique constraint {EXPECTED_UNIQUE} has columns "
                f"{constraint.get('column_names')}; expected {EXPECTED_UNIQUE_COLUMNS}"
            )
        if columns == EXPECTED_UNIQUE_COLUMNS:
            return True
    return False


def _validate_primary_key(inspector: Inspector) -> None:
    pk = inspector.get_pk_constraint(EXPECTED_TABLE)
    if tuple(pk.get("constrained_columns") or ()) != EXPECTED_PRIMARY_KEY:
        raise _drift_error("primary key must be exactly (id); repair this table manually")


def _plan_column_repairs(inspector: Inspector) -> list[str]:
    dialect_name = op.get_bind().dialect.name
    repairs: list[str] = []
    columns = {column["name"]: column for column in inspector.get_columns(EXPECTED_TABLE)}
    for name, (expected_type, length, expected_nullable, repairable) in EXPECTED_COLUMNS.items():
        column = columns.get(name)
        if column is None:
            if not repairable:
                raise _drift_error(f"missing required column {name}; repair this table manually")
            repairs.append(name)
            continue
        if bool(column.get("nullable")) != expected_nullable:
            raise _drift_error(
                f"column {name} has nullable={column.get('nullable')}; "
                f"expected nullable={expected_nullable}"
            )
        if not _type_is_compatible(column["type"], expected_type, length, dialect_name):
            raise _drift_error(
                f"column {name} has incompatible type {column['type']}; repair this table manually"
            )
        if name == "saved_at" and not _is_current_timestamp_default(column.get("default")):
            raise _drift_error(
                "column saved_at must have a CURRENT_TIMESTAMP/now() server default; "
                "repair this table manually"
            )
    return repairs


def _duplicates_exist_for_expected_unique() -> bool:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM saved_verdicts
            GROUP BY org_id, user_id, source_verdict_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    return duplicate is not None


def _orphaned_rows_exist(column: str, target_table: str, target_column: str) -> bool:
    bind = op.get_bind()
    orphan = bind.execute(
        sa.text(
            f"""
            SELECT 1
            FROM saved_verdicts sv
            LEFT JOIN {target_table} target ON target.{target_column} = sv.{column}
            WHERE target.{target_column} IS NULL
            LIMIT 1
            """
        )
    ).first()
    return orphan is not None


def _plan_unique_constraint_repair(inspector: Inspector) -> bool:
    if _find_equivalent_unique_constraint(inspector):
        return False
    if _duplicates_exist_for_expected_unique():
        raise _drift_error(
            "missing uq_saved_verdict_user_source and duplicate "
            "(org_id, user_id, source_verdict_id) rows exist; deduplicate manually"
        )
    return True


def _plan_foreign_key_repairs(inspector: Inspector) -> list[tuple[str, str, str]]:
    foreign_keys = inspector.get_foreign_keys(EXPECTED_TABLE)
    unexpected = [
        fk
        for fk in foreign_keys
        if tuple(fk.get("constrained_columns") or ()) not in {("org_id",), ("user_id",)}
    ]
    if unexpected:
        raise _drift_error(
            "unexpected foreign keys exist; saved verdict snapshots must not cascade "
            "from chats, turns, or verdicts"
        )
    repairs: list[tuple[str, str, str]] = []
    for column, (target_table, target_column) in EXPECTED_FOREIGN_KEYS.items():
        matching = [
            fk for fk in foreign_keys if tuple(fk.get("constrained_columns") or ()) == (column,)
        ]
        wrong = [
            fk
            for fk in matching
            if fk.get("referred_table") != target_table
            or tuple(fk.get("referred_columns") or ()) != (target_column,)
            or fk.get("options", {}).get("ondelete") is not None
        ]
        if wrong:
            raise _drift_error(
                f"foreign key on {column} must reference {target_table}.{target_column} "
                "without ON DELETE behavior"
            )
        if matching:
            continue
        if _orphaned_rows_exist(column, target_table, target_column):
            raise _drift_error(
                f"missing foreign key on {column} and existing rows reference missing "
                f"{target_table}.{target_column}; repair data manually"
            )
        if op.get_bind().dialect.name == "sqlite":
            raise _drift_error(
                f"missing foreign key on {column}; SQLite cannot safely add it in-place"
            )
        repairs.append((column, target_table, target_column))
    return repairs


def _plan_index_repair(inspector: Inspector) -> bool:
    return not _find_equivalent_index(inspector)


def _apply_repairs(
    column_repairs: list[str],
    foreign_key_repairs: list[tuple[str, str, str]],
    unique_repair: bool,
    index_repair: bool,
) -> None:
    for column in column_repairs:
        op.add_column(EXPECTED_TABLE, _column_def(column))
    for column, target_table, target_column in foreign_key_repairs:
        op.create_foreign_key(
            f"fk_saved_verdicts_{column}_{target_table}",
            EXPECTED_TABLE,
            target_table,
            [column],
            [target_column],
        )
    if unique_repair:
        op.create_unique_constraint(EXPECTED_UNIQUE, EXPECTED_TABLE, list(EXPECTED_UNIQUE_COLUMNS))
    if index_repair:
        op.create_index(
            EXPECTED_INDEX,
            EXPECTED_TABLE,
            list(EXPECTED_INDEX_COLUMNS),
        )


def _validate_and_repair_existing_saved_verdicts_schema(inspector: Inspector) -> None:
    _validate_primary_key(inspector)
    column_repairs = _plan_column_repairs(inspector)
    foreign_key_repairs = _plan_foreign_key_repairs(inspector)
    unique_repair = _plan_unique_constraint_repair(inspector)
    index_repair = _plan_index_repair(inspector)
    _apply_repairs(column_repairs, foreign_key_repairs, unique_repair, index_repair)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    # Compatibility guard for databases that previously applied the old
    # conflicting 008_saved_verdicts migration before Saved Verdicts moved to 017.
    if EXPECTED_TABLE in inspector.get_table_names():
        _validate_and_repair_existing_saved_verdicts_schema(inspector)
        return

    op.create_table(
        EXPECTED_TABLE,
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
            "org_id", "user_id", "source_verdict_id", name="uq_saved_verdict_user_source"
        ),
    )
    op.create_index(
        EXPECTED_INDEX,
        EXPECTED_TABLE,
        ["org_id", "user_id", "saved_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if EXPECTED_TABLE not in inspector.get_table_names():
        return

    indexes = {index["name"] for index in inspector.get_indexes(EXPECTED_TABLE)}
    if EXPECTED_INDEX in indexes:
        op.drop_index(EXPECTED_INDEX, table_name=EXPECTED_TABLE)
    op.drop_table(EXPECTED_TABLE)
