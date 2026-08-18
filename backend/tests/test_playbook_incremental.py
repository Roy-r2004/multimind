"""Provider-free coverage for deterministic pending diffs and rerun admission."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.db.models import (
    PLAYBOOK_RUN_STATUS_FAILED,
    PLAYBOOK_RUN_STATUS_QUEUED,
    PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE,
    PLAYBOOK_SOURCE_TYPE_TURN,
    PLAYBOOK_SOURCE_TYPE_USER_BRAIN,
    PLAYBOOK_STATUS_ACTIVE,
    PlaybookExcludedSource,
    PlaybookObservation,
    PlaybookObservationSource,
    PlaybookRun,
    PlaybookSourceState,
)
from app.services.playbook_extraction_service import (
    BatchExtractionResult,
    CanonicalObservation,
    ExtractedCandidate,
    ExtractionWarning,
    ValidatedEvidence,
    playbook_extraction_service,
)
from app.services.playbook_generation_service import playbook_generation_service
from app.services.playbook_pending_service import playbook_pending_service
from app.services.playbook_service import playbook_service
from app.services.playbook_source_service import playbook_source_service
from tests.test_playbook_source_service import _make_brain, _make_chat, _make_knowledge, _make_turn
from tests.test_playbooks import _client_for, _same_org_other_user, _same_user_other_org


async def _active_book(db, auth):
    book = await playbook_service.get_or_create_for_current_user(db, auth)
    book.status = PLAYBOOK_STATUS_ACTIVE
    book.playbook_version = 1
    book.core_summary = "Version one"
    book.last_success_run_id = None
    book.last_success_at = datetime(2026, 1, 1, tzinfo=UTC)
    await db.flush()
    return book


async def _checkpoint_current(db, auth, book, *turns):
    snapshot = await playbook_source_service.assemble_all_transcripts(db, auth)
    hashes = {turn.turn_id: turn.content_hash for chat in snapshot.chats for turn in chat.turns}
    for turn in turns:
        db.add(
            PlaybookSourceState(
                playbook_id=book.id,
                source_type=PLAYBOOK_SOURCE_TYPE_TURN,
                source_id=turn.id,
                content_hash=hashes[turn.id],
                status="processed",
            )
        )
    await db.flush()
    return hashes


async def _observation(db, book, text, sources):
    row = PlaybookObservation(
        playbook_id=book.id,
        category="decision",
        observation=text,
        status="confirmed",
        confidence=0.9,
        evidence_count=len(sources),
    )
    db.add(row)
    await db.flush()
    for chat_id, turn_id, kind in sources:
        db.add(
            PlaybookObservationSource(
                observation_id=row.id,
                chat_id=chat_id,
                turn_id=turn_id,
                source_kind=kind,
                epistemic_role="user_confirmed",
                quote=text,
            )
        )
    await db.flush()
    return row


def _mock_incremental_pipeline(monkeypatch, *, fail_turn_ids=frozenset(), fail_brain=False):
    calls = []
    real_batch = playbook_source_service.batch_transcripts
    monkeypatch.setattr(
        playbook_source_service,
        "batch_transcripts",
        lambda transcripts, **kwargs: real_batch(transcripts, max_chars=1),
    )

    async def extract(batch, brain, *, include_brain=True, source_text=None):
        calls.append(
            (tuple(batch.turn_ids), include_brain, tuple(item.id for item in brain.knowledge_items))
        )
        if any(turn_id in fail_turn_ids for turn_id in batch.turn_ids) or (
            include_brain and fail_brain
        ):
            return BatchExtractionResult(
                [], [ExtractionWarning(code="forced", message="forced")], False
            )
        candidates = [
            ExtractedCandidate(
                candidate_id=f"new:{turn_id}",
                category="decision",
                subject=None,
                observation=f"fresh:{turn_id}",
                status="confirmed",
                confidence=0.9,
                evidence=(
                    ValidatedEvidence(
                        source_kind="user_message",
                        epistemic_role="user_confirmed",
                        quote="fresh",
                        chat_id=batch.chats[0].chat_id,
                        turn_id=turn_id,
                    ),
                ),
            )
            for turn_id in batch.turn_ids
        ]
        if include_brain:
            candidates.append(
                ExtractedCandidate(
                    candidate_id="new:brain",
                    category="preference",
                    subject=None,
                    observation="fresh brain",
                    status="confirmed",
                    confidence=0.9,
                    evidence=(
                        ValidatedEvidence(
                            source_kind="brain", epistemic_role="user_confirmed", quote="brain"
                        ),
                    ),
                )
            )
        return BatchExtractionResult(candidates, [], True)

    async def consolidate(candidates):
        return (
            [
                CanonicalObservation(
                    category=item.category,
                    subject=item.subject,
                    observation=item.observation,
                    status=item.status,
                    confidence=item.confidence,
                    candidate_ids=(item.candidate_id,),
                    evidence=item.evidence,
                    first_observed_at=item.created_at,
                )
                for item in candidates
            ],
            [],
        )

    async def summary(observations):
        return "Version two", []

    monkeypatch.setattr(playbook_extraction_service, "extract_batch", extract)
    monkeypatch.setattr(playbook_extraction_service, "consolidate_candidates", consolidate)
    monkeypatch.setattr(playbook_extraction_service, "generate_core_summary", summary)
    return calls


async def _execute_incremental(db, auth, book, total=1):
    run = PlaybookRun(
        playbook_id=book.id,
        kind="incremental",
        status=PLAYBOOK_RUN_STATUS_QUEUED,
        total_count=total,
    )
    db.add(run)
    await db.commit()
    result = await playbook_generation_service.execute_incremental_generation(
        db, playbook_id=book.id, run_id=run.id, org_id=auth.org_id, user_id=auth.user.id
    )
    return run, result


@pytest.mark.asyncio
async def test_pending_uses_successful_hash_baseline_and_counts_chat_once(
    db: AsyncSession, auth: AuthContext
):
    chat = await _make_chat(db, auth)
    first = await _make_turn(db, chat, user_message="first")
    second = await _make_turn(db, chat, user_message="second")
    playbook = await playbook_service.get_or_create_for_current_user(db, auth)
    playbook.status = PLAYBOOK_STATUS_ACTIVE
    playbook.playbook_version = 1
    snapshot = await playbook_source_service.assemble_all_transcripts(db, auth)
    hashes = {turn.turn_id: turn.content_hash for item in snapshot.chats for turn in item.turns}
    db.add(
        PlaybookSourceState(
            playbook_id=playbook.id,
            source_type=PLAYBOOK_SOURCE_TYPE_TURN,
            source_id=first.id,
            content_hash=hashes[first.id],
            status="processed",
        )
    )
    await db.commit()

    response = await playbook_pending_service.response(db, auth)
    assert response.pending_source_items == 1
    assert response.new_turns == 1
    assert response.new_chats == 0  # chat already existed in the successful baseline
    assert response.changed_turns == response.removed_turns == 0
    assert second.id != first.id


@pytest.mark.asyncio
async def test_failed_run_never_becomes_pending_baseline(db: AsyncSession, auth: AuthContext):
    chat = await _make_chat(db, auth)
    turn = await _make_turn(db, chat)
    playbook = await playbook_service.get_or_create_for_current_user(db, auth)
    playbook.status = PLAYBOOK_STATUS_ACTIVE
    playbook.playbook_version = 1
    db.add(
        PlaybookSourceState(
            playbook_id=playbook.id,
            source_type=PLAYBOOK_SOURCE_TYPE_TURN,
            source_id=turn.id,
            content_hash="old-hash",
            status="processed",
        )
    )
    db.add(
        PlaybookRun(
            playbook_id=playbook.id,
            kind="incremental",
            status=PLAYBOOK_RUN_STATUS_FAILED,
            total_count=1,
            processed_count=1,
            finished_at=datetime.now(UTC),
        )
    )
    await db.commit()

    response = await playbook_pending_service.response(db, auth)
    assert response.changed_turns == 1
    assert response.pending_source_items == 1


@pytest.mark.asyncio
async def test_rerun_rejects_up_to_date_playbook_without_enqueuing(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    calls = []

    async def enqueue(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(playbook_generation_service, "enqueue_generation_job", enqueue)
    chat = await _make_chat(db, auth)
    turn = await _make_turn(db, chat)
    playbook = await playbook_service.get_or_create_for_current_user(db, auth)
    playbook.status = PLAYBOOK_STATUS_ACTIVE
    playbook.playbook_version = 1
    snapshot = await playbook_source_service.assemble_all_transcripts(db, auth)
    current = snapshot.chats[0].turns[0]
    db.add(
        PlaybookSourceState(
            playbook_id=playbook.id,
            source_type=PLAYBOOK_SOURCE_TYPE_TURN,
            source_id=turn.id,
            content_hash=current.content_hash,
            status="processed",
        )
    )
    await db.commit()
    async with await _client_for(db, auth) as client:
        response = await client.post("/api/v1/playbooks/me/rerun")
    assert response.status_code == 409
    assert "up to date" in str(response.json()).lower()
    assert calls == [] and playbook.playbook_version == 1


@pytest.mark.asyncio
async def test_edit_regenerate_is_not_new_chat_and_real_new_chat_is(db, auth):
    old_chat = await _make_chat(db, auth)
    old = await _make_turn(db, old_chat, user_message="old")
    book = await _active_book(db, auth)
    await _checkpoint_current(db, auth, book, old)
    old.deleted_at = datetime.now(UTC)
    await _make_turn(db, old_chat, user_message="replacement")
    new_chat = await _make_chat(db, auth, title="new chat")
    await _make_turn(db, new_chat, user_message="new one")
    await _make_turn(db, new_chat, user_message="new two")
    await db.commit()

    pending = await playbook_pending_service.response(db, auth)
    assert pending.removed_turns == 1
    assert pending.new_turns == 3
    assert pending.new_chats == 1
    assert pending.pending_source_items == 4


@pytest.mark.asyncio
async def test_changed_failure_preserves_old_evidence_checkpoint_and_pending(db, auth, monkeypatch):
    chat = await _make_chat(db, auth)
    changed = await _make_turn(db, chat, user_message="before")
    succeeding = await _make_turn(db, chat, user_message="baseline")
    book = await _active_book(db, auth)
    hashes = await _checkpoint_current(db, auth, book, changed, succeeding)
    old_obs = await _observation(db, book, "last known", [(chat.id, changed.id, "user_message")])
    changed.user_message = "after"
    new_turn = await _make_turn(db, chat, user_message="new pending")
    await db.commit()
    calls = _mock_incremental_pipeline(monkeypatch, fail_turn_ids={changed.id})

    run, result = await _execute_incremental(db, auth, book, total=2)
    assert result["status"] == "completed_with_warnings"
    assert run.processed_count == 1 and run.total_count == 2
    state = (
        await db.execute(
            select(PlaybookSourceState).where(
                PlaybookSourceState.playbook_id == book.id,
                PlaybookSourceState.source_id == changed.id,
            )
        )
    ).scalar_one()
    assert state.content_hash == hashes[changed.id]
    evidence = (
        (
            await db.execute(
                select(PlaybookObservationSource).where(
                    PlaybookObservationSource.turn_id == changed.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert evidence and evidence[0].quote == "last known"
    retained_texts = {
        row.observation
        for row in (
            await db.execute(
                select(PlaybookObservation).where(PlaybookObservation.playbook_id == book.id)
            )
        )
        .scalars()
        .all()
    }
    assert "last known" in retained_texts
    assert (
        await db.get(PlaybookObservation, old_obs.id) is None
    )  # rows are canonicalized, evidence is preserved on replacement row
    pending = await playbook_pending_service.response(db, auth)
    assert pending.changed_turns == 1 and pending.pending_source_items == 1
    assert {ids for ids, _, _ in calls} == {(changed.id,), (new_turn.id,)}


@pytest.mark.asyncio
async def test_failed_new_source_has_no_checkpoint_or_observation_and_stays_pending(
    db, auth, monkeypatch
):
    chat = await _make_chat(db, auth)
    baseline = await _make_turn(db, chat)
    book = await _active_book(db, auth)
    await _checkpoint_current(db, auth, book, baseline)
    await _observation(db, book, "baseline", [(chat.id, baseline.id, "user_message")])
    failed = await _make_turn(db, chat, user_message="failed new")
    success = await _make_turn(db, chat, user_message="successful new")
    await db.commit()
    _mock_incremental_pipeline(monkeypatch, fail_turn_ids={failed.id})
    run, result = await _execute_incremental(db, auth, book, total=2)
    assert result["status"] == "completed_with_warnings"
    assert (run.processed_count, run.total_count) == (1, 2)
    assert (
        await db.execute(
            select(PlaybookSourceState).where(PlaybookSourceState.source_id == failed.id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db.execute(
            select(PlaybookObservationSource).where(PlaybookObservationSource.turn_id == failed.id)
        )
    ).scalar_one_or_none() is None
    assert (await playbook_pending_service.response(db, auth)).new_turns == 1
    assert success.id != failed.id


@pytest.mark.asyncio
async def test_removed_turn_reconciles_evidence_observations_checkpoint_without_extraction(
    db, auth, monkeypatch
):
    chat = await _make_chat(db, auth)
    removed = await _make_turn(db, chat, user_message="remove")
    survivor = await _make_turn(db, chat, user_message="keep")
    book = await _active_book(db, auth)
    await _checkpoint_current(db, auth, book, removed, survivor)
    await _observation(
        db,
        book,
        "shared",
        [(chat.id, removed.id, "user_message"), (chat.id, survivor.id, "user_message")],
    )
    await _observation(db, book, "only removed", [(chat.id, removed.id, "user_message")])
    removed.deleted_at = datetime.now(UTC)
    await db.commit()
    calls = _mock_incremental_pipeline(monkeypatch)
    run, result = await _execute_incremental(db, auth, book)
    assert result["status"] == "completed"
    assert calls == [] and (run.processed_count, run.total_count) == (1, 1)
    observations = (
        (
            await db.execute(
                select(PlaybookObservation).where(PlaybookObservation.playbook_id == book.id)
            )
        )
        .scalars()
        .all()
    )
    assert [row.observation for row in observations] == ["shared"]
    assert observations[0].evidence_count == 1
    assert (
        await db.execute(
            select(PlaybookObservationSource).where(PlaybookObservationSource.turn_id == removed.id)
        )
    ).scalars().all() == []
    assert (
        await db.execute(
            select(PlaybookSourceState).where(PlaybookSourceState.source_id == removed.id)
        )
    ).scalar_one_or_none() is None
    assert (await playbook_pending_service.response(db, auth)).pending_source_items == 0


@pytest.mark.asyncio
async def test_only_new_and_changed_ids_extracted_brain_unchanged(db, auth, monkeypatch):
    chat = await _make_chat(db, auth)
    unchanged = [await _make_turn(db, chat, user_message=f"stable {i}") for i in range(10)]
    changed = await _make_turn(db, chat, user_message="before")
    removed = await _make_turn(db, chat, user_message="removed")
    brain = await _make_brain(db, auth)
    book = await _active_book(db, auth)
    await _checkpoint_current(db, auth, book, *unchanged, changed, removed)
    brain_snapshot = await playbook_source_service.build_brain_source_snapshot(db, auth)
    db.add(
        PlaybookSourceState(
            playbook_id=book.id,
            source_type=PLAYBOOK_SOURCE_TYPE_USER_BRAIN,
            source_id=brain.id,
            content_hash=brain_snapshot.user_brain.content_hash,
            status="processed",
        )
    )
    await _observation(db, book, "stable", [(chat.id, unchanged[0].id, "user_message")])
    changed.user_message = "after"
    removed.deleted_at = datetime.now(UTC)
    new = await _make_turn(db, chat, user_message="new")
    await db.commit()
    calls = _mock_incremental_pipeline(monkeypatch)
    await _execute_incremental(db, auth, book, total=3)
    extracted = {turn_id for ids, _, _ in calls for turn_id in ids}
    assert extracted == {changed.id, new.id}
    assert all(include_brain is False for _, include_brain, _ in calls)
    assert not extracted.intersection({turn.id for turn in unchanged} | {removed.id})


@pytest.mark.asyncio
async def test_brain_refresh_success_failure_and_removal(db, auth, monkeypatch):
    chat = await _make_chat(db, auth)
    stable = await _make_turn(db, chat)
    brain = await _make_brain(db, auth)
    knowledge = await _make_knowledge(db, auth)
    book = await _active_book(db, auth)
    await _checkpoint_current(db, auth, book, stable)
    snapshot = await playbook_source_service.build_brain_source_snapshot(db, auth)
    for kind, item in [
        (PLAYBOOK_SOURCE_TYPE_USER_BRAIN, snapshot.user_brain),
        (PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE, snapshot.knowledge_items[0]),
    ]:
        db.add(
            PlaybookSourceState(
                playbook_id=book.id,
                source_type=kind,
                source_id=item.id,
                content_hash=item.content_hash,
                status="processed",
            )
        )
    await _observation(db, book, "old brain", [(None, None, "brain")])
    brain.summary = "changed brain"
    await db.commit()
    failed_calls = _mock_incremental_pipeline(monkeypatch, fail_brain=True)
    _, failed = await _execute_incremental(db, auth, book)
    assert failed["status"] == "failed"
    assert (
        (
            await db.execute(
                select(PlaybookObservationSource).where(
                    PlaybookObservationSource.turn_id.is_(None),
                    PlaybookObservationSource.chat_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    assert (await playbook_pending_service.response(db, auth)).brain_changes == 1
    assert failed_calls == [((), True, (knowledge.id,))]

    calls = _mock_incremental_pipeline(monkeypatch)
    run, success = await _execute_incremental(db, auth, book)
    assert success["status"] == "completed" and run.processed_count == run.total_count == 1
    assert calls == [((), True, (knowledge.id,))]
    assert (await playbook_pending_service.response(db, auth)).pending_source_items == 0
    brain_evidence = (
        (
            await db.execute(
                select(PlaybookObservationSource).where(
                    PlaybookObservationSource.turn_id.is_(None),
                    PlaybookObservationSource.chat_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.quote for row in brain_evidence} == {"brain"}
    await db.delete(knowledge)
    await db.commit()
    calls = _mock_incremental_pipeline(monkeypatch)
    await _execute_incremental(db, auth, book)
    assert calls == [((), True, ())]
    assert (
        await db.execute(
            select(PlaybookSourceState).where(PlaybookSourceState.source_id == knowledge.id)
        )
    ).scalar_one_or_none() is None
    assert (await playbook_pending_service.response(db, auth)).pending_source_items == 0


@pytest.mark.asyncio
async def test_excluded_turn_is_not_pending_or_extracted(db, auth):
    chat = await _make_chat(db, auth)
    turn = await _make_turn(db, chat)
    book = await _active_book(db, auth)
    db.add(PlaybookExcludedSource(playbook_id=book.id, turn_id=turn.id))
    await db.commit()
    pending = await playbook_pending_service.response(db, auth)
    assert pending.pending_source_items == 0 and pending.new_turns == 0


@pytest.mark.asyncio
async def test_successful_rerun_updates_once_and_fatal_failure_preserves_baseline(
    db, auth, monkeypatch
):
    chat = await _make_chat(db, auth)
    changed = await _make_turn(db, chat, user_message="old")
    removed = await _make_turn(db, chat, user_message="remove")
    changed_id, removed_id = changed.id, removed.id
    book = await _active_book(db, auth)
    old_hashes = await _checkpoint_current(db, auth, book, changed, removed)
    await _observation(db, book, "old semantic", [(chat.id, changed.id, "user_message")])
    changed.user_message = "new semantic"
    removed.deleted_at = datetime.now(UTC)
    new = await _make_turn(db, chat, user_message="new")
    new_id = new.id
    await db.commit()
    _mock_incremental_pipeline(monkeypatch)
    original_persist = playbook_generation_service._persist_final

    async def fail_persist(*args, **kwargs):
        raise RuntimeError("forced final failure")

    monkeypatch.setattr(playbook_generation_service, "_persist_final", fail_persist)
    failed_run, failed = await _execute_incremental(db, auth, book, total=3)
    assert failed["status"] == "failed" and failed_run.status == "failed"
    await db.refresh(auth.user)
    await db.refresh(book)
    assert (book.playbook_version, book.core_summary) == (1, "Version one")
    assert book.last_success_at.replace(tzinfo=UTC) == datetime(2026, 1, 1, tzinfo=UTC)
    states = {
        (row.source_id, row.content_hash)
        for row in (
            await db.execute(
                select(PlaybookSourceState).where(PlaybookSourceState.playbook_id == book.id)
            )
        )
        .scalars()
        .all()
    }
    assert (changed_id, old_hashes[changed_id]) in states and (
        removed_id,
        old_hashes[removed_id],
    ) in states
    assert (await playbook_pending_service.response(db, auth)).pending_source_items == 3

    monkeypatch.setattr(playbook_generation_service, "_persist_final", original_persist)
    run, success = await _execute_incremental(db, auth, book, total=3)
    assert success["status"] == "completed"
    await db.refresh(book)
    assert book.playbook_version == 2 and book.last_success_run_id == run.id
    assert book.last_success_at.replace(tzinfo=UTC) > datetime(2026, 1, 1, tzinfo=UTC)
    assert book.core_summary == "Version two"
    current = await playbook_source_service.assemble_all_transcripts(db, auth)
    current_hash = next(
        turn.content_hash
        for item in current.chats
        for turn in item.turns
        if turn.turn_id == changed_id
    )
    state_rows = (
        (
            await db.execute(
                select(PlaybookSourceState).where(PlaybookSourceState.playbook_id == book.id)
            )
        )
        .scalars()
        .all()
    )
    state_map = {row.source_id: row.content_hash for row in state_rows}
    assert (
        state_map[changed_id] == current_hash
        and new_id in state_map
        and removed_id not in state_map
    )
    observation_texts = {
        row.observation
        for row in (
            await db.execute(
                select(PlaybookObservation).where(PlaybookObservation.playbook_id == book.id)
            )
        )
        .scalars()
        .all()
    }
    assert "old semantic" not in observation_texts
    assert (await playbook_pending_service.response(db, auth)).pending_source_items == 0


@pytest.mark.asyncio
async def test_duplicate_incremental_api_enqueues_one_job_and_returns_same_run(
    db, auth, monkeypatch
):
    chat = await _make_chat(db, auth)
    baseline = await _make_turn(db, chat)
    book = await _active_book(db, auth)
    await _checkpoint_current(db, auth, book, baseline)
    await _make_turn(db, chat, user_message="pending")
    await db.commit()
    jobs = []

    async def enqueue(**kwargs):
        jobs.append(kwargs)

    monkeypatch.setattr(playbook_generation_service, "enqueue_generation_job", enqueue)
    async with await _client_for(db, auth) as client:
        first = await client.post("/api/v1/playbooks/me/rerun")
        second = await client.post("/api/v1/playbooks/me/rerun")
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert len(jobs) == 1
    active = (
        (
            await db.execute(
                select(PlaybookRun).where(
                    PlaybookRun.playbook_id == book.id,
                    PlaybookRun.status.in_(("queued", "processing")),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(active) == 1


@pytest.mark.asyncio
async def test_incremental_pending_and_rerun_are_user_and_org_isolated(db, auth, monkeypatch):
    chat = await _make_chat(db, auth)
    baseline = await _make_turn(db, chat)
    book = await _active_book(db, auth)
    await _checkpoint_current(db, auth, book, baseline)
    await _make_turn(db, chat, user_message="A pending")
    peer = await _same_org_other_user(db, auth)
    other_org = await _same_user_other_org(db, auth)
    await db.commit()
    jobs = []

    async def enqueue(**kwargs):
        jobs.append(kwargs)

    monkeypatch.setattr(playbook_generation_service, "enqueue_generation_job", enqueue)
    async with await _client_for(db, peer) as client:
        peer_pending = await client.get("/api/v1/playbooks/me/pending")
        peer_rerun = await client.post("/api/v1/playbooks/me/rerun")
    async with await _client_for(db, other_org) as client:
        org_pending = await client.get("/api/v1/playbooks/me/pending")
        org_rerun = await client.post("/api/v1/playbooks/me/rerun")
    assert peer_pending.json()["pending_source_items"] == 0
    assert org_pending.json()["pending_source_items"] == 0
    assert peer_rerun.status_code == org_rerun.status_code == 409
    assert jobs == []
    assert (await playbook_pending_service.response(db, auth)).pending_source_items == 1
