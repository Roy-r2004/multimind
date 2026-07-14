"""Scraping run orchestration and persistence."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.db.models import (
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingMission,
    ScrapingMissionStatus,
    ScrapingRun,
    ScrapingRunAgent,
    ScrapingRunAgentStatus,
    ScrapingRunStatus,
)
from app.schemas.api import (
    ScrapingRunAgentResponse,
    ScrapingRunDetail,
    ScrapingRunSummary,
    ScrapingTeamPlanOutput,
)
from app.services.scraping.mission_service import mission_service
from app.services.scraping.team_planner_service import team_planner_service

ACTIVE_APPROVED_BLUEPRINT_REQUIRED = (
    "An AI scraping team can only be planned from the active approved blueprint."
)
RUN_ALREADY_EXISTS_MESSAGE = "An AI scraping team plan already exists for this blueprint version."
RUN_DELETE_ACTIVE_MESSAGE = (
    "This run cannot be deleted while planning or executing. Cancel it first."
)
DELETABLE_RUN_STATUSES = {
    ScrapingRunStatus.PLANNED,
    ScrapingRunStatus.COMPLETED,
    ScrapingRunStatus.FAILED,
    ScrapingRunStatus.CANCELLED,
}


class ScrapingRunService:
    async def plan_team(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> ScrapingRunDetail:
        mission = await mission_service.get_mission_row(db, auth, mission_id)
        blueprint = await self._active_approved_blueprint(db, mission)
        existing_run = await self._existing_run_for_blueprint(db, auth, blueprint.id)
        if existing_run is not None:
            self._raise_existing_run_conflict(existing_run)
        model_set = await mission_service.resolve_model_set(db, auth, mission.model_set_id)

        now = datetime.now(UTC)
        run = ScrapingRun(
            organization_id=auth.org_id,
            mission_id=mission.id,
            blueprint_id=blueprint.id,
            model_set_id=model_set.slug,
            status=ScrapingRunStatus.PLANNING,
            started_at=now,
        )
        db.add(run)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            existing_run = await self._existing_run_for_blueprint(db, auth, blueprint.id)
            if existing_run is not None:
                self._raise_existing_run_conflict(existing_run)
            raise ConflictError(RUN_ALREADY_EXISTS_MESSAGE) from exc
        await db.refresh(run)

        try:
            plan, planner_model_id = await team_planner_service.plan_team(
                mission=mission,
                blueprint=blueprint,
                model_set=model_set,
            )
            await self._persist_successful_plan(db, run, plan, planner_model_id)
        except Exception:
            run.status = ScrapingRunStatus.FAILED
            run.error_message = "AI scraping team planning failed."
            run.completed_at = datetime.now(UTC)
            run.recommended_agent_count = None
            run.planner_rationale = None
            run.plan_json = None
            await db.commit()

        return await self.get_run(db, auth, run.id)

    async def list_runs(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> list[ScrapingRunSummary]:
        mission = await mission_service.get_mission_row(db, auth, mission_id)
        result = await db.execute(
            select(ScrapingRun)
            .where(
                ScrapingRun.mission_id == mission.id,
                ScrapingRun.organization_id == auth.org_id,
            )
            .options(selectinload(ScrapingRun.blueprint))
            .order_by(ScrapingRun.created_at.desc())
        )
        return [self._summary(row) for row in result.scalars().all()]

    async def get_run(self, db: AsyncSession, auth: AuthContext, run_id: str) -> ScrapingRunDetail:
        run = await self.get_run_row(db, auth, run_id)
        return self._detail(run)

    async def cancel_run(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> ScrapingRunDetail:
        run = await self.get_run_row(db, auth, run_id)
        if run.status == ScrapingRunStatus.PLANNED:
            run.status = ScrapingRunStatus.CANCELLED
            run.completed_at = datetime.now(UTC)
            for agent in run.agents:
                agent.status = ScrapingRunAgentStatus.CANCELLED
            await db.commit()
            return await self.get_run(db, auth, run.id)
        if run.status == ScrapingRunStatus.PLANNING:
            # Planning is synchronous in this phase, so there is no worker to interrupt mid-call.
            raise ConflictError("A planning run cannot be cancelled until the plan call finishes.")
        if run.status == ScrapingRunStatus.COMPLETED:
            raise ConflictError("A completed scraping run cannot be cancelled.")
        if run.status in (ScrapingRunStatus.FAILED, ScrapingRunStatus.CANCELLED):
            return self._detail(run)
        if run.status == ScrapingRunStatus.RUNNING:
            raise ConflictError("Running scraping execution is not implemented in this phase.")
        return self._detail(run)

    async def delete_run(self, db: AsyncSession, auth: AuthContext, run_id: str) -> None:
        run = await self.get_run_row(db, auth, run_id)
        if run.status not in DELETABLE_RUN_STATUSES:
            raise ConflictError(RUN_DELETE_ACTIVE_MESSAGE)
        await db.delete(run)
        await db.commit()

    async def get_run_row(self, db: AsyncSession, auth: AuthContext, run_id: str) -> ScrapingRun:
        result = await db.execute(
            select(ScrapingRun)
            .where(ScrapingRun.id == run_id, ScrapingRun.organization_id == auth.org_id)
            .options(
                selectinload(ScrapingRun.mission),
                selectinload(ScrapingRun.blueprint),
                selectinload(ScrapingRun.agents),
            )
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise NotFoundError("ScrapingRun", run_id)
        return run

    async def _active_approved_blueprint(
        self, db: AsyncSession, mission: ScrapingMission
    ) -> ScrapingBlueprint:
        if mission.status == ScrapingMissionStatus.BLUEPRINT_GENERATING:
            raise ConflictError(ACTIVE_APPROVED_BLUEPRINT_REQUIRED)
        if mission.active_blueprint_id is None:
            raise ConflictError(ACTIVE_APPROVED_BLUEPRINT_REQUIRED)
        result = await db.execute(
            select(ScrapingBlueprint).where(
                ScrapingBlueprint.id == mission.active_blueprint_id,
                ScrapingBlueprint.mission_id == mission.id,
            )
        )
        blueprint = result.scalar_one_or_none()
        if blueprint is None or blueprint.status != ScrapingBlueprintStatus.APPROVED:
            raise ConflictError(ACTIVE_APPROVED_BLUEPRINT_REQUIRED)
        return blueprint

    async def _existing_run_for_blueprint(
        self, db: AsyncSession, auth: AuthContext, blueprint_id: str
    ) -> ScrapingRun | None:
        result = await db.execute(
            select(ScrapingRun)
            .where(
                ScrapingRun.blueprint_id == blueprint_id,
                ScrapingRun.organization_id == auth.org_id,
            )
            .options(selectinload(ScrapingRun.blueprint))
        )
        return result.scalar_one_or_none()

    def _raise_existing_run_conflict(self, run: ScrapingRun) -> None:
        raise ConflictError(
            RUN_ALREADY_EXISTS_MESSAGE,
            details={
                "message": RUN_ALREADY_EXISTS_MESSAGE,
                "existing_run_id": run.id,
                "existing_run_status": run.status.value,
            },
        )

    async def _persist_successful_plan(
        self,
        db: AsyncSession,
        run: ScrapingRun,
        plan: ScrapingTeamPlanOutput,
        planner_model_id: str,
    ) -> None:
        run.status = ScrapingRunStatus.PLANNED
        run.recommended_agent_count = plan.recommended_agent_count
        run.planner_model_id = planner_model_id
        run.planner_rationale = plan.rationale
        run.plan_json = plan.model_dump(mode="json")
        run.error_message = None
        run.completed_at = datetime.now(UTC)

        agents_by_sequence: dict[int, ScrapingRunAgent] = {}
        dependency_sequences: dict[int, list[int]] = {}
        for planned_agent in sorted(plan.agents, key=lambda agent: agent.sequence):
            agent = ScrapingRunAgent(
                run_id=run.id,
                sequence=planned_agent.sequence,
                name=planned_agent.name,
                role=planned_agent.role,
                purpose=planned_agent.purpose,
                instructions=planned_agent.instructions,
                assigned_scope=planned_agent.assigned_scope,
                model_id=planned_agent.model_id,
                status=ScrapingRunAgentStatus.PLANNED,
                dependency_agent_ids=[],
            )
            db.add(agent)
            agents_by_sequence[planned_agent.sequence] = agent
            dependency_sequences[planned_agent.sequence] = planned_agent.depends_on

        await db.flush()

        for sequence, agent in agents_by_sequence.items():
            agent.dependency_agent_ids = [
                agents_by_sequence[dependency_sequence].id
                for dependency_sequence in dependency_sequences[sequence]
            ]

        await db.commit()

    def _summary(self, run: ScrapingRun) -> ScrapingRunSummary:
        return ScrapingRunSummary(
            id=run.id,
            mission_id=run.mission_id,
            blueprint_id=run.blueprint_id,
            blueprint_version=run.blueprint.version if run.blueprint else None,
            status=run.status.value,
            recommended_agent_count=run.recommended_agent_count,
            planner_model_id=run.planner_model_id,
            planner_rationale=run.planner_rationale,
            error_message=run.error_message,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _detail(self, run: ScrapingRun) -> ScrapingRunDetail:
        summary = self._summary(run)
        plan_json = None
        if run.plan_json is not None:
            try:
                plan_json = ScrapingTeamPlanOutput.model_validate(run.plan_json)
            except Exception as exc:
                raise ValidationError("Stored team plan is invalid") from exc
        return ScrapingRunDetail(
            **summary.model_dump(),
            model_set_id=run.model_set_id,
            mission_title=run.mission.title if run.mission else "",
            plan_json=plan_json,
            agents=[
                self._agent_response(agent)
                for agent in sorted(run.agents, key=lambda a: a.sequence)
            ],
        )

    def _agent_response(self, agent: ScrapingRunAgent) -> ScrapingRunAgentResponse:
        return ScrapingRunAgentResponse(
            id=agent.id,
            run_id=agent.run_id,
            parent_agent_id=agent.parent_agent_id,
            sequence=agent.sequence,
            name=agent.name,
            role=agent.role,
            purpose=agent.purpose,
            instructions=agent.instructions,
            assigned_scope=agent.assigned_scope,
            model_id=agent.model_id,
            status=agent.status.value,
            dependency_agent_ids=agent.dependency_agent_ids,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )


run_service = ScrapingRunService()
