"""Add maps_places.inpatient_treatment.

Corrects an overly strict Phase 2 eligibility gap: the structured AI
classifier has always extracted an ``inpatient_treatment`` boolean signal
(confirmed inpatient addiction treatment, distinct from outpatient/day/
partial-hospitalization programs), but it was discarded before ever
reaching this model — only ``residential_accommodation`` and
``care_setting`` were persisted, and compute_client_eligibility's
confirmed_residential check only recognized those two. A facility with
clear, credible evidence of confirmed inpatient (or residential, or
24-hour) addiction care already satisfies the mandatory inpatient/
residential-treatment requirement on its own; it must never need an
*additional* explicit "patients stay overnight" statement on top of that.

Purely additive; nullable for every place classified before this
migration (re-classification, not backfill, populates it going forward).

Revision ID: gosom064_inpatient_treatment
Revises: gosom063_health_state
"""

import sqlalchemy as sa
from alembic import op

revision = "gosom064_inpatient_treatment"
down_revision = "gosom063_health_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places",
        sa.Column("inpatient_treatment", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_places", "inpatient_treatment")
