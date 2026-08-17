"""Project, model set, template, and cost services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from re import sub as re_sub
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import (
    AppError,
    ConflictError,
    ForbiddenError,
    InternalServerError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.models import Chat, CostRecord, ModelSet, Project, ScrapingMission, Strategy, Template
from app.llm.catalog import is_builtin_model_id
from app.schemas.api import (
    CostSummaryResponse,
    ChatResponse,
    ModelSetCreateRequest,
    ModelSetResponse,
    ModelSetUpdateRequest,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectResponse,
    ProjectScrapingMissionResponse,
    ProjectUpdateRequest,
    TemplateCreateRequest,
    TemplateResponse,
)

logger = get_logger(__name__)

# Keep aligned with model_sets / org_models column widths (see migration 040).
MODEL_ID_MAX_LEN = 128
BEST_FOR_MAX_LEN = 512
TEMPLATE_NAME_MAX_LEN = 255
# System sets that may be PATCHed in place. Delete remains forbidden for all system sets.
UPDATABLE_SYSTEM_SLUGS = frozenset({"set-7edaefc8"})


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
        mission_result = await db.execute(
            select(ScrapingMission)
            .where(ScrapingMission.project_id == project.id, ScrapingMission.org_id == auth.org_id)
            .order_by(ScrapingMission.updated_at.desc())
        )
        scraping_missions = mission_result.scalars().all()
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
                    model_set_id=c.model_set_id,
                    updated_at=c.updated_at,
                )
                for c in chats
            ],
            scraping_missions=[
                ProjectScrapingMissionResponse(
                    id=mission.id,
                    title=mission.title,
                    status=mission.status.value,
                    project_id=mission.project_id,
                    country_code=mission.country_code,
                    country_name=mission.country_name,
                    active_blueprint_id=mission.active_blueprint_id,
                    created_at=mission.created_at,
                    updated_at=mission.updated_at,
                )
                for mission in scraping_missions
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
        fields = self._submitted_field_names(data)
        try:
            if not auth.org_id:
                raise ForbiddenError("Organization context required to create a model set")
            self._validate_model_ids(data.models, data.verdict_model)
            best_for = self._resolve_best_for(data.best_for, data.description)
            if data.template_name is not None and len(data.template_name) > TEMPLATE_NAME_MAX_LEN:
                raise ValidationError(
                    f"template_name must be at most {TEMPLATE_NAME_MAX_LEN} characters"
                )
            model_set = ModelSet(
                org_id=auth.org_id,
                slug=slug,
                name=data.name,
                description=data.description,
                models=list(data.models),
                verdict_model=data.verdict_model,
                strategy=Strategy(data.strategy.value),
                best_for=best_for,
                template_name=data.template_name,
                custom_instructions=data.custom_instructions,
                is_system=False,
            )
            db.add(model_set)
            await db.flush()
            await db.commit()
            return self._response(model_set)
        except AppError as exc:
            self._log_failure(
                operation="create",
                auth=auth,
                slug=slug,
                field_names=fields,
                exc=exc,
            )
            raise
        except Exception as exc:
            self._log_failure(
                operation="create",
                auth=auth,
                slug=slug,
                field_names=fields,
                exc=exc,
                include_traceback=True,
            )
            raise self._map_db_exception(exc) from exc

    async def update(
        self, db: AsyncSession, auth: AuthContext, slug: str, data: ModelSetUpdateRequest
    ) -> ModelSetResponse:
        fields = self._submitted_field_names(data, exclude_unset=True)
        try:
            model_set = await self._get_updatable(db, auth, slug)
            if data.name is not None:
                model_set.name = data.name
            if data.description is not None:
                model_set.description = data.description
            if data.models is not None or data.verdict_model is not None:
                models = data.models if data.models is not None else list(model_set.models)
                verdict = (
                    data.verdict_model
                    if data.verdict_model is not None
                    else model_set.verdict_model
                )
                self._validate_model_ids(models, verdict)
            if data.models is not None:
                model_set.models = list(data.models)
            if data.verdict_model is not None:
                model_set.verdict_model = data.verdict_model
            if data.strategy is not None:
                model_set.strategy = Strategy(data.strategy.value)
            if data.best_for is not None:
                model_set.best_for = self._resolve_best_for(data.best_for, None)
            if data.template_name is not None:
                if len(data.template_name) > TEMPLATE_NAME_MAX_LEN:
                    raise ValidationError(
                        f"template_name must be at most {TEMPLATE_NAME_MAX_LEN} characters"
                    )
                model_set.template_name = data.template_name
            if data.custom_instructions is not None:
                model_set.custom_instructions = data.custom_instructions
            await db.flush()
            await db.commit()
            return self._response(model_set)
        except AppError as exc:
            self._log_failure(
                operation="update",
                auth=auth,
                slug=slug,
                field_names=fields,
                exc=exc,
            )
            raise
        except Exception as exc:
            self._log_failure(
                operation="update",
                auth=auth,
                slug=slug,
                field_names=fields,
                exc=exc,
                include_traceback=True,
            )
            raise self._map_db_exception(exc) from exc

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

    async def _get_updatable(self, db: AsyncSession, auth: AuthContext, slug: str) -> ModelSet:
        result = await db.execute(select(ModelSet).where(ModelSet.slug == slug))
        model_set = result.scalar_one_or_none()
        if model_set is None:
            raise NotFoundError("ModelSet", slug)
        if model_set.is_system:
            if model_set.slug not in UPDATABLE_SYSTEM_SLUGS:
                raise ForbiddenError("System model sets cannot be modified")
            return model_set
        if model_set.org_id != auth.org_id:
            raise ForbiddenError("Model set belongs to another organization")
        return model_set

    async def _get_editable(self, db: AsyncSession, auth: AuthContext, slug: str) -> ModelSet:
        result = await db.execute(select(ModelSet).where(ModelSet.slug == slug))
        model_set = result.scalar_one_or_none()
        if model_set is None:
            raise NotFoundError("ModelSet", slug)
        if model_set.is_system:
            raise ForbiddenError("System model sets cannot be modified")
        if model_set.org_id != auth.org_id:
            raise ForbiddenError("Model set belongs to another organization")
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

    @staticmethod
    def _submitted_field_names(data: Any, *, exclude_unset: bool = False) -> list[str]:
        payload = data.model_dump(exclude_unset=exclude_unset)
        return sorted(str(key) for key, value in payload.items() if value is not None or not exclude_unset)

    @staticmethod
    def _validate_one_model_id(model_id: str, *, field: str) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValidationError(f"Invalid {field}: model id cannot be empty")
        if len(model_id) > MODEL_ID_MAX_LEN:
            raise ValidationError(
                f"Invalid {field}: model id exceeds {MODEL_ID_MAX_LEN} characters"
            )
        if is_builtin_model_id(model_id):
            return
        if model_id.startswith("or:") and len(model_id) > 3:
            return
        raise ValidationError(
            f"Invalid {field}: unknown model id '{model_id}'. "
            "Use a built-in shortcut or an or:<openrouter-slug> id."
        )

    def _validate_model_ids(self, models: list[str], verdict_model: str) -> None:
        if len(models) < 1 or len(models) > 5:
            raise ValidationError("models must contain between 1 and 5 model ids")
        for model_id in models:
            self._validate_one_model_id(model_id, field="models")
        self._validate_one_model_id(verdict_model, field="verdict_model")

    @staticmethod
    def _resolve_best_for(best_for: str | None, description: str | None) -> str:
        """Map UI blurb into VARCHAR(512) best_for without overflowing Postgres."""
        raw = (best_for if best_for is not None and best_for != "" else None) or description or ""
        if len(raw) > BEST_FOR_MAX_LEN:
            # Description is Text; best_for is a short label. Truncate rather than 500.
            return raw[:BEST_FOR_MAX_LEN]
        return raw

    @staticmethod
    def _sanitize_exc_message(exc: BaseException) -> str:
        message = str(exc)
        message = re_sub(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)=[^\s]+", r"\1=[REDACTED]", message)
        message = re_sub(
            r"(?i)(postgres(?:ql)?|mysql|mongodb)://[^\s]+",
            "[REDACTED_DB_URL]",
            message,
        )
        message = re_sub(r"(?i)\bauthorization\s*:\s*\S+(?:\s+\S+)*", "authorization: [REDACTED]", message)
        message = re_sub(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", message)
        return message[:800]

    def _log_failure(
        self,
        *,
        operation: str,
        auth: AuthContext,
        slug: str | None,
        field_names: list[str],
        exc: BaseException,
        include_traceback: bool = False,
    ) -> None:
        # Never log custom_instructions / prompt bodies — only field names.
        payload = {
            "operation": operation,
            "user_id": getattr(auth.user, "id", None),
            "org_id": auth.org_id,
            "model_set_slug": slug,
            "submitted_fields": field_names,
            "exception_class": type(exc).__name__,
            "exception_message": self._sanitize_exc_message(exc),
        }
        if include_traceback:
            logger.exception("model_set_write_failed", **payload)
        else:
            logger.warning("model_set_write_failed", **payload)

    def _map_db_exception(self, exc: BaseException) -> AppError:
        message = self._sanitize_exc_message(exc).lower()
        if isinstance(exc, DBAPIError) and (
            "permission denied" in message or "insufficient privilege" in message
        ):
            return InternalServerError(
                "Database rejected the write — check role INSERT/UPDATE privileges on model_sets"
            )
        if isinstance(exc, IntegrityError) or "foreign key" in message:
            return ConflictError(
                "Model set could not be saved due to an organization or reference conflict"
            )
        if "unique" in message and "constraint" in message:
            return ConflictError("Model set conflicts with an existing record")
        if isinstance(exc, DataError) or "value too long" in message or "right truncation" in message:
            return ValidationError(
                "One or more fields exceed the database column length "
                f"(verdict_model ≤ {MODEL_ID_MAX_LEN}, best_for ≤ {BEST_FOR_MAX_LEN})"
            )
        return InternalServerError("Unexpected error while saving model set")


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
        await db.commit()
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
