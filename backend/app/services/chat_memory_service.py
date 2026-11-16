"""Per-chat rolling continuation memory — separate from user-level Brain."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    Chat,
    ChatAttachment,
    CostRecord,
    ModelAnswerStatus,
    Turn,
    TurnStatus,
    UsageKind,
)
from app.db.session import AsyncSessionLocal
from app.llm.catalog import estimate_tokens, get_model, resolve_llm_cost
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry
from app.services.document_context import reference_chat_marker

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

# Bounded recovery for the summarizer call itself (oversized output, empty
# output, or a provider/network error). Independent from CAS retries above,
# which handle concurrent writers rather than a bad or failing model response.
CHAT_MEMORY_MAX_SUMMARY_ATTEMPTS = 3
CHAT_MEMORY_RETRY_MAX_TOKENS: tuple[int, ...] = (2048, 1536, 1024)
# One extra, last-resort attempt that explicitly asks the model to compact its
# own oversized draft while preserving named categories (facts, decisions,
# instructions, newest-turn content) rather than a blind character cut.
CHAT_MEMORY_RECOMPACTION_MAX_TOKENS = 1024
CHAT_MEMORY_RECOMPACTION_INPUT_MAX_TOKENS = 6_000

# One-time cross-chat continuation handoff (Chat A → first turn of Chat B).
CONTINUATION_HANDOFF_MAX_CHARS = 500_000
CONTINUATION_HANDOFF_MAX_RECENT_TURNS = 10
CONTINUATION_HANDOFF_ROLLING_MEMORY_MAX_CHARS = 12_000
# Rolling-memory seed stays at the previous handoff size. It is not the prompt handoff.
CONTINUATION_SEED_MAX_CHARS = 40_000
CONTINUATION_HANDOFF_HEADER = "## MultiMind Continuation Handoff"
CONTINUATION_SEED_PREFIX = "Continuation context inherited from a previous chat:"
CONTINUATION_ATTACHMENT_HEADER = "### Previously attached text files"
CONTINUATION_ATTACHMENT_PER_FILE_CHARS = 30_000
CONTINUATION_ATTACHMENT_MAX_FILES = 10
PRIOR_TXT_ATTACHMENT_LIMIT = 20
PRIOR_DOCUMENT_ATTACHMENT_LIMIT = 40
_ATTACHMENT_TRUNCATION_MARKER = "\n[Attachment context truncated]"

TURN_SEPARATOR = "\n\n---\n\n"
TRUNCATION_MARKER = "\n[...truncated...]"
CHALLENGE_TURN_MARKER = "__multimind_challenge_turn__"

_MEMORY_UPDATE_USER_PROMPT = "Update the rolling chat memory text now."
_MEMORY_UPDATE_RETRY_USER_PROMPT = (
    "Update the rolling chat memory text now. "
    "Your previous attempt was too long for the stored-memory budget — "
    "be substantially more concise while keeping the most important facts."
)
_MEMORY_RECOMPACTION_USER_PROMPT = "Compact the draft memory now, following the instructions above."


def _build_recompaction_system_prompt(base_system: str, oversized_draft: str) -> str:
    """Plain-text (non-template) addendum asking for targeted compaction.

    Deliberately does not touch any .j2 template: it reuses the already
    rendered merge prompt (which names what to preserve) and appends an
    explicit instruction to compact the model's own oversized draft instead
    of re-merging from scratch, so it can be judged against the same
    "Current Rolling Memory" / "Newly Expired Turn" sources already in
    ``base_system``.
    """
    bounded_draft = truncate_text_under_token_budget(
        oversized_draft, CHAT_MEMORY_RECOMPACTION_INPUT_MAX_TOKENS
    )
    return (
        f"{base_system}\n\n"
        "---\n"
        "## Compaction Required\n"
        f"Your previous merged draft below exceeded the stored-memory budget "
        f"({CHAT_MEMORY_STORED_MAX_TOKENS} estimated tokens). Rewrite it to fit "
        "within that budget. You must preserve every distinct fact, decision, "
        "requirement, and instruction already present in the 'Current Rolling "
        "Memory' and 'Newly Expired Turn' sections above, and you must retain "
        "the key outcome of the newest turn. Cut redundancy, filler, and less "
        "essential phrasing — never drop a unique fact, decision, or "
        "instruction entirely if it can instead be stated more briefly.\n\n"
        "Previous oversized draft:\n"
        f"{bounded_draft}"
    )


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


def _bound_attachment_excerpt(excerpt: str, *, per_file_chars: int) -> str:
    body = (excerpt or "").strip()
    if len(body) <= per_file_chars:
        return body
    room = per_file_chars - len(_ATTACHMENT_TRUNCATION_MARKER)
    if room <= 0:
        return _ATTACHMENT_TRUNCATION_MARKER.strip()
    return body[:room].rstrip() + _ATTACHMENT_TRUNCATION_MARKER


def format_bounded_txt_attachment_section(
    excerpts: list[tuple[str, str]],
    *,
    per_file_chars: int = CONTINUATION_ATTACHMENT_PER_FILE_CHARS,
    max_files: int = CONTINUATION_ATTACHMENT_MAX_FILES,
) -> str:
    """Up to 10 TXT files, each capped at 30,000 characters with an explicit marker.

    The overall referenced-chat handoff cap is applied later, after this section
    is assembled. Duplicate filenames are skipped.
    """
    if per_file_chars <= 0 or max_files <= 0 or not excerpts:
        return ""
    parts: list[str] = [CONTINUATION_ATTACHMENT_HEADER]
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for filename, excerpt in excerpts:
        name = (filename or "file.txt").strip() or "file.txt"
        key = name.lower()
        if key in seen:
            continue
        body = (excerpt or "").strip()
        if not body:
            continue
        seen.add(key)
        unique.append((name, body))
    # Keep the most recent files when a chat has more than the handoff allows.
    selected = unique[-max_files:]
    omitted = len(unique) - len(selected)
    for name, body in selected:
        parts.append(f"Attached text file: {name}\n{_bound_attachment_excerpt(body, per_file_chars=per_file_chars)}")
    if omitted:
        parts.append(
            f"[Attachment context truncated]\n{omitted} additional text file(s) omitted."
        )
    if len(parts) == 1:
        return ""
    return "\n\n".join(parts)


def format_reference_document_manifest(rows: list, source_chat_id: str) -> str:
    """Name retained documents without pasting a fixed character prefix.

    The destination turn loads the stored extracts and budgets them per model.
    """
    kept = [row for row in rows if (getattr(row, "text_excerpt", None) or "").strip()]
    if not kept:
        return ""
    selected = kept[-CONTINUATION_ATTACHMENT_MAX_FILES:]
    omitted = len(kept) - len(selected)
    lines = [
        CONTINUATION_ATTACHMENT_HEADER,
        reference_chat_marker(source_chat_id),
        "Document text is loaded from the source chat when the question runs. "
        "This handoff lists files and does not paste a character prefix.",
    ]
    for row in selected:
        text = row.text_excerpt or ""
        lines.append(
            f"- {row.filename}: stored_chars={len(text)} status={getattr(row, 'excerpt_status', None) or 'unknown'}"
        )
    if omitted:
        lines.append(f"{omitted} additional file(s) omitted from this manifest.")
    return "\n".join(lines)


def build_continuation_handoff_text(
    *,
    source_title: str,
    rolling_memory: str | None,
    recent_entries_oldest_first: list[TurnHistoryEntry],
    max_chars: int = CONTINUATION_HANDOFF_MAX_CHARS,
    rolling_memory_max_chars: int = CONTINUATION_HANDOFF_ROLLING_MEMORY_MAX_CHARS,
    attachment_section: str | None = None,
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

    attachment_section = (attachment_section or "").strip()
    # History is reserved first (rolling memory already placed, then up to 10
    # recent turns). Attachments receive only the handoff budget that remains.
    recent_budget = remaining() - 2
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

    if attachment_section and remaining() > len(CONTINUATION_ATTACHMENT_HEADER) + 16:
        # Per-file limits are already applied. This is the 500,000-character handoff cap.
        section = attachment_section
        room = remaining() - 2
        if len(section) > room:
            section = truncate_text_under_budget(section, room)
        if section:
            parts.append(section)
            used += 2 + len(section)

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
    return truncate_text_under_budget(seeded, CONTINUATION_SEED_MAX_CHARS)


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
        max_merges: int | None = None,
    ) -> int:
        """Merge newly expired turns into rolling memory. Returns merges performed.

        ``max_merges`` bounds LLM work for this one call: once that many
        turns have been successfully merged, this returns immediately even
        if more compactable turns remain, so a durable worker job can process
        a very long backlog as many small bounded calls (re-enqueuing a
        continuation) instead of one unbounded run inside a single process
        invocation. ``None`` (the default) preserves the exact prior
        behavior — the ordinary per-new-turn incremental path only ever has
        at most one newly-compactable turn anyway, so it is never affected by
        this bound in practice.
        """
        merged = 0
        stale_retries = 0
        while stale_retries < CHAT_MEMORY_MAX_CAS_RETRIES:
            if max_merges is not None and merged >= max_merges:
                return merged
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
        """Make stale memory unavailable and advance its compare-and-swap stamp.

        Also stamps ``rolling_memory_rebuild_requested_at`` — the durable
        signal that a rebuild is owed, distinct from an ordinary new chat
        that has simply never needed one (both otherwise look identical:
        watermark/memory both NULL). A periodic recovery sweep
        (``recover_pending_chat_memory_rebuilds``) and the worker job it
        re-enqueues rely on this column alone to find dormant chats that
        still need rebuilding, independent of any new user activity in that
        chat — see app.chat_memory.worker.
        """
        result = await db.execute(
            update(Chat)
            .where(Chat.id == chat_id)
            .values(
                rolling_memory=None,
                rolling_memory_through_turn_id=None,
                rolling_memory_updated_at=datetime.now(UTC),
                rolling_memory_rebuild_requested_at=datetime.now(UTC),
            )
        )
        return result.rowcount == 1

    async def claim_chat_for_rebuild(
        self, db: AsyncSession, *, chat_id: str, stale_before: datetime
    ) -> bool:
        """Atomic single-execution claim for one rebuild-worker job.

        Mirrors ``DebateSession.execution_claimed_at`` in
        ``app.services.debate_service``: only the caller that wins this
        conditional UPDATE may run a merge pass for this chat. A claim older
        than ``stale_before`` is treated as orphaned (worker crashed/killed
        mid-job) and is reclaimable, so a dead worker can never strand a
        chat's pending rebuild forever.
        """
        result = await db.execute(
            update(Chat)
            .where(
                Chat.id == chat_id,
                Chat.rolling_memory_rebuild_requested_at.is_not(None),
                (Chat.rolling_memory_rebuild_claimed_at.is_(None))
                | (Chat.rolling_memory_rebuild_claimed_at < stale_before),
            )
            .values(rolling_memory_rebuild_claimed_at=datetime.now(UTC))
        )
        return result.rowcount == 1

    async def release_chat_rebuild_claim(
        self, db: AsyncSession, *, chat_id: str, still_pending: bool
    ) -> None:
        """Release a rebuild claim after one bounded job pass.

        ``still_pending=True`` clears only the claim, keeping
        ``rolling_memory_rebuild_requested_at`` set so the next job (or the
        periodic sweep, if re-enqueuing itself failed) resumes exactly where
        this pass left off via the normal watermark. ``still_pending=False``
        clears both columns: no more compactable turns remain, so no further
        rebuild work is owed for this chat.
        """
        values: dict = {"rolling_memory_rebuild_claimed_at": None}
        if not still_pending:
            values["rolling_memory_rebuild_requested_at"] = None
        await db.execute(update(Chat).where(Chat.id == chat_id).values(**values))

    async def list_chats_pending_rebuild(
        self, db: AsyncSession, *, stale_before: datetime, limit: int = 200
    ) -> list[tuple[str, str, str | None]]:
        """Chats with a durable rebuild request not currently (non-stale) claimed.

        Returns ``(chat_id, org_id, project_id)`` tuples, oldest request
        first, for the periodic recovery sweep to re-enqueue — this is the
        only way a chat's pending rebuild is ever resumed without that exact
        chat receiving new user activity.
        """
        result = await db.execute(
            select(Chat.id, Chat.org_id, Chat.project_id)
            .where(
                Chat.rolling_memory_rebuild_requested_at.is_not(None),
                (Chat.rolling_memory_rebuild_claimed_at.is_(None))
                | (Chat.rolling_memory_rebuild_claimed_at < stale_before),
            )
            .order_by(Chat.rolling_memory_rebuild_requested_at.asc())
            .limit(limit)
        )
        return [(row.id, row.org_id, row.project_id) for row in result.all()]

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

    async def _record_billable_usage(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        chat_id: str,
        project_id: str | None,
        turn_id: str,
        response,
    ) -> None:
        """Bill one real provider response immediately, independent of the merge.

        Called exactly once per response object, right after it is obtained
        (never again later), so no response is ever counted twice. Commits
        through a separate session bound to the same engine as ``db`` (not
        the global ``AsyncSessionLocal``, since this module runs against
        whatever engine the caller — production or a test fixture — actually
        uses) so this record survives regardless of what happens afterwards
        in the caller's own transaction — a later ``db.rollback()`` on a CAS
        conflict, or the merge being abandoned after exhausting recovery.
        Provider usage that was actually incurred must never be silently
        dropped just because the memory update it was for didn't land.

        An independent production-safety audit confirmed this engine-wrap
        correctly shares ``db``'s connection pool (no duplicate pool, no
        leak from never calling ``.dispose()`` on the wrapper — closing the
        short-lived session only returns its connection, it does not dispose
        the shared pool) but does mean this and the caller's own `db` session
        can briefly hold two simultaneous connections while `db` is still
        mid-transaction when this runs. An attempt to fix that by having
        `_merge_one_turn` release `db`'s connection (via an extra
        ``db.rollback()``) before reaching here was tried and reverted: it
        expired every object in `db`'s identity map, not just the ones this
        module was done with, which broke a caller holding other ORM objects
        from the same session across the call (confirmed by a real
        ``MissingGreenlet`` test failure). Swapping this call alone to a
        shared sessionmaker would not fix the two-connections point either —
        see the accompanying report's "Finding 2" for why this remains an
        accepted, documented trade-off rather than a fix: correctness for
        the caller's session takes priority over this connection-pool
        optimization.
        """
        try:
            cost_usd = resolve_llm_cost(
                DEFAULT_CHAT_MEMORY_MODEL,
                response.tokens_input,
                response.tokens_output,
                response.cost_usd,
            )
            bind = db.get_bind()
            engine = bind if isinstance(bind, AsyncEngine) else AsyncEngine(bind)
            async with AsyncSession(bind=engine) as cost_db:
                cost_db.add(
                    CostRecord(
                        org_id=org_id,
                        chat_id=chat_id,
                        project_id=project_id,
                        turn_id=turn_id,
                        model_id=DEFAULT_CHAT_MEMORY_MODEL,
                        kind=UsageKind.CHAT_MEMORY,
                        tokens_input=response.tokens_input,
                        tokens_output=response.tokens_output,
                        cost_usd=cost_usd,
                    )
                )
                await cost_db.commit()
        except Exception as exc:  # noqa: BLE001 - cost bookkeeping must never break memory recovery
            logger.warning(
                "chat_rolling_memory_cost_record_failed",
                chat_id=chat_id,
                turn_id=turn_id,
                error=str(exc),
            )

    async def _summarize_with_bounded_recovery(
        self,
        db: AsyncSession,
        *,
        provider,
        model,
        system: str,
        org_id: str,
        project_id: str | None,
        chat_id: str,
        turn_id: str,
    ) -> tuple[object | None, str | None]:
        """Call the memory summarizer with bounded retries and safe recovery.

        Handles three failure modes without ever looping unboundedly: a
        provider/network error, an empty response, and output that exceeds
        ``CHAT_MEMORY_STORED_MAX_TOKENS``. Oversized output is retried with a
        stricter target and an explicit conciseness hint (plain text, not a
        template change). If every retry is still oversized, exactly one
        additional semantic-recompaction call is made: it is shown the
        model's own oversized draft plus the original sources and is asked to
        compact it while explicitly preserving facts, decisions, requirements,
        and the newest turn's content — never a blind character cut.

        Every response actually returned by the provider (any successful
        round-trip, regardless of whether its text was usable) is billed via
        ``_record_billable_usage`` immediately, independent of whether this
        attempt's output ends up persisted — so a retry superseded by a later
        attempt, or a merge that ultimately fails, still records the real
        provider usage it incurred.

        A fixed 2,048-token memory budget cannot guarantee perfect
        preservation of an arbitrarily long turn; when recompaction also
        fails to fit the budget (or errors, or comes back empty), nothing is
        persisted. Returns ``(None, None)`` in that case — callers must leave
        the existing memory and watermark untouched and rely on a later retry
        (triggered by a subsequent turn) rather than silently losing
        information via truncation.
        """
        last_response = None
        last_oversized_text: str | None = None

        for attempt in range(1, CHAT_MEMORY_MAX_SUMMARY_ATTEMPTS + 1):
            max_tokens = CHAT_MEMORY_RETRY_MAX_TOKENS[
                min(attempt - 1, len(CHAT_MEMORY_RETRY_MAX_TOKENS) - 1)
            ]
            user_prompt = (
                _MEMORY_UPDATE_USER_PROMPT
                if attempt == 1
                else _MEMORY_UPDATE_RETRY_USER_PROMPT
            )
            try:
                response = await provider.complete(
                    system=system,
                    user=user_prompt,
                    model=model.provider_model,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 - provider/network errors must not stall memory
                logger.warning(
                    "chat_rolling_memory_summarizer_error",
                    chat_id=chat_id,
                    turn_id=turn_id,
                    attempt=attempt,
                    error=str(exc),
                )
                continue

            await self._record_billable_usage(
                db,
                org_id=org_id,
                chat_id=chat_id,
                project_id=project_id,
                turn_id=turn_id,
                response=response,
            )
            text = (response.text or "").strip()
            if not text:
                logger.warning(
                    "chat_rolling_memory_empty_output",
                    chat_id=chat_id,
                    turn_id=turn_id,
                    attempt=attempt,
                )
                continue

            estimated = estimate_tokens(text)
            if estimated <= CHAT_MEMORY_STORED_MAX_TOKENS:
                return response, text

            logger.warning(
                "chat_rolling_memory_oversized_output",
                chat_id=chat_id,
                turn_id=turn_id,
                attempt=attempt,
                estimated_tokens=estimated,
            )
            last_response = response
            last_oversized_text = text

        if last_response is not None and last_oversized_text:
            recompacted = await self._attempt_semantic_recompaction(
                db,
                provider=provider,
                model=model,
                base_system=system,
                oversized_draft=last_oversized_text,
                org_id=org_id,
                project_id=project_id,
                chat_id=chat_id,
                turn_id=turn_id,
            )
            if recompacted is not None:
                return recompacted

        logger.error(
            "chat_rolling_memory_summarizer_exhausted",
            chat_id=chat_id,
            turn_id=turn_id,
            attempts=CHAT_MEMORY_MAX_SUMMARY_ATTEMPTS,
        )
        return None, None

    async def _attempt_semantic_recompaction(
        self,
        db: AsyncSession,
        *,
        provider,
        model,
        base_system: str,
        oversized_draft: str,
        org_id: str,
        project_id: str | None,
        chat_id: str,
        turn_id: str,
    ) -> tuple[object, str] | None:
        """One bounded, targeted attempt to compact an oversized draft in place.

        Returns ``(response, text)`` only when the recompacted text fits
        ``CHAT_MEMORY_STORED_MAX_TOKENS``. Any failure (error, empty output,
        still oversized) returns ``None`` — this is the last recovery step,
        never retried further within this call. A successful round-trip is
        billed immediately regardless of outcome, same as every other attempt.
        """
        recompaction_system = _build_recompaction_system_prompt(base_system, oversized_draft)
        try:
            response = await provider.complete(
                system=recompaction_system,
                user=_MEMORY_RECOMPACTION_USER_PROMPT,
                model=model.provider_model,
                max_tokens=CHAT_MEMORY_RECOMPACTION_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 - last-resort step must not raise
            logger.warning(
                "chat_rolling_memory_recompaction_error",
                chat_id=chat_id,
                turn_id=turn_id,
                error=str(exc),
            )
            return None

        await self._record_billable_usage(
            db,
            org_id=org_id,
            chat_id=chat_id,
            project_id=project_id,
            turn_id=turn_id,
            response=response,
        )
        text = (response.text or "").strip()
        if not text:
            logger.warning(
                "chat_rolling_memory_recompaction_empty_output",
                chat_id=chat_id,
                turn_id=turn_id,
            )
            return None

        estimated = estimate_tokens(text)
        if estimated > CHAT_MEMORY_STORED_MAX_TOKENS:
            logger.warning(
                "chat_rolling_memory_recompaction_still_oversized",
                chat_id=chat_id,
                turn_id=turn_id,
                estimated_tokens=estimated,
            )
            return None

        logger.info(
            "chat_rolling_memory_recompaction_used",
            chat_id=chat_id,
            turn_id=turn_id,
            estimated_tokens=estimated,
        )
        return response, text

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
        turn_id = turn.id
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

        # NOTE: an earlier version of this fix called `await db.rollback()`
        # here to release this session's connection during the slow
        # summarizer calls below (motivated by the independent audit's
        # connection-pool analysis). That was reverted: `db` is a session
        # the *caller* owns and may hold other ORM objects from (e.g. a
        # `turns` list spanning multiple merge calls in the same session) —
        # `rollback()` expires every object in the session's identity map,
        # not just the ones this method is done with, and a later plain
        # attribute access on one of those objects outside an `await` can
        # raise `MissingGreenlet` (confirmed by a real test failure during
        # implementation: test_failed_background_merge_later_resumes_and_removes_raw_overlap).
        # Correctness for a caller's unrelated session state takes priority
        # over this connection-pool optimization — see the final report's
        # "Finding 2" section for the accepted trade-off this leaves in place.

        response, updated = await self._summarize_with_bounded_recovery(
            db,
            provider=provider,
            model=model,
            system=system,
            org_id=org_id,
            project_id=project_id,
            chat_id=chat_id,
            turn_id=turn_id,
        )
        if response is None or updated is None:
            # Every attempt failed (provider error, empty output, or — after
            # retries plus one semantic-recompaction attempt — still
            # oversized with nothing safe to persist). Transient: leave the
            # existing memory and watermark untouched; the next background
            # merge (triggered by a later turn) retries this same turn.
            # Real provider usage from each attempt was already billed as it
            # occurred (see _record_billable_usage) — a failed merge does not
            # erase costs that were actually incurred.
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
                rolling_memory_through_turn_id=turn_id,
                rolling_memory_updated_at=now,
            )
        )
        if claimed.rowcount != 1:
            await db.rollback()
            logger.info("chat_rolling_memory_stale_candidate_discarded", chat_id=chat_id)
            # The discarded (stale) candidate's own provider usage was
            # already billed as it occurred, independent of this rollback —
            # a CAS conflict must not erase costs that were actually incurred.
            return None

        logger.info(
            "chat_rolling_memory_updated",
            chat_id=chat_id,
            through_turn_id=turn_id,
            rolling_memory_chars=len(updated),
        )
        return True

    async def load_prior_document_attachments(
        self,
        db: AsyncSession,
        chat_id: str,
        *,
        org_id: str | None = None,
        exclude_relative_paths: set[str] | None = None,
        exclude_turn_id: str | None = None,
        limit: int = PRIOR_DOCUMENT_ATTACHMENT_LIMIT,
    ) -> list[ChatAttachment]:
        """Latest document snapshots on non-deleted turns, one row per stored file.

        PDF, DOCX, XLSX, text, and partial extracts are included. A later file with
        the same stored path does not hide an earlier one of a different path.
        """
        conditions = [
            ChatAttachment.chat_id == chat_id,
            ChatAttachment.turn_id.is_not(None),
            ChatAttachment.excerpt_status.in_(("ready", "partial", "empty", "failed", "image")),
            Turn.deleted_at.is_(None),
        ]
        if org_id is not None:
            conditions.append(ChatAttachment.org_id == org_id)
        if exclude_turn_id is not None:
            conditions.append(ChatAttachment.turn_id != exclude_turn_id)
        result = await db.execute(
            select(ChatAttachment)
            .join(Turn, Turn.id == ChatAttachment.turn_id)
            .where(*conditions)
            .order_by(ChatAttachment.created_at.desc(), ChatAttachment.id.desc())
            .limit(max(limit, 1) * 4)
        )
        excluded = exclude_relative_paths or set()
        seen: set[str] = set()
        picked: list[ChatAttachment] = []
        for row in result.scalars().all():
            has_text = bool((row.text_excerpt or "").strip())
            if row.excerpt_status in {"ready", "partial"} and not has_text:
                continue
            key = row.relative_path or row.stored_name or row.id
            if key in excluded or key in seen:
                continue
            seen.add(key)
            picked.append(row)
            if len(picked) >= limit:
                break
        picked.reverse()
        return picked

    async def load_prior_txt_attachments(
        self,
        db: AsyncSession,
        chat_id: str,
        *,
        exclude_relative_paths: set[str] | None = None,
    ) -> list[ChatAttachment]:
        """Text-file subset used by the referenced-chat handoff formatter."""
        rows = await self.load_prior_document_attachments(
            db,
            chat_id,
            exclude_relative_paths=exclude_relative_paths,
            limit=PRIOR_TXT_ATTACHMENT_LIMIT,
        )
        return [
            row
            for row in rows
            if (row.filename or "").lower().endswith(".txt") and (row.text_excerpt or "").strip()
        ]

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
        prior_docs = await self.load_prior_document_attachments(db, source_chat.id)
        attachment_section = format_reference_document_manifest(prior_docs, source_chat.id)
        handoff = build_continuation_handoff_text(
            source_title=source_chat.title,
            rolling_memory=source_chat.rolling_memory,
            recent_entries_oldest_first=entries,
            attachment_section=attachment_section or None,
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


CHAT_MEMORY_ARQ_JOB_NAME = "process_chat_memory_rebuild_job"


async def enqueue_chat_memory_rebuild(
    *, chat_id: str, org_id: str, project_id: str | None
) -> None:
    """Queue one durable rebuild job for this chat. Never raises.

    Called right after ``invalidate_memory`` commits (delete/restore/
    regenerate — see app.services.chat_service), replacing the old
    synchronous inline rebuild. A Redis failure here leaves
    ``rolling_memory_rebuild_requested_at`` set on the chat row, which the
    periodic ``recover_pending_chat_memory_rebuilds`` sweep will pick up on
    its next run — so losing this one enqueue attempt is never the only path
    to recovery (mirrors app.services.document_processing.enqueue_processing's
    "Redis failures leave the row processing" durability contract).
    """
    settings = get_settings()
    try:
        from urllib.parse import urlparse

        from arq import create_pool
        from arq.connections import RedisSettings

        parsed = urlparse(settings.redis_url)
        redis = RedisSettings(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            database=int((parsed.path or "/0").lstrip("/") or "0"),
            password=parsed.password,
        )
        pool = await create_pool(redis, default_queue_name=settings.chat_memory_worker_queue_name)
        try:
            await pool.enqueue_job(
                CHAT_MEMORY_ARQ_JOB_NAME,
                chat_id,
                org_id,
                project_id,
                _queue_name=settings.chat_memory_worker_queue_name,
            )
        finally:
            await pool.close()
    except Exception as exc:  # noqa: BLE001 - enqueue must never break the caller's request
        logger.warning(
            "chat_memory_rebuild_enqueue_failed",
            chat_id=chat_id,
            error=str(exc),
        )


async def process_chat_memory_rebuild_job(
    ctx: dict, chat_id: str, org_id: str, project_id: str | None
) -> dict:
    """ARQ job entrypoint — registered only on the dedicated chat-memory
    worker's ``WorkerSettings.functions`` (see app/chat_memory/worker.py).

    Claims the chat (no-op if another delivery already holds a live claim),
    runs one bounded merge pass, then re-enqueues a continuation if more
    compactable turns remain, or clears the rebuild request if the chat is
    fully caught up. The claim is always released in a ``finally`` — even a
    crash mid-pass leaves ``rolling_memory_rebuild_requested_at`` set (never
    silently cleared) so the periodic sweep or a later claim resumes the
    rebuild from the watermark's last successfully committed position.

    ``ctx["session_factory"]`` overrides the session factory (defaults to
    the production ``AsyncSessionLocal``) — set by the worker's own
    ``startup()`` in production, and by tests that need this job to run
    against their own isolated engine instead (same convention as
    ``app.documents.worker.process_document_job``).
    """
    session_factory = ctx.get("session_factory") or AsyncSessionLocal
    settings = get_settings()
    stale_before = datetime.now(UTC) - timedelta(
        seconds=settings.chat_memory_rebuild_claim_stale_seconds
    )
    async with session_factory() as claim_db:
        claimed = await chat_memory_service.claim_chat_for_rebuild(
            claim_db, chat_id=chat_id, stale_before=stale_before
        )
        await claim_db.commit()
    if not claimed:
        logger.info("chat_memory_rebuild_already_claimed", chat_id=chat_id)
        return {"status": "already_claimed", "chat_id": chat_id}

    merged = 0
    still_pending = True
    try:
        async with session_factory() as db:
            merged = await chat_memory_service.merge_expired_turns(
                db,
                chat_id=chat_id,
                org_id=org_id,
                project_id=project_id,
                max_merges=settings.chat_memory_rebuild_max_merges_per_job,
            )
            chat = await db.get(Chat, chat_id, populate_existing=True)
            if chat is None:
                still_pending = False
            else:
                eligible = await chat_memory_service.list_eligible_turns_oldest_first(db, chat_id)
                remaining = chat_memory_service.newly_expired_turns(
                    eligible, through_turn_id=chat.rolling_memory_through_turn_id
                )
                still_pending = bool(remaining)
    finally:
        async with session_factory() as release_db:
            await chat_memory_service.release_chat_rebuild_claim(
                release_db, chat_id=chat_id, still_pending=still_pending
            )
            await release_db.commit()

    if still_pending:
        await enqueue_chat_memory_rebuild(chat_id=chat_id, org_id=org_id, project_id=project_id)
    logger.info(
        "chat_memory_rebuild_job_pass_complete",
        chat_id=chat_id,
        merged=merged,
        still_pending=still_pending,
    )
    return {"status": "ok", "chat_id": chat_id, "merged": merged, "still_pending": still_pending}


async def recover_pending_chat_memory_rebuilds(ctx: dict) -> int:
    """Startup + periodic cron: re-enqueue any chat whose rebuild request is
    not currently (non-stale) claimed.

    This is the only recovery path that does not depend on new user activity
    in the affected chat — a dormant chat whose rebuild job crashed, or whose
    original enqueue attempt failed outright, is still found and resumed here
    (mirrors app.services.debate_service.recover_debate_sessions /
    app.documents.worker.recover_stale_documents).

    ``ctx["session_factory"]`` overrides the session factory, same
    convention as ``process_chat_memory_rebuild_job`` above.
    """
    session_factory = ctx.get("session_factory") or AsyncSessionLocal
    settings = get_settings()
    stale_before = datetime.now(UTC) - timedelta(
        seconds=settings.chat_memory_rebuild_claim_stale_seconds
    )
    async with session_factory() as db:
        pending = await chat_memory_service.list_chats_pending_rebuild(
            db, stale_before=stale_before
        )
    for chat_id, org_id, project_id in pending:
        await enqueue_chat_memory_rebuild(chat_id=chat_id, org_id=org_id, project_id=project_id)
    if pending:
        logger.info("chat_memory_rebuild_recovery_swept", count=len(pending))
    return len(pending)
