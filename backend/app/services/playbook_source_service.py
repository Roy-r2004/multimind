"""Playbook Phase 2: source eligibility, transcript reconstruction, hashing, batching.

Read-only. Does not persist Playbook runs, observations, source states, or hashes.
Does not call LLMs, rolling-memory summarization, vision, embeddings, or retrieval.

Query strategy
--------------
Eligible chats: one ``SELECT`` on ``chats`` with SQL-level ownership
(``Chat.org_id`` + ``Chat.created_by``) and an ``EXISTS`` subquery for at least
one eligible turn (status, not deleted, not challenge, persisted verdict).

Eligible turns: one ``SELECT`` joining ``chats`` and ``verdicts``, with the same
SQL ownership and eligibility filters, ``selectinload`` for council answers,
verdicts, lessons, and attachments. Child collections are sorted in Python
because those relationships have no ``order_by``.

Brain: one ``UserBrain`` lookup by ``user_id`` (user-global) and one
``BrainKnowledgeItem`` query filtered by ``org_id`` + ``user_id``.

Referenced-chat handoff boundary
--------------------------------
``referenced_chat_id`` is not a database column. Surviving handoff text may
appear inside ``Turn.custom_instructions``.

1. Prefer ``extract_continuation_handoff`` from chat-memory, which finds the
   exact header ``## MultiMind Continuation Handoff`` and takes text from that
   header through the first ``\\n\\nAttached file:`` or ``\\n\\nIMAGE CONTEXT``
   stop marker (or end of instructions).
2. If that exact header is absent, a defensive regex matches a heading line
   ``#{2,}`` + flexible whitespace + ``MultiMind Continuation Handoff`` and
   uses the same stop markers. Unusual spacing emits a nonfatal warning.
3. The full original ``custom_instructions`` string is always preserved.
   No native referenced-chat ID is claimed or reconstructed.

Turn content-hash canonical payload
-----------------------------------
SHA-256 of UTF-8 canonical JSON (``sort_keys=True``, separators ``(',', ':')``).

Included::

    {
      "attachments": [
        {
          "attachment_id": str,
          "content_type": str,
          "excerpt_status": "ready",
          "filename": str,
          "text_excerpt": str | null
        }
      ],
      "council_answers": [
        {
          "confidence": int | null,
          "model_answer_id": str,
          "model_id": str,
          "status": str,
          "text": str
        }
      ],
      "custom_instructions": str | null,
      "lesson": {
        "disagreement_reason": str,
        "discussion_messages": [{"content": str, "role": str}, ...],
        "lesson_id": str,
        "status": str,
        "user_position": str
      } | null,
      "model_set_id": str,
      "status": str,
      "strategy": str,
      "user_message": str,
      "verdict": {
        "reason": str,
        "text": str,
        "verdict_id": str
      },
      "verdict_model": str
    }

Lists are already in deterministic database order before hashing.
Confidence is normalized to ``int`` or JSON ``null``.
Missing optional values are JSON ``null`` (not omitted).
Ready attachments only. Completed lessons only. Usable council answers only.

Excluded from the turn content hash: ``Turn.id``, ``Chat.id``, rolling memory,
Playbook IDs, timestamps, reconstruction warnings, ORM/session state, wall clock,
non-ready attachment text, failed/empty council answers, incomplete lessons.

Child row IDs (verdict / model answer / attachment / lesson) are included as
identity of those persisted content objects. The turn ID is the future
``source_id`` and is intentionally excluded so the hash is content-only.

Default batch budget ``PLAYBOOK_SOURCE_BATCH_MAX_CHARS`` is a reconstruction
estimate default, not the production LLM context budget.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.db.models import (
    BrainKnowledgeItem,
    Chat,
    ChatAttachment,
    LessonStatus,
    ModelAnswer,
    ModelAnswerStatus,
    Turn,
    TurnStatus,
    UserBrain,
    Verdict,
    VerdictLesson,
)
from app.services.chat_memory_service import (
    CHALLENGE_TURN_MARKER,
    extract_continuation_handoff,
)
from app.services.multi_reference_context_service import extract_multi_reference_context

ELIGIBLE_TURN_STATUSES = (TurnStatus.COMPLETED, TurnStatus.PARTIAL)
USABLE_COUNCIL_STATUSES = (ModelAnswerStatus.COMPLETED,)
READY_ATTACHMENT_STATUS = "ready"

# Reconstruction estimate default — not the production model context window.
PLAYBOOK_SOURCE_BATCH_MAX_CHARS = 24_000

_HANDOFF_HEADER_RE = re.compile(
    r"^#{2,}[ \t]+MultiMind[ \t]+(?:Continuation[ \t]+Handoff|Multi-Reference[ \t]+Context)[ \t]*$",
    re.MULTILINE,
)
_HANDOFF_STOPS = ("\n\nAttached file:", "\n\nIMAGE CONTEXT")

WARNING_MALFORMED_LESSON = "malformed_lesson_discussion"
WARNING_ATTACHMENT_NOT_READY = "attachment_excerpt_not_ready"
WARNING_UNUSUAL_HANDOFF = "unusual_continuation_handoff"


@dataclass(frozen=True)
class PlaybookSourceWarning:
    code: str
    message: str
    chat_id: str | None = None
    turn_id: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class PlaybookCouncilAnswer:
    model_answer_id: str
    model_id: str
    text: str
    status: str
    confidence: int | None
    created_at: datetime | None


@dataclass(frozen=True)
class PlaybookVerdictSource:
    verdict_id: str
    text: str
    reason: str
    created_at: datetime | None


@dataclass(frozen=True)
class PlaybookDiscussionMessage:
    role: str
    content: str


@dataclass(frozen=True)
class PlaybookLessonSource:
    lesson_id: str
    status: str
    disagreement_reason: str
    user_position: str
    discussion_messages: tuple[PlaybookDiscussionMessage, ...]
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class PlaybookAttachmentSource:
    attachment_id: str
    filename: str
    content_type: str
    excerpt_status: str
    text_excerpt: str | None
    created_at: datetime | None
    excerpt_is_ready: bool


@dataclass(frozen=True)
class PlaybookTurnSource:
    turn_id: str
    created_at: datetime | None
    status: str
    user_message: str
    custom_instructions: str | None
    has_referenced_chat_handoff: bool
    referenced_chat_handoff: str | None
    model_set_id: str
    strategy: str
    verdict_model: str
    council_answers: tuple[PlaybookCouncilAnswer, ...]
    verdict: PlaybookVerdictSource
    lesson: PlaybookLessonSource | None
    attachments: tuple[PlaybookAttachmentSource, ...]
    content_hash: str
    warnings: tuple[PlaybookSourceWarning, ...] = ()


@dataclass(frozen=True)
class PlaybookChatTranscript:
    chat_id: str
    chat_title: str
    project_id: str | None
    chat_created_at: datetime | None
    chat_updated_at: datetime | None
    turns: tuple[PlaybookTurnSource, ...]
    warnings: tuple[PlaybookSourceWarning, ...] = ()


@dataclass(frozen=True)
class PlaybookTranscriptSet:
    chats: tuple[PlaybookChatTranscript, ...]
    warnings: tuple[PlaybookSourceWarning, ...] = ()


@dataclass(frozen=True)
class PlaybookUserBrainSource:
    id: str
    user_id: str
    org_id: str
    summary: str
    thinking_style: str
    likes: tuple[str, ...]
    dislikes: tuple[str, ...]
    memories: tuple[dict[str, Any], ...]
    lesson_count: int
    created_at: datetime | None
    updated_at: datetime | None
    is_user_global: bool
    content_hash: str


@dataclass(frozen=True)
class PlaybookBrainKnowledgeSource:
    id: str
    source_type: str
    source_id: str
    title: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime | None
    updated_at: datetime | None
    content_hash: str


@dataclass(frozen=True)
class PlaybookBrainSnapshot:
    """Authenticated-user Brain sources. UserBrain is user-global; knowledge is org-scoped."""

    user_brain: PlaybookUserBrainSource | None
    knowledge_items: tuple[PlaybookBrainKnowledgeSource, ...]
    user_brain_is_global: bool = True
    warnings: tuple[PlaybookSourceWarning, ...] = ()


@dataclass(frozen=True)
class PlaybookExtractionBatch:
    batch_index: int
    chat_ids: tuple[str, ...]
    turn_ids: tuple[str, ...]
    estimated_characters: int
    chat_count: int
    turn_count: int
    oversized: bool
    spanning_chat_ids: tuple[str, ...] = ()
    chats: tuple[PlaybookChatTranscript, ...] = ()


def canonical_dumps(value: Any) -> str:
    """Stable JSON for hashing: sorted keys, compact separators, UTF-8 text."""
    return json.dumps(
        _canonicalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_hex(value: Any) -> str:
    digest = hashlib.sha256(canonical_dumps(value).encode("utf-8")).hexdigest()
    return digest.lower()


def _canonicalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, dict):
        return {str(key): _canonicalize(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _confidence_value(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def detect_referenced_chat_handoff(
    custom_instructions: str | None,
) -> tuple[bool, str | None, bool]:
    """Detect a persisted continuation-handoff block inside custom_instructions.

    Returns ``(has_handoff, handoff_block, used_defensive_spacing)``.

    Block start: exact ``## MultiMind Continuation Handoff`` when present;
    otherwise a heading line matching ``#{2,}`` plus flexible whitespace around
    ``MultiMind Continuation Handoff``.

    Block end: first ``\\n\\nAttached file:`` or ``\\n\\nIMAGE CONTEXT``, else
    the end of the instructions string.
    """
    text = custom_instructions or ""
    exact = extract_continuation_handoff(text)
    if exact:
        return True, exact, False
    exact_multi = extract_multi_reference_context(text)
    if exact_multi:
        return True, exact_multi, False

    stripped = text.strip()
    match = _HANDOFF_HEADER_RE.search(stripped)
    if match is None:
        return False, None, False
    block = _slice_handoff_block(stripped, match.start())
    if not block:
        return False, None, False
    return True, block, True


def _slice_handoff_block(text: str, start: int) -> str | None:
    rest = text[start:]
    for stop in _HANDOFF_STOPS:
        idx = rest.find(stop)
        if idx > 0:
            rest = rest[:idx]
            break
    handoff = rest.strip()
    return handoff or None


def render_turn_for_estimate(turn: PlaybookTurnSource) -> str:
    """Stable labeled rendering for character estimates. Not an LLM prompt."""
    sections: list[str] = [
        "### Turn",
        f"Status: {turn.status}",
        f"Model set: {turn.model_set_id}",
        f"Strategy: {turn.strategy}",
        f"Verdict model: {turn.verdict_model}",
        "",
        "## User message",
        turn.user_message if turn.user_message else "",
    ]
    if turn.custom_instructions is not None:
        sections.extend(["", "## Custom instructions", turn.custom_instructions])
    if turn.has_referenced_chat_handoff and turn.referenced_chat_handoff:
        sections.extend(
            ["", "## Referenced-chat handoff", turn.referenced_chat_handoff]
        )
    sections.append("")
    sections.append("## Council answers")
    if turn.council_answers:
        for answer in turn.council_answers:
            sections.append(f"### {answer.model_id}")
            sections.append(f"Status: {answer.status}")
            if answer.confidence is not None:
                sections.append(f"Confidence: {answer.confidence}")
            sections.append(answer.text)
    else:
        sections.append("(none)")
    sections.extend(["", "## Verdict", turn.verdict.text])
    sections.extend(["", "## Verdict reason", turn.verdict.reason])
    if turn.lesson is not None:
        lesson = turn.lesson
        sections.extend(
            [
                "",
                "## Lesson and user correction",
                f"Status: {lesson.status}",
                f"Disagreement: {lesson.disagreement_reason}",
                f"User position: {lesson.user_position}",
                "Discussion:",
            ]
        )
        if lesson.discussion_messages:
            for message in lesson.discussion_messages:
                sections.append(f"- {message.role}: {message.content}")
        else:
            sections.append("(none)")
    sections.append("")
    sections.append("## Attachments")
    if turn.attachments:
        for attachment in turn.attachments:
            ready = "ready" if attachment.excerpt_is_ready else attachment.excerpt_status
            sections.append(f"### {attachment.filename} ({ready})")
            sections.append(f"Content type: {attachment.content_type}")
            if attachment.excerpt_is_ready:
                sections.append(attachment.text_excerpt or "")
            else:
                sections.append("[excerpt not ready]")
    else:
        sections.append("(none)")
    return "\n".join(sections)


def estimate_rendered_characters(turns: Sequence[PlaybookTurnSource]) -> int:
    if not turns:
        return 0
    return len("\n\n".join(render_turn_for_estimate(turn) for turn in turns))


class PlaybookSourceService:
    def _owner_chat_filters(self, auth: AuthContext):
        return (
            Chat.org_id == auth.org_id,
            Chat.created_by == auth.user.id,
        )

    def _eligible_turn_filters(self):
        return (
            Turn.deleted_at.is_(None),
            Turn.status.in_(ELIGIBLE_TURN_STATUSES),
            or_(
                Turn.error_message.is_(None),
                Turn.error_message != CHALLENGE_TURN_MARKER,
            ),
        )

    def _eligible_turn_exists(self):
        return exists(
            select(Turn.id)
            .join(Verdict, Verdict.turn_id == Turn.id)
            .where(
                Turn.chat_id == Chat.id,
                *self._eligible_turn_filters(),
            )
        )

    def _eligible_turns_statement(self, auth: AuthContext, *, chat_id: str | None = None):
        stmt = (
            select(Turn)
            .join(Chat, Chat.id == Turn.chat_id)
            .join(Verdict, Verdict.turn_id == Turn.id)
            .where(
                *self._owner_chat_filters(auth),
                *self._eligible_turn_filters(),
            )
            .options(
                selectinload(Turn.model_answers),
                selectinload(Turn.verdict),
                selectinload(Turn.lesson),
                selectinload(Turn.attachments),
                selectinload(Turn.chat),
            )
        )
        if chat_id is not None:
            stmt = stmt.where(Turn.chat_id == chat_id)
        return stmt.order_by(
            Chat.created_at.asc(),
            Chat.id.asc(),
            Turn.created_at.asc(),
            Turn.id.asc(),
        )

    async def list_eligible_chats(
        self, db: AsyncSession, auth: AuthContext
    ) -> list[Chat]:
        result = await db.execute(
            select(Chat)
            .where(
                *self._owner_chat_filters(auth),
                self._eligible_turn_exists(),
            )
            .order_by(Chat.created_at.asc(), Chat.id.asc())
        )
        return list(result.scalars().all())

    async def list_eligible_turns(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        chat_id: str | None = None,
    ) -> list[Turn]:
        result = await db.execute(self._eligible_turns_statement(auth, chat_id=chat_id))
        return list(result.scalars().unique().all())

    async def assemble_chat_transcript(
        self, db: AsyncSession, auth: AuthContext, chat_id: str
    ) -> PlaybookChatTranscript | None:
        result = await db.execute(
            select(Chat).where(Chat.id == chat_id, *self._owner_chat_filters(auth))
        )
        chat = result.scalar_one_or_none()
        if chat is None:
            return None
        turns = await self.list_eligible_turns(db, auth, chat_id=chat_id)
        return self._transcript_for_chat(chat, turns)

    async def assemble_all_transcripts(
        self, db: AsyncSession, auth: AuthContext
    ) -> PlaybookTranscriptSet:
        chats = await self.list_eligible_chats(db, auth)
        turns = await self.list_eligible_turns(db, auth)
        turns_by_chat: dict[str, list[Turn]] = {chat.id: [] for chat in chats}
        for turn in turns:
            turns_by_chat.setdefault(turn.chat_id, []).append(turn)
        transcripts = tuple(
            self._transcript_for_chat(chat, turns_by_chat.get(chat.id, []))
            for chat in chats
        )
        warnings = tuple(
            warning for transcript in transcripts for warning in transcript.warnings
        )
        return PlaybookTranscriptSet(chats=transcripts, warnings=warnings)

    async def build_brain_source_snapshot(
        self, db: AsyncSession, auth: AuthContext
    ) -> PlaybookBrainSnapshot:
        brain_result = await db.execute(
            select(UserBrain).where(UserBrain.user_id == auth.user.id)
        )
        brain_row = brain_result.scalar_one_or_none()
        user_brain = None
        if brain_row is not None:
            user_brain = PlaybookUserBrainSource(
                id=brain_row.id,
                user_id=brain_row.user_id,
                org_id=brain_row.org_id,
                summary=brain_row.summary,
                thinking_style=brain_row.thinking_style,
                likes=tuple(brain_row.likes or []),
                dislikes=tuple(brain_row.dislikes or []),
                memories=tuple(_copy_json(item) for item in (brain_row.memories or [])),
                lesson_count=int(brain_row.lesson_count or 0),
                created_at=brain_row.created_at,
                updated_at=brain_row.updated_at,
                is_user_global=True,
                content_hash=self.compute_user_brain_hash(brain_row),
            )

        knowledge_result = await db.execute(
            select(BrainKnowledgeItem)
            .where(
                BrainKnowledgeItem.org_id == auth.org_id,
                BrainKnowledgeItem.user_id == auth.user.id,
            )
            .order_by(
                BrainKnowledgeItem.created_at.asc(),
                BrainKnowledgeItem.id.asc(),
            )
        )
        knowledge_items = tuple(
            PlaybookBrainKnowledgeSource(
                id=item.id,
                source_type=item.source_type,
                source_id=item.source_id,
                title=item.title,
                content=item.content,
                metadata=_copy_json(item.metadata_json or {}),
                created_at=item.created_at,
                updated_at=item.updated_at,
                content_hash=self.compute_brain_knowledge_item_hash(item),
            )
            for item in knowledge_result.scalars().all()
        )
        return PlaybookBrainSnapshot(
            user_brain=user_brain,
            knowledge_items=knowledge_items,
            user_brain_is_global=True,
        )

    def compute_turn_content_hash(self, turn: PlaybookTurnSource) -> str:
        return sha256_hex(self._turn_hash_payload(turn))

    def compute_user_brain_hash(self, brain: UserBrain | PlaybookUserBrainSource) -> str:
        return sha256_hex(
            {
                "dislikes": list(brain.dislikes or []),
                "lesson_count": int(brain.lesson_count or 0),
                "likes": list(brain.likes or []),
                "memories": list(brain.memories or []),
                "summary": brain.summary or "",
                "thinking_style": brain.thinking_style or "",
            }
        )

    def compute_brain_knowledge_item_hash(
        self, item: BrainKnowledgeItem | PlaybookBrainKnowledgeSource
    ) -> str:
        metadata = (
            item.metadata
            if isinstance(item, PlaybookBrainKnowledgeSource)
            else (item.metadata_json or {})
        )
        return sha256_hex(
            {
                "content": item.content or "",
                "metadata": metadata or {},
                "source_id": item.source_id,
                "source_type": item.source_type,
                "title": item.title or "",
            }
        )

    def batch_transcripts(
        self,
        transcripts: Sequence[PlaybookChatTranscript],
        *,
        max_chars: int = PLAYBOOK_SOURCE_BATCH_MAX_CHARS,
    ) -> list[PlaybookExtractionBatch]:
        """Group chats into extraction batches under a character budget.

        Full chats stay together when they fit. A chat larger than the budget is
        split only between turns. A single turn larger than the budget becomes
        its own oversized batch and is never truncated.
        """
        if max_chars <= 0:
            raise ValueError("max_chars must be a positive character budget")

        raw_batches: list[list[PlaybookChatTranscript]] = []
        oversized_flags: list[bool] = []
        current: list[PlaybookChatTranscript] = []
        current_oversized = False

        def current_turns() -> list[PlaybookTurnSource]:
            return [turn for chat in current for turn in chat.turns]

        def flush() -> None:
            nonlocal current, current_oversized
            if not current:
                return
            raw_batches.append(current)
            oversized_flags.append(current_oversized)
            current = []
            current_oversized = False

        def append_chat_slice(
            chat: PlaybookChatTranscript, turns: Sequence[PlaybookTurnSource]
        ) -> None:
            current.append(replace(chat, turns=tuple(turns), warnings=()))

        for chat in transcripts:
            chat_turns = list(chat.turns)
            if not chat_turns:
                continue
            chat_size = estimate_rendered_characters(chat_turns)
            current_size = estimate_rendered_characters(current_turns())
            if current and _joined_size(current_size, chat_size) <= max_chars:
                append_chat_slice(chat, chat_turns)
                continue
            if chat_size <= max_chars:
                flush()
                append_chat_slice(chat, chat_turns)
                continue
            flush()
            pending: list[PlaybookTurnSource] = []
            for turn in chat_turns:
                turn_size = estimate_rendered_characters([turn])
                if turn_size > max_chars:
                    flush()
                    append_chat_slice(chat, [turn])
                    current_oversized = True
                    flush()
                    continue
                pending_size = estimate_rendered_characters(pending)
                if pending and _joined_size(pending_size, turn_size) > max_chars:
                    append_chat_slice(chat, pending)
                    flush()
                    pending = [turn]
                else:
                    pending.append(turn)
            if pending:
                append_chat_slice(chat, pending)
                flush()
        flush()

        chat_batch_counts: dict[str, int] = {}
        for group in raw_batches:
            for chat in group:
                chat_batch_counts[chat.chat_id] = chat_batch_counts.get(chat.chat_id, 0) + 1

        batches: list[PlaybookExtractionBatch] = []
        for index, (group, oversized) in enumerate(zip(raw_batches, oversized_flags)):
            turns = tuple(turn for chat in group for turn in chat.turns)
            chat_ids = tuple(chat.chat_id for chat in group)
            unique_chat_ids = tuple(dict.fromkeys(chat_ids))
            spanning = tuple(
                chat_id
                for chat_id in unique_chat_ids
                if chat_batch_counts.get(chat_id, 0) > 1
            )
            batches.append(
                PlaybookExtractionBatch(
                    batch_index=index,
                    chat_ids=unique_chat_ids,
                    turn_ids=tuple(turn.turn_id for turn in turns),
                    estimated_characters=estimate_rendered_characters(turns),
                    chat_count=len(unique_chat_ids),
                    turn_count=len(turns),
                    oversized=oversized,
                    spanning_chat_ids=spanning,
                    chats=tuple(group),
                )
            )
        return batches

    def _transcript_for_chat(
        self, chat: Chat, turns: Sequence[Turn]
    ) -> PlaybookChatTranscript:
        reconstructed = tuple(self._reconstruct_turn(chat, turn) for turn in turns)
        warnings = tuple(warning for turn in reconstructed for warning in turn.warnings)
        return PlaybookChatTranscript(
            chat_id=chat.id,
            chat_title=chat.title,
            project_id=chat.project_id,
            chat_created_at=chat.created_at,
            chat_updated_at=chat.updated_at,
            turns=reconstructed,
            warnings=warnings,
        )

    def _reconstruct_turn(self, chat: Chat, turn: Turn) -> PlaybookTurnSource:
        warnings: list[PlaybookSourceWarning] = []
        verdict_row = turn.verdict
        if verdict_row is None:
            raise RuntimeError(
                f"Eligible turn {turn.id} is missing a persisted verdict after SQL join"
            )
        verdict = PlaybookVerdictSource(
            verdict_id=verdict_row.id,
            text=verdict_row.text,
            reason=verdict_row.reason,
            created_at=verdict_row.created_at,
        )
        council_answers = tuple(
            PlaybookCouncilAnswer(
                model_answer_id=answer.id,
                model_id=answer.model_id,
                text=answer.text or "",
                status=_enum_value(answer.status),
                confidence=_confidence_value(answer.confidence),
                created_at=answer.created_at,
            )
            for answer in _sorted_model_answers(turn.model_answers)
            if _is_usable_council_answer(answer)
        )
        lesson, lesson_warnings = self._reconstruct_lesson(chat.id, turn)
        warnings.extend(lesson_warnings)
        attachments, attachment_warnings = self._reconstruct_attachments(chat.id, turn)
        warnings.extend(attachment_warnings)
        has_handoff, handoff, unusual = detect_referenced_chat_handoff(
            turn.custom_instructions
        )
        if unusual:
            warnings.append(
                PlaybookSourceWarning(
                    code=WARNING_UNUSUAL_HANDOFF,
                    message=(
                        "Continuation handoff header matched with unusual spacing; "
                        "block extracted defensively. No native referenced_chat_id exists."
                    ),
                    chat_id=chat.id,
                    turn_id=turn.id,
                    source_id=turn.id,
                )
            )
        reconstructed = PlaybookTurnSource(
            turn_id=turn.id,
            created_at=turn.created_at,
            status=_enum_value(turn.status),
            user_message=turn.user_message,
            custom_instructions=turn.custom_instructions,
            has_referenced_chat_handoff=has_handoff,
            referenced_chat_handoff=handoff,
            model_set_id=turn.model_set_id,
            strategy=_enum_value(turn.strategy),
            verdict_model=turn.verdict_model,
            council_answers=council_answers,
            verdict=verdict,
            lesson=lesson,
            attachments=tuple(attachments),
            content_hash="",
            warnings=tuple(warnings),
        )
        return replace(
            reconstructed,
            content_hash=self.compute_turn_content_hash(reconstructed),
        )

    def _reconstruct_lesson(
        self, chat_id: str, turn: Turn
    ) -> tuple[PlaybookLessonSource | None, list[PlaybookSourceWarning]]:
        lesson = turn.lesson
        if lesson is None:
            return None, []
        status = _enum_value(lesson.status)
        if status != LessonStatus.COMPLETED.value:
            return None, []
        messages, warnings = _normalize_discussion_messages(
            lesson.discussion_messages,
            chat_id=chat_id,
            turn_id=turn.id,
            lesson_id=lesson.id,
        )
        return (
            PlaybookLessonSource(
                lesson_id=lesson.id,
                status=status,
                disagreement_reason=lesson.disagreement_reason,
                user_position=lesson.user_position,
                discussion_messages=tuple(messages),
                created_at=lesson.created_at,
                updated_at=lesson.updated_at,
            ),
            warnings,
        )

    def _reconstruct_attachments(
        self, chat_id: str, turn: Turn
    ) -> tuple[list[PlaybookAttachmentSource], list[PlaybookSourceWarning]]:
        warnings: list[PlaybookSourceWarning] = []
        attachments: list[PlaybookAttachmentSource] = []
        for row in _sorted_attachments(turn.attachments):
            if row.turn_id is None:
                continue
            ready = row.excerpt_status == READY_ATTACHMENT_STATUS
            if not ready:
                warnings.append(
                    PlaybookSourceWarning(
                        code=WARNING_ATTACHMENT_NOT_READY,
                        message=(
                            "Attachment excerpt is not ready; metadata preserved "
                            "without treating text as extraction content."
                        ),
                        chat_id=chat_id,
                        turn_id=turn.id,
                        source_id=row.id,
                    )
                )
            attachments.append(
                PlaybookAttachmentSource(
                    attachment_id=row.id,
                    filename=row.filename,
                    content_type=row.content_type,
                    excerpt_status=row.excerpt_status,
                    text_excerpt=row.text_excerpt if ready else None,
                    created_at=row.created_at,
                    excerpt_is_ready=ready,
                )
            )
        return attachments, warnings

    def _turn_hash_payload(self, turn: PlaybookTurnSource) -> dict[str, Any]:
        ready_attachments = [
            {
                "attachment_id": attachment.attachment_id,
                "content_type": attachment.content_type,
                "excerpt_status": attachment.excerpt_status,
                "filename": attachment.filename,
                "text_excerpt": attachment.text_excerpt,
            }
            for attachment in turn.attachments
            if attachment.excerpt_is_ready
        ]
        lesson_payload = None
        if turn.lesson is not None:
            lesson_payload = {
                "disagreement_reason": turn.lesson.disagreement_reason,
                "discussion_messages": [
                    {"content": message.content, "role": message.role}
                    for message in turn.lesson.discussion_messages
                ],
                "lesson_id": turn.lesson.lesson_id,
                "status": turn.lesson.status,
                "user_position": turn.lesson.user_position,
            }
        return {
            "attachments": ready_attachments,
            "council_answers": [
                {
                    "confidence": answer.confidence,
                    "model_answer_id": answer.model_answer_id,
                    "model_id": answer.model_id,
                    "status": answer.status,
                    "text": answer.text,
                }
                for answer in turn.council_answers
            ],
            "custom_instructions": turn.custom_instructions,
            "lesson": lesson_payload,
            "model_set_id": turn.model_set_id,
            "status": turn.status,
            "strategy": turn.strategy,
            "user_message": turn.user_message,
            "verdict": {
                "reason": turn.verdict.reason,
                "text": turn.verdict.text,
                "verdict_id": turn.verdict.verdict_id,
            },
            "verdict_model": turn.verdict_model,
        }


def _joined_size(current_size: int, added_size: int) -> int:
    if current_size <= 0:
        return added_size
    return current_size + len("\n\n") + added_size


def _is_usable_council_answer(answer: ModelAnswer) -> bool:
    if answer.status not in USABLE_COUNCIL_STATUSES:
        return False
    return bool((answer.text or "").strip())


def _sorted_model_answers(answers: Sequence[ModelAnswer] | None) -> list[ModelAnswer]:
    return sorted(
        answers or [],
        key=lambda answer: (answer.created_at, answer.id),
    )


def _sorted_attachments(
    attachments: Sequence[ChatAttachment] | None,
) -> list[ChatAttachment]:
    return sorted(
        attachments or [],
        key=lambda row: (row.created_at, row.id),
    )


def _normalize_discussion_messages(
    raw: Any,
    *,
    chat_id: str,
    turn_id: str,
    lesson_id: str,
) -> tuple[list[PlaybookDiscussionMessage], list[PlaybookSourceWarning]]:
    warnings: list[PlaybookSourceWarning] = []
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        warnings.append(
            PlaybookSourceWarning(
                code=WARNING_MALFORMED_LESSON,
                message="Lesson discussion_messages is not a list; skipped without mutation.",
                chat_id=chat_id,
                turn_id=turn_id,
                source_id=lesson_id,
            )
        )
        return [], warnings

    messages: list[PlaybookDiscussionMessage] = []
    skipped = 0
    for entry in raw:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        role = entry.get("role")
        content = entry.get("content")
        if not isinstance(role, str) or not role.strip():
            skipped += 1
            continue
        if not isinstance(content, str):
            skipped += 1
            continue
        messages.append(PlaybookDiscussionMessage(role=role, content=content))
    if skipped:
        warnings.append(
            PlaybookSourceWarning(
                code=WARNING_MALFORMED_LESSON,
                message=(
                    f"Skipped {skipped} malformed lesson discussion message(s); "
                    "valid role/content entries were preserved."
                ),
                chat_id=chat_id,
                turn_id=turn_id,
                source_id=lesson_id,
            )
        )
    return messages, warnings


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


playbook_source_service = PlaybookSourceService()
