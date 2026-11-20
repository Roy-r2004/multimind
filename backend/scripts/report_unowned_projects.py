"""List legacy projects whose ownership migration could not safely infer an owner.

Run with the backend environment configured: ``python -m scripts.report_unowned_projects``.
This report is read-only; it does not assign ownership.
"""

import asyncio

from sqlalchemy import select

from app.db.models import Project
from app.db.session import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as db:
        projects = (
            await db.execute(
                select(Project.id, Project.org_id, Project.name, Project.created_at)
                .where(Project.created_by.is_(None))
                .order_by(Project.org_id, Project.created_at, Project.id)
            )
        ).all()
    for project_id, org_id, name, created_at in projects:
        print(f"{project_id}\t{org_id}\t{created_at}\t{name}")
    print(f"Unassigned projects: {len(projects)}")


if __name__ == "__main__":
    asyncio.run(main())
