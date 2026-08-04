"""Add contact_email column to maps_places."""

from alembic import op
import sqlalchemy as sa

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places",
        sa.Column("contact_email", sa.String(length=320), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_places", "contact_email")
