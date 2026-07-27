"""Scraping blueprint business logic."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from arq import create_pool
from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.db.models import (
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingMission,
    ScrapingMissionStatus,
)
from app.schemas.api import (
    ScrapingBlueprintChangeRequest,
    ScrapingBlueprintContent,
    ScrapingBlueprintRejectRequest,
    ScrapingBlueprintRenameRequest,
    ScrapingBlueprintResponse,
)
from app.scraping.worker import _redis_settings
from app.services.scraping.blueprint_prompt_service import blueprint_prompt_service
from app.services.scraping.blueprint_state_service import blueprint_state_service
from app.services.scraping.mission_service import mission_service
from app.services.scraping.countries import resolve_country


class ScrapingBlueprintService:
    async def _create_queued_version(
        self,
        db: AsyncSession,
        mission: ScrapingMission,
        *,
        revision_request: str | None = None,
        source: ScrapingBlueprint | None = None,
    ) -> ScrapingBlueprint:
        # PostgreSQL serializes allocations per mission; SQLite ignores FOR UPDATE
        # and is protected by the unique constraint plus the bounded retry below.
        await db.execute(
            select(ScrapingMission.id)
            .where(ScrapingMission.id == mission.id)
            .with_for_update()
        )
        if source is None:
            active = await db.scalar(
                select(func.count(ScrapingBlueprint.id)).where(
                    ScrapingBlueprint.mission_id == mission.id,
                    ScrapingBlueprint.status.in_(
                        [ScrapingBlueprintStatus.QUEUED, ScrapingBlueprintStatus.RUNNING]
                    ),
                )
            )
            if active:
                raise ConflictError("Blueprint generation is already in progress.")
        country = resolve_country(mission.country_code or mission.country_iso3 or "")
        prompt = blueprint_prompt_service.render_country_maximum_coverage(
            mission_title=mission.title, country=country
        )
        rendered_prompt = prompt.rendered_prompt
        if source is not None:
            rendered_prompt += (
                "\n\nPrevious human blueprint:\n"
                f"{source.human_readable_blueprint or ''}\n\nPrevious structured blueprint:\n"
                f"{source.structured_blueprint or {}}\n\nRevision instruction:\n{revision_request or ''}"
            )
        settings = get_settings()
        blueprint = ScrapingBlueprint(
            mission_id=mission.id,
            version=await self._next_version(db, mission.id),
            status=ScrapingBlueprintStatus.QUEUED,
            model_set_id=mission.model_set_id,
            country_name_snapshot=country.name,
            country_iso3_snapshot=country.iso3,
            continent_snapshot=country.continent,
            provider="openrouter",
            provider_model_id=settings.openrouter_blueprint_research_model,
            prompt_template_version=prompt.template_version,
            rendered_prompt_snapshot=rendered_prompt,
            revision_request=revision_request,
            queued_at=datetime.now(UTC),
        )
        db.add(blueprint)
        mission.status = ScrapingMissionStatus.BLUEPRINT_GENERATING
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A concurrent blueprint version was created. Retry the request."
            ) from exc
        await db.refresh(blueprint)
        await self.enqueue_blueprint(blueprint.id)
        return blueprint

    async def generate_blueprint(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> ScrapingBlueprintResponse:
        mission = await mission_service.get_mission_row(db, auth, mission_id)
        active = await db.scalar(
            select(func.count(ScrapingBlueprint.id)).where(
                ScrapingBlueprint.mission_id == mission.id,
                ScrapingBlueprint.status.in_(
                    [ScrapingBlueprintStatus.QUEUED, ScrapingBlueprintStatus.RUNNING]
                ),
            )
        )
        if active:
            raise ConflictError("Blueprint generation is already in progress.")
        blueprint = await self._create_queued_version(db, mission)
        return self._response(blueprint)

    async def enqueue_blueprint(self, blueprint_id: str) -> None:
        settings = get_settings()
        inline = (
            settings.scraping_inline_blueprint_generation
            if settings.scraping_inline_blueprint_generation is not None
            else settings.environment == "development"
        )
        if not inline:
            try:
                redis = await create_pool(_redis_settings())
                await redis.enqueue_job(
                    "run_blueprint_generation",
                    blueprint_id,
                    _job_id=f"scraping-blueprint:{blueprint_id}",
                )
                await redis.close()
                return
            except (OSError, RedisError, TimeoutError):
                # Local development deliberately falls back to the in-process task.
                pass
        from app.services.scraping.blueprint_generation_orchestrator import run_blueprint_generation

        asyncio.create_task(run_blueprint_generation({}, blueprint_id))

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

    async def rename_blueprint(
        self,
        db: AsyncSession,
        auth: AuthContext,
        blueprint_id: str,
        data: ScrapingBlueprintRenameRequest,
    ) -> ScrapingBlueprintResponse:
        blueprint = await self.get_blueprint_row(db, auth, blueprint_id)
        if blueprint.status == ScrapingBlueprintStatus.GENERATING:
            raise ConflictError("A blueprint that is currently generating cannot be renamed.")

        blueprint.display_name = data.name
        await db.commit()
        await db.refresh(blueprint)
        return self._response(blueprint)

    async def delete_blueprint(
        self, db: AsyncSession, auth: AuthContext, blueprint_id: str
    ) -> None:
        blueprint = await self.get_blueprint_row(db, auth, blueprint_id)
        mission = blueprint.mission
        if (
            mission.active_blueprint_id == blueprint.id
            or blueprint.status == ScrapingBlueprintStatus.APPROVED
        ):
            raise ConflictError(
                "The active approved blueprint cannot be deleted. "
                "Approve another version or delete the mission instead."
            )
        if blueprint.status == ScrapingBlueprintStatus.GENERATING:
            raise ConflictError("A blueprint that is currently generating cannot be deleted.")

        await db.delete(blueprint)
        await db.flush()
        result = await db.execute(
            select(ScrapingBlueprint).where(ScrapingBlueprint.mission_id == mission.id)
        )
        remaining = result.scalars().all()
        self._recalculate_mission_status_after_delete(mission, remaining)
        await db.commit()

    async def approve_blueprint(
        self, db: AsyncSession, auth: AuthContext, blueprint_id: str
    ) -> ScrapingBlueprintResponse:
        blueprint = await self.get_blueprint_row(db, auth, blueprint_id)
        if blueprint.status != ScrapingBlueprintStatus.READY_FOR_REVIEW:
            raise ValidationError("Only review-ready blueprints can be approved")
        if blueprint.structured_blueprint is None:
            raise ValidationError("A structured blueprint is required before approval")
        from app.services.scraping.blueprint_structured_contract import (
            validate_structured_blueprint_for_campaign,
        )
        from pydantic import ValidationError as PydanticValidationError

        try:
            # Newly approved blueprints must be complete Step-3-capable v2.
            validate_structured_blueprint_for_campaign(blueprint.structured_blueprint)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        except PydanticValidationError as exc:
            raise ValidationError(
                "Structured blueprint v2 failed validation and cannot be approved."
            ) from exc

        await db.execute(
            update(ScrapingBlueprint)
            .where(
                ScrapingBlueprint.mission_id == blueprint.mission_id,
                ScrapingBlueprint.id != blueprint.id,
                ScrapingBlueprint.status.in_(
                    [ScrapingBlueprintStatus.APPROVED]
                ),
            )
            .values(status=ScrapingBlueprintStatus.SUPERSEDED)
        )
        now = datetime.now(UTC)
        blueprint_state_service.transition(blueprint, ScrapingBlueprintStatus.APPROVED)
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
        if blueprint.status != ScrapingBlueprintStatus.READY_FOR_REVIEW:
            raise ValidationError("Only review-ready blueprints can be rejected")

        blueprint_state_service.transition(blueprint, ScrapingBlueprintStatus.REJECTED)
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
            ScrapingBlueprintStatus.READY_FOR_REVIEW,
            ScrapingBlueprintStatus.APPROVED,
            ScrapingBlueprintStatus.FAILED,
        ):
            raise ValidationError("Only review-ready, approved, or failed blueprints can be revised")
        new_blueprint = await self._create_queued_version(
            db, source.mission, revision_request=change_instructions, source=source
        )
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

    async def regenerate_blueprint(
        self, db: AsyncSession, auth: AuthContext, blueprint_id: str
    ) -> ScrapingBlueprintResponse:
        source = await self.get_blueprint_row(db, auth, blueprint_id)
        if source.status in (ScrapingBlueprintStatus.QUEUED, ScrapingBlueprintStatus.RUNNING):
            raise ConflictError("A queued or running blueprint cannot be regenerated.")
        return self._response(await self._create_queued_version(db, source.mission, source=source))

    async def discard_blueprint(
        self, db: AsyncSession, auth: AuthContext, blueprint_id: str
    ) -> ScrapingBlueprintResponse:
        blueprint = await self.get_blueprint_row(db, auth, blueprint_id)
        if blueprint.status not in (
            ScrapingBlueprintStatus.DRAFT,
            ScrapingBlueprintStatus.READY_FOR_REVIEW,
        ):
            raise ValidationError("Only draft or review-ready blueprints can be discarded.")
        blueprint_state_service.transition(blueprint, ScrapingBlueprintStatus.DISCARDED)
        blueprint.discarded_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(blueprint)
        return self._response(blueprint)

    async def latest_blueprint(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> ScrapingBlueprintResponse:
        mission = await mission_service.get_mission_row(db, auth, mission_id)
        row = await db.scalar(
            select(ScrapingBlueprint)
            .where(ScrapingBlueprint.mission_id == mission.id)
            .order_by(ScrapingBlueprint.version.desc())
            .limit(1)
        )
        if row is None:
            raise NotFoundError("ScrapingBlueprint")
        return self._response(row)

    async def active_blueprint(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> ScrapingBlueprintResponse:
        mission = await mission_service.get_mission_row(db, auth, mission_id)
        if not mission.active_blueprint_id:
            raise NotFoundError("ScrapingBlueprint")
        return self._response(await self.get_blueprint_row(db, auth, mission.active_blueprint_id))

    async def edit_blueprint(
        self,
        db: AsyncSession,
        auth: AuthContext,
        blueprint_id: str,
        *,
        human_readable_blueprint: str,
        structured_blueprint: dict[str, Any],
    ) -> ScrapingBlueprintResponse:
        blueprint = await self.get_blueprint_row(db, auth, blueprint_id)
        if blueprint.status not in (
            ScrapingBlueprintStatus.DRAFT,
            ScrapingBlueprintStatus.READY_FOR_REVIEW,
        ):
            raise ValidationError("Only draft or review-ready blueprints can be edited.")
        from app.services.scraping.blueprint_structured_contract import (
            validate_structured_blueprint_for_campaign,
        )
        from pydantic import ValidationError as PydanticValidationError

        try:
            # Edits intended for future campaigns must persist a complete v2 contract.
            validated = validate_structured_blueprint_for_campaign(structured_blueprint)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        except PydanticValidationError as exc:
            raise ValidationError(
                "Structured blueprint v2 failed validation and cannot be saved."
            ) from exc
        blueprint.human_readable_blueprint = human_readable_blueprint.strip()
        blueprint.structured_blueprint = validated.model_dump(mode="json")
        blueprint.citations = [item.model_dump(mode="json") for item in validated.citations]
        await db.commit()
        await db.refresh(blueprint)
        return self._response(blueprint)

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
        structured = None
        if blueprint.structured_blueprint is not None:
            from pydantic import ValidationError as PydanticValidationError

            from app.schemas.api import BlueprintCitation
            from app.services.scraping.blueprint_structured_contract import (
                parse_structured_blueprint,
            )

            # Version-aware: historical v1 and complete v2 both serialize without mutation.
            try:
                structured = parse_structured_blueprint(blueprint.structured_blueprint)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            except PydanticValidationError as exc:
                raise ValidationError("Structured blueprint failed validation.") from exc
            citations = [BlueprintCitation.model_validate(item) for item in blueprint.citations or []]
        else:
            citations = None
        return ScrapingBlueprintResponse(
            id=blueprint.id,
            mission_id=blueprint.mission_id,
            version=blueprint.version,
            display_name=blueprint.display_name,
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
            provider=blueprint.provider,
            provider_model_id=blueprint.provider_model_id,
            prompt_template_version=blueprint.prompt_template_version,
            human_readable_blueprint=blueprint.human_readable_blueprint,
            structured_blueprint=structured,
            citations=citations,
            revision_request=blueprint.revision_request,
            generation_error=blueprint.generation_error,
            queued_at=blueprint.queued_at,
            started_at=blueprint.started_at,
            completed_at=blueprint.completed_at,
            failed_at=blueprint.failed_at,
            discarded_at=blueprint.discarded_at,
            created_at=blueprint.created_at,
            updated_at=blueprint.updated_at,
        )

    def _recalculate_mission_status_after_delete(
        self, mission: ScrapingMission, blueprints: list[ScrapingBlueprint]
    ) -> None:
        if mission.active_blueprint_id is not None:
            mission.status = ScrapingMissionStatus.APPROVED
        elif any(
            blueprint.status == ScrapingBlueprintStatus.GENERATING for blueprint in blueprints
        ):
            mission.status = ScrapingMissionStatus.BLUEPRINT_GENERATING
        elif any(blueprint.status == ScrapingBlueprintStatus.DRAFT for blueprint in blueprints):
            mission.status = ScrapingMissionStatus.AWAITING_APPROVAL
        elif blueprints and all(
            blueprint.status == ScrapingBlueprintStatus.FAILED for blueprint in blueprints
        ):
            mission.status = ScrapingMissionStatus.FAILED
        elif blueprints and not any(
            blueprint.status
            in (
                ScrapingBlueprintStatus.DRAFT,
                ScrapingBlueprintStatus.GENERATING,
                ScrapingBlueprintStatus.APPROVED,
            )
            for blueprint in blueprints
        ):
            mission.status = ScrapingMissionStatus.REJECTED
        elif not blueprints:
            mission.status = ScrapingMissionStatus.DRAFT


blueprint_service = ScrapingBlueprintService()
