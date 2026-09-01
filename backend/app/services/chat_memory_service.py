"""Per-chat rolling continuation memory — separate from user-level Brain."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.models import (
    Chat,
    CostRecord,
    ModelAnswerStatus,
    Turn,
    TurnStatus,
    UsageKind,
)
from app.llm.catalog import estimate_tokens, get_model, resolve_llm_cost
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry

logger = get_logger(__name__)

RECENT_HISTORY_MAX_TURNS = 10
RECENT_HISTORY_MAX_CHARS = 30_000
RECENT_HISTORY_MAX_TOKENS = 7_500
DEFAULT_CHAT_MEMORY_MODEL = "gpt-4.1"
CHAT_MEMORY_EXISTING_MAX_TOKENS = 4_000
CHAT_MEMORY_USER_MAX_TOKENS = 3_000
CHAT_MEMORY_ASSISTANT_MAX_TOKENS = 9_000
CHAT_MEMORY_REASON_MAX_TOKENS = 2_000
CHAT_MEMORY_STORED_MAX_TOKENS = 2_048
CHAT_MEMORY_MAX_CAS_RETRIES = 4

# One-time cross-chat continuation handoff (Chat A → first turn of Chat B).
CONTINUATION_HANDOFF_MAX_CHARS = 14_000
CONTINUATION_HANDOFF_MAX_RECENT_TURNS = 4
CONTINUATION_HANDOFF_ROLLING_MEMORY_MAX_CHARS = 8_000
CONTINUATION_HANDOFF_HEADER = "## MultiMind Continuation Handoff"
CONTINUATION_SEED_PREFIX = "Continuation context inherited from a previous chat:"

TURN_SEPARATOR = "\n\n---\n\n"
TRUNCATION_MARKER = "\n[...truncated...]"
CHALLENGE_TURN_MARKER = "__multimind_challenge_turn__"


class TurnHistoryEntry(NamedTuple):
    turn_id: str
    user_message: str
    verdict_text: str
    verdict_reason: str | None
    created_at: datetime | None
    is_verdict: bool = True


class TurnAssistantResult(NamedTuple):
    text: str
    reason: str | None
    is_verdict: bool


class HistoryPartition(NamedTuple):
    compactable: list[tuple[Turn, TurnAssistantResult]]
    recent: list[tuple[Turn, TurnAssistantResult]]


def format_turn_history_block(
    user_message: str,
    verdict_text: str,
    verdict_reason: str | None = None,
    *,
    is_verdict: bool = True,
) -> str:
    result_label = "Final verdict" if is_verdict else "Assistant answer"
    parts = [
        f"User question:\n{(user_message or '').strip()}",
        f"{result_label}:\n{(verdict_text or '').strip()}",
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
    blocks = []
    for entry in entries_oldest_first:
        block = format_turn_history_block(
            entry.user_message,
            entry.verdict_text,
            entry.verdict_reason,
            is_verdict=entry.is_verdict,
        )
        if len(block) > max_chars:
            block = format_oversized_turn_history_block(entry, max_chars=max_chars)
        blocks.append(block)
    # Selection is authoritative upstream. In particular, uncompacted fallback
    # turns must never be dropped merely to satisfy a second rendering budget.
    text = TURN_SEPARATOR.join(blocks)
    return text or None


def format_oversized_turn_history_block(entry: TurnHistoryEntry, *, max_chars: int) -> str:
    """Represent both sides of one oversized turn with deterministic budgets."""
    if max_chars <= 0:
        return ""
    result_label = "Final verdict" if entry.is_verdict else "Assistant answer"
    user_label = "User question:\n"
    answer_label = f"\n\n{result_label}:\n"
    reason_label = "\n\nVerdict rationale:\n"
    include_reason = bool((entry.verdict_reason or "").strip())
    overhead = len(user_label) + len(answer_label) + (len(reason_label) if include_reason else 0)
    usable = max(0, max_chars - overhead)
    reason_budget = usable * 15 // 100 if include_reason else 0
    user_budget = (usable - reason_budget) * 40 // 85
    answer_budget = usable - reason_budget - user_budget
    rendered = (
        user_label
        + truncate_text_under_budget(entry.user_message, user_budget)
        + answer_label
        + truncate_text_under_budget(entry.verdict_text, answer_budget)
    )
    if include_reason:
        rendered += reason_label + truncate_text_under_budget(
            entry.verdict_reason or "", reason_budget
        )
    return rendered[:max_chars]


def truncate_text_under_budget(text: str, max_chars: int) -> str:
    """Truncate plain text with a clear marker when over budget."""
    cleaned = (text or "").strip()
    if max_chars <= 0 or not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    marker = TRUNCATION_MARKER
    room = max_chars - len(marker)
    if room <= 0:
        return marker.strip()
    return cleaned[:room].rstrip() + marker


def truncate_text_under_token_budget(text: str, max_tokens: int) -> str:
    """Bound text using the repository's deterministic chars/4 estimator."""
    cleaned = (text or "").strip()
    if not cleaned or max_tokens <= 0:
        return ""
    if estimate_tokens(cleaned) <= max_tokens:
        return cleaned
    return truncate_text_under_budget(cleaned, max_tokens * 4)


def partition_history_by_recent_budget(
    eligible_oldest_first: list[tuple[Turn, TurnAssistantResult]],
    *,
    max_tokens: int = RECENT_HISTORY_MAX_TOKENS,
    max_turns: int = RECENT_HISTORY_MAX_TURNS,
) -> HistoryPartition:
    """Return the one authoritative compact/raw boundary."""
    if not eligible_oldest_first:
        return HistoryPartition([], [])

    selected: list[tuple[Turn, TurnAssistantResult]] = []
    selected_blocks: list[str] = []
    for pair in reversed(eligible_oldest_first):
        turn, assistant_result = pair
        block = format_turn_history_block(
            turn.user_message,
            assistant_result.text,
            assistant_result.reason,
            is_verdict=assistant_result.is_verdict,
        )
        trial_blocks = [block, *selected_blocks]
        within_turn_guard = max_turns <= 0 or len(trial_blocks) <= max_turns
        within_token_budget = estimate_tokens(TURN_SEPARATOR.join(trial_blocks)) <= max_tokens
        if not selected or (within_turn_guard and within_token_budget):
            selected.insert(0, pair)
            selected_blocks = trial_blocks
            continue
        break

    compact_count = len(eligible_oldest_first) - len(selected)
    return HistoryPartition(eligible_oldest_first[:compact_count], selected)


def build_continuation_handoff_text(
    *,
    source_title: str,
    rolling_memory: str | None,
    recent_entries_oldest_first: list[TurnHistoryEntry],
    max_chars: int = CONTINUATION_HANDOFF_MAX_CHARS,
    rolling_memory_max_chars: int = CONTINUATION_HANDOFF_ROLLING_MEMORY_MAX_CHARS,
) -> str:
    """Build a compact, bounded continuation handoff from Chat A context."""
    title = (source_title or "Untitled chat").strip() or "Untitled chat"
    framing = (
        f"The user is continuing a previous conversation from chat '{title}'. "
        "Treat the following as prior conversation context."
    )
    header = f"{CONTINUATION_HANDOFF_HEADER}\n{framing}"

    parts: list[str] = [header]
    used = len(header)

    def remaining() -> int:
        return max_chars - used

    memory = truncate_text_under_budget(
        (rolling_memory or "").strip(),
        min(rolling_memory_max_chars, max(0, remaining() - 64)),
    )
    if memory:
        section = f"### Older memory from previous chat\n{memory}"
        separator = "\n\n"
        if used + len(separator) + len(section) <= max_chars:
            parts.append(section)
            used += len(separator) + len(section)

    recent_budget = remaining() - 2  # account for upcoming \n\n
    if recent_budget > 64 and recent_entries_oldest_first:
        blocks = [
            format_turn_history_block(
                e.user_message,
                e.verdict_text,
                e.verdict_reason,
                is_verdict=e.is_verdict,
            )
            for e in recent_entries_oldest_first
        ]
        recent_text = select_recent_history_under_budget(blocks, max_chars=recent_budget)
        if recent_text:
            section_header = "### Recent turns from previous chat\n"
            # Re-budget after section header so total stays under max_chars.
            body_budget = recent_budget - len(section_header)
            if body_budget > 0:
                recent_text = select_recent_history_under_budget(blocks, max_chars=body_budget)
            if recent_text:
                section = f"{section_header}{recent_text}"
                separator = "\n\n"
                if used + len(separator) + len(section) <= max_chars:
                    parts.append(section)
                    used += len(separator) + len(section)

    return "\n\n".join(parts).strip()


def extract_continuation_handoff(custom_instructions: str | None) -> str | None:
    """Extract the continuation handoff block from stored turn custom_instructions."""
    text = (custom_instructions or "").strip()
    if not text:
        return None
    start = text.find(CONTINUATION_HANDOFF_HEADER)
    if start < 0:
        return None
    rest = text[start:]
    for stop in ("\n\nAttached file:", "\n\nIMAGE CONTEXT"):
        idx = rest.find(stop)
        if idx > 0:
            rest = rest[:idx]
            break
    handoff = rest.strip()
    return handoff or None


def format_continuation_seed_memory(handoff: str) -> str:
    """Frame handoff text for a one-time Chat B rolling_memory seed."""
    body = (handoff or "").strip()
    if body.startswith(CONTINUATION_HANDOFF_HEADER):
        body = body[len(CONTINUATION_HANDOFF_HEADER) :].lstrip("\n").strip()
    if not body:
        return ""
    seeded = f"{CONTINUATION_SEED_PREFIX}\n\n{body}".strip()
    return truncate_text_under_budget(seeded, CONTINUATION_HANDOFF_MAX_CHARS)


def eligible_prior_turn_filters(
    chat_id: str,
    current_turn_id: str,
    current_turn_created_at: datetime | None,
):
    """Shared SQLAlchemy filters for prior candidate conversation turns."""
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
    @staticmethod
    def _assistant_result(turn: Turn) -> TurnAssistantResult | None:
        """Resolve an intentional final assistant output without fabricating a verdict."""
        if turn.status not in (TurnStatus.COMPLETED, TurnStatus.PARTIAL):
            return None
        if turn.verdict is not None and (turn.verdict.text or "").strip():
            return TurnAssistantResult(
                text=turn.verdict.text,
                reason=(turn.verdict.reason or "").strip() or None,
                is_verdict=True,
            )
        answers = list(turn.model_answers or [])
        if len(answers) != 1:
            return None
        answer = answers[0]
        text = (answer.text or "").strip()
        if answer.status != ModelAnswerStatus.COMPLETED or not text:
            return None
        return TurnAssistantResult(text=text, reason=None, is_verdict=False)

    async def load_recent_history_entries(
        self,
        db: AsyncSession,
        chat_id: str,
        current_turn_id: str,
        current_turn_created_at: datetime | None,
        *,
        limit: int = RECENT_HISTORY_MAX_TURNS,
        max_tokens: int = RECENT_HISTORY_MAX_TOKENS,
    ) -> list[TurnHistoryEntry]:
        filters = eligible_prior_turn_filters(chat_id, current_turn_id, current_turn_created_at)
        result = await db.execute(
            select(Turn)
            .where(*filters)
            .options(selectinload(Turn.verdict), selectinload(Turn.model_answers))
            .order_by(Turn.created_at.desc())
        )
        rows: list[tuple[Turn, TurnAssistantResult]] = []
        for turn in result.scalars().all():
            assistant_result = self._assistant_result(turn)
            if assistant_result is not None:
                rows.append((turn, assistant_result))
        rows.reverse()  # chronological oldest → newest
        partition = partition_history_by_recent_budget(rows, max_tokens=max_tokens, max_turns=limit)
        chat = await db.get(Chat, chat_id)
        persisted_through_index = -1
        if chat is not None and (chat.rolling_memory or "").strip():
            ids = [turn.id for turn, _ in rows]
            if chat.rolling_memory_through_turn_id in ids:
                persisted_through_index = ids.index(chat.rolling_memory_through_turn_id)

        # The desired compact boundary does not authorize omission. Only an
        # actual non-empty persisted memory plus its watermark does.
        row_indexes = {turn.id: index for index, (turn, _) in enumerate(rows)}
        unpersisted_ids = {
            turn.id
            for turn, _ in partition.compactable
            if row_indexes[turn.id] > persisted_through_index
        }
        recent_ids = {
            turn.id
            for turn, _ in partition.recent
            if row_indexes[turn.id] > persisted_through_index
        }
        prompt_ids = unpersisted_ids | recent_ids
        prompt_rows = [pair for pair in rows if pair[0].id in prompt_ids]
        entries: list[TurnHistoryEntry] = []
        for turn, assistant_result in prompt_rows:
            entries.append(
                TurnHistoryEntry(
                    turn_id=turn.id,
                    user_message=turn.user_message,
                    verdict_text=assistant_result.text,
                    verdict_reason=assistant_result.reason,
                    created_at=turn.created_at,
                    is_verdict=assistant_result.is_verdict,
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
        max_tokens: int = RECENT_HISTORY_MAX_TOKENS,
        max_chars: int | None = None,
    ) -> str | None:
        effective_tokens = (
            max(1, estimate_tokens("x" * max_chars)) if max_chars is not None else max_tokens
        )
        entries = await self.load_recent_history_entries(
            db,
            chat_id,
            current_turn_id,
            current_turn_created_at,
            limit=limit,
            max_tokens=effective_tokens,
        )
        context = format_recent_conversation_context(
            entries,
            max_chars=max_chars if max_chars is not None else effective_tokens * 4,
        )
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
    ) -> list[tuple[Turn, TurnAssistantResult]]:
        result = await db.execute(
            select(Turn)
            .where(
                Turn.chat_id == chat_id,
                Turn.deleted_at.is_(None),
                (Turn.error_message.is_(None)) | (Turn.error_message != CHALLENGE_TURN_MARKER),
            )
            .options(selectinload(Turn.verdict), selectinload(Turn.model_answers))
            .order_by(Turn.created_at.asc())
        )
        eligible: list[tuple[Turn, TurnAssistantResult]] = []
        for turn in result.scalars().all():
            assistant_result = self._assistant_result(turn)
            if assistant_result is not None:
                eligible.append((turn, assistant_result))
        return eligible

    def newly_expired_turns(
        self,
        eligible_oldest_first: list[tuple[Turn, TurnAssistantResult]],
        *,
        through_turn_id: str | None,
        recent_window: int = RECENT_HISTORY_MAX_TURNS,
        recent_token_budget: int = RECENT_HISTORY_MAX_TOKENS,
    ) -> list[tuple[Turn, TurnAssistantResult]]:
        expired = partition_history_by_recent_budget(
            eligible_oldest_first,
            max_tokens=recent_token_budget,
            max_turns=recent_window,
        ).compactable
        if not expired:
            return []
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
        merged = 0
        stale_retries = 0
        while stale_retries < CHAT_MEMORY_MAX_CAS_RETRIES:
            await db.rollback()
            chat = await db.get(Chat, chat_id, populate_existing=True)
            if chat is None:
                return merged
            eligible = await self.list_eligible_turns_oldest_first(db, chat_id)
            to_merge = self.newly_expired_turns(
                eligible, through_turn_id=chat.rolling_memory_through_turn_id
            )
            if not to_merge:
                return merged
            turn, assistant_result = to_merge[0]
            outcome = await self._merge_one_turn(
                db,
                chat=chat,
                turn=turn,
                assistant_result=assistant_result,
                org_id=org_id,
                project_id=project_id,
            )
            if outcome is True:
                await db.commit()
                merged += 1
                stale_retries = 0
                continue
            if outcome is False:
                return merged
            stale_retries += 1
        return merged

    async def invalidate_memory(self, db: AsyncSession, *, chat_id: str) -> bool:
        """Make stale memory unavailable and advance its compare-and-swap stamp."""
        result = await db.execute(
            update(Chat)
            .where(Chat.id == chat_id)
            .values(
                rolling_memory=None,
                rolling_memory_through_turn_id=None,
                rolling_memory_updated_at=datetime.now(UTC),
            )
        )
        return result.rowcount == 1

    async def rebuild_memory_best_effort(
        self,
        db: AsyncSession,
        *,
        chat_id: str,
        org_id: str,
        project_id: str | None = None,
    ) -> int:
        """Rebuild from surviving compactable turns after committed invalidation."""
        try:
            return await self.merge_expired_turns(
                db, chat_id=chat_id, org_id=org_id, project_id=project_id
            )
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.warning("chat_rolling_memory_rebuild_failed", chat_id=chat_id, error=str(exc))
            return 0

    async def _merge_one_turn(
        self,
        db: AsyncSession,
        *,
        chat: Chat,
        turn: Turn,
        assistant_result: TurnAssistantResult,
        org_id: str,
        project_id: str | None,
    ) -> bool | None:
        # Idempotency guard: skip if watermark already at/past this turn.
        if chat.rolling_memory_through_turn_id == turn.id:
            return False

        chat_id = chat.id
        expected_memory = chat.rolling_memory
        expected_through = chat.rolling_memory_through_turn_id
        expected_updated_at = chat.rolling_memory_updated_at

        system = get_prompt_engine().chat_memory_update_prompt(
            current_rolling_memory=truncate_text_under_token_budget(
                (expected_memory or "").strip(), CHAT_MEMORY_EXISTING_MAX_TOKENS
            ),
            user_message=truncate_text_under_token_budget(
                turn.user_message, CHAT_MEMORY_USER_MAX_TOKENS
            ),
            verdict_text=truncate_text_under_token_budget(
                assistant_result.text, CHAT_MEMORY_ASSISTANT_MAX_TOKENS
            ),
            verdict_reason=truncate_text_under_token_budget(
                assistant_result.reason or "", CHAT_MEMORY_REASON_MAX_TOKENS
            ),
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
            logger.warning("chat_rolling_memory_empty_output", chat_id=chat_id, turn_id=turn.id)
            return False
        if estimate_tokens(updated) > CHAT_MEMORY_STORED_MAX_TOKENS:
            logger.warning(
                "chat_rolling_memory_oversized_output",
                chat_id=chat_id,
                turn_id=turn.id,
                estimated_tokens=estimate_tokens(updated),
            )
            return False

        now = datetime.now(UTC)
        statement = update(Chat).where(Chat.id == chat_id)
        statement = statement.where(
            Chat.rolling_memory.is_(None)
            if expected_memory is None
            else Chat.rolling_memory == expected_memory,
            Chat.rolling_memory_through_turn_id.is_(None)
            if expected_through is None
            else Chat.rolling_memory_through_turn_id == expected_through,
            Chat.rolling_memory_updated_at.is_(None)
            if expected_updated_at is None
            else Chat.rolling_memory_updated_at == expected_updated_at,
        )
        claimed = await db.execute(
            statement.values(
                rolling_memory=updated,
                rolling_memory_through_turn_id=turn.id,
                rolling_memory_updated_at=now,
            )
        )
        if claimed.rowcount != 1:
            await db.rollback()
            logger.info("chat_rolling_memory_stale_candidate_discarded", chat_id=chat_id)
            return None

        cost_usd = resolve_llm_cost(
            DEFAULT_CHAT_MEMORY_MODEL,
            response.tokens_input,
            response.tokens_output,
            response.cost_usd,
        )
        db.add(
            CostRecord(
                org_id=org_id,
                chat_id=chat_id,
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
            chat_id=chat_id,
            through_turn_id=turn.id,
            rolling_memory_chars=len(updated),
        )
        return True

    async def build_continuation_handoff(
        self,
        db: AsyncSession,
        *,
        source_chat: Chat,
    ) -> str:
        """Build a compact handoff from Chat A (caller must already org-authorize)."""
        eligible = await self.list_eligible_turns_oldest_first(db, source_chat.id)
        recent_pairs = eligible[-CONTINUATION_HANDOFF_MAX_RECENT_TURNS:]
        entries = [
            TurnHistoryEntry(
                turn_id=turn.id,
                user_message=turn.user_message,
                verdict_text=assistant_result.text,
                verdict_reason=assistant_result.reason,
                created_at=turn.created_at,
                is_verdict=assistant_result.is_verdict,
            )
            for turn, assistant_result in recent_pairs
        ]
        handoff = build_continuation_handoff_text(
            source_title=source_chat.title,
            rolling_memory=source_chat.rolling_memory,
            recent_entries_oldest_first=entries,
        )
        logger.info(
            "continuation_handoff_built",
            source_chat_id=source_chat.id,
            handoff_chars=len(handoff),
            recent_turn_count=len(entries),
            has_rolling_memory=bool((source_chat.rolling_memory or "").strip()),
        )
        return handoff

    async def seed_continuation_memory_if_empty(
        self,
        db: AsyncSession,
        *,
        chat_id: str,
        custom_instructions: str | None,
    ) -> bool:
        """Seed destination chat rolling_memory once from a stored handoff.

        Does not copy source watermarks. Never overwrites non-empty memory.
        Returns True when a seed was written.
        """
        handoff = extract_continuation_handoff(custom_instructions)
        if not handoff:
            return False

        chat = await db.get(Chat, chat_id)
        if chat is None:
            return False
        if (chat.rolling_memory or "").strip():
            return False

        seeded = format_continuation_seed_memory(handoff)
        if not seeded:
            return False

        # One-time inheritance into Chat B's own memory. Keep watermark null so
        # Chat B's normal expiry merge owns the lifecycle going forward.
        chat.rolling_memory = seeded
        chat.rolling_memory_updated_at = datetime.now(UTC)
        await db.flush()
        logger.info(
            "continuation_memory_seeded",
            chat_id=chat.id,
            rolling_memory_chars=len(seeded),
            through_turn_id=chat.rolling_memory_through_turn_id,
        )
        return True


chat_memory_service = ChatMemoryService()
