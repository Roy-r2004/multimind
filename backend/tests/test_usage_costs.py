"""Focused tests for unified cost recorder and user usage scoping."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.dependencies import AuthContext
from app.db.models import (
    Chat,
    CostRecord,
    CostRecordStatus,
    CostSource,
    OrgMembership,
    OrgRole,
    UsageKind,
    User,
)
from app.llm.providers import LLMResponse
from app.services.cost_recorder import (
    CostRecordInput,
    cost_recorder,
    resolve_cost_with_source,
    sanitize_cost_metadata,
)
from app.services.usage_service import usage_service


@pytest.mark.asyncio
async def test_reported_cost_preferred_and_sanitized_metadata(db, auth: AuthContext):
    response = LLMResponse(
        text="hello",
        tokens_input=10,
        tokens_output=5,
        cost_usd=0.0123,
        raw={"id": "gen-1", "usage": {"prompt_tokens": 10}},
    )
    record = await cost_recorder.record_llm_success(
        db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        model_id="gpt-4.1",
        kind=UsageKind.HELPER,
        operation="prompt_improve",
        idempotency_key="test:reported:1",
        response=response,
        metadata={"prompt": "SECRET", "chunk_id": "c1"},
    )
    await db.commit()
    assert record is not None
    assert record.cost_usd == pytest.approx(0.0123)
    assert record.cost_source == CostSource.REPORTED.value
    assert record.request_id == "gen-1"
    assert record.metadata_ is not None
    assert "prompt" not in record.metadata_
    assert record.metadata_.get("chunk_id") == "c1"


@pytest.mark.asyncio
async def test_idempotent_duplicate_does_not_create_second_row(db, auth: AuthContext):
    payload = CostRecordInput(
        org_id=auth.org_id,
        user_id=auth.user.id,
        model_id="gpt-4.1",
        kind=UsageKind.ANSWER,
        operation="council_answer",
        idempotency_key="test:dup:answer",
        tokens_input=1,
        tokens_output=1,
        reported_cost_usd=0.01,
    )
    first = await cost_recorder.record(db, payload)
    await db.commit()
    second = await cost_recorder.record(db, payload)
    await db.commit()
    assert first is not None
    assert second is None
    rows = (
        await db.execute(
            select(CostRecord).where(CostRecord.idempotency_key == "test:dup:answer")
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_failed_call_records_zero_unknown_cost(db, auth: AuthContext):
    record = await cost_recorder.record_llm_failure(
        db,
        org_id=auth.org_id,
        user_id=auth.user.id,
        model_id="gpt-4.1",
        kind=UsageKind.ANSWER,
        operation="council_answer",
        idempotency_key="test:failed:1",
        error_code="MODEL_ANSWER_FAILED",
    )
    await db.commit()
    assert record is not None
    assert record.status == CostRecordStatus.FAILED.value
    assert record.cost_usd == 0.0
    assert record.cost_source == CostSource.UNKNOWN.value


@pytest.mark.asyncio
async def test_user_summary_excludes_other_users_and_unattributed(db, auth: AuthContext):
    other = User(email="other@example.com", hashed_password="x", full_name="Other")
    db.add(other)
    await db.flush()
    db.add(OrgMembership(org_id=auth.org_id, user_id=other.id, role=OrgRole.MEMBER))

    chat = Chat(
        org_id=auth.org_id,
        created_by=auth.user.id,
        title="Mine",
    )
    db.add(chat)
    await db.flush()

    db.add_all(
        [
            CostRecord(
                org_id=auth.org_id,
                user_id=auth.user.id,
                chat_id=chat.id,
                model_id="gpt-4.1",
                kind=UsageKind.ANSWER,
                operation="council_answer",
                status="succeeded",
                cost_usd=1.5,
                tokens_input=10,
                tokens_output=10,
                idempotency_key="hist:user:1",
                cost_source="unknown",
                provider="openrouter",
            ),
            CostRecord(
                org_id=auth.org_id,
                user_id=other.id,
                model_id="gpt-4.1",
                kind=UsageKind.ANSWER,
                operation="council_answer",
                status="succeeded",
                cost_usd=9.0,
                tokens_input=10,
                tokens_output=10,
                idempotency_key="hist:other:1",
                cost_source="unknown",
                provider="openrouter",
            ),
            CostRecord(
                org_id=auth.org_id,
                user_id=None,
                model_id="gpt-4.1",
                kind=UsageKind.EXTRACTION,
                operation="facility_extract",
                status="succeeded",
                cost_usd=3.0,
                tokens_input=10,
                tokens_output=10,
                idempotency_key="hist:unattr:1",
                cost_source="unknown",
                provider="openrouter",
            ),
        ]
    )
    await db.commit()

    summary = await usage_service.user_summary(db, auth)
    assert summary["all_time_usd"] == pytest.approx(1.5)
    assert summary["tracked_calls"] == 1
    assert summary["historical_notice"] is True

    # New post-migration-style row continues the same total (does not reset / double-count).
    await cost_recorder.record(
        db,
        CostRecordInput(
            org_id=auth.org_id,
            user_id=auth.user.id,
            chat_id=chat.id,
            model_id="gpt-4.1",
            kind=UsageKind.VERDICT,
            operation="verdict",
            idempotency_key="new:verdict:1",
            tokens_input=5,
            tokens_output=5,
            reported_cost_usd=0.25,
        ),
    )
    await db.commit()
    again = await usage_service.user_summary(db, auth)
    assert again["all_time_usd"] == pytest.approx(1.75)
    assert again["all_time_tokens"] == 30
    refreshed = await usage_service.user_summary(db, auth)
    assert refreshed["all_time_usd"] == again["all_time_usd"]

    extras = await usage_service.org_extras(db, auth)
    assert extras["all_time_usd"] == pytest.approx(13.75)


def test_resolve_cost_and_sanitize_helpers():
    reported, source = resolve_cost_with_source("gpt-4.1", 10, 10, 0.5)
    assert reported == 0.5
    assert source == CostSource.REPORTED
    calc, source2 = resolve_cost_with_source("gpt-4.1", 1000, 1000, None)
    assert source2 == CostSource.CALCULATED
    assert calc >= 0
    cleaned = sanitize_cost_metadata({"prompt": "x", "mission_ref": "m1"})
    assert cleaned == {"mission_ref": "m1"}
