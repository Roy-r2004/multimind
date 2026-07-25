"""Add hard-gate result JSON columns.

Revision ID: 024
Revises: 022
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "024"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rehabilitation_facilities",
        sa.Column("hard_gate_results_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "rehabilitation_facility_locations",
        sa.Column("hard_gate_results_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rehabilitation_facility_locations", "hard_gate_results_json")
    op.drop_column("rehabilitation_facilities", "hard_gate_results_json")
