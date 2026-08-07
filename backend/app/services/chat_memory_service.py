"""Per-chat rolling continuation memory — separate from user-level Brain."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Chat, CostRecord, Turn, UsageKind, Verdict
from app.llm.catalog import get_model, resolve_llm_cost
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry

logger = get_logger(__name__)

RECENT_HISTORY_MAX_TURNS = 10
RECENT_HISTORY_MAX_CHARS = 30_000
DEFAULT_CHAT_MEMORY_MODEL = "gpt-4.1"

TURN_SEPARATOR = "\n\n---\n\n"
TRUNCATION_MARKER = "\n[...truncated...]"
CHALLENGE_TURN_MARKER = "__multimind_challenge_turn__"


class TurnHistoryEntry(NamedTuple):
    turn_id: str
    user_message: str
    verdict_text: str
    verdict_reason: str | None
    created_at: datetime | None


def format_turn_history_block(
    user_message: str,
    verdict_text: str,
    verdict_reason: str | None = None,
) -> str:
    parts = [
        f"User question:\n{(user_message or '').strip()}",
        f"Final verdict:\n{(verdict_text or '').strip()}",
    ]
    reason = (verdict_reason or "").strip()
    if reason:
        parts.append(f"Verdict rationale:\n{reason}")
    return "\n\n".join(part for part in parts if part.strip())


def select_recent_history_under_budget(
    blocks_oldest_first: list[str],
    *,
    max_chars: int = RECENT_HISTORY_MAX_CHARS,
) -> str:
    """Prefer newer turn blocks; never split a block mid-label ambiguously.

    ``blocks_oldest_first`` must already be chronological. When the budget is
    exceeded, whole older blocks are dropped. If even the newest block alone
    exceeds the budget, that single block is truncated with a clear marker.
    """
    if not blocks_oldest_first or max_chars <= 0:
        return ""

    selected: list[str] = []
    for block in reversed(blocks_oldest_first):
        trial = [block, *selected]
        sep_cost = len(TURN_SEPARATOR) * (len(trial) - 1)
        size = sum(len(b) for b in trial) + sep_cost
        if size <= max_chars:
            selected = trial
            continue
        if not selected:
            marker = TRUNCATION_MARKER
            room = max_chars - len(marker)
            if room <= 0:
                return marker.strip()
            return block[:room].rstrip() + marker
        break
    return TURN_SEPARATOR.join(selected)


def format_recent_conversation_context(
    entries_oldest_first: list[TurnHistoryEntry],
    *,
    max_chars: int = RECENT_HISTORY_MAX_CHARS,
) -> str | None:
    blocks = [
        format_turn_history_block(e.user_message, e.verdict_text, e.verdict_reason)
        for e in entries_oldest_first
    ]
    text = select_recent_history_under_budget(blocks, max_chars=max_chars)
    return text or None


def eligible_prior_turn_filters(
    chat_id: str,
    current_turn_id: str,
    current_turn_created_at: datetime | None,
):
    """Shared SQLAlchemy filter clauses for prior completed turns with verdicts."""
    filters = [
        Turn.chat_id == chat_id,
        Turn.id != current_turn_id,
        Turn.deleted_at.is_(None),
        (Turn.error_message.is_(None)) | (Turn.error_message != CHALLENGE_TURN_MARKER),
    ]
    if current_turn_created_at is not None:
        filters.append(Turn.created_at < current_turn_created_at)
    return filters


class ChatMemoryService:
    async def load_recent_history_entries(
        self,
        db: AsyncSession,
        chat_id: str,
        current_turn_id: str,
        current_turn_created_at: datetime | None,
        *,
        limit: int = RECENT_HISTORY_MAX_TURNS,
    ) -> list[TurnHistoryEntry]:
        filters = eligible_prior_turn_filters(chat_id, current_turn_id, current_turn_created_at)
        result = await db.execute(
            select(Turn, Verdict)
            .join(Verdict, Verdict.turn_id == Turn.id)
            .where(*filters)
            .order_by(Turn.created_at.desc())
            .limit(limit)
        )
        rows = list(result.all())
        rows.reverse()  # chronological oldest → newest
        entries: list[TurnHistoryEntry] = []
        for turn, verdict in rows:
            reason = (verdict.reason or "").strip() or None
            entries.append(
                TurnHistoryEntry(
                    turn_id=turn.id,
                    user_message=turn.user_message,
                    verdict_text=verdict.text,
                    verdict_reason=reason,
                    created_at=turn.created_at,
                )
            )
        return entries

    async def build_recent_conversation_context(
        self,
        db: AsyncSession,
        chat_id: str,
        current_turn_id: str,
        current_turn_created_at: datetime | None,
        *,
        limit: int = RECENT_HISTORY_MAX_TURNS,
        max_chars: int = RECENT_HISTORY_MAX_CHARS,
    ) -> str | None:
        entries = await self.load_recent_history_entries(
            db,
            chat_id,
            current_turn_id,
            current_turn_created_at,
            limit=limit,
        )
        context = format_recent_conversation_context(entries, max_chars=max_chars)
        logger.debug(
            "recent_conversation_context_lookup",
            chat_id=chat_id,
            current_turn_id=current_turn_id,
            recent_turn_count=len(entries),
            recent_context_chars=len(context or ""),
            recent_turn_ids=[e.turn_id for e in entries],
        )
        return context

    async def list_eligible_turns_oldest_first(
        self, db: AsyncSession, chat_id: str
    ) -> list[tuple[Turn, Verdict]]:
        result = await db.execute(
            select(Turn, Verdict)
            .join(Verdict, Verdict.turn_id == Turn.id)
            .where(
                Turn.chat_id == chat_id,
                Turn.deleted_at.is_(None),
                (Turn.error_message.is_(None)) | (Turn.error_message != CHALLENGE_TURN_MARKER),
            )
            .order_by(Turn.created_at.asc())
        )
        return list(result.all())

    def newly_expired_turns(
        self,
        eligible_oldest_first: list[tuple[Turn, Verdict]],
        *,
        through_turn_id: str | None,
        recent_window: int = RECENT_HISTORY_MAX_TURNS,
    ) -> list[tuple[Turn, Verdict]]:
        if len(eligible_oldest_first) <= recent_window:
            return []
        expired = eligible_oldest_first[:-recent_window]
        if not through_turn_id:
            return expired
        expired_ids = [turn.id for turn, _ in expired]
        if through_turn_id in expired_ids:
            return expired[expired_ids.index(through_turn_id) + 1 :]
        # Watermark may still be in the recent window (or deleted mid-chat).
        # Advance past any expired turns at-or-before the watermark's position
        # in the full eligible list.
        all_ids = [turn.id for turn, _ in eligible_oldest_first]
        if through_turn_id in all_ids:
            wm_index = all_ids.index(through_turn_id)
            return [(t, v) for t, v in expired if all_ids.index(t.id) > wm_index]
        return expired

    async def merge_expired_turns(
        self,
        db: AsyncSession,
        *,
        chat_id: str,
        org_id: str,
        project_id: str | None = None,
    ) -> int:
        """Merge newly expired turns into rolling memory. Returns merges performed."""
        chat = await db.get(Chat, chat_id)
        if chat is None:
            return 0

        eligible = await self.list_eligible_turns_oldest_first(db, chat_id)
        to_merge = self.newly_expired_turns(
            eligible, through_turn_id=chat.rolling_memory_through_turn_id
        )
        if not to_merge:
            return 0

        merged = 0
        for turn, verdict in to_merge:
            await self._merge_one_turn(
                db,
                chat=chat,
                turn=turn,
                verdict=verdict,
                org_id=org_id,
                project_id=project_id,
            )
            merged += 1
        return merged

    async def _merge_one_turn(
        self,
        db: AsyncSession,
        *,
        chat: Chat,
        turn: Turn,
        verdict: Verdict,
        org_id: str,
        project_id: str | None,
    ) -> None:
        # Idempotency guard: skip if watermark already at/past this turn.
        if chat.rolling_memory_through_turn_id == turn.id:
            return

        system = get_prompt_engine().chat_memory_update_prompt(
            current_rolling_memory=(chat.rolling_memory or "").strip(),
            user_message=turn.user_message,
            verdict_text=verdict.text,
            verdict_reason=(verdict.reason or "").strip() or None,
        )
        model = get_model(DEFAULT_CHAT_MEMORY_MODEL)
        provider = get_provider_registry().get_provider(model.provider)
        response = await provider.complete(
            system=system,
            user="Update the rolling chat memory text now.",
            model=model.provider_model,
            max_tokens=2048,
        )
        updated = (response.text or "").strip()
        if not updated:
            updated = (chat.rolling_memory or "").strip()

        chat.rolling_memory = updated or None
        chat.rolling_memory_through_turn_id = turn.id
        chat.rolling_memory_updated_at = datetime.now(UTC)

        cost_usd = resolve_llm_cost(
            DEFAULT_CHAT_MEMORY_MODEL,
            response.tokens_input,
            response.tokens_output,
            response.cost_usd,
        )
        db.add(
            CostRecord(
                org_id=org_id,
                chat_id=chat.id,
                project_id=project_id,
                turn_id=turn.id,
                model_id=DEFAULT_CHAT_MEMORY_MODEL,
                kind=UsageKind.CHAT_MEMORY,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                cost_usd=cost_usd,
            )
        )
        await db.flush()
        logger.info(
            "chat_rolling_memory_updated",
            chat_id=chat.id,
            through_turn_id=turn.id,
            rolling_memory_chars=len(chat.rolling_memory or ""),
        )


chat_memory_service = ChatMemoryService()
