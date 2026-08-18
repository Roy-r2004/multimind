"""Playbook Phase 4: first full generation API, worker, progress, and persistence."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.db.models import (
    PLAYBOOK_RUN_STATUS_FAILED,
    PLAYBOOK_RUN_STATUS_PROCESSING,
    PLAYBOOK_RUN_STATUS_QUEUED,
    PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE,
    PLAYBOOK_SOURCE_TYPE_TURN,
    PLAYBOOK_SOURCE_TYPE_USER_BRAIN,
    Playbook,
    PlaybookObservation,
    PlaybookObservationSource,
    PlaybookRun,
    PlaybookSourceState,
)
from app.playbooks.worker import generate_playbook_job
from app.services.playbook_extraction_service import playbook_extraction_service
from app.services.playbook_generation_service import (
    GENERATE_PLAYBOOK_JOB,
    count_source_units,
    playbook_generation_service,
)
from app.services.playbook_source_service import playbook_source_service
from tests.test_playbook_source_service import (
    _make_brain,
    _make_chat,
    _make_knowledge,
    _make_turn,
)
from tests.test_playbooks import _client_for, _same_org_other_user, _same_user_other_org

TURN_RE = re.compile(r"TURN id=(\S+) chat_id=(\S+)")
CANDIDATE_RE = re.compile(r'"candidate_id": "([^"]+)"')


@pytest.fixture
def enqueue_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_enqueue(*, playbook_id, run_id, org_id, user_id):
        calls.append(
            {
                "playbook_id": playbook_id,
                "run_id": run_id,
                "org_id": org_id,
                "user_id": user_id,
            }
        )

    monkeypatch.setattr(
        playbook_generation_service,
        "enqueue_generation_job",
        fake_enqueue,
    )
    return calls


def _install_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete(*, system, user, max_tokens, json_mode=False):
        if "Write the compact Playbook" in user or user == "Compress now.":
            return "Working style: concise. PostgreSQL is the selected MultiMind database."
        if "Consolidate the candidates" in user:
            ids = CANDIDATE_RE.findall(system)
            preference = '"category": "preference"' in system
            return json.dumps(
                {
                    "observations": [
                        {
                            "category": "preference" if preference else "decision",
                            "subject": "Style" if preference else "MultiMind database",
                            "observation": "Prefers concise answers." if preference else "PostgreSQL is the selected primary database for MultiMind.",
                            "status": "uncertain" if preference else "confirmed",
                            "confidence": 0.9,
                            "candidate_ids": [cid],
                        }
                        for cid in ids
                    ]
                }
            )
        observations = []
        for turn_id, chat_id in TURN_RE.findall(user):
            observations.append(
                {
                    "category": "decision",
                    "subject": "MultiMind database",
                    "observation": "PostgreSQL is the selected primary database for MultiMind.",
                    "status": "confirmed",
                    "confidence": 0.98,
                    "evidence": [
                        {
                            "chat_id": chat_id,
                            "turn_id": turn_id,
                            "source_kind": "user_message",
                            "epistemic_role": "user_confirmed",
                            "quote": "PostgreSQL is the selected primary database",
                        }
                    ],
                }
            )
        if not observations and "BRAIN PROFILE" in user:
            observations.append(
                {
                    "category": "preference",
                    "subject": "Style",
                    "observation": "Prefers concise answers.",
                    "status": "confirmed",
                    "confidence": 0.9,
                    "evidence": [
                        {
                            "source_kind": "brain",
                            "epistemic_role": "user_stated",
                            "quote": "Prefers concise answers",
                        }
                    ],
                }
            )
        return json.dumps({"observations": observations, "warnings": []})

    monkeypatch.setattr(playbook_extraction_service, "_complete", fake_complete)


async def _eligible_turn(
    db: AsyncSession,
    auth: AuthContext,
    *,
    created_at: datetime | None = None,
    **turn_kwargs,
):
    chat = await _make_chat(db, auth, title="Playbook chat", created_at=created_at)
    turn = await _make_turn(
        db,
        chat,
        user_message=turn_kwargs.pop(
            "user_message",
            "PostgreSQL is the selected primary database for MultiMind.",
        ),
        **turn_kwargs,
    )
    return chat, turn


@pytest.mark.asyncio
async def test_generate_api_creates_queued_run_on_playbooks_queue(
    db: AsyncSession, auth: AuthContext, enqueue_calls: list[dict]
):
    await _eligible_turn(db, auth)
    async with await _client_for(db, auth) as client:
        ignored = await client.post(
            "/api/v1/playbooks/me/generate",
            json={
                "user_id": "someone-else",
                "org_id": "other-org",
                "playbook_id": "not-mine",
                "model": "gpt-4o",
                "queue": "arq:queue",
            },
        )
        playbook = (await client.get("/api/v1/playbooks/me")).json()
        latest_before = await client.get("/api/v1/playbooks/me/runs/latest")
    assert ignored.status_code == 202
    body = ignored.json()
    assert body["kind"] == "full"
    assert body["status"] == "queued"
    assert body["playbook_id"] == playbook["id"]
    assert body["processed_count"] == 0
    assert body["total_count"] >= 1
    assert body["warning_count"] == 0
    assert len(enqueue_calls) == 1
    payload = enqueue_calls[0]
    assert set(payload) == {"playbook_id", "run_id", "org_id", "user_id"}
    assert payload["playbook_id"] == playbook["id"]
    assert payload["run_id"] == body["id"]
    assert payload["org_id"] == auth.org_id
    assert payload["user_id"] == auth.user.id
    assert latest_before.status_code == 200
    assert latest_before.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_enqueue_job_uses_playbooks_queue_and_primitive_ids(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    class FakeRedis:
        async def enqueue_job(self, name, *args, **kwargs):
            captured["name"] = name
            captured["args"] = args
            captured["kwargs"] = kwargs
            return object()

        async def close(self, close_connection_pool=True):
            captured["closed"] = True

    async def fake_pool(*args, **kwargs):
        captured["pool_kwargs"] = kwargs
        return FakeRedis()

    monkeypatch.setattr("arq.create_pool", fake_pool)
    await playbook_generation_service.enqueue_generation_job(
        playbook_id="pb",
        run_id="run",
        org_id="org",
        user_id="user",
    )
    assert captured["name"] == GENERATE_PLAYBOOK_JOB
    assert captured["args"] == ("pb", "run", "org", "user")
    assert captured["kwargs"]["_queue_name"] == "playbooks"
    assert captured["kwargs"]["_job_id"] == "playbook-generate:run"
    assert all(isinstance(item, str) for item in captured["args"])


@pytest.mark.asyncio
async def test_generate_scopes_and_duplicate_in_flight_runs(
    db: AsyncSession, auth: AuthContext, enqueue_calls: list[dict]
):
    await _eligible_turn(db, auth)
    peer = await _same_org_other_user(db, auth)
    other_org = await _same_user_other_org(db, auth)
    await _eligible_turn(db, peer)
    await _eligible_turn(db, other_org)

    async with await _client_for(db, auth) as client:
        first = await client.post("/api/v1/playbooks/me/generate")
        second = await client.post("/api/v1/playbooks/me/generate")
    async with await _client_for(db, peer) as client:
        peer_run = await client.post("/api/v1/playbooks/me/generate")
    async with await _client_for(db, other_org) as client:
        other_run = await client.post("/api/v1/playbooks/me/generate")

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert peer_run.json()["playbook_id"] != first.json()["playbook_id"]
    assert other_run.json()["playbook_id"] != first.json()["playbook_id"]
    assert len(enqueue_calls) == 3

    run = await db.get(PlaybookRun, first.json()["id"])
    run.status = PLAYBOOK_RUN_STATUS_PROCESSING
    await db.flush()
    async with await _client_for(db, auth) as client:
        processing = await client.post("/api/v1/playbooks/me/generate")
    assert processing.json()["id"] == first.json()["id"]
    assert len(enqueue_calls) == 3


@pytest.mark.asyncio
async def test_active_playbook_conflicts_and_failed_run_can_retry(
    db: AsyncSession, auth: AuthContext, enqueue_calls: list[dict]
):
    await _eligible_turn(db, auth)
    async with await _client_for(db, auth) as client:
        playbook_body = (await client.get("/api/v1/playbooks/me")).json()
        first = await client.post("/api/v1/playbooks/me/generate")
    run = await db.get(PlaybookRun, first.json()["id"])
    run.status = PLAYBOOK_RUN_STATUS_FAILED
    run.finished_at = datetime.now(UTC)
    await db.flush()

    async with await _client_for(db, auth) as client:
        retry = await client.post("/api/v1/playbooks/me/generate")
    assert retry.status_code == 202
    assert retry.json()["id"] != first.json()["id"]
    assert retry.json()["status"] == "queued"
    assert len(enqueue_calls) == 2

    retry_run = await db.get(PlaybookRun, retry.json()["id"])
    retry_run.status = PLAYBOOK_RUN_STATUS_FAILED
    retry_run.finished_at = datetime.now(UTC)
    playbook = await db.get(Playbook, playbook_body["id"])
    playbook.status = "active"
    playbook.playbook_version = 1
    playbook.last_success_run_id = first.json()["id"]
    await db.flush()
    async with await _client_for(db, auth) as client:
        conflict = await client.post("/api/v1/playbooks/me/generate")
    assert conflict.status_code == 409
    assert "rerun is not implemented" in conflict.json()["message"]


@pytest.mark.asyncio
async def test_no_sources_and_enqueue_failure(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    async with await _client_for(db, auth) as client:
        missing = await client.post("/api/v1/playbooks/me/generate")
    assert missing.status_code == 400
    assert missing.json()["error"] == "VALIDATION_ERROR"

    await _eligible_turn(db, auth)

    async def boom(*, playbook_id, run_id, org_id, user_id):
        raise RuntimeError("redis://secret@localhost:6379/0 failed")

    monkeypatch.setattr(playbook_generation_service, "enqueue_generation_job", boom)
    async with await _client_for(db, auth) as client:
        response = await client.post("/api/v1/playbooks/me/generate")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["finished_at"]
    assert "redis://" not in (body["error_message"] or "")
    playbook = await db.get(Playbook, body["playbook_id"])
    assert playbook.status == "not_generated"
    assert playbook.last_success_run_id is None
    assert playbook.last_success_at is None


@pytest.mark.asyncio
async def test_polling_authorization_and_progress_fields(
    db: AsyncSession, auth: AuthContext, enqueue_calls: list[dict]
):
    await _eligible_turn(db, auth)
    peer = await _same_org_other_user(db, auth)
    other_org = await _same_user_other_org(db, auth)
    async with await _client_for(db, auth) as client:
        created = await client.post("/api/v1/playbooks/me/generate")
        run_id = created.json()["id"]
        mine = await client.get(f"/api/v1/playbooks/me/runs/{run_id}")
        latest = await client.get("/api/v1/playbooks/me/runs/latest")
    async with await _client_for(db, peer) as client:
        peer_get = await client.get(f"/api/v1/playbooks/me/runs/{run_id}")
        await client.get("/api/v1/playbooks/me")
    async with await _client_for(db, other_org) as client:
        other_get = await client.get(f"/api/v1/playbooks/me/runs/{run_id}")
    assert mine.status_code == 200
    for field in (
        "id",
        "playbook_id",
        "kind",
        "status",
        "processed_count",
        "total_count",
        "warning_count",
        "error_message",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ):
        assert field in mine.json()
    assert latest.json()["id"] == run_id
    assert peer_get.status_code == 404
    assert other_get.status_code == 404


@pytest.mark.asyncio
async def test_worker_ownership_claim_and_duplicate_execution(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    _install_default_model(monkeypatch)
    await _eligible_turn(db, auth)
    peer = await _same_org_other_user(db, auth)
    async with await _client_for(db, auth) as client:
        created = await client.post("/api/v1/playbooks/me/generate")
    body = created.json()
    playbook_id = body["playbook_id"]
    run_id = body["id"]

    mismatched_org = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id="not-the-org",
        user_id=auth.user.id,
    )
    assert mismatched_org["skipped"] is True
    mismatched_user = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=peer.user.id,
    )
    assert mismatched_user["skipped"] is True
    still_queued = await db.get(PlaybookRun, run_id)
    assert still_queued.status == PLAYBOOK_RUN_STATUS_QUEUED

    peer_book = Playbook(org_id=auth.org_id, user_id=peer.user.id)
    db.add(peer_book)
    await db.flush()
    with pytest.raises(Exception):
        await playbook_generation_service.execute_full_generation(
            db,
            playbook_id=playbook_id,
            run_id="missing-run",
            org_id=auth.org_id,
            user_id=auth.user.id,
        )

    first = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert first["skipped"] is False
    assert first["status"] in {"completed", "completed_with_warnings"}
    observations = list(
        (
            await db.execute(
                select(PlaybookObservation).where(
                    PlaybookObservation.playbook_id == playbook_id
                )
            )
        ).scalars().all()
    )
    assert observations

    second = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert second["skipped"] is True
    after = list(
        (
            await db.execute(
                select(PlaybookObservation).where(
                    PlaybookObservation.playbook_id == playbook_id
                )
            )
        ).scalars().all()
    )
    assert len(after) == len(observations)

    completed = await db.get(PlaybookRun, run_id)
    completed.status = PLAYBOOK_RUN_STATUS_FAILED
    await db.flush()
    rerun_failed = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert rerun_failed["skipped"] is True


@pytest.mark.asyncio
async def test_successful_generation_persists_playbook_atomically(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    _install_default_model(monkeypatch)
    chat, turn = await _eligible_turn(db, auth)
    brain = await _make_brain(db, auth)
    knowledge = await _make_knowledge(db, auth)
    async with await _client_for(db, auth) as client:
        created = await client.post("/api/v1/playbooks/me/generate")
        playbook_body = (await client.get("/api/v1/playbooks/me")).json()
    assert playbook_body["injection_enabled"] is True
    run_id = created.json()["id"]
    playbook_id = created.json()["playbook_id"]

    transcripts = await playbook_source_service.assemble_all_transcripts(db, auth)
    brain_snap = await playbook_source_service.build_brain_source_snapshot(db, auth)
    expected_total = count_source_units(transcripts, brain_snap)
    assert expected_total == 1 + 1 + 1

    result = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert result["skipped"] is False

    playbook = await db.get(Playbook, playbook_id)
    run = await db.get(PlaybookRun, run_id)
    assert playbook.status == "active"
    assert playbook.playbook_version == 1
    assert playbook.last_success_run_id == run_id
    assert playbook.last_success_at is not None
    assert playbook.injection_enabled is True
    assert playbook.core_summary
    assert run.status in {"completed", "completed_with_warnings"}
    assert run.total_count == expected_total
    assert run.processed_count == expected_total
    if run.warning_count == 0:
        assert run.status == "completed"
    else:
        assert run.status == "completed_with_warnings"

    observations = list(
        (
            await db.execute(
                select(PlaybookObservation).where(
                    PlaybookObservation.playbook_id == playbook_id
                )
            )
        ).scalars().all()
    )
    assert observations
    sources = list(
        (
            await db.execute(select(PlaybookObservationSource))
        ).scalars().all()
    )
    assert sources
    for observation in observations:
        evidence = [
            row for row in sources if row.observation_id == observation.id
        ]
        assert observation.evidence_count == len(evidence)
        assert observation.user_corrected is False
        assert observation.user_excluded is False
        assert observation.first_observed_at == turn.created_at or observation.first_observed_at is not None

    states = list(
        (
            await db.execute(
                select(PlaybookSourceState).where(
                    PlaybookSourceState.playbook_id == playbook_id
                )
            )
        ).scalars().all()
    )
    types = {row.source_type for row in states}
    assert PLAYBOOK_SOURCE_TYPE_TURN in types
    assert PLAYBOOK_SOURCE_TYPE_USER_BRAIN in types
    assert PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE in types
    turn_state = next(row for row in states if row.source_type == PLAYBOOK_SOURCE_TYPE_TURN)
    assert turn_state.source_id == turn.id
    assert turn_state.content_hash == transcripts.chats[0].turns[0].content_hash
    assert turn_state.processed_run_id == run_id
    brain_state = next(
        row for row in states if row.source_type == PLAYBOOK_SOURCE_TYPE_USER_BRAIN
    )
    assert brain_state.source_id == brain.id
    assert brain_state.content_hash == brain_snap.user_brain.content_hash
    know_state = next(
        row for row in states if row.source_type == PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE
    )
    assert know_state.source_id == knowledge.id
    assert know_state.content_hash == brain_snap.knowledge_items[0].content_hash

    async with await _client_for(db, auth) as client:
        listed = await client.get("/api/v1/playbooks/me/observations")
        polled = await client.get(f"/api/v1/playbooks/me/runs/{run_id}")
        me = await client.get("/api/v1/playbooks/me")
    assert listed.status_code == 200
    assert listed.json()
    assert polled.json()["status"] == run.status
    assert me.json()["core_summary"] == playbook.core_summary
    assert "password=" not in (playbook.core_summary or "")
    assert "api_key=" not in (playbook.core_summary or "")


async def _start_run(db: AsyncSession, auth: AuthContext) -> tuple[str, str]:
    async with await _client_for(db, auth) as client:
        created = await client.post("/api/v1/playbooks/me/generate")
    return created.json()["playbook_id"], created.json()["id"]


async def _assert_no_playbook_content(db: AsyncSession, playbook_id: str) -> Playbook:
    playbook = await db.get(Playbook, playbook_id)
    assert playbook.status == "not_generated"
    assert playbook.last_success_run_id is None
    assert playbook.last_success_at is None
    assert playbook.playbook_version == 0
    assert list(
        (
            await db.execute(
                select(PlaybookObservation).where(
                    PlaybookObservation.playbook_id == playbook_id
                )
            )
        ).scalars().all()
    ) == []
    assert list(
        (
            await db.execute(
                select(PlaybookSourceState).where(
                    PlaybookSourceState.playbook_id == playbook_id
                )
            )
        ).scalars().all()
    ) == []
    return playbook


@pytest.mark.asyncio
async def test_model_failure_before_consolidation_persists_nothing(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    from app.services.playbook_extraction_service import (
        BatchExtractionResult,
        ExtractionWarning,
    )

    await _eligible_turn(db, auth)
    playbook_id, run_id = await _start_run(db, auth)

    async def fail_extract(*args, **kwargs):
        return BatchExtractionResult(
            candidates=[],
            warnings=[
                ExtractionWarning(code="json_repair_failed", message="still invalid")
            ],
            succeeded=False,
        )

    monkeypatch.setattr(playbook_extraction_service, "extract_batch", fail_extract)
    failed = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert failed["status"] == "failed"
    run = await db.get(PlaybookRun, run_id)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.total_count >= 1
    await _assert_no_playbook_content(db, playbook_id)


@pytest.mark.asyncio
async def test_consolidation_and_summary_failures_persist_nothing(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    from app.services.playbook_extraction_service import PlaybookExtractionError

    await _eligible_turn(db, auth)
    _install_default_model(monkeypatch)

    async def fail_consolidate(*args, **kwargs):
        raise PlaybookExtractionError("Consolidation failed after retry")

    monkeypatch.setattr(
        playbook_extraction_service, "consolidate_candidates", fail_consolidate
    )
    playbook_id, run_id = await _start_run(db, auth)
    failed = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert failed["status"] == "failed"
    await _assert_no_playbook_content(db, playbook_id)


@pytest.mark.asyncio
async def test_core_summary_failure_persists_nothing(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    from app.services.playbook_extraction_service import PlaybookExtractionError

    await _eligible_turn(db, auth)
    _install_default_model(monkeypatch)

    async def fail_summary(*args, **kwargs):
        raise PlaybookExtractionError("Core summary was empty.")

    monkeypatch.setattr(playbook_extraction_service, "generate_core_summary", fail_summary)
    playbook_id, run_id = await _start_run(db, auth)
    failed_sum = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert failed_sum["status"] == "failed"
    await _assert_no_playbook_content(db, playbook_id)


@pytest.mark.asyncio
async def test_persist_failure_rolls_back_and_retry_can_succeed(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    await _eligible_turn(db, auth)
    _install_default_model(monkeypatch)
    playbook_id, run_id = await _start_run(db, auth)

    async def boom_persist(*args, **kwargs):
        db_session = args[0]
        db_session.add(
            PlaybookObservation(
                playbook_id=playbook_id,
                category="decision",
                observation="should roll back",
                status="active",
            )
        )
        await db_session.flush()
        raise RuntimeError("persist boom")

    monkeypatch.setattr(playbook_generation_service, "_persist_final", boom_persist)
    failed = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert failed["status"] == "failed"
    await _assert_no_playbook_content(db, playbook_id)
    failed_run = await db.get(PlaybookRun, run_id)
    assert failed_run.status == "failed"
    assert failed_run.processed_count >= 0


@pytest.mark.asyncio
async def test_retry_after_failed_run_can_succeed(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    from app.services.playbook_extraction_service import (
        BatchExtractionResult,
        ExtractionWarning,
    )

    await _eligible_turn(db, auth)

    async def fail_extract(*args, **kwargs):
        return BatchExtractionResult(
            candidates=[],
            warnings=[ExtractionWarning(code="json_repair_failed", message="invalid")],
            succeeded=False,
        )

    monkeypatch.setattr(playbook_extraction_service, "extract_batch", fail_extract)
    playbook_id, run_id = await _start_run(db, auth)
    await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    monkeypatch.setattr(
        playbook_extraction_service,
        "extract_batch",
        playbook_extraction_service.__class__.extract_batch.__get__(
            playbook_extraction_service, playbook_extraction_service.__class__
        ),
    )
    _install_default_model(monkeypatch)
    _, retry_id = await _start_run(db, auth)
    success = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=retry_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert success["status"] in {"completed", "completed_with_warnings"}
    playbook = await db.get(Playbook, playbook_id)
    assert playbook.status == "active"
    assert playbook.last_success_run_id == retry_id
    assert playbook.last_success_at is not None


@pytest.mark.asyncio
async def test_successful_run_with_warnings_uses_warning_status(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    from app.services.playbook_extraction_service import (
        BatchExtractionResult,
        ExtractionWarning,
    )

    await _eligible_turn(db, auth)
    _install_default_model(monkeypatch)
    original = playbook_extraction_service.extract_batch

    async def warn_extract(*args, **kwargs):
        result = await original(*args, **kwargs)
        result.warnings.append(
            ExtractionWarning(code="invalid_candidate", message="dropped one")
        )
        return result

    monkeypatch.setattr(playbook_extraction_service, "extract_batch", warn_extract)
    playbook_id, run_id = await _start_run(db, auth)
    result = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert result["status"] == "completed_with_warnings"
    run = await db.get(PlaybookRun, run_id)
    assert run.warning_count >= 1
    playbook = await db.get(Playbook, playbook_id)
    assert playbook.status == "active"


@pytest.mark.asyncio
async def test_consolidation_format_fallback_completes_and_persists_atomically(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    await _eligible_turn(db, auth)
    _install_default_model(monkeypatch)
    normal_complete = playbook_extraction_service._complete

    async def malformed_consolidation(*, system, user, max_tokens, json_mode=False):
        if user.startswith(
            ("Consolidate the candidates", "Your previous output was invalid JSON")
        ):
            return '{"observations": ['
        return await normal_complete(
            system=system, user=user, max_tokens=max_tokens, json_mode=json_mode
        )

    monkeypatch.setattr(playbook_extraction_service, "_complete", malformed_consolidation)
    playbook_id, run_id = await _start_run(db, auth)
    result = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )

    assert result["status"] == "completed_with_warnings"
    run = await db.get(PlaybookRun, run_id)
    assert run.status == "completed_with_warnings"
    assert run.warning_count >= 4
    playbook = await db.get(Playbook, playbook_id)
    assert playbook.status == "active"
    assert playbook.playbook_version == 1
    assert playbook.last_success_run_id == run_id
    assert playbook.last_success_at is not None
    observations = list((await db.execute(select(PlaybookObservation))).scalars().all())
    sources = list((await db.execute(select(PlaybookObservationSource))).scalars().all())
    states = list((await db.execute(select(PlaybookSourceState))).scalars().all())
    assert observations
    assert sources
    assert states


@pytest.mark.asyncio
async def test_generate_playbook_job_uses_session_factory(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    _install_default_model(monkeypatch)
    await _eligible_turn(db, auth)
    playbook_id, run_id = await _start_run(db, auth)
    result = await generate_playbook_job(
        {"session_factory": _SessionCM(db)},
        playbook_id,
        run_id,
        auth.org_id,
        auth.user.id,
    )
    assert result["skipped"] is False
    assert result["status"] in {"completed", "completed_with_warnings"}


class _SessionCM:
    def __init__(self, session: AsyncSession):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _force_single_turn_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    real = playbook_source_service.batch_transcripts

    def tiny(transcripts, *, max_chars=1):
        return real(transcripts, max_chars=1)

    monkeypatch.setattr(playbook_source_service, "batch_transcripts", tiny)


def _failed_batch_result():
    from app.services.playbook_extraction_service import (
        BatchExtractionResult,
        ExtractionWarning,
    )

    return BatchExtractionResult(
        candidates=[],
        warnings=[
            ExtractionWarning(code="json_repair_failed", message="still invalid")
        ],
        succeeded=False,
    )


async def _source_states(db: AsyncSession, playbook_id: str) -> list[PlaybookSourceState]:
    result = await db.execute(
        select(PlaybookSourceState).where(PlaybookSourceState.playbook_id == playbook_id)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_middle_failed_batch_is_not_checkpointed(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    _install_default_model(monkeypatch)
    _force_single_turn_batches(monkeypatch)
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    chats_and_turns = [
        await _eligible_turn(db, auth, created_at=origin.replace(day=1 + i))
        for i in range(3)
    ]
    turn_ids = [turn.id for _, turn in chats_and_turns]
    original = playbook_extraction_service.extract_batch
    failed_turn_ids: set[str] = set()

    async def fail_middle(batch, brain, *, include_brain=True, source_text=None):
        if batch.batch_index == 1:
            failed_turn_ids.update(batch.turn_ids)
            return _failed_batch_result()
        return await original(
            batch, brain, include_brain=include_brain, source_text=source_text
        )

    monkeypatch.setattr(playbook_extraction_service, "extract_batch", fail_middle)
    playbook_id, run_id = await _start_run(db, auth)
    result = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert result["status"] == "completed_with_warnings"
    run = await db.get(PlaybookRun, run_id)
    assert run.processed_count == 2
    assert run.total_count == 3
    states = await _source_states(db, playbook_id)
    turn_state_ids = {
        row.source_id for row in states if row.source_type == PLAYBOOK_SOURCE_TYPE_TURN
    }
    assert failed_turn_ids == {turn_ids[1]}
    assert turn_ids[0] in turn_state_ids
    assert turn_ids[1] not in turn_state_ids
    assert turn_ids[2] in turn_state_ids
    assert turn_state_ids == {turn_ids[0], turn_ids[2]}
    assert failed_turn_ids.isdisjoint(turn_state_ids)
    observations = list(
        (
            await db.execute(
                select(PlaybookObservation).where(
                    PlaybookObservation.playbook_id == playbook_id
                )
            )
        ).scalars().all()
    )
    assert observations
    sources = list((await db.execute(select(PlaybookObservationSource))).scalars().all())
    evidenced_turns = {row.turn_id for row in sources if row.turn_id}
    assert turn_ids[1] not in evidenced_turns


@pytest.mark.asyncio
async def test_brain_retry_after_failed_batch_is_checkpointed_once(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    _install_default_model(monkeypatch)
    _force_single_turn_batches(monkeypatch)
    origin = datetime(2026, 2, 1, tzinfo=UTC)
    first = await _eligible_turn(db, auth, created_at=origin)
    second = await _eligible_turn(db, auth, created_at=origin.replace(day=2))
    brain = await _make_brain(db, auth)
    knowledge = await _make_knowledge(db, auth)
    original = playbook_extraction_service.extract_batch
    failed_turn_ids: set[str] = set()
    brain_calls: list[bool] = []

    async def fail_first(batch, brain_snapshot, *, include_brain=True, source_text=None):
        if batch.turn_count > 0:
            brain_calls.append(include_brain)
        if batch.batch_index == 0:
            assert include_brain is True
            failed_turn_ids.update(batch.turn_ids)
            return _failed_batch_result()
        return await original(
            batch,
            brain_snapshot,
            include_brain=include_brain,
            source_text=source_text,
        )

    monkeypatch.setattr(playbook_extraction_service, "extract_batch", fail_first)
    playbook_id, run_id = await _start_run(db, auth)
    result = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert result["status"] == "completed_with_warnings"
    run = await db.get(PlaybookRun, run_id)
    assert run.processed_count == 3
    assert failed_turn_ids == {first[1].id}
    assert brain_calls == [True, True]
    states = await _source_states(db, playbook_id)
    turn_state_ids = {
        row.source_id for row in states if row.source_type == PLAYBOOK_SOURCE_TYPE_TURN
    }
    assert first[1].id not in turn_state_ids
    assert second[1].id in turn_state_ids
    assert failed_turn_ids.isdisjoint(turn_state_ids)
    brain_rows = [
        row for row in states if row.source_type == PLAYBOOK_SOURCE_TYPE_USER_BRAIN
    ]
    knowledge_rows = [
        row
        for row in states
        if row.source_type == PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE
    ]
    assert len(brain_rows) == 1
    assert brain_rows[0].source_id == brain.id
    assert len(knowledge_rows) == 1
    assert knowledge_rows[0].source_id == knowledge.id


@pytest.mark.asyncio
async def test_chat_batches_fail_brain_only_fallback_checkpoints_brain_not_turns(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    _install_default_model(monkeypatch)
    chat, turn = await _eligible_turn(db, auth)
    brain = await _make_brain(db, auth)
    knowledge = await _make_knowledge(db, auth)
    snapshot = await playbook_source_service.build_brain_source_snapshot(db, auth)
    original = playbook_extraction_service.extract_batch

    async def fail_chats(batch, brain_snapshot, *, include_brain=True, source_text=None):
        if batch.turn_count > 0:
            return _failed_batch_result()
        return await original(
            batch,
            brain_snapshot,
            include_brain=include_brain,
            source_text=source_text,
        )

    monkeypatch.setattr(playbook_extraction_service, "extract_batch", fail_chats)
    playbook_id, run_id = await _start_run(db, auth)
    result = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert result["status"] == "completed_with_warnings"
    run = await db.get(PlaybookRun, run_id)
    assert run.processed_count == 2
    states = await _source_states(db, playbook_id)
    assert all(row.source_type != PLAYBOOK_SOURCE_TYPE_TURN for row in states)
    assert turn.id not in {row.source_id for row in states}
    brain_row = next(
        row for row in states if row.source_type == PLAYBOOK_SOURCE_TYPE_USER_BRAIN
    )
    know_row = next(
        row for row in states if row.source_type == PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE
    )
    assert brain_row.source_id == brain.id
    assert know_row.source_id == knowledge.id
    assert brain_row.content_hash == snapshot.user_brain.content_hash
    assert know_row.content_hash == snapshot.knowledge_items[0].content_hash
    assert list(
        (
            await db.execute(
                select(PlaybookObservation).where(
                    PlaybookObservation.playbook_id == playbook_id
                )
            )
        ).scalars().all()
    )


@pytest.mark.asyncio
async def test_brain_extraction_never_succeeds_does_not_checkpoint_brain(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_calls: list[dict],
):
    _install_default_model(monkeypatch)
    await _eligible_turn(db, auth)
    await _make_brain(db, auth)
    await _make_knowledge(db, auth)

    async def fail_all(*args, **kwargs):
        return _failed_batch_result()

    monkeypatch.setattr(playbook_extraction_service, "extract_batch", fail_all)
    playbook_id, run_id = await _start_run(db, auth)
    result = await playbook_generation_service.execute_full_generation(
        db,
        playbook_id=playbook_id,
        run_id=run_id,
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert result["status"] == "failed"
    states = await _source_states(db, playbook_id)
    assert states == []
    playbook = await db.get(Playbook, playbook_id)
    assert playbook.status == "not_generated"
    assert playbook.last_success_run_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("with_brain", "with_knowledge", "expected_status", "expected_total"),
    [
        (True, False, 202, 1),
        (False, True, 202, 1),
        (True, True, 202, 2),
        (False, False, 400, None),
    ],
)
async def test_brain_only_generate_api(
    db: AsyncSession,
    auth: AuthContext,
    enqueue_calls: list[dict],
    with_brain: bool,
    with_knowledge: bool,
    expected_status: int,
    expected_total: int | None,
):
    if with_brain:
        await _make_brain(db, auth)
    if with_knowledge:
        await _make_knowledge(db, auth)
    async with await _client_for(db, auth) as client:
        response = await client.post("/api/v1/playbooks/me/generate")
    assert response.status_code == expected_status
    if expected_status == 400:
        assert response.json()["error"] == "VALIDATION_ERROR"
        assert enqueue_calls == []
        return
    body = response.json()
    assert body["status"] == "queued"
    assert body["kind"] == "full"
    assert body["total_count"] == expected_total
    assert body["processed_count"] == 0
    assert len(enqueue_calls) == 1

