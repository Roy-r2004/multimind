"""Deterministic incremental diff against the last successfully persisted source states."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.db.models import (
    PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE,
    PLAYBOOK_SOURCE_TYPE_TURN,
    PLAYBOOK_SOURCE_TYPE_USER_BRAIN,
    Chat,
    PlaybookExcludedSource,
    PlaybookSourceState,
    Turn,
)
from app.schemas.api import PlaybookPendingResponse
from app.services.playbook_service import playbook_service
from app.services.playbook_source_service import (
    PlaybookBrainSnapshot,
    PlaybookChatTranscript,
    PlaybookTranscriptSet,
    playbook_source_service,
)


@dataclass(frozen=True)
class PendingDiff:
    transcripts: PlaybookTranscriptSet
    brain: PlaybookBrainSnapshot
    new_turn_ids: frozenset[str]
    changed_turn_ids: frozenset[str]
    removed_turn_ids: frozenset[str]
    brain_changed: bool
    brain_changes: int
    new_chats: int

    @property
    def pending_source_items(self) -> int:
        return (
            len(self.new_turn_ids)
            + len(self.changed_turn_ids)
            + len(self.removed_turn_ids)
            + self.brain_changes
        )


class PlaybookPendingService:
    async def compute(self, db: AsyncSession, auth: AuthContext) -> tuple[object, PendingDiff]:
        playbook = await playbook_service.get_or_create_for_current_user(db, auth)
        transcripts = await playbook_source_service.assemble_all_transcripts(db, auth)
        brain = await playbook_source_service.build_brain_source_snapshot(db, auth)
        rows = (
            (
                await db.execute(
                    select(PlaybookSourceState).where(
                        PlaybookSourceState.playbook_id == playbook.id
                    )
                )
            )
            .scalars()
            .all()
        )
        baseline = {(row.source_type, row.source_id): row.content_hash for row in rows}
        exclusions = (
            (
                await db.execute(
                    select(PlaybookExcludedSource).where(
                        PlaybookExcludedSource.playbook_id == playbook.id
                    )
                )
            )
            .scalars()
            .all()
        )
        excluded_turn_ids = {row.turn_id for row in exclusions if row.turn_id}
        excluded_chat_ids = {row.chat_id for row in exclusions if row.chat_id}
        transcripts = PlaybookTranscriptSet(
            chats=tuple(
                PlaybookChatTranscript(
                    chat_id=chat.chat_id,
                    chat_title=chat.chat_title,
                    project_id=chat.project_id,
                    chat_created_at=chat.chat_created_at,
                    chat_updated_at=chat.chat_updated_at,
                    turns=tuple(
                        turn for turn in chat.turns if turn.turn_id not in excluded_turn_ids
                    ),
                    warnings=chat.warnings,
                )
                for chat in transcripts.chats
                if chat.chat_id not in excluded_chat_ids
            ),
            warnings=transcripts.warnings,
        )
        current_turns = {
            turn.turn_id: turn.content_hash for chat in transcripts.chats for turn in chat.turns
        }
        old_turns = {
            source_id: value
            for (kind, source_id), value in baseline.items()
            if kind == PLAYBOOK_SOURCE_TYPE_TURN
        }
        baseline_turn_chat_rows = (
            await db.execute(
                select(Turn.id, Turn.chat_id)
                .join(Chat, Chat.id == Turn.chat_id)
                .where(
                    Turn.id.in_(tuple(old_turns) or ("",)),
                    Chat.org_id == auth.org_id,
                    Chat.created_by == auth.user.id,
                )
            )
        ).all()
        baseline_turn_chats = {turn_id: chat_id for turn_id, chat_id in baseline_turn_chat_rows}
        old_turns = {
            turn_id: content_hash
            for turn_id, content_hash in old_turns.items()
            if turn_id not in excluded_turn_ids
            and baseline_turn_chats.get(turn_id) not in excluded_chat_ids
        }
        new_ids = frozenset(current_turns.keys() - old_turns.keys())
        changed_ids = frozenset(
            source_id
            for source_id in current_turns.keys() & old_turns.keys()
            if current_turns[source_id] != old_turns[source_id]
        )
        removed_ids = frozenset(old_turns.keys() - current_turns.keys())
        chat_turns = {
            chat.chat_id: {turn.turn_id for turn in chat.turns} for chat in transcripts.chats
        }
        baseline_chat_ids = set(baseline_turn_chats.values())
        new_chats = sum(
            1
            for chat_id, ids in chat_turns.items()
            if ids and ids & new_ids and chat_id not in baseline_chat_ids
        )

        current_brain: dict[tuple[str, str], str] = {}
        if brain.user_brain is not None:
            current_brain[(PLAYBOOK_SOURCE_TYPE_USER_BRAIN, brain.user_brain.id)] = (
                brain.user_brain.content_hash
            )
        current_brain.update(
            {
                (PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE, item.id): item.content_hash
                for item in brain.knowledge_items
            }
        )
        old_brain = {
            key: value
            for key, value in baseline.items()
            if key[0] in {PLAYBOOK_SOURCE_TYPE_USER_BRAIN, PLAYBOOK_SOURCE_TYPE_BRAIN_KNOWLEDGE}
        }
        brain_keys = current_brain.keys() | old_brain.keys()
        brain_changes = sum(1 for key in brain_keys if current_brain.get(key) != old_brain.get(key))
        return playbook, PendingDiff(
            transcripts,
            brain,
            new_ids,
            changed_ids,
            removed_ids,
            brain_changes > 0,
            brain_changes,
            new_chats,
        )

    async def response(self, db: AsyncSession, auth: AuthContext) -> PlaybookPendingResponse:
        playbook, diff = await self.compute(db, auth)
        return PlaybookPendingResponse(
            up_to_date=diff.pending_source_items == 0,
            pending_source_items=diff.pending_source_items,
            new_chats=diff.new_chats,
            new_turns=len(diff.new_turn_ids),
            changed_turns=len(diff.changed_turn_ids),
            removed_turns=len(diff.removed_turn_ids),
            brain_changes=diff.brain_changes,
            last_success_at=playbook.last_success_at,
            playbook_version=playbook.playbook_version,
        )


playbook_pending_service = PlaybookPendingService()
