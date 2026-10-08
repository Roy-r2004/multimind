"""TXT attachment context stays available after the upload turn."""

from __future__ import annotations

from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import NotFoundError
from app.db.models import Chat, ChatAttachment, Turn, TurnStatus
from app.db.session import get_db
from app.llm.prompt_engine import PromptEngine
from app.main import create_app
from app.schemas.api import TurnCreateRequest, TurnRegenerateRequest
from app.services.chat_attachment_text import decode_plain_text_bytes, extract_attachment_text
from app.services.chat_memory_service import (
    CONTINUATION_ATTACHMENT_HEADER,
    CONTINUATION_ATTACHMENT_PER_FILE_CHARS,
    CONTINUATION_HANDOFF_HEADER,
    CONTINUATION_HANDOFF_MAX_CHARS,
    TurnHistoryEntry,
    build_continuation_handoff_text,
    chat_memory_service,
    format_bounded_txt_attachment_section,
)
from app.services.chat_service import chat_service
from tests.conftest import create_model_set


async def _create_chat(db: AsyncSession, auth: AuthContext) -> Chat:
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="TXT chat")
    db.add(chat)
    await db.flush()
    return chat


async def _client_for(db: AsyncSession, auth: AuthContext) -> AsyncClient:
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = lambda: auth
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_plain_text_encodings_round_trip():
    text = "Beirut stays the capital.\nLine two."
    cases = {
        "utf-8": text.encode("utf-8"),
        "utf-8-bom": b"\xef\xbb\xbf" + text.encode("utf-8"),
        "utf-16-le": text.encode("utf-16"),
        "utf-16-be": text.encode("utf-16-be"),
    }
    # utf-16-be from encode includes no BOM; add one.
    cases["utf-16-be"] = b"\xfe\xff" + text.encode("utf-16-be")
    for label, raw in cases.items():
        decoded = decode_plain_text_bytes(raw)
        assert "Beirut" in decoded, label
        assert "\x00" not in decoded, label
        excerpt, status = extract_attachment_text(raw, ".txt")
        assert status == "ready", label
        assert excerpt is not None and "Beirut" in excerpt

    plain = "hello world".encode("utf-8")
    assert decode_plain_text_bytes(plain) == "hello world"
    assert "\x00" not in decode_plain_text_bytes(text.encode("utf-16"))


def test_large_txt_excerpt_keeps_a_bounded_prefix():
    raw = ("A" * 50).encode("utf-8")
    from app.services import chat_attachment_text as text_mod

    original = text_mod.ATTACHMENT_TEXT_EXCERPT_MAX
    text_mod.ATTACHMENT_TEXT_EXCERPT_MAX = 20
    try:
        excerpt, status = extract_attachment_text(raw, ".txt")
    finally:
        text_mod.ATTACHMENT_TEXT_EXCERPT_MAX = original
    assert status == "ready"
    assert excerpt == "A" * 20


def test_attachment_block_reaches_council_verdict_and_referee():
    engine = PromptEngine()
    context = "Attached file: notes.txt\nContent:\n```text\nATTACHMENT_SOURCE_MARKER\n```"
    council = engine.model_answer_prompt(
        user_message="What does the file say?",
        model_id="gpt-4.1",
        model_name="GPT",
        vendor="openai",
        model_set_name="Research",
        council_runtime_context=context,
    )
    verdict = engine.verdict_prompt(
        strategy="Synthesize",
        user_message="What does the file say?",
        model_answers=[{"model_name": "GPT", "text": "It says Beirut.", "failed": False, "error_message": None}],
        supporting_context=context,
    )
    referee = engine.verdict_prompt(
        strategy="Referee",
        user_message="What does the file say?",
        model_answers=[{"model_name": "GPT", "text": "It says Beirut.", "failed": False, "error_message": None}],
        supporting_context=context,
        strict_referee_behavior="Use the supplied source material.",
    )
    assert "ATTACHMENT_SOURCE_MARKER" in council
    assert "ATTACHMENT_SOURCE_MARKER" in verdict
    assert "ATTACHMENT_SOURCE_MARKER" in referee


@pytest.mark.asyncio
async def test_follow_up_reuses_txt_without_reupload(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(tmp_path / "attachments"))
    await create_model_set(db, auth, slug="research-set")
    chat = await _create_chat(db, auth)
    async with await _client_for(db, auth) as client:
        first = await client.post(
            f"/api/v1/chats/{chat.id}/attachments",
            files={"file": ("alpha.txt", BytesIO(b"ALPHA_MARKER"), "text/plain")},
        )
        second = await client.post(
            f"/api/v1/chats/{chat.id}/attachments",
            files={"file": ("beta.txt", BytesIO(b"BETA_MARKER"), "text/plain")},
        )
    assert first.status_code == 201
    assert second.status_code == 201

    original = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(
            user_message="Read both files",
            model_set_id="research-set",
            attachment_ids=[first.json()["id"], second.json()["id"]],
        ),
    )
    await db.commit()
    instructions = (await db.get(Turn, original.id)).custom_instructions or ""
    assert "ALPHA_MARKER" in instructions
    assert "BETA_MARKER" in instructions
    assert instructions.count("ALPHA_MARKER") == 1

    follow_up = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(user_message="What was in the files?", model_set_id="research-set"),
    )
    await db.commit()
    stored = await db.get(Turn, follow_up.id)
    assert stored is not None
    follow_instructions = stored.custom_instructions or ""
    assert "ALPHA_MARKER" in follow_instructions
    assert "BETA_MARKER" in follow_instructions
    assert follow_up.attachments == []

    paths = list(
        (
            await db.execute(
                select(ChatAttachment.relative_path).where(ChatAttachment.chat_id == chat.id)
            )
        ).scalars().all()
    )
    assert len(paths) == 4
    assert len(set(paths)) == 2

    again = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(
            user_message="Read alpha again",
            model_set_id="research-set",
            attachment_ids=[first.json()["id"]],
        ),
    )
    await db.commit()
    again_text = (await db.get(Turn, again.id)).custom_instructions or ""
    assert again_text.count("ALPHA_MARKER") == 1
    assert "BETA_MARKER" in again_text

    other = await _create_chat(db, auth)
    isolated = await chat_service.start_turn(
        db,
        auth,
        other.id,
        TurnCreateRequest(user_message="Unrelated", model_set_id="research-set"),
    )
    await db.commit()
    isolated_text = (await db.get(Turn, isolated.id)).custom_instructions or ""
    assert "ALPHA_MARKER" not in isolated_text
    assert "BETA_MARKER" not in isolated_text


@pytest.mark.asyncio
async def test_context_budget_marks_omitted_txt(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(tmp_path / "attachments"))
    monkeypatch.setattr(get_settings(), "chat_attachment_context_max_chars", 80)
    await create_model_set(db, auth, slug="research-set")
    chat = await _create_chat(db, auth)
    async with await _client_for(db, auth) as client:
        uploaded = await client.post(
            f"/api/v1/chats/{chat.id}/attachments",
            files={"file": ("big.txt", BytesIO(b"Z" * 500), "text/plain")},
        )
    turn = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(
            user_message="Read it",
            model_set_id="research-set",
            attachment_ids=[uploaded.json()["id"]],
        ),
    )
    text = (await db.get(Turn, turn.id)).custom_instructions or ""
    assert "Attachment context truncated" in text or "Content omitted due to attachment context budget" in text
    assert len(text) <= 80


@pytest.mark.asyncio
async def test_missing_attachment_is_not_ignored(db: AsyncSession, auth: AuthContext):
    await create_model_set(db, auth, slug="research-set")
    chat = await _create_chat(db, auth)
    with pytest.raises(NotFoundError):
        await chat_service.start_turn(
            db,
            auth,
            chat.id,
            TurnCreateRequest(
                user_message="Missing file",
                model_set_id="research-set",
                attachment_ids=["00000000-0000-0000-0000-000000000099"],
            ),
        )


@pytest.mark.asyncio
async def test_regenerate_copies_attachment_association_not_bytes(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    root = tmp_path / "attachments"
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(root))
    await create_model_set(db, auth, slug="research-set")
    chat = await _create_chat(db, auth)
    async with await _client_for(db, auth) as client:
        uploaded = await client.post(
            f"/api/v1/chats/{chat.id}/attachments",
            files={"file": ("notes.txt", BytesIO(b"KEEP_FILE"), "text/plain")},
        )
    turn = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(
            user_message="Read notes",
            model_set_id="research-set",
            attachment_ids=[uploaded.json()["id"]],
        ),
    )
    await db.commit()
    stored_turn = await db.get(Turn, turn.id)
    assert stored_turn is not None
    stored_turn.status = TurnStatus.COMPLETED
    await db.commit()
    before = (
        await db.execute(
            select(func.count()).select_from(ChatAttachment).where(ChatAttachment.turn_id == turn.id)
        )
    ).scalar_one()
    assert before == 1
    original = (
        await db.execute(select(ChatAttachment).where(ChatAttachment.turn_id == turn.id))
    ).scalar_one()
    file_count = len(list(root.rglob("*.txt")))

    regenerated = await chat_service.regenerate_turn(
        db,
        auth,
        chat.id,
        turn.id,
        TurnRegenerateRequest(prompt="Read notes again"),
    )
    await db.commit()
    copied = (
        await db.execute(
            select(ChatAttachment).where(ChatAttachment.turn_id == regenerated.new_turn.id)
        )
    ).scalars().all()
    assert len(copied) == 1
    assert copied[0].id != original.id
    assert copied[0].filename == "notes.txt"
    assert copied[0].relative_path == original.relative_path
    assert copied[0].text_excerpt == original.text_excerpt
    assert len(list(root.rglob("*.txt"))) == file_count
    assert regenerated.new_turn.attachments[0].filename == "notes.txt"


@pytest.mark.asyncio
async def test_referenced_chat_handoff_keeps_bounded_txt(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(tmp_path / "attachments"))
    await create_model_set(db, auth, slug="research-set")
    source = await _create_chat(db, auth)
    dest = await _create_chat(db, auth)
    async with await _client_for(db, auth) as client:
        uploaded = await client.post(
            f"/api/v1/chats/{source.id}/attachments",
            files={"file": ("source.txt", BytesIO(b"SOURCE_TXT_MARKER"), "text/plain")},
        )
    await chat_service.start_turn(
        db,
        auth,
        source.id,
        TurnCreateRequest(
            user_message="See the file",
            model_set_id="research-set",
            attachment_ids=[uploaded.json()["id"]],
        ),
    )
    await db.commit()

    handoff = await chat_memory_service.build_continuation_handoff(db, source_chat=source)
    assert CONTINUATION_HANDOFF_HEADER in handoff
    assert CONTINUATION_ATTACHMENT_HEADER in handoff
    assert "SOURCE_TXT_MARKER" in handoff
    assert len(handoff) <= CONTINUATION_HANDOFF_MAX_CHARS
    assert CONTINUATION_HANDOFF_MAX_CHARS == 500_000
    assert CONTINUATION_ATTACHMENT_PER_FILE_CHARS == 30_000

    continued = await chat_service.start_turn(
        db,
        auth,
        dest.id,
        TurnCreateRequest(
            user_message="Continue",
            model_set_id="research-set",
            referenced_chat_id=source.id,
        ),
    )
    await db.commit()
    instructions = (await db.get(Turn, continued.id)).custom_instructions or ""
    assert "SOURCE_TXT_MARKER" in instructions


def test_referenced_handoff_bounds_one_two_and_ten_txt_files():
    one = format_bounded_txt_attachment_section(
        [("only.txt", "A" * (CONTINUATION_ATTACHMENT_PER_FILE_CHARS + 500))]
    )
    assert "Attached text file: only.txt" in one
    assert "[Attachment context truncated]" in one
    assert "A" * (CONTINUATION_ATTACHMENT_PER_FILE_CHARS + 1) not in one

    two = format_bounded_txt_attachment_section(
        [("a.txt", "ALPHA"), ("b.txt", "BETA")]
    )
    assert "ALPHA" in two and "BETA" in two
    assert "[Attachment context truncated]" not in two

    many = [(f"f{index}.txt", f"BODY{index}") for index in range(10)]
    ten = format_bounded_txt_attachment_section(many)
    for index in range(10):
        assert f"f{index}.txt" in ten
        assert f"BODY{index}" in ten
    assert "additional text file" not in ten

    extra = format_bounded_txt_attachment_section(
        [(f"f{index}.txt", f"BODY{index}") for index in range(11)]
    )
    assert "f0.txt" not in extra
    assert "f10.txt" in extra
    assert "1 additional text file(s) omitted" in extra

    recent = [
        TurnHistoryEntry(
            turn_id="t1",
            user_message="RECENT_QUESTION_MARKER",
            verdict_text="RECENT_VERDICT_MARKER",
            verdict_reason=None,
            created_at=None,
        )
    ]
    fits = build_continuation_handoff_text(
        source_title="Source",
        rolling_memory="M" * 100,
        recent_entries_oldest_first=recent,
        attachment_section=format_bounded_txt_attachment_section(
            [("notes.txt", "N" * CONTINUATION_ATTACHMENT_PER_FILE_CHARS)]
        ),
    )
    assert len(fits) <= CONTINUATION_HANDOFF_MAX_CHARS
    assert "RECENT_QUESTION_MARKER" in fits
    assert "N" * CONTINUATION_ATTACHMENT_PER_FILE_CHARS in fits
    assert "[Attachment context truncated]" not in fits

    over = build_continuation_handoff_text(
        source_title="Source",
        rolling_memory=None,
        recent_entries_oldest_first=recent,
        attachment_section="Z" * (CONTINUATION_HANDOFF_MAX_CHARS + 5_000),
    )
    assert len(over) <= CONTINUATION_HANDOFF_MAX_CHARS
    assert len(over) > 150_000
    assert "RECENT_QUESTION_MARKER" in over
    assert "[...truncated...]" in over
