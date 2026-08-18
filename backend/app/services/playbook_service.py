"""Personal My Playbooks — read-only foundation (no extraction or jobs)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.models import (
    PLAYBOOK_STATUS_NOT_GENERATED,
    Playbook,
    PlaybookObservation,
    PlaybookRun,
)
from app.schemas.api import (
    PlaybookObservationResponse,
    PlaybookObservationSourceResponse,
    PlaybookResponse,
    PlaybookRunResponse,
)

PLAYBOOK_UNIQUE_CONSTRAINT = "uq_playbook_org_user"
PLAYBOOK_UNIQUE_COLUMNS = ("playbooks.org_id", "playbooks.user_id")


class PlaybookService:
    def _owner_filters(self, auth: AuthContext):
        return (
            Playbook.org_id == auth.org_id,
            Playbook.user_id == auth.user.id,
        )

    async def get_or_create_for_current_user(
        self, db: AsyncSession, auth: AuthContext
    ) -> Playbook:
        playbook = await self._load_for_current_user(db, auth)
        if playbook is not None:
            return playbook

        playbook = Playbook(
            org_id=auth.org_id,
            user_id=auth.user.id,
            status=PLAYBOOK_STATUS_NOT_GENERATED,
            injection_enabled=True,
            extraction_version=1,
            playbook_version=0,
        )
        try:
            async with db.begin_nested():
                db.add(playbook)
                await db.flush()
        except IntegrityError as exc:
            existing = await self._existing_after_unique_race(db, auth, exc)
            if existing is None:
                raise
            return existing
        return playbook

    async def _existing_after_unique_race(
        self, db: AsyncSession, auth: AuthContext, exc: IntegrityError
    ) -> Playbook | None:
        if not self._is_expected_unique_violation(exc):
            return None
        return await self._load_for_current_user(db, auth)

    def _is_expected_unique_violation(self, exc: IntegrityError) -> bool:
        orig = getattr(exc, "orig", None)
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        constraint_name = getattr(orig, "constraint_name", None)
        diag = getattr(orig, "diag", None)
        if constraint_name is None and diag is not None:
            constraint_name = getattr(diag, "constraint_name", None)
        cause = getattr(orig, "__cause__", None)
        if constraint_name is None and cause is not None:
            constraint_name = getattr(cause, "constraint_name", None)
        if sqlstate == "23505" and constraint_name == PLAYBOOK_UNIQUE_CONSTRAINT:
            return True

        message = str(orig or exc).lower()
        if PLAYBOOK_UNIQUE_CONSTRAINT in message:
            return True
        return all(column in message for column in PLAYBOOK_UNIQUE_COLUMNS)

    async def get_for_current_user(self, db: AsyncSession, auth: AuthContext) -> Playbook:
        playbook = await self._load_for_current_user(db, auth)
        if playbook is None:
            raise NotFoundError("Playbook")
        return playbook

    async def _load_for_current_user(
        self, db: AsyncSession, auth: AuthContext
    ) -> Playbook | None:
        result = await db.execute(
            select(Playbook).where(*self._owner_filters(auth))
        )
        return result.scalar_one_or_none()

    async def list_observations_for_current_user(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        category: str | None = None,
        status: str | None = None,
        include_excluded: bool = False,
    ) -> list[PlaybookObservationResponse]:
        playbook = await self.get_or_create_for_current_user(db, auth)
        stmt = (
            select(PlaybookObservation)
            .where(PlaybookObservation.playbook_id == playbook.id)
            .options(selectinload(PlaybookObservation.sources))
            .order_by(PlaybookObservation.created_at.desc(), PlaybookObservation.id.desc())
        )
        if category:
            stmt = stmt.where(PlaybookObservation.category == category)
        if status:
            stmt = stmt.where(PlaybookObservation.status == status)
        if not include_excluded:
            stmt = stmt.where(PlaybookObservation.user_excluded.is_(False))
        result = await db.execute(stmt)
        return [self._observation_response(row) for row in result.scalars().all()]

    async def get_run_for_current_user(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> PlaybookRunResponse:
        playbook = await self._load_for_current_user(db, auth)
        if playbook is None:
            raise NotFoundError("PlaybookRun", run_id)
        run = await db.get(PlaybookRun, run_id)
        if run is None or run.playbook_id != playbook.id:
            raise NotFoundError("PlaybookRun", run_id)
        return self.run_response(run)

    async def get_latest_run_for_current_user(
        self, db: AsyncSession, auth: AuthContext
    ) -> PlaybookRunResponse | None:
        playbook = await self.get_or_create_for_current_user(db, auth)
        result = await db.execute(
            select(PlaybookRun)
            .where(PlaybookRun.playbook_id == playbook.id)
            .order_by(PlaybookRun.created_at.desc(), PlaybookRun.id.desc())
            .limit(1)
        )
        run = result.scalar_one_or_none()
        if run is None:
            return None
        return self._run_response(run)

    def playbook_response(self, playbook: Playbook) -> PlaybookResponse:
        return PlaybookResponse(
            id=playbook.id,
            org_id=playbook.org_id,
            user_id=playbook.user_id,
            status=playbook.status,
            injection_enabled=playbook.injection_enabled,
            core_summary=playbook.core_summary,
            extraction_version=playbook.extraction_version,
            playbook_version=playbook.playbook_version,
            last_success_run_id=playbook.last_success_run_id,
            last_success_at=playbook.last_success_at,
            created_at=playbook.created_at,
            updated_at=playbook.updated_at,
        )

    def _observation_response(
        self, observation: PlaybookObservation
    ) -> PlaybookObservationResponse:
        sources = [
            PlaybookObservationSourceResponse(
                id=source.id,
                observation_id=source.observation_id,
                chat_id=source.chat_id,
                turn_id=source.turn_id,
                source_kind=source.source_kind,
                epistemic_role=source.epistemic_role,
                quote=source.quote,
                created_at=source.created_at,
            )
            for source in (observation.sources or [])
        ]
        return PlaybookObservationResponse(
            id=observation.id,
            playbook_id=observation.playbook_id,
            category=observation.category,
            subject=observation.subject,
            observation=observation.observation,
            status=observation.status,
            confidence=observation.confidence,
            evidence_count=observation.evidence_count,
            first_observed_at=observation.first_observed_at,
            last_confirmed_at=observation.last_confirmed_at,
            superseded_by_id=observation.superseded_by_id,
            user_corrected=observation.user_corrected,
            user_excluded=observation.user_excluded,
            sources=sources,
            created_at=observation.created_at,
            updated_at=observation.updated_at,
        )

    def run_response(self, run: PlaybookRun) -> PlaybookRunResponse:
        return PlaybookRunResponse(
            id=run.id,
            playbook_id=run.playbook_id,
            kind=run.kind,
            status=run.status,
            processed_count=run.processed_count,
            total_count=run.total_count,
            warning_count=run.warning_count,
            error_message=run.error_message,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _run_response(self, run: PlaybookRun) -> PlaybookRunResponse:
        return self.run_response(run)


playbook_service = PlaybookService()
