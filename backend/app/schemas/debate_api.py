"""Response contracts for the Debate Mode API (Phase 4).

Separate from app.schemas.api so Debate Mode's wire format can evolve
without touching any existing Council/Verdict/Challenge response schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DebateChallengeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    author_model_id: str
    status: str
    challenges: list[dict[str, Any]] | None
    error_message: str | None
    tokens_input: int | None
    tokens_output: int | None
    cost_usd: float | None
    created_at: datetime


class DebateResponseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    author_model_id: str
    status: str
    responses: list[dict[str, Any]] | None
    error_message: str | None
    tokens_input: int | None
    tokens_output: int | None
    cost_usd: float | None
    created_at: datetime


class DebateUsageSummary(BaseModel):
    """Aggregated across every challenge/response call in this session. A
    call that failed before the provider ever responded contributes nothing
    here (unknown, not zero) — see DebateChallenge/DebateResponse docstrings
    in app.db.models for why those columns are nullable."""

    model_config = ConfigDict(extra="forbid")

    total_tokens_input: int
    total_tokens_output: int
    total_cost_usd: float
    calls_with_unknown_usage: int


class DebateSessionResponse(BaseModel):
    """Full state of one debate session — returned by both the start and
    get endpoints so a client never has to guess what shape a 200 carries."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    turn_id: str
    status: str
    model_ids: list[str]
    agreements: list[dict[str, Any]] | None
    unchallenged_model_ids: list[str] | None
    unresolved_disagreements: list[dict[str, Any]] | None
    corrections: list[dict[str, Any]] | None
    risks: list[dict[str, Any]] | None
    uncertainties: list[dict[str, Any]] | None
    final_summary: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    challenges: list[DebateChallengeResponse]
    responses: list[DebateResponseResponse]
    usage: DebateUsageSummary


class DebateStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: DebateSessionResponse
    freshly_started: bool
