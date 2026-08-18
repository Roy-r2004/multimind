"""First full Playbook generation: enqueue, extract, persist, and fail safely.

Progress
--------
``total_count`` is the number of eligible source units:

* every eligible reconstructed turn;
* plus 1 when a UserBrain snapshot is present;
* plus each Brain knowledge item.

``processed_count`` increases by a batch's ``turn_count`` after that batch's
extraction JSON is parsed (including an empty-but-valid observations list).
Brain units are added once, after the first successful batch that included
Brain sources. Failed JSON (after one repair) does not advance processed
counts and does not record those turn IDs as successful. Consolidation and
core-summary may run with ``processed_count == total_count`` while the run
remains ``processing``.

Source checkpoints
------------------
``PlaybookSourceState`` rows are written only for sources that were included
in a successful extraction request:

* turn IDs from batches where ``result.succeeded`` is true;
* UserBrain and Brain knowledge items only when a successful extraction
  included Brain (``include_brain`` and ``succeeded``).

``processed_count`` and the persisted source-state rows describe the same
successfully processed sources: each successful turn and, at most once, the
Brain units from a successful Brain-containing extract. Failed-batch turns
remain absent from ``playbook_source_states``.

Batch vs run failure
--------------------
A malformed optional batch is a warning. If at least one batch succeeds,
generation continues and the run may finish ``completed_with_warnings``.
If no batch succeeds, the run fails. Consolidation failure, empty core
summary, or final persistence failure fail the run; no partial Playbook is
kept.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import (
    PLAYBOOK_RUN_KIND_FULL,
    PLAYBOOK_RUN_KIND_INCREMENTAL,
    PLAYBOOK_RUN_STATUS_COMPLETED,
    PLAYBOOK_RUN_STATUS_COMPLETED_WITH_WARNINGS,
    PLAYBOOK_RUN_STATUS_FAILED,
    PLAYBOOK_RUN_STATUS_PROCESSING,
    PLAYBOOK_RUN_STATUS_QUEUED,
    PLAYBOOK_SOURCE_STATE_PROCESSED,
    PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE,
    PLAYBOOK_SOURCE_TYPE_TURN,
    PLAYBOOK_SOURCE_TYPE_USER_BRAIN,
    PLAYBOOK_STATUS_ACTIVE,
    OrgRole,
    Playbook,
    PlaybookObservation,
    PlaybookObservationSource,
    PlaybookRun,
    PlaybookSourceState,
    User,
)
from app.schemas.api import PlaybookRunResponse
from app.services.playbook_extraction_service import (
    CanonicalObservation,
    ExtractedCandidate,
    ExtractionWarning,
    PlaybookExtractionError,
    ValidatedEvidence,
    playbook_extraction_service,
)
from app.services.playbook_service import playbook_service
from app.services.playbook_source_service import (
    PlaybookBrainSnapshot,
    PlaybookChatTranscript,
    PlaybookExtractionBatch,
    PlaybookTranscriptSet,
    playbook_source_service,
)

logger = get_logger(__name__)

IN_FLIGHT_STATUSES = frozenset({PLAYBOOK_RUN_STATUS_QUEUED, PLAYBOOK_RUN_STATUS_PROCESSING})
TERMINAL_STATUSES = frozenset(
    {
        PLAYBOOK_RUN_STATUS_COMPLETED,
        PLAYBOOK_RUN_STATUS_COMPLETED_WITH_WARNINGS,
        PLAYBOOK_RUN_STATUS_FAILED,
    }
)
SAFE_ERROR_ENQUEUE = "Playbook generation could not be queued."
SAFE_ERROR_GENERIC = "Playbook generation failed."
SAFE_ERROR_NO_BATCHES = "No eligible Playbook sources could be processed."
SAFE_ERROR_NO_OBSERVATIONS = "Playbook generation produced no valid observations."
SAFE_ERROR_CONSOLIDATION = "Playbook consolidation failed."
SAFE_ERROR_SUMMARY = "Playbook summary generation failed."
SAFE_ERROR_PERSIST = "Playbook persistence failed."
ERROR_MESSAGE_MAX_CHARS = 500
GENERATE_PLAYBOOK_JOB = "generate_playbook_job"

_SECRET_IN_ERROR = re.compile(
    r"(?i)(redis://\S+|postgres(?:ql)?://\S+|mongodb(?:\+srv)?://\S+|"
    r"api[_-]?key|password|secret|token|bearer\s+\S+)"
)


class PlaybookGenerationService:
    async def start_full_generation(
        self, db: AsyncSession, auth: AuthContext
    ) -> PlaybookRunResponse:
        playbook = await playbook_service.get_or_create_for_current_user(db, auth)
        await db.flush()
        locked = await db.execute(
            select(Playbook).where(Playbook.id == playbook.id).with_for_update()
        )
        playbook = locked.scalar_one()

        existing = await self._load_in_flight_run(db, playbook.id)
        if existing is not None:
            return playbook_service.run_response(existing)

        if self._has_successful_version(playbook):
            raise ConflictError("Incremental Playbook rerun is not implemented yet.")

        transcripts = await playbook_source_service.assemble_all_transcripts(db, auth)
        brain = await playbook_source_service.build_brain_source_snapshot(db, auth)
        total_count = count_source_units(transcripts, brain)
        if total_count <= 0:
            raise ValidationError("No eligible Playbook sources exist.")

        run = PlaybookRun(
            playbook_id=playbook.id,
            kind=PLAYBOOK_RUN_KIND_FULL,
            status=PLAYBOOK_RUN_STATUS_QUEUED,
            processed_count=0,
            total_count=total_count,
            warning_count=0,
        )
        db.add(run)
        await db.flush()
        await db.commit()
        await db.refresh(run)

        try:
            await self.enqueue_generation_job(
                playbook_id=playbook.id,
                run_id=run.id,
                org_id=playbook.org_id,
                user_id=playbook.user_id,
            )
        except Exception:
            logger.exception("playbook_enqueue_failed", run_id=run.id)
            await self.mark_run_failed(db, run.id, SAFE_ERROR_ENQUEUE)
            await db.refresh(run)
            return playbook_service.run_response(run)

        await db.refresh(run)
        return playbook_service.run_response(run)

    async def start_incremental_generation(
        self, db: AsyncSession, auth: AuthContext
    ) -> PlaybookRunResponse:
        from app.services.playbook_pending_service import playbook_pending_service

        playbook, diff = await playbook_pending_service.compute(db, auth)
        await db.flush()
        locked = (
            await db.execute(select(Playbook).where(Playbook.id == playbook.id).with_for_update())
        ).scalar_one()
        existing = await self._load_in_flight_run(db, locked.id)
        if existing is not None:
            return playbook_service.run_response(existing)
        if not self._has_successful_version(locked):
            raise ConflictError("Generate the first Playbook version before rerunning.")
        if diff.pending_source_items == 0:
            raise ConflictError("Playbook is already up to date.")
        run = PlaybookRun(
            playbook_id=locked.id,
            kind=PLAYBOOK_RUN_KIND_INCREMENTAL,
            status=PLAYBOOK_RUN_STATUS_QUEUED,
            processed_count=0,
            total_count=diff.pending_source_items,
            warning_count=0,
        )
        db.add(run)
        await db.flush()
        await db.commit()
        await db.refresh(run)
        try:
            await self.enqueue_generation_job(
                playbook_id=locked.id, run_id=run.id, org_id=locked.org_id, user_id=locked.user_id
            )
        except Exception:
            logger.exception("playbook_incremental_enqueue_failed", run_id=run.id)
            await self.mark_run_failed(db, run.id, SAFE_ERROR_ENQUEUE)
            await db.refresh(run)
        return playbook_service.run_response(run)

    async def enqueue_generation_job(
        self,
        *,
        playbook_id: str,
        run_id: str,
        org_id: str,
        user_id: str,
    ) -> None:
        from arq import create_pool

        from app.playbooks.worker import playbook_queue_name, playbook_redis_settings

        redis = await create_pool(
            playbook_redis_settings(),
            default_queue_name=playbook_queue_name(),
        )
        try:
            await redis.enqueue_job(
                GENERATE_PLAYBOOK_JOB,
                playbook_id,
                run_id,
                org_id,
                user_id,
                _queue_name=playbook_queue_name(),
                _job_id=f"playbook-generate:{run_id}",
            )
        finally:
            await redis.close(close_connection_pool=True)

    async def execute_full_generation(
        self,
        db: AsyncSession,
        *,
        playbook_id: str,
        run_id: str,
        org_id: str,
        user_id: str,
    ) -> dict[str, object]:
        playbook = await db.get(Playbook, playbook_id)
        if playbook is None:
            raise NotFoundError("Playbook", playbook_id)
        if playbook.org_id != org_id or playbook.user_id != user_id:
            logger.warning(
                "playbook_generation_ownership_mismatch",
                playbook_id=playbook_id,
                run_id=run_id,
            )
            return {"status": "rejected", "reason": "ownership_mismatch", "skipped": True}

        run = await db.get(PlaybookRun, run_id)
        if run is None or run.playbook_id != playbook.id:
            raise NotFoundError("PlaybookRun", run_id)

        claimed = await self._claim_queued_run(db, run)
        if not claimed:
            current = await db.get(PlaybookRun, run_id)
            status = current.status if current is not None else "missing"
            logger.info(
                "playbook_generation_skipped_duplicate",
                run_id=run_id,
                status=status,
            )
            return {"status": status, "run_id": run_id, "skipped": True}

        run = claimed
        claimed_run_id = run.id
        user = await db.get(User, user_id)
        if user is None:
            await self.mark_run_failed(db, claimed_run_id, SAFE_ERROR_GENERIC)
            return {
                "status": PLAYBOOK_RUN_STATUS_FAILED,
                "run_id": claimed_run_id,
                "skipped": False,
            }

        auth = AuthContext(user=user, org_id=org_id, role=OrgRole.MEMBER)
        try:
            await self._run_pipeline(db, playbook, run, auth)
            await db.refresh(run)
            return {"status": run.status, "run_id": claimed_run_id, "skipped": False}
        except PlaybookExtractionError as exc:
            logger.warning("playbook_generation_failed", run_id=claimed_run_id, error=str(exc))
            await db.rollback()
            try:
                await db.refresh(user)
            except Exception:
                pass
            current = await db.get(PlaybookRun, claimed_run_id)
            await self.mark_run_failed(
                db,
                claimed_run_id,
                _safe_error_message(exc, fallback=SAFE_ERROR_GENERIC),
                processed_count=current.processed_count if current is not None else None,
                warning_count=current.warning_count if current is not None else None,
            )
            return {
                "status": PLAYBOOK_RUN_STATUS_FAILED,
                "run_id": claimed_run_id,
                "skipped": False,
            }
        except Exception:
            logger.exception("playbook_generation_failed", run_id=claimed_run_id)
            await db.rollback()
            try:
                await db.refresh(user)
            except Exception:
                pass
            current = await db.get(PlaybookRun, claimed_run_id)
            await self.mark_run_failed(
                db,
                claimed_run_id,
                SAFE_ERROR_GENERIC,
                processed_count=current.processed_count if current is not None else None,
                warning_count=current.warning_count if current is not None else None,
            )
            return {
                "status": PLAYBOOK_RUN_STATUS_FAILED,
                "run_id": claimed_run_id,
                "skipped": False,
            }

    async def execute_incremental_generation(self, db: AsyncSession, **ids) -> dict[str, object]:
        return await self._execute_incremental(db, **ids)

    async def _execute_incremental(
        self, db: AsyncSession, *, playbook_id: str, run_id: str, org_id: str, user_id: str
    ) -> dict[str, object]:
        from app.services.playbook_pending_service import playbook_pending_service

        playbook = await db.get(Playbook, playbook_id)
        run = await db.get(PlaybookRun, run_id)
        if playbook is None or playbook.org_id != org_id or playbook.user_id != user_id:
            return {"status": "rejected", "reason": "ownership_mismatch", "skipped": True}
        if (
            run is None
            or run.playbook_id != playbook.id
            or run.kind != PLAYBOOK_RUN_KIND_INCREMENTAL
        ):
            raise NotFoundError("PlaybookRun", run_id)
        claimed = await self._claim_queued_run(db, run)
        if not claimed:
            return {"status": run.status, "run_id": run_id, "skipped": True}
        user = await db.get(User, user_id)
        if user is None:
            await self.mark_run_failed(db, run_id, SAFE_ERROR_GENERIC)
            return {"status": PLAYBOOK_RUN_STATUS_FAILED, "run_id": run_id, "skipped": False}
        auth = AuthContext(user=user, org_id=org_id, role=OrgRole.MEMBER)
        try:
            playbook, diff = await playbook_pending_service.compute(db, auth)
            if diff.pending_source_items == 0:
                raise PlaybookExtractionError("Playbook is already up to date.")
            await self._run_incremental_pipeline(db, playbook, claimed, diff)
            await db.refresh(claimed)
            return {"status": claimed.status, "run_id": run_id, "skipped": False}
        except Exception as exc:
            logger.exception("playbook_incremental_failed", run_id=run_id)
            await db.rollback()
            try:
                await db.refresh(user)
            except Exception:
                pass
            current = await db.get(PlaybookRun, run_id)
            await self.mark_run_failed(
                db,
                run_id,
                _safe_error_message(exc),
                processed_count=current.processed_count if current else None,
                warning_count=current.warning_count if current else None,
            )
            return {"status": PLAYBOOK_RUN_STATUS_FAILED, "run_id": run_id, "skipped": False}

    async def _run_incremental_pipeline(
        self, db: AsyncSession, playbook: Playbook, run: PlaybookRun, diff
    ) -> None:
        total = diff.pending_source_items
        warnings = len(diff.transcripts.warnings) + len(diff.brain.warnings)
        processed = len(diff.removed_turn_ids)
        await self._save_progress(
            db, run, processed_count=processed, total_count=total, warning_count=warnings
        )
        selected_ids = diff.new_turn_ids | diff.changed_turn_ids
        selected_chats = tuple(
            PlaybookChatTranscript(
                chat_id=chat.chat_id,
                chat_title=chat.chat_title,
                project_id=chat.project_id,
                chat_created_at=chat.chat_created_at,
                chat_updated_at=chat.chat_updated_at,
                turns=tuple(turn for turn in chat.turns if turn.turn_id in selected_ids),
                warnings=chat.warnings,
            )
            for chat in diff.transcripts.chats
            if any(turn.turn_id in selected_ids for turn in chat.turns)
        )
        candidates: list[ExtractedCandidate] = []
        successful_turn_ids: set[str] = set()
        brain_success = not diff.brain_changed
        include_brain = diff.brain_changed
        success_count = 0
        for batch in playbook_source_service.batch_transcripts(selected_chats):
            result = await playbook_extraction_service.extract_batch(
                batch, diff.brain, include_brain=include_brain
            )
            warnings += len(result.warnings)
            if result.succeeded:
                success_count += 1
                candidates.extend(result.candidates)
                successful_turn_ids.update(batch.turn_ids)
                processed += batch.turn_count
                if include_brain:
                    brain_success = True
                    processed += diff.brain_changes
                    include_brain = False
            await self._save_progress(
                db, run, processed_count=processed, total_count=total, warning_count=warnings
            )
        if include_brain:
            result = await playbook_extraction_service.extract_batch(
                _empty_batch(), diff.brain, include_brain=True
            )
            warnings += len(result.warnings)
            if result.succeeded:
                success_count += 1
                candidates.extend(result.candidates)
                brain_success = True
                processed += diff.brain_changes
            await self._save_progress(
                db, run, processed_count=processed, total_count=total, warning_count=warnings
            )
        if success_count == 0 and (selected_ids or diff.brain_changed):
            raise PlaybookExtractionError(SAFE_ERROR_NO_BATCHES)

        observations = (
            (
                await db.execute(
                    select(PlaybookObservation)
                    .where(PlaybookObservation.playbook_id == playbook.id)
                    .options(selectinload(PlaybookObservation.sources))
                )
            )
            .scalars()
            .unique()
            .all()
        )
        # Replace last-successful evidence only for changed turns whose new
        # extraction succeeded. Failed changed turns remain safely pending.
        invalid_turns = frozenset(successful_turn_ids) | diff.removed_turn_ids
        for old in observations:
            evidence = tuple(
                ValidatedEvidence(
                    source_kind=s.source_kind,
                    epistemic_role=s.epistemic_role or "supporting",
                    quote=s.quote or "",
                    chat_id=s.chat_id,
                    turn_id=s.turn_id,
                )
                for s in old.sources
                if s.turn_id not in invalid_turns
                and not (
                    diff.brain_changed and brain_success and s.turn_id is None and s.chat_id is None
                )
            )
            if evidence:
                candidates.append(
                    ExtractedCandidate(
                        candidate_id=f"existing:{old.id}",
                        category=old.category,
                        subject=old.subject,
                        observation=old.observation,
                        status=old.status,
                        confidence=float(old.confidence or 0),
                        evidence=evidence,
                        created_at=old.first_observed_at,
                    )
                )
        canonical, extra = await playbook_extraction_service.consolidate_candidates(candidates)
        warnings += len(extra)
        if not canonical:
            raise PlaybookExtractionError(SAFE_ERROR_NO_OBSERVATIONS)
        summary, extra = await playbook_extraction_service.generate_core_summary(canonical)
        warnings += len(extra)
        if not (summary or "").strip():
            raise PlaybookExtractionError(SAFE_ERROR_SUMMARY)

        baseline_rows = (
            (
                await db.execute(
                    select(PlaybookSourceState).where(
                        PlaybookSourceState.playbook_id == playbook.id
                    )
                )
            )
            .scalars()
            .all()
        )
        states = {(row.source_type, row.source_id): row.content_hash for row in baseline_rows}
        for turn_id in diff.removed_turn_ids | frozenset(successful_turn_ids):
            states.pop((PLAYBOOK_SOURCE_TYPE_TURN, turn_id), None)
        current_turns = {
            turn.turn_id: turn for chat in diff.transcripts.chats for turn in chat.turns
        }
        for turn_id in successful_turn_ids:
            states[(PLAYBOOK_SOURCE_TYPE_TURN, turn_id)] = current_turns[turn_id].content_hash
        if diff.brain_changed and brain_success:
            states = {
                key: value
                for key, value in states.items()
                if key[0]
                not in {PLAYBOOK_SOURCE_TYPE_USER_BRAIN, PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE}
            }
            if diff.brain.user_brain:
                states[(PLAYBOOK_SOURCE_TYPE_USER_BRAIN, diff.brain.user_brain.id)] = (
                    diff.brain.user_brain.content_hash
                )
            states.update(
                {
                    (PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE, item.id): item.content_hash
                    for item in diff.brain.knowledge_items
                }
            )
        await self._persist_final(
            db,
            playbook=playbook,
            run=run,
            observations=canonical,
            core_summary=summary,
            transcripts=diff.transcripts,
            brain=diff.brain,
            successful_turn_ids=set(),
            brain_extracted_successfully=False,
            warning_count=warnings,
            processed_count=processed,
            total_count=total,
            source_state_hashes=states,
        )

    async def mark_run_failed(
        self,
        db: AsyncSession,
        run_id: str,
        message: str,
        *,
        playbook_id: str | None = None,
        processed_count: int | None = None,
        warning_count: int | None = None,
    ) -> None:
        run = await db.get(PlaybookRun, run_id)
        if run is None:
            return
        if playbook_id is not None and run.playbook_id != playbook_id:
            return
        if run.status in {
            PLAYBOOK_RUN_STATUS_COMPLETED,
            PLAYBOOK_RUN_STATUS_COMPLETED_WITH_WARNINGS,
        }:
            return
        run.status = PLAYBOOK_RUN_STATUS_FAILED
        run.error_message = _safe_error_message(message)
        run.finished_at = datetime.now(UTC)
        if processed_count is not None:
            run.processed_count = processed_count
        if warning_count is not None:
            run.warning_count = warning_count
        await db.commit()

    async def _run_pipeline(
        self,
        db: AsyncSession,
        playbook: Playbook,
        run: PlaybookRun,
        auth: AuthContext,
    ) -> None:
        transcripts = await playbook_source_service.assemble_all_transcripts(db, auth)
        brain = await playbook_source_service.build_brain_source_snapshot(db, auth)
        total_count = count_source_units(transcripts, brain)
        warning_count = len(transcripts.warnings) + len(brain.warnings)
        processed_count = 0
        await self._save_progress(
            db,
            run,
            processed_count=processed_count,
            total_count=total_count,
            warning_count=warning_count,
        )

        batches = playbook_source_service.batch_transcripts(transcripts.chats)
        candidates: list[ExtractedCandidate] = []
        extraction_warnings: list[ExtractionWarning] = []
        successful_batches = 0
        successful_turn_ids: set[str] = set()
        brain_extracted_successfully = False
        brain_units = _brain_unit_count(brain)
        brain_counted = False
        include_brain_next = True

        for batch in batches:
            include_brain = include_brain_next
            result = await playbook_extraction_service.extract_batch(
                batch, brain, include_brain=include_brain
            )
            extraction_warnings.extend(result.warnings)
            warning_count += len(result.warnings)
            if not result.succeeded:
                include_brain_next = include_brain or include_brain_next
                await self._save_progress(
                    db,
                    run,
                    processed_count=processed_count,
                    total_count=total_count,
                    warning_count=warning_count,
                )
                continue
            successful_batches += 1
            successful_turn_ids.update(batch.turn_ids)
            candidates.extend(result.candidates)
            processed_count += batch.turn_count
            if include_brain:
                brain_extracted_successfully = True
                if not brain_counted:
                    processed_count += brain_units
                    brain_counted = True
                include_brain_next = False
            else:
                include_brain_next = False
            await self._save_progress(
                db,
                run,
                processed_count=processed_count,
                total_count=total_count,
                warning_count=warning_count,
            )

        if successful_batches == 0 and brain_units > 0:
            result = await playbook_extraction_service.extract_batch(
                _empty_batch(), brain, include_brain=True
            )
            extraction_warnings.extend(result.warnings)
            warning_count += len(result.warnings)
            if result.succeeded:
                successful_batches += 1
                candidates.extend(result.candidates)
                processed_count += brain_units
                brain_counted = True
                brain_extracted_successfully = True
            await self._save_progress(
                db,
                run,
                processed_count=processed_count,
                total_count=total_count,
                warning_count=warning_count,
            )

        if successful_batches == 0:
            raise PlaybookExtractionError(SAFE_ERROR_NO_BATCHES)

        try:
            canonical, extra = await playbook_extraction_service.consolidate_candidates(candidates)
        except PlaybookExtractionError as exc:
            raise PlaybookExtractionError(SAFE_ERROR_CONSOLIDATION) from exc
        warning_count += len(extra)
        await self._save_progress(
            db,
            run,
            processed_count=processed_count,
            total_count=total_count,
            warning_count=warning_count,
        )
        if not canonical:
            raise PlaybookExtractionError(SAFE_ERROR_NO_OBSERVATIONS)

        try:
            core_summary, extra = await playbook_extraction_service.generate_core_summary(canonical)
        except PlaybookExtractionError as exc:
            raise PlaybookExtractionError(SAFE_ERROR_SUMMARY) from exc
        warning_count += len(extra)
        await self._save_progress(
            db,
            run,
            processed_count=processed_count,
            total_count=total_count,
            warning_count=warning_count,
        )
        if not (core_summary or "").strip():
            raise PlaybookExtractionError(SAFE_ERROR_SUMMARY)

        try:
            await self._persist_final(
                db,
                playbook=playbook,
                run=run,
                observations=canonical,
                core_summary=core_summary,
                transcripts=transcripts,
                brain=brain,
                successful_turn_ids=successful_turn_ids,
                brain_extracted_successfully=brain_extracted_successfully,
                warning_count=warning_count,
                processed_count=processed_count,
                total_count=total_count,
            )
        except Exception as exc:
            raise PlaybookExtractionError(SAFE_ERROR_PERSIST) from exc

    async def _persist_final(
        self,
        db: AsyncSession,
        *,
        playbook: Playbook,
        run: PlaybookRun,
        observations: list[CanonicalObservation],
        core_summary: str,
        transcripts: PlaybookTranscriptSet,
        brain: PlaybookBrainSnapshot,
        successful_turn_ids: set[str],
        brain_extracted_successfully: bool,
        warning_count: int,
        processed_count: int,
        total_count: int,
        source_state_hashes: dict[tuple[str, str], str] | None = None,
    ) -> None:
        locked_playbook = await db.execute(
            select(Playbook)
            .where(Playbook.id == playbook.id)
            .options(
                selectinload(Playbook.observations).selectinload(PlaybookObservation.sources),
                selectinload(Playbook.source_states),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        playbook = locked_playbook.scalar_one()
        locked_run = await db.execute(
            select(PlaybookRun).where(PlaybookRun.id == run.id).with_for_update()
        )
        run = locked_run.scalar_one()
        if run.playbook_id != playbook.id:
            raise PlaybookExtractionError(SAFE_ERROR_PERSIST)
        if run.status != PLAYBOOK_RUN_STATUS_PROCESSING:
            raise PlaybookExtractionError(SAFE_ERROR_PERSIST)

        playbook.observations.clear()
        playbook.source_states.clear()
        await db.flush()

        now = datetime.now(UTC)
        for item in observations:
            evidence = tuple(item.evidence)
            row = PlaybookObservation(
                playbook_id=playbook.id,
                category=item.category,
                subject=item.subject,
                observation=item.observation,
                status=item.status,
                confidence=item.confidence,
                evidence_count=len(evidence),
                first_observed_at=item.first_observed_at,
                last_confirmed_at=item.last_confirmed_at,
                superseded_by_id=None,
                user_corrected=False,
                user_excluded=False,
            )
            db.add(row)
            await db.flush()
            for ev in evidence:
                db.add(
                    PlaybookObservationSource(
                        observation_id=row.id,
                        chat_id=ev.chat_id,
                        turn_id=ev.turn_id,
                        source_kind=ev.source_kind,
                        epistemic_role=ev.epistemic_role,
                        quote=ev.quote or None,
                    )
                )

        if source_state_hashes is not None:
            for (source_type, source_id), content_hash in source_state_hashes.items():
                db.add(
                    PlaybookSourceState(
                        playbook_id=playbook.id,
                        source_type=source_type,
                        source_id=source_id,
                        content_hash=content_hash,
                        processed_run_id=run.id,
                        processed_at=now,
                        status=PLAYBOOK_SOURCE_STATE_PROCESSED,
                    )
                )
        for chat in transcripts.chats if source_state_hashes is None else ():
            for turn in chat.turns:
                if turn.turn_id not in successful_turn_ids:
                    continue
                db.add(
                    PlaybookSourceState(
                        playbook_id=playbook.id,
                        source_type=PLAYBOOK_SOURCE_TYPE_TURN,
                        source_id=turn.turn_id,
                        content_hash=turn.content_hash,
                        processed_run_id=run.id,
                        processed_at=now,
                        status=PLAYBOOK_SOURCE_STATE_PROCESSED,
                    )
                )
        if brain_extracted_successfully and source_state_hashes is None:
            if brain.user_brain is not None:
                db.add(
                    PlaybookSourceState(
                        playbook_id=playbook.id,
                        source_type=PLAYBOOK_SOURCE_TYPE_USER_BRAIN,
                        source_id=brain.user_brain.id,
                        content_hash=brain.user_brain.content_hash,
                        processed_run_id=run.id,
                        processed_at=now,
                        status=PLAYBOOK_SOURCE_STATE_PROCESSED,
                    )
                )
            for item in brain.knowledge_items:
                db.add(
                    PlaybookSourceState(
                        playbook_id=playbook.id,
                        source_type=PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE,
                        source_id=item.id,
                        content_hash=item.content_hash,
                        processed_run_id=run.id,
                        processed_at=now,
                        status=PLAYBOOK_SOURCE_STATE_PROCESSED,
                    )
                )

        injection_enabled = playbook.injection_enabled
        playbook.core_summary = core_summary
        playbook.status = PLAYBOOK_STATUS_ACTIVE
        playbook.playbook_version = int(playbook.playbook_version or 0) + 1
        playbook.injection_enabled = injection_enabled
        playbook.last_success_run_id = run.id
        playbook.last_success_at = now
        run.processed_count = processed_count
        run.total_count = total_count
        run.warning_count = warning_count
        run.error_message = None
        run.finished_at = now
        run.status = (
            PLAYBOOK_RUN_STATUS_COMPLETED_WITH_WARNINGS
            if warning_count > 0
            else PLAYBOOK_RUN_STATUS_COMPLETED
        )
        await db.commit()

    async def _save_progress(
        self,
        db: AsyncSession,
        run: PlaybookRun,
        *,
        processed_count: int,
        total_count: int,
        warning_count: int,
    ) -> None:
        run.processed_count = processed_count
        run.total_count = total_count
        run.warning_count = warning_count
        await db.commit()
        await db.refresh(run)

    async def _load_in_flight_run(self, db: AsyncSession, playbook_id: str) -> PlaybookRun | None:
        result = await db.execute(
            select(PlaybookRun)
            .where(
                PlaybookRun.playbook_id == playbook_id,
                PlaybookRun.status.in_(tuple(IN_FLIGHT_STATUSES)),
            )
            .order_by(PlaybookRun.created_at.desc(), PlaybookRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _claim_queued_run(self, db: AsyncSession, run: PlaybookRun) -> PlaybookRun | None:
        now = datetime.now(UTC)
        result = await db.execute(
            update(PlaybookRun)
            .where(
                PlaybookRun.id == run.id,
                PlaybookRun.status == PLAYBOOK_RUN_STATUS_QUEUED,
            )
            .values(status=PLAYBOOK_RUN_STATUS_PROCESSING, started_at=now)
        )
        await db.commit()
        if result.rowcount != 1:
            return None
        claimed = await db.get(PlaybookRun, run.id)
        return claimed

    def _has_successful_version(self, playbook: Playbook) -> bool:
        return (
            playbook.status == PLAYBOOK_STATUS_ACTIVE
            or bool(playbook.last_success_run_id)
            or int(playbook.playbook_version or 0) > 0
        )


def count_source_units(transcripts: PlaybookTranscriptSet, brain: PlaybookBrainSnapshot) -> int:
    turns = sum(len(chat.turns) for chat in transcripts.chats)
    return turns + _brain_unit_count(brain)


def _brain_unit_count(brain: PlaybookBrainSnapshot) -> int:
    return (1 if brain.user_brain is not None else 0) + len(brain.knowledge_items)


def _empty_batch() -> PlaybookExtractionBatch:
    return PlaybookExtractionBatch(
        batch_index=0,
        chat_ids=(),
        turn_ids=(),
        estimated_characters=0,
        chat_count=0,
        turn_count=0,
        oversized=False,
        chats=(),
    )


def _safe_error_message(exc: object, *, fallback: str = SAFE_ERROR_GENERIC) -> str:
    if isinstance(exc, str):
        text = exc.strip()
    else:
        text = str(exc).strip()
    if not text:
        return fallback
    if _SECRET_IN_ERROR.search(text):
        return fallback
    return text[:ERROR_MESSAGE_MAX_CHARS]


playbook_generation_service = PlaybookGenerationService()
