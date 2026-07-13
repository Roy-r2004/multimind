"""Scraping blueprint business logic."""

from datetime import UTC, datetime
from typing import Any
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import (
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingMission,
    ScrapingMissionStatus,
)
from app.schemas.api import (
    ScrapingBlueprintChangeRequest,
    ScrapingBlueprintContent,
    ScrapingBlueprintResponse,
    ScrapingBlueprintRejectRequest,
)
from app.scraping.blueprint_orchestrator import get_blueprint_orchestrator
from app.services.scraping.mission_service import mission_service


class ScrapingBlueprintService:
    async def generate_blueprint(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> ScrapingBlueprintResponse:
        mission = await mission_service.get_mission_row(db, auth, mission_id)
        if mission.status == ScrapingMissionStatus.BLUEPRINT_GENERATING:
            raise ValidationError("Blueprint generation is already in progress")

        model_set = await mission_service.resolve_model_set(db, auth, mission.model_set_id)
        version = await self._next_version(db, mission.id)
        blueprint = ScrapingBlueprint(
            mission_id=mission.id,
            version=version,
            status=ScrapingBlueprintStatus.GENERATING,
            model_set_id=model_set.slug,
            judge_model_id=self._judge_model_id(model_set),
        )
        mission.status = ScrapingMissionStatus.BLUEPRINT_GENERATING
        db.add(blueprint)
        await db.commit()
        await db.refresh(blueprint)

        try:
            content = await get_blueprint_orchestrator().generate(mission, model_set)
            validated = content.model_dump(mode="json")
            blueprint.blueprint_json = validated
            blueprint.status = ScrapingBlueprintStatus.DRAFT
            mission.status = ScrapingMissionStatus.AWAITING_APPROVAL
            await db.commit()
            await db.refresh(blueprint)
            return self._response(blueprint)
        except Exception:
            blueprint.status = ScrapingBlueprintStatus.FAILED
            blueprint.blueprint_json = None
            blueprint.error_message = "Blueprint generation failed"
            mission.status = ScrapingMissionStatus.FAILED
            await db.commit()
            await db.refresh(blueprint)
            return self._response(blueprint)

    async def list_blueprints(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> list[ScrapingBlueprintResponse]:
        mission = await mission_service.get_mission_row(db, auth, mission_id)
        result = await db.execute(
            select(ScrapingBlueprint)
            .where(ScrapingBlueprint.mission_id == mission.id)
            .order_by(ScrapingBlueprint.version.desc())
        )
        return [self._response(row) for row in result.scalars().all()]

    async def get_blueprint(
        self, db: AsyncSession, auth: AuthContext, blueprint_id: str
    ) -> ScrapingBlueprintResponse:
        return self._response(await self.get_blueprint_row(db, auth, blueprint_id))

    async def approve_blueprint(
        self, db: AsyncSession, auth: AuthContext, blueprint_id: str
    ) -> ScrapingBlueprintResponse:
        blueprint = await self.get_blueprint_row(db, auth, blueprint_id)
        if blueprint.status != ScrapingBlueprintStatus.DRAFT:
            raise ValidationError("Only draft blueprints can be approved")

        await db.execute(
            update(ScrapingBlueprint)
            .where(
                ScrapingBlueprint.mission_id == blueprint.mission_id,
                ScrapingBlueprint.id != blueprint.id,
                ScrapingBlueprint.status.in_(
                    [ScrapingBlueprintStatus.APPROVED, ScrapingBlueprintStatus.DRAFT]
                ),
            )
            .values(status=ScrapingBlueprintStatus.SUPERSEDED)
        )
        now = datetime.now(UTC)
        blueprint.status = ScrapingBlueprintStatus.APPROVED
        blueprint.approved_by = auth.user.id
        blueprint.approved_at = now
        blueprint.mission.active_blueprint_id = blueprint.id
        blueprint.mission.status = ScrapingMissionStatus.APPROVED
        await db.commit()
        await db.refresh(blueprint)
        return self._response(blueprint)

    async def reject_blueprint(
        self,
        db: AsyncSession,
        auth: AuthContext,
        blueprint_id: str,
        data: ScrapingBlueprintRejectRequest,
    ) -> ScrapingBlueprintResponse:
        reason = data.reason.strip()
        if not reason:
            raise ValidationError("Rejection reason is required")
        blueprint = await self.get_blueprint_row(db, auth, blueprint_id)
        if blueprint.status != ScrapingBlueprintStatus.DRAFT:
            raise ValidationError("Only draft blueprints can be rejected")

        blueprint.status = ScrapingBlueprintStatus.REJECTED
        blueprint.rejected_by = auth.user.id
        blueprint.rejected_at = datetime.now(UTC)
        blueprint.rejection_reason = reason
        if not blueprint.mission.active_blueprint_id:
            blueprint.mission.status = ScrapingMissionStatus.REJECTED
        await db.commit()
        await db.refresh(blueprint)
        return self._response(blueprint)

    async def request_changes(
        self,
        db: AsyncSession,
        auth: AuthContext,
        blueprint_id: str,
        data: ScrapingBlueprintChangeRequest,
    ) -> ScrapingBlueprintResponse:
        change_instructions = data.change_instructions.strip()
        if not change_instructions:
            raise ValidationError("Change instructions are required")
        source = await self.get_blueprint_row(db, auth, blueprint_id)
        if source.status not in (
            ScrapingBlueprintStatus.DRAFT,
            ScrapingBlueprintStatus.APPROVED,
            ScrapingBlueprintStatus.REJECTED,
        ):
            raise ValidationError("Only draft, approved, or rejected blueprints can be revised")

        model_set = await mission_service.resolve_model_set(db, auth, source.model_set_id)
        new_blueprint = ScrapingBlueprint(
            mission_id=source.mission_id,
            version=await self._next_version(db, source.mission_id),
            status=ScrapingBlueprintStatus.GENERATING,
            model_set_id=source.model_set_id,
            judge_model_id=self._judge_model_id(model_set),
            change_instructions=change_instructions,
        )
        source.mission.status = ScrapingMissionStatus.BLUEPRINT_GENERATING
        db.add(new_blueprint)
        await db.commit()
        await db.refresh(new_blueprint)

        try:
            content = await get_blueprint_orchestrator().generate(
                source.mission,
                model_set,
                previous_blueprint=source.blueprint_json,
                change_instructions=change_instructions,
            )
            new_blueprint.blueprint_json = content.model_dump(mode="json")
            new_blueprint.status = ScrapingBlueprintStatus.DRAFT
            source.mission.status = ScrapingMissionStatus.AWAITING_APPROVAL
            await db.commit()
            await db.refresh(new_blueprint)
            return self._response(new_blueprint)
        except Exception:
            new_blueprint.status = ScrapingBlueprintStatus.FAILED
            new_blueprint.blueprint_json = None
            new_blueprint.error_message = "Blueprint generation failed"
            source.mission.status = ScrapingMissionStatus.FAILED
            await db.commit()
            await db.refresh(new_blueprint)
            return self._response(new_blueprint)

    async def get_blueprint_row(
        self, db: AsyncSession, auth: AuthContext, blueprint_id: str
    ) -> ScrapingBlueprint:
        result = await db.execute(
            select(ScrapingBlueprint)
            .join(ScrapingMission, ScrapingMission.id == ScrapingBlueprint.mission_id)
            .where(ScrapingBlueprint.id == blueprint_id, ScrapingMission.org_id == auth.org_id)
            .options(selectinload(ScrapingBlueprint.mission))
        )
        blueprint = result.scalar_one_or_none()
        if blueprint is None:
            raise NotFoundError("ScrapingBlueprint", blueprint_id)
        return blueprint

    async def _next_version(self, db: AsyncSession, mission_id: str) -> int:
        result = await db.execute(
            select(func.coalesce(func.max(ScrapingBlueprint.version), 0)).where(
                ScrapingBlueprint.mission_id == mission_id
            )
        )
        return int(result.scalar_one()) + 1

    def _judge_model_id(self, model_set: Any) -> str | None:
        return model_set.verdict_model or (model_set.models[0] if model_set.models else None)

    def _response(self, blueprint: ScrapingBlueprint) -> ScrapingBlueprintResponse:
        content = None
        if blueprint.blueprint_json is not None:
            content = ScrapingBlueprintContent.model_validate(blueprint.blueprint_json)
        return ScrapingBlueprintResponse(
            id=blueprint.id,
            mission_id=blueprint.mission_id,
            version=blueprint.version,
            status=blueprint.status.value,
            blueprint_json=content,
            model_set_id=blueprint.model_set_id,
            judge_model_id=blueprint.judge_model_id,
            approved_by=blueprint.approved_by,
            approved_at=blueprint.approved_at,
            rejected_by=blueprint.rejected_by,
            rejected_at=blueprint.rejected_at,
            rejection_reason=blueprint.rejection_reason,
            change_instructions=blueprint.change_instructions,
            error_message=blueprint.error_message,
            created_at=blueprint.created_at,
            updated_at=blueprint.updated_at,
        )


blueprint_service = ScrapingBlueprintService()
