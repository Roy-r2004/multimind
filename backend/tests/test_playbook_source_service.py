"""My Playbooks Phase 2: source eligibility, reconstruction, hashing, and batching."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.db.models import (
    BrainKnowledgeItem,
    Chat,
    ChatAttachment,
    LessonStatus,
    ModelAnswer,
    ModelAnswerStatus,
    OrgMembership,
    OrgRole,
    Organization,
    Playbook,
    PlaybookObservation,
    PlaybookObservationSource,
    PlaybookRun,
    PlaybookSourceState,
    ShareLink,
    Strategy,
    Turn,
    TurnStatus,
    User,
    UserBrain,
    Verdict,
    VerdictLesson,
)
from app.services.brain_knowledge_service import brain_knowledge_service
from app.services.chat_memory_service import CONTINUATION_HANDOFF_HEADER
from app.services.multi_reference_context_service import MULTI_REFERENCE_HEADER
from app.services.chat_vision import ensure_image_context_for_turn
from app.services.playbook_service import playbook_service
from app.services.playbook_source_service import (
    PLAYBOOK_SOURCE_BATCH_MAX_CHARS,
    PlaybookTurnSource,
    estimate_rendered_characters,
    playbook_source_service,
    sha256_hex,
)
from tests.conftest import create_other_auth


async def _same_org_other_user(db: AsyncSession, auth: AuthContext) -> AuthContext:
    user = User(email="playbook-src-peer@example.com", hashed_password="x", full_name="Peer")
    db.add(user)
    await db.flush()
    db.add(OrgMembership(org_id=auth.org_id, user_id=user.id, role=OrgRole.MEMBER))
    await db.flush()
    return AuthContext(user=user, org_id=auth.org_id, role=OrgRole.MEMBER)


async def _same_user_other_org(db: AsyncSession, auth: AuthContext) -> AuthContext:
    org = Organization(name="Playbook Source Org Two", slug="playbook-src-org-two")
    db.add(org)
    await db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=auth.user.id, role=OrgRole.MEMBER))
    await db.flush()
    return AuthContext(user=auth.user, org_id=org.id, role=OrgRole.MEMBER)


async def _make_chat(
    db: AsyncSession,
    auth: AuthContext,
    *,
    title: str = "Source chat",
    created_at: datetime | None = None,
    project_id: str | None = None,
    created_by: str | None = None,
    org_id: str | None = None,
    rolling_memory: str | None = None,
) -> Chat:
    chat = Chat(
        org_id=org_id or auth.org_id,
        created_by=created_by or auth.user.id,
        title=title,
        project_id=project_id,
        rolling_memory=rolling_memory,
    )
    if created_at is not None:
        chat.created_at = created_at
        chat.updated_at = created_at
    db.add(chat)
    await db.flush()
    return chat


async def _make_turn(
    db: AsyncSession,
    chat: Chat,
    *,
    user_message: str = "Hello",
    status: TurnStatus = TurnStatus.COMPLETED,
    with_verdict: bool = True,
    verdict_text: str = "The verdict",
    verdict_reason: str = "The reason",
    custom_instructions: str | None = None,
    error_message: str | None = None,
    deleted_at: datetime | None = None,
    created_at: datetime | None = None,
    model_set_id: str = "research-set",
    strategy: Strategy = Strategy.SYNTHESIZE,
    verdict_model: str = "gpt-4.1",
    answers: list[dict] | None = None,
) -> Turn:
    turn = Turn(
        chat_id=chat.id,
        user_message=user_message,
        model_set_id=model_set_id,
        strategy=strategy,
        verdict_model=verdict_model,
        status=status,
        custom_instructions=custom_instructions,
        error_message=error_message,
        deleted_at=deleted_at,
    )
    if created_at is not None:
        turn.created_at = created_at
        turn.updated_at = created_at
    db.add(turn)
    await db.flush()
    if answers is None:
        answers = [
            {
                "model_id": "gpt-4.1",
                "text": "Council says yes",
                "status": ModelAnswerStatus.COMPLETED,
                "confidence": 80,
            }
        ]
    for index, spec in enumerate(answers):
        answer = ModelAnswer(
            turn_id=turn.id,
            model_id=spec["model_id"],
            text=spec.get("text"),
            status=spec.get("status", ModelAnswerStatus.COMPLETED),
            confidence=spec.get("confidence"),
        )
        if spec.get("created_at") is not None:
            answer.created_at = spec["created_at"]
            answer.updated_at = spec["created_at"]
        elif created_at is not None:
            answer.created_at = created_at + timedelta(seconds=index + 1)
        db.add(answer)
    if with_verdict:
        verdict = Verdict(
            turn_id=turn.id,
            model_id=verdict_model,
            strategy=strategy,
            text=verdict_text,
            reason=verdict_reason,
        )
        if created_at is not None:
            verdict.created_at = created_at + timedelta(seconds=30)
        db.add(verdict)
    await db.flush()
    return turn


async def _make_lesson(
    db: AsyncSession,
    auth: AuthContext,
    turn: Turn,
    chat: Chat,
    *,
    status: LessonStatus = LessonStatus.COMPLETED,
    disagreement_reason: str = "Too long",
    user_position: str = "Be brief",
    discussion_messages: list | None = None,
) -> VerdictLesson:
    lesson = VerdictLesson(
        turn_id=turn.id,
        chat_id=chat.id,
        org_id=auth.org_id,
        user_id=auth.user.id,
        user_name=auth.user.full_name or "User",
        user_message=turn.user_message,
        disagreement_reason=disagreement_reason,
        user_position=user_position,
        verdict_model_id=turn.verdict_model,
        verdict_model_name="GPT",
        verdict_text="The verdict",
        verdict_reason="The reason",
        strategy=turn.strategy,
        title="Lesson",
        summary="Corrected length",
        comparison={},
        discussion_messages=discussion_messages
        if discussion_messages is not None
        else [{"role": "user", "content": "Please shorten it"}],
        status=status,
    )
    db.add(lesson)
    await db.flush()
    return lesson


async def _make_attachment(
    db: AsyncSession,
    auth: AuthContext,
    chat: Chat,
    *,
    turn_id: str | None,
    filename: str = "notes.txt",
    content_type: str = "text/plain",
    excerpt_status: str = "ready",
    text_excerpt: str | None = "Attachment excerpt",
) -> ChatAttachment:
    row = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        turn_id=turn_id,
        filename=filename,
        stored_name=filename,
        content_type=content_type,
        size_bytes=12,
        relative_path=f"attachments/{filename}",
        text_excerpt=text_excerpt,
        excerpt_status=excerpt_status,
    )
    db.add(row)
    await db.flush()
    return row


async def _make_brain(
    db: AsyncSession,
    auth: AuthContext,
    *,
    org_id: str | None = None,
    summary: str = "Prefers concise answers",
    thinking_style: str = "direct",
    likes: list[str] | None = None,
    dislikes: list[str] | None = None,
    memories: list[dict] | None = None,
    lesson_count: int = 2,
) -> UserBrain:
    brain = UserBrain(
        user_id=auth.user.id,
        org_id=org_id or auth.org_id,
        user_name=auth.user.full_name or "User",
        summary=summary,
        thinking_style=thinking_style,
        likes=likes or ["clarity"],
        dislikes=dislikes or ["fluff"],
        memories=memories or [{"id": "m1", "title": "keep", "insight": "stay brief"}],
        lesson_count=lesson_count,
    )
    db.add(brain)
    await db.flush()
    return brain


async def _make_knowledge(
    db: AsyncSession,
    auth: AuthContext,
    *,
    source_type: str = "saved_document",
    source_id: str = "doc-1",
    title: str = "Doc",
    content: str = "Knowledge content",
    metadata: dict | None = None,
    embedding: list[float] | None = None,
    org_id: str | None = None,
    user_id: str | None = None,
) -> BrainKnowledgeItem:
    item = BrainKnowledgeItem(
        org_id=org_id or auth.org_id,
        user_id=user_id or auth.user.id,
        source_type=source_type,
        source_id=source_id,
        title=title,
        content=content,
        metadata_json=metadata or {"k": "v"},
        embedding=embedding,
    )
    db.add(item)
    await db.flush()
    return item


def _first_turn(transcript_set) -> PlaybookTurnSource:
    assert transcript_set.chats
    assert transcript_set.chats[0].turns
    return transcript_set.chats[0].turns[0]


@pytest.mark.asyncio
async def test_ownership_filters_chats_at_sql_level(db: AsyncSession, auth: AuthContext):
    mine = await _make_chat(db, auth, title="Mine")
    await _make_turn(db, mine)

    peer = await _same_org_other_user(db, auth)
    peer_chat = await _make_chat(db, peer, title="Peer chat")
    await _make_turn(db, peer_chat)

    other_org = await create_other_auth(db)
    foreign = await _make_chat(db, other_org, title="Other org")
    await _make_turn(db, foreign)

    other_org_auth = await _same_user_other_org(db, auth)
    second_org_chat = await _make_chat(db, other_org_auth, title="Same user other org")
    await _make_turn(db, second_org_chat)

    db.add(ShareLink(chat_id=peer_chat.id, token="shared-token", created_by=peer.user.id))
    await db.flush()

    chats = await playbook_source_service.list_eligible_chats(db, auth)
    assert [chat.id for chat in chats] == [mine.id]

    other_org_chats = await playbook_source_service.list_eligible_chats(db, other_org_auth)
    assert [chat.id for chat in other_org_chats] == [second_org_chat.id]

    peer_chats = await playbook_source_service.list_eligible_chats(db, peer)
    assert [chat.id for chat in peer_chats] == [peer_chat.id]


@pytest.mark.asyncio
async def test_turn_eligibility_includes_completed_and_partial_with_verdicts(
    db: AsyncSession, auth: AuthContext
):
    from app.services.chat_memory_service import CHALLENGE_TURN_MARKER

    chat = await _make_chat(db, auth)
    completed = await _make_turn(db, chat, user_message="done", status=TurnStatus.COMPLETED)
    partial = await _make_turn(db, chat, user_message="partial", status=TurnStatus.PARTIAL)
    await _make_turn(
        db, chat, user_message="no verdict", status=TurnStatus.COMPLETED, with_verdict=False, answers=[]
    )
    await _make_turn(db, chat, user_message="pending", status=TurnStatus.PENDING, with_verdict=False)
    await _make_turn(db, chat, user_message="running", status=TurnStatus.RUNNING, with_verdict=False)
    await _make_turn(db, chat, user_message="failed", status=TurnStatus.FAILED, with_verdict=False)
    await _make_turn(
        db,
        chat,
        user_message="deleted",
        deleted_at=datetime.now(UTC),
    )
    await _make_turn(
        db,
        chat,
        user_message="challenge",
        error_message=CHALLENGE_TURN_MARKER,
    )
    superseded = await _make_turn(db, chat, user_message="old regenerated")
    superseded.deleted_at = datetime.now(UTC)
    replacement = await _make_turn(db, chat, user_message="new regenerated")
    await db.flush()

    turns = await playbook_source_service.list_eligible_turns(db, auth)
    ids = [turn.id for turn in turns]
    messages = [turn.user_message for turn in turns]
    assert completed.id in ids
    assert partial.id in ids
    assert replacement.id in ids
    assert "no verdict" not in messages
    assert "pending" not in messages
    assert "running" not in messages
    assert "failed" not in messages
    assert "deleted" not in messages
    assert "challenge" not in messages
    assert superseded.id not in ids
    assert "old regenerated" not in messages


@pytest.mark.asyncio
async def test_stable_ordering_for_chats_turns_and_council_answers(
    db: AsyncSession, auth: AuthContext
):
    later = datetime(2026, 1, 2, tzinfo=UTC)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    same = datetime(2026, 1, 3, tzinfo=UTC)

    chat_b = await _make_chat(db, auth, title="B", created_at=later)
    chat_a = await _make_chat(db, auth, title="A", created_at=earlier)
    chat_d = await _make_chat(db, auth, title="D", created_at=same)
    chat_c = await _make_chat(db, auth, title="C", created_at=same)
    # Force ID ordering for the same created_at pair.
    if chat_c.id > chat_d.id:
        chat_c, chat_d = chat_d, chat_c

    turn_b = await _make_turn(
        db,
        chat_a,
        user_message="second",
        created_at=later,
        answers=[
            {
                "model_id": "claude",
                "text": "later answer",
                "created_at": later + timedelta(seconds=2),
            },
            {
                "model_id": "gpt-4.1",
                "text": "earlier answer",
                "created_at": later + timedelta(seconds=1),
            },
        ],
    )
    turn_a = await _make_turn(db, chat_a, user_message="first", created_at=earlier)
    await _make_turn(db, chat_b, user_message="b")
    await _make_turn(db, chat_c, user_message="c")
    await _make_turn(db, chat_d, user_message="d")

    first = await playbook_source_service.assemble_all_transcripts(db, auth)
    second = await playbook_source_service.assemble_all_transcripts(db, auth)
    chat_ids = [chat.chat_id for chat in first.chats]
    assert chat_ids == [chat_a.id, chat_b.id, chat_c.id, chat_d.id]
    a_turns = next(chat for chat in first.chats if chat.chat_id == chat_a.id).turns
    assert [turn.turn_id for turn in a_turns] == [turn_a.id, turn_b.id]
    answers = next(turn for turn in a_turns if turn.turn_id == turn_b.id).council_answers
    assert [answer.model_id for answer in answers] == ["gpt-4.1", "claude"]
    assert [chat.chat_id for chat in second.chats] == chat_ids
    assert [turn.turn_id for chat in second.chats for turn in chat.turns] == [
        turn.turn_id for chat in first.chats for turn in chat.turns
    ]
    assert [
        answer.model_answer_id
        for chat in second.chats
        for turn in chat.turns
        for answer in turn.council_answers
    ] == [
        answer.model_answer_id
        for chat in first.chats
        for turn in chat.turns
        for answer in turn.council_answers
    ]


@pytest.mark.asyncio
async def test_core_reconstruction_preserves_persisted_fields(
    db: AsyncSession, auth: AuthContext
):
    chat = await _make_chat(db, auth, title="Core")
    instructions = "Use short bullets.\nKeep citations."
    await _make_turn(
        db,
        chat,
        user_message="How should I write?",
        status=TurnStatus.COMPLETED,
        custom_instructions=instructions,
        model_set_id="research-set",
        strategy=Strategy.REFEREE,
        verdict_model="claude",
        verdict_text="Write in short bullets.",
        verdict_reason="User asked for brevity.",
        answers=[
            {
                "model_id": "gpt-4.1",
                "text": "Use bullets",
                "status": ModelAnswerStatus.COMPLETED,
                "confidence": 70,
            },
            {
                "model_id": "claude",
                "text": "",
                "status": ModelAnswerStatus.COMPLETED,
            },
            {
                "model_id": "gemini",
                "text": "failed body",
                "status": ModelAnswerStatus.FAILED,
            },
            {
                "model_id": "pending-model",
                "text": "not yet",
                "status": ModelAnswerStatus.PENDING,
            },
        ],
    )
    assembled = await playbook_source_service.assemble_all_transcripts(db, auth)
    turn = _first_turn(assembled)
    assert assembled.chats[0].chat_title == "Core"
    assert assembled.chats[0].project_id is None
    assert turn.user_message == "How should I write?"
    assert turn.status == "completed"
    assert turn.custom_instructions == instructions
    assert turn.model_set_id == "research-set"
    assert turn.strategy == "Referee"
    assert turn.verdict_model == "claude"
    assert [answer.model_id for answer in turn.council_answers] == ["gpt-4.1"]
    assert turn.council_answers[0].text == "Use bullets"
    assert turn.council_answers[0].status == "completed"
    assert turn.verdict.text == "Write in short bullets."
    assert turn.verdict.reason == "User asked for brevity."
    assert "Synthesized from model responses." not in turn.verdict.text
    assert "Synthesized from model responses." not in turn.verdict.reason


@pytest.mark.asyncio
async def test_empty_verdict_reason_is_preserved_without_fallback(
    db: AsyncSession, auth: AuthContext
):
    chat = await _make_chat(db, auth)
    await _make_turn(db, chat, verdict_text="Just the verdict", verdict_reason="")
    turn = _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth))
    assert turn.verdict.text == "Just the verdict"
    assert turn.verdict.reason == ""


@pytest.mark.asyncio
async def test_lesson_reconstruction_and_malformed_discussion_handling(
    db: AsyncSession, auth: AuthContext
):
    from app.services.chat_memory_service import CHALLENGE_TURN_MARKER

    chat = await _make_chat(db, auth)
    turn = await _make_turn(db, chat, user_message="Original question")
    raw_messages = [
        {"role": "user", "content": "Please shorten it"},
        {"role": "assistant", "content": "Understood"},
        "not-an-object",
        {"role": "user"},
        {"content": "missing role"},
        {"role": "", "content": "blank role"},
        {"role": "user", "content": 12},
    ]
    lesson = await _make_lesson(
        db,
        auth,
        turn,
        chat,
        disagreement_reason="Too long",
        user_position="Be brief",
        discussion_messages=raw_messages,
    )
    await _make_lesson(
        db,
        auth,
        await _make_turn(db, chat, user_message="still discussing"),
        chat,
        status=LessonStatus.DISCUSSING,
        disagreement_reason="Ignore me",
        user_position="Not ready",
    )
    await _make_turn(
        db,
        chat,
        user_message="hidden challenge",
        error_message=CHALLENGE_TURN_MARKER,
    )
    assembled = await playbook_source_service.assemble_all_transcripts(db, auth)
    reconstructed = next(
        item for item in assembled.chats[0].turns if item.turn_id == turn.id
    )
    assert reconstructed.lesson is not None
    assert reconstructed.lesson.lesson_id == lesson.id
    assert reconstructed.lesson.status == "completed"
    assert reconstructed.lesson.disagreement_reason == "Too long"
    assert reconstructed.lesson.user_position == "Be brief"
    assert [
        (message.role, message.content) for message in reconstructed.lesson.discussion_messages
    ] == [
        ("user", "Please shorten it"),
        ("assistant", "Understood"),
    ]
    assert any(warning.code == "malformed_lesson_discussion" for warning in reconstructed.warnings)
    assert any(warning.code == "malformed_lesson_discussion" for warning in assembled.warnings)
    discussing = next(
        item for item in assembled.chats[0].turns if item.user_message == "still discussing"
    )
    assert discussing.lesson is None
    assert all(item.user_message != "hidden challenge" for item in assembled.chats[0].turns)
    stored = await db.get(VerdictLesson, lesson.id)
    assert stored.discussion_messages == raw_messages


@pytest.mark.asyncio
async def test_attachment_reconstruction_ready_pending_and_non_ready(
    db: AsyncSession, auth: AuthContext
):
    chat = await _make_chat(db, auth)
    turn = await _make_turn(db, chat)
    ready = await _make_attachment(
        db,
        auth,
        chat,
        turn_id=turn.id,
        filename="ready.txt",
        excerpt_status="ready",
        text_excerpt="Ready excerpt",
    )
    failed = await _make_attachment(
        db,
        auth,
        chat,
        turn_id=turn.id,
        filename="failed.txt",
        excerpt_status="failed",
        text_excerpt="should not be extraction text",
    )
    pending = await _make_attachment(
        db,
        auth,
        chat,
        turn_id=None,
        filename="pending.txt",
        excerpt_status="ready",
        text_excerpt="composer only",
    )
    assembled = await playbook_source_service.assemble_all_transcripts(db, auth)
    turn_source = _first_turn(assembled)
    by_name = {item.filename: item for item in turn_source.attachments}
    assert "pending.txt" not in by_name
    assert by_name["ready.txt"].attachment_id == ready.id
    assert by_name["ready.txt"].excerpt_is_ready is True
    assert by_name["ready.txt"].text_excerpt == "Ready excerpt"
    assert by_name["ready.txt"].content_type == "text/plain"
    assert by_name["failed.txt"].attachment_id == failed.id
    assert by_name["failed.txt"].excerpt_is_ready is False
    assert by_name["failed.txt"].text_excerpt is None
    assert by_name["failed.txt"].excerpt_status == "failed"
    assert any(warning.code == "attachment_excerpt_not_ready" for warning in turn_source.warnings)
    reloaded = await db.get(ChatAttachment, failed.id)
    assert reloaded.text_excerpt == "should not be extraction text"
    assert reloaded.excerpt_status == "failed"
    still_pending = await db.get(ChatAttachment, pending.id)
    assert still_pending.turn_id is None


@pytest.mark.asyncio
async def test_referenced_chat_handoff_detection(db: AsyncSession, auth: AuthContext):
    chat = await _make_chat(db, auth)
    original = (
        f"{CONTINUATION_HANDOFF_HEADER}\n"
        "The user is continuing a previous conversation from chat 'Alpha'.\n\n"
        "### Recent turns from previous chat\n"
        "User question:\nKeep going\n\n"
        "Attached file:\nnotes.txt\nReady excerpt"
    )
    unusual = (
        "Prefix notes\n"
        "##  MultiMind  Continuation  Handoff\n"
        "Continuing with extra spaces.\n\n"
        "IMAGE CONTEXT\n"
        "Image 1: ignored for boundary"
    )
    await _make_turn(db, chat, user_message="with handoff", custom_instructions=original)
    await _make_turn(db, chat, user_message="unusual", custom_instructions=unusual)
    await _make_turn(
        db,
        chat,
        user_message="multi handoff",
        custom_instructions=(
            f"{MULTI_REFERENCE_HEADER}\n\n### Source 1 — Alpha\n- [USER STATED] Fact"
            "\n\nAttached file:\nnotes.txt\nignored"
        ),
    )
    await _make_turn(db, chat, user_message="none", custom_instructions="Just be concise.")

    assembled = await playbook_source_service.assemble_all_transcripts(db, auth)
    by_message = {turn.user_message: turn for turn in assembled.chats[0].turns}

    marked = by_message["with handoff"]
    assert marked.has_referenced_chat_handoff is True
    assert marked.referenced_chat_handoff is not None
    assert CONTINUATION_HANDOFF_HEADER in marked.referenced_chat_handoff
    assert "Keep going" in marked.referenced_chat_handoff
    assert "Attached file:" not in marked.referenced_chat_handoff
    assert marked.custom_instructions == original
    assert not hasattr(marked, "referenced_chat_id")

    spaced = by_message["unusual"]
    assert spaced.has_referenced_chat_handoff is True
    assert "Continuing with extra spaces." in (spaced.referenced_chat_handoff or "")
    assert "IMAGE CONTEXT" not in (spaced.referenced_chat_handoff or "")
    assert spaced.custom_instructions == unusual
    assert any(warning.code == "unusual_continuation_handoff" for warning in spaced.warnings)

    multi = by_message["multi handoff"]
    assert multi.has_referenced_chat_handoff is True
    assert MULTI_REFERENCE_HEADER in (multi.referenced_chat_handoff or "")
    assert "Attached file:" not in (multi.referenced_chat_handoff or "")

    none = by_message["none"]
    assert none.has_referenced_chat_handoff is False
    assert none.referenced_chat_handoff is None
    assert none.custom_instructions == "Just be concise."


@pytest.mark.asyncio
async def test_voice_and_image_sources_use_persisted_text_only(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    vision = AsyncMock(side_effect=AssertionError("vision must not be invoked"))
    monkeypatch.setattr(
        "app.services.chat_vision.ensure_image_context_for_turn", vision
    )
    monkeypatch.setattr(
        "app.services.playbook_source_service.ensure_image_context_for_turn",
        vision,
        raising=False,
    )

    chat = await _make_chat(db, auth)
    turn = await _make_turn(
        db,
        chat,
        user_message="Transcribed from voice: schedule the review",
        custom_instructions="IMAGE CONTEXT\nA whiteboard photo was attached.",
    )
    await _make_attachment(
        db,
        auth,
        chat,
        turn_id=turn.id,
        filename="board.png",
        content_type="image/png",
        excerpt_status="pending",
        text_excerpt="should not be used",
    )
    await _make_attachment(
        db,
        auth,
        chat,
        turn_id=turn.id,
        filename="ready.png",
        content_type="image/png",
        excerpt_status="ready",
        text_excerpt="A red circle on a whiteboard.",
    )
    assembled = await playbook_source_service.assemble_all_transcripts(db, auth)
    reconstructed = _first_turn(assembled)
    assert reconstructed.user_message == "Transcribed from voice: schedule the review"
    field_names = set(reconstructed.__dataclass_fields__)
    assert "audio" not in field_names
    assert "voice" not in field_names
    assert "recording" not in field_names
    by_name = {item.filename: item for item in reconstructed.attachments}
    assert by_name["board.png"].content_type == "image/png"
    assert by_name["board.png"].excerpt_is_ready is False
    assert by_name["board.png"].text_excerpt is None
    assert by_name["ready.png"].excerpt_is_ready is True
    assert by_name["ready.png"].text_excerpt == "A red circle on a whiteboard."
    assert "IMAGE CONTEXT" in (reconstructed.custom_instructions or "")
    vision.assert_not_called()
    assert ensure_image_context_for_turn is not None


@pytest.mark.asyncio
async def test_brain_snapshot_is_user_global_and_org_scoped_for_knowledge(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    retrieve = AsyncMock(side_effect=AssertionError("relevance retrieval must not run"))
    monkeypatch.setattr(brain_knowledge_service, "retrieve", retrieve)

    other_org_auth = await _same_user_other_org(db, auth)
    peer = await _same_org_other_user(db, auth)
    brain = await _make_brain(db, auth, org_id=auth.org_id, summary="Global brain")
    mine = await _make_knowledge(
        db, auth, source_id="mine", content="Current org knowledge"
    )
    await _make_knowledge(
        db,
        auth,
        source_id="other-org",
        content="Other org knowledge",
        org_id=other_org_auth.org_id,
    )
    await _make_knowledge(
        db,
        peer,
        source_id="peer",
        content="Peer knowledge",
        user_id=peer.user.id,
    )
    chat = await _make_chat(db, auth, rolling_memory="ROLLING_MEMORY_SECRET")
    await _make_turn(db, chat)

    snapshot = await playbook_source_service.build_brain_source_snapshot(db, auth)
    transcripts = await playbook_source_service.assemble_all_transcripts(db, auth)

    assert snapshot.user_brain is not None
    assert snapshot.user_brain_is_global is True
    assert snapshot.user_brain.is_user_global is True
    assert snapshot.user_brain.id == brain.id
    assert snapshot.user_brain.user_id == auth.user.id
    assert snapshot.user_brain.summary == "Global brain"
    assert [item.id for item in snapshot.knowledge_items] == [mine.id]
    assert snapshot.knowledge_items[0].content == "Current org knowledge"
    assert all("ROLLING_MEMORY_SECRET" not in str(chat) for chat in transcripts.chats)
    assert all(
        not hasattr(chat, "rolling_memory") for chat in transcripts.chats
    )
    retrieve.assert_not_called()

    other_snapshot = await playbook_source_service.build_brain_source_snapshot(
        db, other_org_auth
    )
    assert other_snapshot.user_brain is not None
    assert other_snapshot.user_brain.id == brain.id
    assert [item.content for item in other_snapshot.knowledge_items] == [
        "Other org knowledge"
    ]

    brain_count = (
        await db.execute(select(func.count()).select_from(UserBrain))
    ).scalar()
    assert brain_count == 1
    reloaded = await db.get(UserBrain, brain.id)
    assert reloaded.summary == "Global brain"


@pytest.mark.asyncio
async def test_missing_user_brain_is_not_created(db: AsyncSession, auth: AuthContext):
    snapshot = await playbook_source_service.build_brain_source_snapshot(db, auth)
    assert snapshot.user_brain is None
    assert snapshot.user_brain_is_global is True
    count = (await db.execute(select(func.count()).select_from(UserBrain))).scalar()
    assert count == 0


@pytest.mark.asyncio
async def test_turn_content_hash_is_deterministic_and_content_sensitive(
    db: AsyncSession, auth: AuthContext
):
    chat = await _make_chat(db, auth, rolling_memory="ignored rolling")
    turn = await _make_turn(
        db,
        chat,
        user_message="Hash me",
        custom_instructions="Be precise",
        verdict_text="Do X",
        verdict_reason="Because Y",
        answers=[
            {
                "model_id": "gpt-4.1",
                "text": "Answer A",
                "status": ModelAnswerStatus.COMPLETED,
                "confidence": 55,
            }
        ],
    )
    await _make_attachment(
        db,
        auth,
        chat,
        turn_id=turn.id,
        filename="notes.txt",
        excerpt_status="ready",
        text_excerpt="Excerpt A",
    )
    await _make_lesson(
        db,
        auth,
        turn,
        chat,
        disagreement_reason="Disagree A",
        user_position="Position A",
        discussion_messages=[{"role": "user", "content": "No"}],
    )
    await db.flush()

    first = _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth))
    assert first.content_hash == playbook_source_service.compute_turn_content_hash(first)
    assert first.content_hash == first.content_hash.lower()
    assert len(first.content_hash) == 64
    assert all(char in "0123456789abcdef" for char in first.content_hash)

    second = _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth))
    assert second.content_hash == first.content_hash

    renamed = first.__class__(**{**first.__dict__, "turn_id": "not-the-turn-id"})
    assert playbook_source_service.compute_turn_content_hash(renamed) == first.content_hash

    chat.rolling_memory = "CHANGED ROLLING"
    chat.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    turn.created_at = datetime(2026, 6, 2, tzinfo=UTC)
    turn.updated_at = datetime(2026, 6, 3, tzinfo=UTC)
    await db.flush()
    after_noise = _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth))
    assert after_noise.content_hash == first.content_hash

    mutations = [
        ("user_message", "Hash me now"),
        ("custom_instructions", "Be precise and short"),
    ]
    for field_name, value in mutations:
        original = getattr(turn, field_name)
        setattr(turn, field_name, value)
        await db.flush()
        changed = _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth))
        assert changed.content_hash != first.content_hash
        setattr(turn, field_name, original)
        await db.flush()

    answer = (
        await db.execute(select(ModelAnswer).where(ModelAnswer.turn_id == turn.id))
    ).scalar_one()
    answer.text = "Answer B"
    await db.flush()
    assert _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth)).content_hash != first.content_hash
    answer.text = "Answer A"
    answer.confidence = 99
    await db.flush()
    assert _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth)).content_hash != first.content_hash
    answer.confidence = 55
    await db.flush()

    verdict = (await db.execute(select(Verdict).where(Verdict.turn_id == turn.id))).scalar_one()
    verdict.text = "Do Z"
    await db.flush()
    assert _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth)).content_hash != first.content_hash
    verdict.text = "Do X"
    verdict.reason = "Because Z"
    await db.flush()
    assert _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth)).content_hash != first.content_hash
    verdict.reason = "Because Y"
    await db.flush()

    attachment = (
        await db.execute(
            select(ChatAttachment).where(ChatAttachment.turn_id == turn.id)
        )
    ).scalar_one()
    attachment.text_excerpt = "Excerpt B"
    await db.flush()
    assert _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth)).content_hash != first.content_hash
    attachment.text_excerpt = "Excerpt A"
    await db.flush()

    lesson = (await db.execute(select(VerdictLesson).where(VerdictLesson.turn_id == turn.id))).scalar_one()
    lesson.disagreement_reason = "Disagree B"
    await db.flush()
    assert _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth)).content_hash != first.content_hash
    lesson.disagreement_reason = "Disagree A"
    lesson.user_position = "Position B"
    await db.flush()
    assert _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth)).content_hash != first.content_hash
    lesson.user_position = "Position A"
    lesson.discussion_messages = [{"role": "user", "content": "Yes"}]
    await db.flush()
    assert _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth)).content_hash != first.content_hash
    lesson.discussion_messages = [{"role": "user", "content": "No"}]
    await db.flush()
    assert _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth)).content_hash == first.content_hash

    assert sha256_hex({"b": 1, "a": 2}) == sha256_hex({"a": 2, "b": 1})

    await db.commit()
    db.expire_all()
    await db.refresh(auth.user)
    other_turn = _first_turn(await playbook_source_service.assemble_all_transcripts(db, auth))
    assert other_turn.content_hash == first.content_hash


@pytest.mark.asyncio
async def test_brain_hashing_ignores_ids_and_embeddings(
    db: AsyncSession, auth: AuthContext
):
    brain = await _make_brain(db, auth, summary="Alpha", thinking_style="careful")
    item = await _make_knowledge(
        db,
        auth,
        content="Body",
        metadata={"z": 1, "a": 2},
        embedding=[0.1, 0.2],
    )
    first_brain = playbook_source_service.compute_user_brain_hash(brain)
    first_item = playbook_source_service.compute_brain_knowledge_item_hash(item)
    assert first_brain == first_brain.lower()
    assert len(first_brain) == 64
    assert playbook_source_service.compute_user_brain_hash(brain) == first_brain
    assert playbook_source_service.compute_brain_knowledge_item_hash(item) == first_item

    brain.summary = "Beta"
    assert playbook_source_service.compute_user_brain_hash(brain) != first_brain
    brain.summary = "Alpha"
    assert playbook_source_service.compute_user_brain_hash(brain) == first_brain

    item.content = "Body changed"
    assert playbook_source_service.compute_brain_knowledge_item_hash(item) != first_item
    item.content = "Body"
    item.embedding = [9.9, 8.8]
    assert playbook_source_service.compute_brain_knowledge_item_hash(item) == first_item
    item.metadata_json = {"a": 2, "z": 1}
    assert playbook_source_service.compute_brain_knowledge_item_hash(item) == first_item


@pytest.mark.asyncio
async def test_batching_preserves_order_and_handles_splits(
    db: AsyncSession, auth: AuthContext
):
    chat_a = await _make_chat(
        db, auth, title="A", created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    chat_b = await _make_chat(
        db, auth, title="B", created_at=datetime(2026, 1, 2, tzinfo=UTC)
    )
    await _make_turn(db, chat_a, user_message="A1 " + ("a" * 40))
    await _make_turn(db, chat_a, user_message="A2 " + ("b" * 40))
    await _make_turn(db, chat_b, user_message="B1 " + ("c" * 40))

    transcripts = await playbook_source_service.assemble_all_transcripts(db, auth)
    turns_a = list(transcripts.chats[0].turns)
    turns_b = list(transcripts.chats[1].turns)
    size_a = estimate_rendered_characters(turns_a)
    size_first_a = estimate_rendered_characters(turns_a[:1])
    size_b = estimate_rendered_characters(turns_b)

    together = playbook_source_service.batch_transcripts(
        transcripts.chats, max_chars=size_a + size_b + 10
    )
    assert len(together) == 1
    assert together[0].chat_ids == (chat_a.id, chat_b.id)
    assert together[0].turn_ids == tuple(turn.turn_id for turn in turns_a + turns_b)
    assert together[0].chat_count == 2
    assert together[0].turn_count == 3
    assert together[0].oversized is False
    assert together[0].estimated_characters == estimate_rendered_characters(
        turns_a + turns_b
    )

    keep_chat = playbook_source_service.batch_transcripts(
        transcripts.chats, max_chars=size_a
    )
    assert [batch.chat_ids for batch in keep_chat] == [(chat_a.id,), (chat_b.id,)]
    assert keep_chat[0].turn_ids == tuple(turn.turn_id for turn in turns_a)

    split = playbook_source_service.batch_transcripts(
        transcripts.chats, max_chars=max(size_first_a, size_b)
    )
    a_batches = [batch for batch in split if chat_a.id in batch.chat_ids]
    assert len(a_batches) == 2
    assert a_batches[0].turn_ids == (turns_a[0].turn_id,)
    assert a_batches[1].turn_ids == (turns_a[1].turn_id,)
    assert chat_a.id in a_batches[0].spanning_chat_ids
    assert chat_a.id in a_batches[1].spanning_chat_ids
    assert all(len(batch.turn_ids) >= 1 for batch in split)
    all_turn_ids = [turn_id for batch in split for turn_id in batch.turn_ids]
    assert all_turn_ids == [
        turns_a[0].turn_id,
        turns_a[1].turn_id,
        turns_b[0].turn_id,
    ]

    huge_chat = await _make_chat(
        db, auth, title="Huge", created_at=datetime(2026, 1, 3, tzinfo=UTC)
    )
    await _make_turn(db, huge_chat, user_message="HUGE " + ("x" * 5000))
    all_transcripts = await playbook_source_service.assemble_all_transcripts(db, auth)
    huge_turn = all_transcripts.chats[-1].turns[0]
    huge_size = estimate_rendered_characters([huge_turn])
    oversized = playbook_source_service.batch_transcripts(
        all_transcripts.chats, max_chars=200
    )
    huge_batch = next(batch for batch in oversized if huge_turn.turn_id in batch.turn_ids)
    assert huge_batch.oversized is True
    assert huge_batch.turn_ids == (huge_turn.turn_id,)
    assert huge_batch.estimated_characters == huge_size
    assert huge_size > 200

    repeated = playbook_source_service.batch_transcripts(
        all_transcripts.chats, max_chars=200
    )
    assert [batch.turn_ids for batch in repeated] == [
        batch.turn_ids for batch in oversized
    ]
    assert [batch.estimated_characters for batch in repeated] == [
        batch.estimated_characters for batch in oversized
    ]
    assert PLAYBOOK_SOURCE_BATCH_MAX_CHARS > 0


@pytest.mark.asyncio
async def test_reconstruction_is_read_only(db: AsyncSession, auth: AuthContext):
    playbook = await playbook_service.get_or_create_for_current_user(db, auth)
    playbook.core_summary = "Do not change"
    playbook.playbook_version = 3
    await db.flush()
    playbook_updated = playbook.updated_at

    chat = await _make_chat(db, auth, rolling_memory="keep memory")
    turn = await _make_turn(
        db,
        chat,
        user_message="immutable",
        custom_instructions="stay",
        verdict_text="unchanged verdict",
        verdict_reason="unchanged reason",
    )
    attachment = await _make_attachment(db, auth, chat, turn_id=turn.id)
    brain = await _make_brain(db, auth)
    knowledge = await _make_knowledge(db, auth, embedding=[1.0, 2.0])
    await db.flush()

    chat_updated = chat.updated_at
    turn_updated = turn.updated_at
    attachment_status = attachment.excerpt_status
    attachment_excerpt = attachment.text_excerpt
    brain_summary = brain.summary
    knowledge_content = knowledge.content
    knowledge_embedding = list(knowledge.embedding or [])

    counts = {
        "playbooks": (await db.execute(select(func.count()).select_from(Playbook))).scalar(),
        "runs": (await db.execute(select(func.count()).select_from(PlaybookRun))).scalar(),
        "observations": (
            await db.execute(select(func.count()).select_from(PlaybookObservation))
        ).scalar(),
        "sources": (
            await db.execute(select(func.count()).select_from(PlaybookObservationSource))
        ).scalar(),
        "states": (
            await db.execute(select(func.count()).select_from(PlaybookSourceState))
        ).scalar(),
        "brains": (await db.execute(select(func.count()).select_from(UserBrain))).scalar(),
        "knowledge": (
            await db.execute(select(func.count()).select_from(BrainKnowledgeItem))
        ).scalar(),
        "attachments": (
            await db.execute(select(func.count()).select_from(ChatAttachment))
        ).scalar(),
    }

    await playbook_source_service.assemble_all_transcripts(db, auth)
    await playbook_source_service.build_brain_source_snapshot(db, auth)
    await playbook_source_service.list_eligible_chats(db, auth)
    await playbook_source_service.list_eligible_turns(db, auth)

    assert (
        await db.execute(select(func.count()).select_from(PlaybookRun))
    ).scalar() == counts["runs"] == 0
    assert (
        await db.execute(select(func.count()).select_from(PlaybookObservation))
    ).scalar() == counts["observations"] == 0
    assert (
        await db.execute(select(func.count()).select_from(PlaybookObservationSource))
    ).scalar() == counts["sources"] == 0
    assert (
        await db.execute(select(func.count()).select_from(PlaybookSourceState))
    ).scalar() == counts["states"] == 0
    reloaded_playbook = await db.get(Playbook, playbook.id)
    assert reloaded_playbook.core_summary == "Do not change"
    assert reloaded_playbook.playbook_version == 3
    assert reloaded_playbook.updated_at == playbook_updated
    reloaded_chat = await db.get(Chat, chat.id)
    assert reloaded_chat.updated_at == chat_updated
    assert reloaded_chat.rolling_memory == "keep memory"
    reloaded_turn = await db.get(Turn, turn.id)
    assert reloaded_turn.user_message == "immutable"
    assert reloaded_turn.custom_instructions == "stay"
    assert reloaded_turn.updated_at == turn_updated
    reloaded_attachment = await db.get(ChatAttachment, attachment.id)
    assert reloaded_attachment.excerpt_status == attachment_status
    assert reloaded_attachment.text_excerpt == attachment_excerpt
    reloaded_brain = await db.get(UserBrain, brain.id)
    assert reloaded_brain.summary == brain_summary
    reloaded_knowledge = await db.get(BrainKnowledgeItem, knowledge.id)
    assert reloaded_knowledge.content == knowledge_content
    assert list(reloaded_knowledge.embedding or []) == knowledge_embedding
    assert (
        await db.execute(select(func.count()).select_from(Playbook))
    ).scalar() == counts["playbooks"]
    assert (
        await db.execute(select(func.count()).select_from(UserBrain))
    ).scalar() == counts["brains"]
    assert (
        await db.execute(select(func.count()).select_from(BrainKnowledgeItem))
    ).scalar() == counts["knowledge"]
    assert (
        await db.execute(select(func.count()).select_from(ChatAttachment))
    ).scalar() == counts["attachments"]
