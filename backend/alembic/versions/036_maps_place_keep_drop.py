"""Add keep/drop decision fields to maps_places."""

from alembic import op
import sqlalchemy as sa

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places",
        sa.Column("keep_drop_decision", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("keep_drop_reason", sa.String(length=400), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("keep_drop_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("keep_drop_source", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("keep_drop_evidence", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_maps_places_run_keep_drop",
        "maps_places",
        ["run_id", "keep_drop_decision"],
    )


def downgrade() -> None:
    op.drop_index("ix_maps_places_run_keep_drop", table_name="maps_places")
    op.drop_column("maps_places", "keep_drop_evidence")
    op.drop_column("maps_places", "keep_drop_source")
    op.drop_column("maps_places", "keep_drop_confidence")
    op.drop_column("maps_places", "keep_drop_reason")
    op.drop_column("maps_places", "keep_drop_decision")
