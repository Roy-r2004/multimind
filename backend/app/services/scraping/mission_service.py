"""Scraping mission business logic."""

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.db.models import (
    ModelSet,
    Project,
    ScrapingBlueprint,
    ScrapingMission,
    ScrapingMissionStatus,
)
from app.schemas.api import (
    ScrapingMissionCreate,
    ScrapingMissionDetail,
    ScrapingMissionSummary,
    ScrapingMissionUpdate,
)


class ScrapingMissionService:
    async def create_mission(
        self, db: AsyncSession, auth: AuthContext, data: ScrapingMissionCreate
    ) -> ScrapingMissionDetail:
        title = data.title.strip()
        prompt = data.original_prompt.strip()
        if not title:
            raise ValidationError("Mission title is required")
        if not prompt:
            raise ValidationError("Mission prompt is required")

        model_set = await self.resolve_model_set(db, auth, data.model_set_id)
        if data.project_id is not None:
            await self.resolve_project(db, auth, data.project_id)

        mission = ScrapingMission(
            org_id=auth.org_id,
            created_by=auth.user.id,
            project_id=data.project_id,
            model_set_id=model_set.slug,
            title=title,
            original_prompt=prompt,
        )
        db.add(mission)
        await db.flush()
        return await self.get_mission(db, auth, mission.id)

    async def list_missions(
        self, db: AsyncSession, auth: AuthContext
    ) -> list[ScrapingMissionSummary]:
        rows = await db.execute(
            select(ScrapingMission, ScrapingBlueprint.version, Project.name)
            .outerjoin(
                ScrapingBlueprint,
                ScrapingBlueprint.id == ScrapingMission.active_blueprint_id,
            )
            .outerjoin(Project, Project.id == ScrapingMission.project_id)
            .where(ScrapingMission.org_id == auth.org_id)
            .order_by(ScrapingMission.updated_at.desc())
        )
        return [
            ScrapingMissionSummary(
                id=mission.id,
                title=mission.title,
                original_prompt=mission.original_prompt,
                status=mission.status.value,
                project_id=mission.project_id,
                project_name=project_name,
                active_blueprint_id=mission.active_blueprint_id,
                active_blueprint_version=version,
                created_at=mission.created_at,
                updated_at=mission.updated_at,
            )
            for mission, version, project_name in rows.all()
        ]

    async def get_mission(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> ScrapingMissionDetail:
        mission = await self.get_mission_row(db, auth, mission_id)
        active_version = None
        if mission.active_blueprint_id:
            result = await db.execute(
                select(ScrapingBlueprint.version).where(
                    ScrapingBlueprint.id == mission.active_blueprint_id
                )
            )
            active_version = result.scalar_one_or_none()
        model_set = await self.resolve_model_set(db, auth, mission.model_set_id)
        return ScrapingMissionDetail(
            id=mission.id,
            title=mission.title,
            original_prompt=mission.original_prompt,
            status=mission.status.value,
            active_blueprint_id=mission.active_blueprint_id,
            active_blueprint_version=active_version,
            created_at=mission.created_at,
            updated_at=mission.updated_at,
            created_by=mission.created_by,
            project_id=mission.project_id,
            project_name=mission.project.name if mission.project else None,
            model_set_id=mission.model_set_id,
            model_set_name=model_set.name,
        )

    async def update_mission(
        self,
        db: AsyncSession,
        auth: AuthContext,
        mission_id: str,
        data: ScrapingMissionUpdate,
    ) -> ScrapingMissionDetail:
        mission = await self.get_mission_row(db, auth, mission_id)
        if data.title is not None:
            title = data.title.strip()
            if not title:
                raise ValidationError("Mission title is required")
            mission.title = title
        if "project_id" in data.model_fields_set:
            project = None
            if data.project_id is not None:
                project = await self.resolve_project(db, auth, data.project_id)
            mission.project_id = data.project_id
            mission.project = project
        await db.flush()
        return await self.get_mission(db, auth, mission_id)

    async def delete_mission(self, db: AsyncSession, auth: AuthContext, mission_id: str) -> None:
        mission = await self.get_mission_row(db, auth, mission_id)
        if mission.status == ScrapingMissionStatus.BLUEPRINT_GENERATING:
            raise ConflictError("A mission cannot be deleted while its blueprint is generating.")
        await db.delete(mission)
        await db.flush()

    async def get_mission_row(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> ScrapingMission:
        result = await db.execute(
            select(ScrapingMission)
            .where(ScrapingMission.id == mission_id, ScrapingMission.org_id == auth.org_id)
            .options(selectinload(ScrapingMission.project))
        )
        mission = result.scalar_one_or_none()
        if mission is None:
            raise NotFoundError("ScrapingMission", mission_id)
        return mission

    async def resolve_model_set(
        self, db: AsyncSession, auth: AuthContext, model_set_id: str | None
    ) -> ModelSet:
        query = select(ModelSet).where(
            (ModelSet.org_id == auth.org_id) | (ModelSet.is_system.is_(True))
        )
        if model_set_id is not None:
            query = query.where(ModelSet.slug == model_set_id)
        query = query.order_by(
            desc(ModelSet.org_id == auth.org_id),
            ModelSet.is_system.asc(),
            ModelSet.updated_at.desc(),
            ModelSet.created_at.desc(),
            ModelSet.id.asc(),
        ).limit(1)
        result = await db.execute(query)
        model_set = result.scalars().first()
        if model_set is None and model_set_id is None:
            result = await db.execute(
                select(ModelSet)
                .where(ModelSet.is_system.is_(True))
                .order_by(ModelSet.updated_at.desc(), ModelSet.created_at.desc(), ModelSet.id.asc())
                .limit(1)
            )
            model_set = result.scalars().first()
        if model_set is None:
            raise NotFoundError("ModelSet", model_set_id or "default")
        return model_set

    async def resolve_project(
        self, db: AsyncSession, auth: AuthContext, project_id: str
    ) -> Project:
        result = await db.execute(
            select(Project).where(Project.id == project_id, Project.org_id == auth.org_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise NotFoundError("Project", project_id)
        return project


mission_service = ScrapingMissionService()
