"""Scraping mission business logic."""

from sqlalchemy import desc, func, select
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
    ScrapingRun,
)
from app.schemas.api import (
    ScrapingMissionCreate,
    ScrapingMissionDetail,
    ScrapingMissionSummary,
    ScrapingMissionUpdate,
)
from app.services.scraping.countries import resolve_country

# Legacy compatibility only: scraping_missions.model_set_id and scraping_blueprints.model_set_id
# are plain non-null String(64) columns with no foreign key to model_sets. Country-blueprint
# missions store this placeholder so the legacy columns stay satisfiable while the scraper stays
# independent of Chat model sets; it is never resolved, displayed, or used to pick an AI model.
SCRAPER_FIXED_MODEL_SET_ID = "scraper-fixed"
# Legacy compatibility only: scraping_missions.original_prompt is non-null, and the mission
# instruction is now owned by the backend blueprint prompt template instead of user input.
BACKEND_OWNED_MISSION_PROMPT = "Generate the backend-owned country-specific blueprint."


class ScrapingMissionService:
    async def create_mission(
        self, db: AsyncSession, auth: AuthContext, data: ScrapingMissionCreate
    ) -> ScrapingMissionDetail:
        title = data.title.strip()
        if not title:
            raise ValidationError("Mission title is required")
        country = resolve_country(data.country or data.country_code or "")

        if data.project_id is not None:
            await self.resolve_project(db, auth, data.project_id)

        mission = ScrapingMission(
            org_id=auth.org_id,
            created_by=auth.user.id,
            project_id=data.project_id,
            # Legacy non-null column retained for historical scraper records.
            # New country-blueprint missions never resolve or use Chat model sets.
            model_set_id=SCRAPER_FIXED_MODEL_SET_ID,
            title=title,
            original_prompt=BACKEND_OWNED_MISSION_PROMPT,
            country_code=country.code,
            country_name=country.name,
            country_iso3=country.iso3,
            continent=country.continent,
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
                country_code=mission.country_code,
                country_name=mission.country_name,
                country_iso3=mission.country_iso3,
                continent=mission.continent,
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
        return ScrapingMissionDetail(
            id=mission.id,
            title=mission.title,
            original_prompt=mission.original_prompt,
            status=mission.status.value,
            country_code=mission.country_code,
            country_name=mission.country_name,
            country_iso3=mission.country_iso3,
            continent=mission.continent,
            active_blueprint_id=mission.active_blueprint_id,
            active_blueprint_version=active_version,
            created_at=mission.created_at,
            updated_at=mission.updated_at,
            created_by=mission.created_by,
            project_id=mission.project_id,
            project_name=mission.project.name if mission.project else None,
            model_set_id=mission.model_set_id,
            model_set_name=None,
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
        country_fields = {"country", "country_code"} & data.model_fields_set
        if country_fields:
            if bool(data.country) == bool(data.country_code):
                raise ValidationError("Country is required")
            await self._apply_country_update(db, mission, data.country or data.country_code or "")
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
        # SCRAPER_FIXED_MODEL_SET_ID is a legacy placeholder, never a Chat model-set slug,
        # so historical callers resolve their default set instead of a non-existent row.
        if model_set_id == SCRAPER_FIXED_MODEL_SET_ID:
            model_set_id = None
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

    async def _apply_country_update(
        self, db: AsyncSession, mission: ScrapingMission, country_code: str
    ) -> None:
        country = resolve_country(country_code)
        if mission.country_code == country.code:
            mission.country_name = country.name
            mission.country_iso3 = country.iso3
            mission.continent = country.continent
            return
        if mission.country_code is None:
            mission.country_code = country.code
            mission.country_name = country.name
            mission.country_iso3 = country.iso3
            mission.continent = country.continent
            return

        blueprint_count_result = await db.execute(
            select(func.count(ScrapingBlueprint.id)).where(ScrapingBlueprint.mission_id == mission.id)
        )
        run_count_result = await db.execute(
            select(func.count(ScrapingRun.id)).where(ScrapingRun.mission_id == mission.id)
        )
        if blueprint_count_result.scalar_one() > 0 or run_count_result.scalar_one() > 0:
            raise ConflictError(
                "Country cannot be changed after a blueprint or AI team plan exists. "
                "Create a new mission for another country."
            )

        mission.country_code = country.code
        mission.country_name = country.name
        mission.country_iso3 = country.iso3
        mission.continent = country.continent


mission_service = ScrapingMissionService()
