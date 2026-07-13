"""Project, model set, template, and cost services."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError
from app.db.models import Chat, CostRecord, ModelSet, Project, ScrapingMission, Strategy, Template
from app.schemas.api import (
    CostSummaryResponse,
    ChatResponse,
    ModelSetCreateRequest,
    ModelSetResponse,
    ModelSetUpdateRequest,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectResponse,
    ProjectUpdateRequest,
    TemplateCreateRequest,
    TemplateResponse,
)


class ProjectService:
    async def list(self, db: AsyncSession, auth: AuthContext) -> list[ProjectResponse]:
        result = await db.execute(
            select(Project, func.count(Chat.id))
            .outerjoin(Chat, Chat.project_id == Project.id)
            .where(Project.org_id == auth.org_id)
            .group_by(Project.id)
            .order_by(Project.updated_at.desc())
        )
        return [
            ProjectResponse(
                id=p.id,
                name=p.name,
                description=p.description,
                chat_count=count,
                updated_at=p.updated_at,
            )
            for p, count in result.all()
        ]

    async def create(
        self, db: AsyncSession, auth: AuthContext, data: ProjectCreateRequest
    ) -> ProjectResponse:
        project = Project(
            org_id=auth.org_id,
            name=data.name.strip(),
            description=data.description,
        )
        db.add(project)
        await db.flush()
        return ProjectResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            chat_count=0,
            updated_at=project.updated_at,
        )

    async def get(self, db: AsyncSession, auth: AuthContext, project_id: str) -> Project:
        result = await db.execute(
            select(Project).where(Project.id == project_id, Project.org_id == auth.org_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise NotFoundError("Project", project_id)
        return project

    async def get_detail(
        self, db: AsyncSession, auth: AuthContext, project_id: str
    ) -> ProjectDetailResponse:
        project = await self.get(db, auth, project_id)
        result = await db.execute(
            select(Chat)
            .where(Chat.project_id == project.id, Chat.org_id == auth.org_id)
            .order_by(Chat.updated_at.desc())
        )
        chats = result.scalars().all()
        return ProjectDetailResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            chat_count=len(chats),
            updated_at=project.updated_at,
            chats=[
                ChatResponse(
                    id=c.id,
                    title=c.title,
                    project_id=c.project_id,
                    updated_at=c.updated_at,
                )
                for c in chats
            ],
        )

    async def update(
        self,
        db: AsyncSession,
        auth: AuthContext,
        project_id: str,
        data: ProjectUpdateRequest,
    ) -> ProjectDetailResponse:
        project = await self.get(db, auth, project_id)
        if data.name is not None:
            project.name = data.name.strip()
        if "description" in data.__fields_set__:
            project.description = (data.description.strip() or None  
                                   if data.description is not None else None)
        await db.flush()
        return await self.get_detail(db, auth, project_id)

    async def delete(self, db: AsyncSession, auth: AuthContext, project_id: str) -> None:
        project = await self.get(db, auth, project_id)
        await db.execute(
            update(Chat)
            .where(Chat.project_id == project.id, Chat.org_id == auth.org_id)
            .values(project_id=None)
        )
        await db.execute(
            update(ScrapingMission)
            .where(ScrapingMission.project_id == project.id, ScrapingMission.org_id == auth.org_id)
            .values(project_id=None)
        )
        await db.delete(project)


class ModelSetService:
    async def list(self, db: AsyncSession, auth: AuthContext) -> list[ModelSetResponse]:
        result = await db.execute(
            select(ModelSet)
            .where((ModelSet.org_id == auth.org_id) | (ModelSet.is_system.is_(True)))
            .order_by(ModelSet.is_system.desc(), ModelSet.name)
        )
        return [self._response(s) for s in result.scalars().all()]

    async def create(
        self, db: AsyncSession, auth: AuthContext, data: ModelSetCreateRequest
    ) -> ModelSetResponse:
        slug = f"set-{uuid4().hex[:8]}"
        model_set = ModelSet(
            org_id=auth.org_id,
            slug=slug,
            name=data.name,
            description=data.description,
            models=data.models,
            verdict_model=data.verdict_model,
            strategy=Strategy(data.strategy.value),
            best_for=data.best_for or data.description,
            template_name=data.template_name,
            custom_instructions=data.custom_instructions,
            is_system=False,
        )
        db.add(model_set)
        await db.flush()
        return self._response(model_set)

    async def update(
        self, db: AsyncSession, auth: AuthContext, slug: str, data: ModelSetUpdateRequest
    ) -> ModelSetResponse:
        model_set = await self._get_editable(db, auth, slug)
        if data.name is not None:
            model_set.name = data.name
        if data.description is not None:
            model_set.description = data.description
        if data.models is not None:
            model_set.models = data.models
        if data.verdict_model is not None:
            model_set.verdict_model = data.verdict_model
        if data.strategy is not None:
            model_set.strategy = Strategy(data.strategy.value)
        if data.best_for is not None:
            model_set.best_for = data.best_for
        if data.template_name is not None:
            model_set.template_name = data.template_name
        if data.custom_instructions is not None:
            model_set.custom_instructions = data.custom_instructions
        await db.flush()
        return self._response(model_set)

    async def delete(self, db: AsyncSession, auth: AuthContext, slug: str) -> None:
        model_set = await self._get_editable(db, auth, slug)
        in_use = await db.execute(
            select(ScrapingMission.id).where(
                ScrapingMission.org_id == auth.org_id,
                ScrapingMission.model_set_id == model_set.slug,
            )
        )
        if in_use.scalar_one_or_none() is not None:
            raise ConflictError("Model set is used by a scraping mission")
        await db.delete(model_set)

    async def _get_editable(self, db: AsyncSession, auth: AuthContext, slug: str) -> ModelSet:
        result = await db.execute(
            select(ModelSet).where(
                ModelSet.slug == slug,
                ModelSet.org_id == auth.org_id,
                ModelSet.is_system.is_(False),
            )
        )
        model_set = result.scalar_one_or_none()
        if model_set is None:
            raise NotFoundError("ModelSet", slug)
        return model_set

    def _response(self, s: ModelSet) -> ModelSetResponse:
        return ModelSetResponse(
            id=s.slug,
            name=s.name,
            description=s.description,
            models=list(s.models),
            verdict_model=s.verdict_model,
            strategy=s.strategy,
            best_for=s.best_for,
            template_name=s.template_name,
            custom_instructions=s.custom_instructions,
            is_system=s.is_system,
        )


class TemplateService:
    async def list(self, db: AsyncSession, auth: AuthContext) -> list[TemplateResponse]:
        result = await db.execute(
            select(Template)
            .where((Template.org_id == auth.org_id) | (Template.is_system.is_(True)))
            .order_by(Template.is_system.desc(), Template.title)
        )
        return [TemplateResponse.model_validate(t) for t in result.scalars().all()]

    async def create(
        self, db: AsyncSession, auth: AuthContext, data: TemplateCreateRequest
    ) -> TemplateResponse:
        template = Template(
            org_id=auth.org_id,
            title=data.title,
            description=data.description,
            category=data.category,
            instructions=data.instructions,
            is_system=False,
        )
        db.add(template)
        await db.flush()
        return TemplateResponse.model_validate(template)


class CostService:
    async def summary(self, db: AsyncSession, auth: AuthContext) -> CostSummaryResponse:
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())
        month_start = day_start.replace(day=1)

        async def sum_since(since: datetime) -> tuple[float, int]:
            result = await db.execute(
                select(
                    func.coalesce(func.sum(CostRecord.cost_usd), 0.0),
                    func.coalesce(
                        func.sum(CostRecord.tokens_input + CostRecord.tokens_output), 0
                    ),
                ).where(CostRecord.org_id == auth.org_id, CostRecord.recorded_at >= since)
            )
            row = result.one()
            return float(row[0]), int(row[1])

        today_usd, _ = await sum_since(day_start)
        week_usd, _ = await sum_since(week_start)
        month_usd, month_tokens = await sum_since(month_start)

        result = await db.execute(
            select(
                CostRecord.model_id,
                func.sum(CostRecord.cost_usd),
                func.sum(CostRecord.tokens_input + CostRecord.tokens_output),
            )
            .where(CostRecord.org_id == auth.org_id, CostRecord.recorded_at >= month_start)
            .group_by(CostRecord.model_id)
        )
        by_model = [
            {
                "model_id": row[0],
                "cost_usd": float(row[1]),
                "tokens": int(row[2]),
            }
            for row in result.all()
        ]

        from app.db.models import Organization

        org = await db.get(Organization, auth.org_id)
        budget = (org.monthly_budget_cents / 100.0) if org else 50.0
        used_pct = min(100.0, (month_usd / budget * 100) if budget else 0)

        return CostSummaryResponse(
            today_usd=today_usd,
            week_usd=week_usd,
            month_usd=month_usd,
            month_tokens=month_tokens,
            budget_usd=budget,
            budget_used_pct=used_pct,
            by_model=by_model,
        )


project_service = ProjectService()
model_set_service = ModelSetService()
template_service = TemplateService()
cost_service = CostService()
