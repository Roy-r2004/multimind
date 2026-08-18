"""Read-only, deterministic Playbook context retrieval for normal chat turns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.logging import get_logger
from app.db.models import Playbook, PlaybookObservation
from app.llm.prompt_engine import get_prompt_engine

logger = get_logger(__name__)

PLAYBOOK_CONTEXT_MAX_OBSERVATIONS = 8
PLAYBOOK_CONTEXT_DETAIL_MAX_CHARS = 6000
PLAYBOOK_CONTEXT_MIN_SCORE = 2.5

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "be", "change", "current", "do", "for", "how",
        "i", "in", "is", "it", "me", "my", "new", "of", "on", "or", "project",
        "should", "system", "the", "this", "to", "use", "user", "we", "what", "with",
        "work", "would",
    }
)
_STATUS_WEIGHT = {
    "active": 1.5,
    "confirmed": 1.5,
    "planned": 1.0,
    "completed": 0.75,
    "rejected": 0.25,
    "superseded": 0.0,
    "uncertain": -1.0,
}
_CATEGORY_CUES = {
    "architecture": frozenset({"architecture", "backend", "database", "design", "stack"}),
    "blocker": frozenset({"blocker", "blocked", "issue", "problem"}),
    "decision": frozenset({"choose", "decision", "decide", "selected"}),
    "next_step": frozenset({"next", "step"}),
    "priority": frozenset({"priority", "prioritize"}),
    "rejected_option": frozenset({"again", "retry", "revisit"}),
}


@dataclass(frozen=True)
class RankedPlaybookObservation:
    observation: PlaybookObservation
    score: float


def playbook_tokens(value: str | None) -> frozenset[str]:
    return frozenset(
        token for token in _TOKEN_RE.findall((value or "").lower())
        if token not in _STOPWORDS and len(token) > 1
    )


def score_playbook_observation(query: str, item: PlaybookObservation) -> float:
    query_tokens = playbook_tokens(query)
    if not query_tokens:
        return 0.0
    subject_tokens = playbook_tokens(item.subject)
    body_tokens = playbook_tokens(item.observation)
    subject_overlap = len(query_tokens & subject_tokens)
    body_overlap = len(query_tokens & body_tokens)
    if subject_overlap == 0 and body_overlap == 0:
        return 0.0

    score = subject_overlap * 6.0 + body_overlap * 2.0
    if subject_tokens and subject_tokens <= query_tokens:
        score += 12.0
    if subject_tokens:
        score += 4.0 * subject_overlap / len(subject_tokens)
    if query_tokens & _CATEGORY_CUES.get(item.category, frozenset()):
        score += 3.0
    score += _STATUS_WEIGHT.get(item.status, 0.0)
    score += max(0.0, min(float(item.confidence or 0.0), 1.0))
    score += min(max(item.evidence_count or 0, 0), 3) * 0.15
    return score


def rank_playbook_observations(
    query: str, observations: list[PlaybookObservation]
) -> list[RankedPlaybookObservation]:
    ranked = [
        RankedPlaybookObservation(item, score_playbook_observation(query, item))
        for item in observations
    ]
    eligible = [item for item in ranked if item.score >= PLAYBOOK_CONTEXT_MIN_SCORE]
    return sorted(
        eligible,
        key=lambda ranked_item: (
            -ranked_item.score,
            -(ranked_item.observation.confidence or 0.0),
            -_timestamp_value(
                ranked_item.observation.last_confirmed_at
                or ranked_item.observation.updated_at
            ),
            ranked_item.observation.id,
        ),
    )


def select_playbook_details(
    ranked: list[RankedPlaybookObservation],
    *,
    max_observations: int = PLAYBOOK_CONTEXT_MAX_OBSERVATIONS,
    max_chars: int = PLAYBOOK_CONTEXT_DETAIL_MAX_CHARS,
) -> tuple[list[PlaybookObservation], str]:
    selected: list[PlaybookObservation] = []
    lines: list[str] = []
    used = 0
    for ranked_item in ranked:
        if len(selected) >= max_observations:
            break
        item = ranked_item.observation
        subject = f" {item.subject}:" if (item.subject or "").strip() else ""
        line = f"- [{item.category.upper()} | {item.status.upper()}]{subject} {item.observation.strip()}"
        added = len(line) + (1 if lines else 0)
        if added > max_chars - used:
            continue
        selected.append(item)
        lines.append(line)
        used += added
    return selected, "\n".join(lines)


class PlaybookContextService:
    async def build_for_turn(
        self, db: AsyncSession, auth: AuthContext, *, query: str
    ) -> str | None:
        playbook = (
            await db.execute(
                select(Playbook).where(
                    Playbook.org_id == auth.org_id,
                    Playbook.user_id == auth.user.id,
                    Playbook.status == "active",
                    Playbook.injection_enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if playbook is None:
            return None

        observations = list(
            (
                await db.execute(
                    select(PlaybookObservation).where(
                        PlaybookObservation.playbook_id == playbook.id,
                        PlaybookObservation.user_excluded.is_(False),
                    )
                )
            ).scalars().all()
        )
        ranked = rank_playbook_observations(query, observations)
        selected, details = select_playbook_details(ranked)
        core_summary = (playbook.core_summary or "").strip()
        if not core_summary and not details:
            return None

        rendered = get_prompt_engine().render(
            "partials/user_playbook.j2",
            core_summary=core_summary,
            playbook_details=details,
        ).strip()
        logger.info(
            "playbook_context_built",
            playbook_id=playbook.id,
            playbook_version=playbook.playbook_version,
            selected_observation_count=len(selected),
            core_summary_chars=len(core_summary),
            detail_chars=len(details),
            injection_enabled=playbook.injection_enabled,
        )
        return rendered or None


def _timestamp_value(value: datetime | None) -> float:
    return value.timestamp() if value is not None else 0.0


playbook_context_service = PlaybookContextService()
