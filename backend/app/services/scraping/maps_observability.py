"""Structured logging + metrics for maps census observability."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def log_stage_start(run_id: str, stage: str, context: dict[str, Any] | None = None) -> None:
    """Log pipeline stage start."""
    extra = {"run_id": run_id, "stage": stage, "event": "stage_start", **(context or {})}
    logger.info(f"maps_census_stage_start stage={stage} run_id={run_id}", extra=extra)


def log_stage_end(
    run_id: str,
    stage: str,
    duration_ms: int,
    status: str = "success",
    context: dict[str, Any] | None = None,
) -> None:
    """Log pipeline stage completion."""
    extra = {
        "run_id": run_id,
        "stage": stage,
        "event": "stage_end",
        "duration_ms": duration_ms,
        "status": status,
        **(context or {}),
    }
    logger.info(f"maps_census_stage_end stage={stage} run_id={run_id} duration_ms={duration_ms} status={status}", extra=extra)


def log_cell_processing(
    run_id: str,
    cell_id: str,
    country_code: str,
    query_family: str,
    results_found: int,
    new_places: int,
    duration_ms: int,
    cost_usd: float | None = None,
    error: str | None = None,
) -> None:
    """Log cell execution."""
    extra = {
        "run_id": run_id,
        "cell_id": cell_id,
        "event": "cell_processed",
        "country_code": country_code,
        "query_family": query_family,
        "results_found": results_found,
        "new_places": new_places,
        "duration_ms": duration_ms,
        "cost_usd": cost_usd,
        "error": error,
    }
    status = "failed" if error else "success"
    logger.info(
        f"maps_census_cell_processed cell_id={cell_id} results={results_found} "
        f"new_places={new_places} duration_ms={duration_ms} status={status}",
        extra=extra,
    )


def log_llm_call(
    run_id: str,
    model: str,
    provider: str,
    purpose: str,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Log LLM API call."""
    extra = {
        "run_id": run_id,
        "event": "llm_call",
        "model": model,
        "provider": provider,
        "purpose": purpose,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "error": error,
    }
    level = logging.ERROR if error else logging.DEBUG
    logger.log(
        level,
        f"maps_census_llm_call model={model} purpose={purpose} cost={cost_usd} error={error}",
        extra=extra,
    )


def log_external_api_call(
    run_id: str,
    api: str,
    endpoint: str,
    status_code: int | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    retry_count: int = 0,
) -> None:
    """Log external API calls (Google Places, Sonar, etc.)."""
    extra = {
        "run_id": run_id,
        "event": "external_api_call",
        "api": api,
        "endpoint": endpoint,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "error": error,
        "retry_count": retry_count,
    }
    level = logging.WARNING if error else logging.DEBUG
    logger.log(
        level,
        f"maps_census_api_call api={api} endpoint={endpoint} status={status_code} error={error}",
        extra=extra,
    )


def log_circuit_breaker_event(
    run_id: str,
    provider: str,
    event: str,
    state: str,
    failures: int | None = None,
) -> None:
    """Log circuit breaker state changes."""
    extra = {
        "run_id": run_id,
        "event": "circuit_breaker",
        "provider": provider,
        "circuit_event": event,
        "state": state,
        "failures": failures,
    }
    logger.warning(f"maps_census_circuit_breaker provider={provider} event={event} state={state}", extra=extra)


def log_budget_exceeded(
    run_id: str,
    budget_type: str,
    spent: int,
    max_allowed: int,
    cell_id: str | None = None,
) -> None:
    """Log budget exceeded."""
    extra = {
        "run_id": run_id,
        "event": "budget_exceeded",
        "budget_type": budget_type,
        "spent": spent,
        "max_allowed": max_allowed,
        "cell_id": cell_id,
    }
    logger.error(
        f"maps_census_budget_exceeded budget_type={budget_type} spent={spent} max={max_allowed}",
        extra=extra,
    )


def log_recovery_attempt(
    run_id: str,
    recovery_type: str,
    from_state: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Log recovery/retry attempts."""
    extra = {
        "run_id": run_id,
        "event": "recovery_attempt",
        "recovery_type": recovery_type,
        "from_state": from_state,
        **(context or {}),
    }
    logger.info(f"maps_census_recovery_attempt type={recovery_type} from_state={from_state}", extra=extra)


@asynccontextmanager
async def timed_operation(run_id: str, operation: str, context: dict[str, Any] | None = None):
    """Context manager for timing operations and logging."""
    start_time = time.time()
    extra = {"run_id": run_id, "operation": operation, **(context or {})}
    logger.debug(f"maps_operation_start operation={operation} run_id={run_id}", extra=extra)
    try:
        yield
    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        extra["duration_ms"] = duration_ms
        extra["error"] = str(exc)
        logger.error(f"maps_operation_failed operation={operation} duration_ms={duration_ms} error={exc}", extra=extra)
        raise
    else:
        duration_ms = int((time.time() - start_time) * 1000)
        extra["duration_ms"] = duration_ms
        logger.debug(f"maps_operation_end operation={operation} duration_ms={duration_ms}", extra=extra)
