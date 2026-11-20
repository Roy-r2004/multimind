"""Add an owner to projects and infer ownership for legacy rows.

Revision ID: 067_project_ownership
Revises: 064
"""

from alembic import op
import sqlalchemy as sa


revision = "067_project_ownership"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("created_by", sa.String(length=36), nullable=True))
    op.create_index("ix_projects_created_by", "projects", ["created_by"])

    # Existing ownership can be inferred when the project has chats or missions
    # and every such resource was created by the same user. Ambiguous and empty
    # legacy projects remain unassigned and are visible only to org admins.
    op.execute(
        """
        UPDATE projects
        SET created_by = (
            SELECT MIN(owner_id)
            FROM (
                SELECT chats.project_id AS project_id, chats.created_by AS owner_id
                FROM chats
                WHERE chats.project_id IS NOT NULL
                UNION ALL
                SELECT scraping_missions.project_id AS project_id,
                       scraping_missions.created_by AS owner_id
                FROM scraping_missions
                WHERE scraping_missions.project_id IS NOT NULL
            ) AS owners
            WHERE owners.project_id = projects.id
            GROUP BY owners.project_id
            HAVING COUNT(DISTINCT owners.owner_id) = 1
        )
        WHERE EXISTS (
            SELECT 1 FROM chats WHERE chats.project_id = projects.id
        ) OR EXISTS (
            SELECT 1 FROM scraping_missions WHERE scraping_missions.project_id = projects.id
        )
        """
    )
    op.create_foreign_key(
        "fk_projects_created_by_users", "projects", "users", ["created_by"], ["id"]
    )


def downgrade() -> None:
    op.drop_index("ix_projects_created_by", table_name="projects")
    op.drop_constraint("fk_projects_created_by_users", "projects", type_="foreignkey")
    op.drop_column("projects", "created_by")
