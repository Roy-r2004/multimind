"""Unified, idempotent AI usage / OpenRouter cost recording."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import CostRecord, CostRecordStatus, CostSource, UsageKind
from app.llm.catalog import resolve_llm_cost
from app.llm.providers import LLMResponse

logger = get_logger(__name__)

PROVIDER_OPENROUTER = "openrouter"


@dataclass(frozen=True)
class CostRecordInput:
    org_id: str
    model_id: str
    kind: UsageKind
    operation: str
    idempotency_key: str
    user_id: str | None = None
    chat_id: str | None = None
    project_id: str | None = None
    turn_id: str | None = None
    mission_id: str | None = None
    execution_id: str | None = None
    provider: str = PROVIDER_OPENROUTER
    status: CostRecordStatus = CostRecordStatus.SUCCEEDED
    request_id: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_reasoning: int | None = None
    tokens_cached_input: int | None = None
    reported_cost_usd: float | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    metadata: dict[str, Any] | None = None
    recorded_at: datetime | None = None


def request_id_from_llm_response(response: LLMResponse | None) -> str | None:
    if response is None or not response.raw:
        return None
    raw_id = response.raw.get("id")
    return str(raw_id) if raw_id else None


def usage_extras_from_llm_response(
    response: LLMResponse | None,
) -> tuple[int | None, int | None]:
    """Return (tokens_reasoning, tokens_cached_input) when OpenRouter provides them."""
    if response is None or not response.raw:
        return None, None
    usage = response.raw.get("usage") or {}
    reasoning = usage.get("reasoning_tokens")
    if reasoning is None:
        details = usage.get("completion_tokens_details") or {}
        reasoning = details.get("reasoning_tokens")
    cached = usage.get("cached_tokens")
    if cached is None:
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached = prompt_details.get("cached_tokens")
    return (
        int(reasoning) if reasoning is not None else None,
        int(cached) if cached is not None else None,
    )


def resolve_cost_with_source(
    model_id: str,
    tokens_input: int,
    tokens_output: int,
    reported_cost_usd: float | None,
) -> tuple[float, CostSource]:
    if reported_cost_usd is not None:
        return float(reported_cost_usd), CostSource.REPORTED
    calculated = resolve_llm_cost(model_id, tokens_input, tokens_output, None)
    return float(calculated or 0.0), CostSource.CALCULATED


def sanitize_cost_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip any accidental prompt/response content from cost metadata."""
    if not metadata:
        return None
    blocked = {
        "prompt",
        "system",
        "user",
        "messages",
        "text",
        "content",
        "response",
        "verdict",
        "raw",
        "body",
        "scraped_text",
        "html",
    }
    cleaned = {
        str(k): v
        for k, v in metadata.items()
        if str(k).lower() not in blocked and not str(k).lower().endswith("_text")
    }
    return cleaned or None


class CostRecorder:
    """Persist CostRecord rows without crashing primary AI flows."""

    async def record(self, db: AsyncSession, payload: CostRecordInput) -> CostRecord | None:
        cost_usd, cost_source = resolve_cost_with_source(
            payload.model_id,
            payload.tokens_input,
            payload.tokens_output,
            payload.reported_cost_usd,
        )
        if payload.status == CostRecordStatus.FAILED and payload.reported_cost_usd is None:
            cost_usd = 0.0
            cost_source = CostSource.UNKNOWN

        record = CostRecord(
            org_id=payload.org_id,
            user_id=payload.user_id,
            chat_id=payload.chat_id,
            project_id=payload.project_id,
            turn_id=payload.turn_id,
            mission_id=payload.mission_id,
            execution_id=payload.execution_id,
            model_id=payload.model_id,
            kind=payload.kind,
            provider=payload.provider,
            operation=payload.operation,
            status=(
                payload.status.value
                if isinstance(payload.status, CostRecordStatus)
                else payload.status
            ),
            request_id=payload.request_id,
            idempotency_key=payload.idempotency_key[:191],
            tokens_input=int(payload.tokens_input or 0),
            tokens_output=int(payload.tokens_output or 0),
            tokens_reasoning=payload.tokens_reasoning,
            tokens_cached_input=payload.tokens_cached_input,
            cost_usd=cost_usd,
            cost_source=cost_source.value,
            latency_ms=payload.latency_ms,
            error_code=payload.error_code,
            metadata_=sanitize_cost_metadata(payload.metadata),
            recorded_at=payload.recorded_at or datetime.now(UTC),
        )
        try:
            # Savepoint so a duplicate/failed cost insert never rolls back the
            # surrounding AI transaction (answer/verdict/lesson persistence).
            async with db.begin_nested():
                db.add(record)
                await db.flush()
            return record
        except IntegrityError:
            logger.info(
                "cost_record_duplicate",
                idempotency_key=payload.idempotency_key,
                operation=payload.operation,
            )
            return None
        except Exception as exc:  # noqa: BLE001 — never fail the primary AI path
            logger.warning(
                "cost_record_failed",
                operation=payload.operation,
                error=str(exc),
            )
            return None

    async def record_llm_success(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        model_id: str,
        kind: UsageKind,
        operation: str,
        idempotency_key: str,
        response: LLMResponse,
        user_id: str | None = None,
        chat_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
        mission_id: str | None = None,
        execution_id: str | None = None,
        latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CostRecord | None:
        reasoning, cached = usage_extras_from_llm_response(response)
        return await self.record(
            db,
            CostRecordInput(
                org_id=org_id,
                user_id=user_id,
                chat_id=chat_id,
                project_id=project_id,
                turn_id=turn_id,
                mission_id=mission_id,
                execution_id=execution_id,
                model_id=model_id,
                kind=kind,
                operation=operation,
                idempotency_key=idempotency_key,
                request_id=request_id_from_llm_response(response),
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                tokens_reasoning=reasoning,
                tokens_cached_input=cached,
                reported_cost_usd=response.cost_usd,
                latency_ms=latency_ms,
                status=CostRecordStatus.SUCCEEDED,
                metadata=metadata,
            ),
        )

    async def record_llm_failure(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        model_id: str,
        kind: UsageKind,
        operation: str,
        idempotency_key: str,
        user_id: str | None = None,
        chat_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
        mission_id: str | None = None,
        execution_id: str | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CostRecord | None:
        return await self.record(
            db,
            CostRecordInput(
                org_id=org_id,
                user_id=user_id,
                chat_id=chat_id,
                project_id=project_id,
                turn_id=turn_id,
                mission_id=mission_id,
                execution_id=execution_id,
                model_id=model_id,
                kind=kind,
                operation=operation,
                idempotency_key=idempotency_key,
                status=CostRecordStatus.FAILED,
                error_code=(error_code or "llm_error")[:64],
                latency_ms=latency_ms,
                reported_cost_usd=None,
                metadata=metadata,
            ),
        )


cost_recorder = CostRecorder()
