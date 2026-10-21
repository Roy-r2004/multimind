"""Flag-on large-document preparation. Flag off stays on the PR #98 path."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import AttachmentTooLargeError
from app.db.models import (
    Chat,
    ChatAttachment,
    CostRecord,
    ProcessedChunk,
    ProcessedDocument,
    ProcessedSection,
    Strategy,
    Turn,
    TurnStatus,
    UsageKind,
)
from app.documents.worker import process_document_job, recover_stale_documents
from app.services.chat_attachment_storage import UPLOAD_CHUNK_SIZE, stream_upload_to_temp_file
from app.services.document_context import (
    DocumentSource,
    is_context_overflow_error,
    prepare_model_context,
)
from app.services.document_processing import (
    hash_file,
    local_embedding,
    prepare_stored_file,
    render_page,
    resolve_document_sources,
    should_defer_extraction,
    upload_max_bytes,
    wait_for_document_processing,
)
from tests.conftest import create_other_auth
from tests.test_chat_attachments import _pdf_with_pages
from tests.test_document_context import (
    _fitting_window,
    _many_facts,
    _RecordingProvider,
    _run_single,
)

PDF_MIME = "application/pdf"


class _Chunks:
    def __init__(self, parts: list[bytes]) -> None:
        self.parts = list(parts)
        self.sizes: list[int] = []
        self.filename = "chunked.txt"

    async def read(self, size: int) -> bytes:
        self.sizes.append(size)
        if not self.parts:
            return b""
        return self.parts.pop(0)


class _SessionFactory:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *_args) -> bool:
        return False


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "large_documents_enabled", True)
    monkeypatch.setattr(get_settings(), "document_processing_poll_seconds", 0.01)
    monkeypatch.setattr(get_settings(), "document_chunk_chars", 80)


async def _attachment(
    db: AsyncSession,
    auth: AuthContext,
    path,
    *,
    filename: str,
) -> ChatAttachment:
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Docs")
    db.add(chat)
    await db.flush()
    relative = f"{auth.org_id}/{chat.id}/{path.name}"
    stored = path.parent / relative
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(path.read_bytes())
    row = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        filename=filename,
        stored_name=path.name,
        content_type="text/plain" if filename.endswith(".txt") else PDF_MIME,
        size_bytes=stored.stat().st_size,
        relative_path=relative,
        text_excerpt=None,
        excerpt_status="processing",
        content_sha256=hash_file(stored),
    )
    db.add(row)
    await db.flush()
    return row


@pytest.mark.asyncio
async def test_upload_limit_is_200_mb_only_when_the_flag_is_on(monkeypatch, tmp_path):
    assert upload_max_bytes() == 10 * 1024 * 1024
    assert should_defer_extraction(".pdf") is False
    monkeypatch.setattr(get_settings(), "large_documents_enabled", True)
    assert upload_max_bytes() == 200 * 1024 * 1024
    assert upload_max_bytes(library=True) == 200 * 1024 * 1024
    upload = _Chunks([b"a" * 1024, b"b" * 1024])
    path, size = await stream_upload_to_temp_file(
        upload, max_bytes=200 * 1024 * 1024, extension=".txt", root=tmp_path
    )
    assert size == 2048
    assert upload.sizes[0] == UPLOAD_CHUNK_SIZE
    assert path.read_bytes() == b"a" * 1024 + b"b" * 1024
    path.unlink()
    overflow = _Chunks([b"x" * 32, b"y" * 32])
    with pytest.raises(AttachmentTooLargeError):
        await stream_upload_to_temp_file(overflow, max_bytes=40, extension=".txt", root=tmp_path)
    assert list(tmp_path.rglob("*.upload*")) == []


@pytest.mark.asyncio
async def test_worker_prepares_a_text_file_and_a_restart_does_not_repeat_it(
    db, auth, tmp_path, monkeypatch
):
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(tmp_path))
    source = tmp_path / "notes.txt"
    source.write_text("BEGIN_FACT\n" + ("m" * 100) + "\nEND_FACT", encoding="utf-8")
    row = await _attachment(db, auth, source, filename="notes.txt")
    calls: list[int] = []

    async def ocr(images):
        calls.append(len(images))
        return []

    await prepare_stored_file(
        db,
        org_id=auth.org_id,
        path=tmp_path / row.relative_path,
        extension=".txt",
        filename=row.filename,
        chat_id=row.chat_id,
        attachment=row,
        ocr_pages=ocr,
    )
    assert calls == []
    assert row.excerpt_status == "ready"
    assert "BEGIN_FACT" in (row.text_excerpt or "")
    assert "END_FACT" in (row.text_excerpt or "")
    document = (
        await db.execute(select(ProcessedDocument).where(ProcessedDocument.org_id == auth.org_id))
    ).scalar_one()
    document.status = "processing"
    document.heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()
    result = await process_document_job(
        {"session_factory": _SessionFactory(db)}, "chat", row.id, auth.org_id
    )
    assert result["status"] == "ready"
    assert calls == []


@pytest.mark.asyncio
async def test_same_hash_is_not_processed_twice_inside_one_organization(db, auth, tmp_path, monkeypatch):
    _enable(monkeypatch)
    path = tmp_path / "same.txt"
    path.write_text("DEDUP_FACT", encoding="utf-8")
    first = await prepare_stored_file(
        db, org_id=auth.org_id, path=path, extension=".txt", filename="same.txt"
    )
    second = await prepare_stored_file(
        db, org_id=auth.org_id, path=path, extension=".txt", filename="same.txt"
    )
    assert first.id == second.id
    rows = (await db.execute(select(ProcessedDocument))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_another_organization_cannot_read_the_processed_document(db, auth, tmp_path, monkeypatch):
    _enable(monkeypatch)
    path = tmp_path / "secret.txt"
    path.write_text("ORG_SECRET_FACT", encoding="utf-8")
    await prepare_stored_file(
        db, org_id=auth.org_id, path=path, extension=".txt", filename="secret.txt"
    )
    other = await create_other_auth(db)
    other_doc = await prepare_stored_file(
        db, org_id=other.org_id, path=path, extension=".txt", filename="secret.txt"
    )
    own = (
        await db.execute(select(ProcessedSection).where(ProcessedSection.org_id == auth.org_id))
    ).scalars().all()
    leaked = (
        await db.execute(
            select(ProcessedDocument).where(
                ProcessedDocument.org_id == other.org_id,
                ProcessedDocument.id == own[0].document_id,
            )
        )
    ).scalar_one_or_none()
    assert leaked is None
    assert other_doc.org_id == other.org_id
    assert own[0].text == "ORG_SECRET_FACT"


@pytest.mark.asyncio
async def test_native_and_scanned_pdf_pages_keep_citations_and_progress(
    db, auth, tmp_path, monkeypatch
):
    _enable(monkeypatch)
    path = tmp_path / "mixed.pdf"
    path.write_bytes(_pdf_with_pages(["NATIVE_PAGE_FACT", ""]))
    seen: list[int] = []

    async def ocr(images):
        for page, _png in images:
            seen.append(page)
        return [
            (
                page,
                "SCANNED_PAGE_FACT",
                {"tokens_input": 11, "tokens_output": 7, "cost_usd": 0.4, "model_id": "vision-test"},
            )
            for page, _png in images
        ]

    monkeypatch.setattr(
        "app.services.document_processing.render_pdf_page_png", lambda _path, page: b"png"
    )
    document = await prepare_stored_file(
        db,
        org_id=auth.org_id,
        path=path,
        extension=".pdf",
        filename="mixed.pdf",
        chat_id=None,
        ocr_pages=ocr,
    )
    assert seen == [2]
    assert document.pages_done == document.pages_total == 2
    sections = (
        await db.execute(
            select(ProcessedSection)
            .where(ProcessedSection.document_id == document.id)
            .order_by(ProcessedSection.section_index)
        )
    ).scalars().all()
    joined = "\n".join(
        render_page(section.text, section.page_number, origin=section.origin) for section in sections
    )
    assert sections[0].origin == "native"
    assert sections[1].origin == "ocr"
    assert "[p. 1]" in joined and "NATIVE_PAGE_FACT" in joined
    assert "[p. 2]" in joined and "SCANNED_PAGE_FACT" in joined
    cost = (await db.execute(select(CostRecord))).scalars().all()
    assert any(row.kind == UsageKind.DOCUMENT_PROCESSING and row.turn_id is None for row in cost)
    assert any(row.metadata_ and row.metadata_.get("stage") == "ocr" and row.cost_usd == 0.4 for row in cost)


@pytest.mark.asyncio
async def test_hybrid_retrieval_finds_the_beginning_middle_and_end(db, auth, monkeypatch):
    _enable(monkeypatch)
    document = ProcessedDocument(
        org_id=auth.org_id,
        content_sha256="a" * 64,
        filename="long.txt",
        status="ready",
        pages_done=3,
        pages_total=3,
    )
    db.add(document)
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Hybrid")
    db.add(chat)
    await db.flush()
    facts = [
        (1, "BEGIN_FACT lives on the first page"),
        (2, "MIDDLE_FACT lives far from either end"),
        (3, "END_FACT lives on the last page"),
    ]
    for index, (page, text) in enumerate(facts):
        db.add(
            ProcessedSection(
                document_id=document.id,
                org_id=auth.org_id,
                section_index=index,
                page_number=page,
                origin="native",
                text=text,
            )
        )
        db.add(
            ProcessedChunk(
                document_id=document.id,
                org_id=auth.org_id,
                chunk_index=index,
                page_number=page,
                text=text,
                embedding=local_embedding(text),
            )
        )
    row = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        filename="long.txt",
        stored_name="long.txt",
        content_type="text/plain",
        size_bytes=10,
        relative_path="long.txt",
        text_excerpt="short",
        excerpt_status="ready",
        content_sha256="a" * 64,
    )
    db.add(row)
    await db.flush()
    targeted = await resolve_document_sources(
        db,
        [row],
        {},
        "Where are BEGIN_FACT MIDDLE_FACT and END_FACT?",
    )
    assert "BEGIN_FACT" in (targeted[0].text or "")
    assert "MIDDLE_FACT" in (targeted[0].text or "")
    assert "END_FACT" in (targeted[0].text or "")
    assert "[p. 1]" in (targeted[0].text or "")
    assert "[p. 3]" in (targeted[0].text or "")
    whole = await resolve_document_sources(db, [row], {}, "Summarize the entire document")
    assert "END_FACT" in (whole[0].text or "")
    assert "MIDDLE_FACT" in (whole[0].text or "")


@pytest.mark.asyncio
async def test_one_send_waits_for_processing_and_keeps_flag_off_fast(db, auth, monkeypatch):
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Wait")
    db.add(chat)
    await db.flush()
    row = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        filename="wait.txt",
        stored_name="wait.txt",
        content_type="text/plain",
        size_bytes=4,
        relative_path="wait.txt",
        text_excerpt=None,
        excerpt_status="processing",
    )
    db.add(row)
    await db.commit()

    await wait_for_document_processing(db, [row])
    assert row.excerpt_status == "processing"

    _enable(monkeypatch)

    async def finish() -> None:
        await asyncio.sleep(0.03)
        row.excerpt_status = "ready"
        row.text_excerpt = "WAIT_FACT"
        await db.commit()

    waiter = asyncio.create_task(wait_for_document_processing(db, [row]))
    await finish()
    await waiter
    assert row.excerpt_status == "ready"
    assert row.text_excerpt == "WAIT_FACT"


@pytest.mark.asyncio
async def test_flag_on_whole_document_still_finishes_in_the_original_turn(
    db, auth, tmp_path, monkeypatch
):
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "document_analysis_progress_dir", str(tmp_path / "progress"))
    monkeypatch.setattr("app.services.document_context.lookup_context_window", _fitting_window)
    provider = _RecordingProvider(overflow_over=3000)
    text = _many_facts(6, 90_000)
    turn = await _run_single(
        db,
        auth,
        provider,
        message="Summarize the entire document",
        documents=[DocumentSource("policy.txt", text, "ready")],
        brain="BRAIN_MARKER_UNIQUE",
    )
    assert turn.status == TurnStatus.COMPLETED
    assert len(provider.systems) > 4
    assert "[[" not in (turn.model_answers[0].text or "")
    assert any("BRAIN_MARKER_UNIQUE" in system for system in provider.systems)
    assert any("HISTORY_MARKER" in system for system in provider.systems)


@pytest.mark.asyncio
async def test_stale_processing_is_requeued_once(db, auth, monkeypatch):
    _enable(monkeypatch)
    document = ProcessedDocument(
        org_id=auth.org_id,
        content_sha256="b" * 64,
        filename="stale.txt",
        status="processing",
        pages_done=1,
        pages_total=2,
        heartbeat_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db.add(document)
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Stale")
    db.add(chat)
    await db.flush()
    row = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        filename="stale.txt",
        stored_name="stale.txt",
        content_type="text/plain",
        size_bytes=1,
        relative_path="stale.txt",
        excerpt_status="processing",
        content_sha256="b" * 64,
    )
    db.add(row)
    await db.commit()
    queued: list[tuple[str, str]] = []

    async def enqueue(*, kind: str, source_id: str, org_id: str) -> None:
        queued.append((kind, source_id))

    monkeypatch.setattr("app.services.document_processing.enqueue_processing", enqueue)
    count = await recover_stale_documents({"session_factory": _SessionFactory(db)})
    assert count == 1
    assert queued == [("chat", row.id)]


@pytest.mark.asyncio
async def test_flag_off_resolve_keeps_the_stored_excerpt(db, auth):
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Off")
    db.add(chat)
    await db.flush()
    row = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        filename="off.txt",
        stored_name="off.txt",
        content_type="text/plain",
        size_bytes=4,
        relative_path="off.txt",
        text_excerpt="STORED_ONLY",
        excerpt_status="ready",
        content_sha256="c" * 64,
    )
    db.add(row)
    await db.flush()
    sources = await resolve_document_sources(db, [row], {}, "What is STORED_ONLY?")
    assert sources[0].text == "STORED_ONLY"


def test_context_overflow_detection_still_rejects_an_oversized_prompt():
    assert is_context_overflow_error(RuntimeError("maximum context length exceeded"))
    prepared = prepare_model_context(
        model_id="gpt-4.1",
        user_message="Read the entire file",
        fixed_system="system",
        references=None,
        documents=[DocumentSource("big.txt", "Z" * 500_000, "ready")],
        requested_output_tokens=800,
        context_window=1_200,
        window_source="provider_metadata",
    )
    assert prepared.overflow_message


@pytest.mark.asyncio
async def test_two_models_receive_the_same_processed_facts(db, auth, tmp_path, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "document_analysis_progress_dir", str(tmp_path / "progress"))
    monkeypatch.setattr("app.services.document_context.lookup_context_window", _fitting_window)

    class _Provider(_RecordingProvider):
        @staticmethod
        def parse_json_object_lenient(text: str):
            return {"summary": text, "winner": "gpt-4.1"}

    provider = _Provider()
    from app.llm.orchestrator import TurnContext, TurnOrchestrator

    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Council")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="Where is COUNCIL_FACT?",
        model_set_id="research-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.PENDING,
    )
    db.add(turn)
    await db.flush()
    turn_id = turn.id
    orchestrator = TurnOrchestrator()
    orchestrator._providers = type("Registry", (), {"get_provider": lambda self, _name: provider})()
    await orchestrator.run(
        db,
        TurnContext(
            turn_id=turn.id,
            chat_id=chat.id,
            org_id=auth.org_id,
            project_id=None,
            user_message=turn.user_message,
            model_ids=["gpt-4.1", "claude"],
            verdict_model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            model_set_name="Council",
            user_brain_context="BRAIN_MARKER_UNIQUE",
            recent_conversation_context="HISTORY_MARKER stays with the question.",
            document_sources=[DocumentSource("policy.txt", "COUNCIL_FACT on page 37", "ready")],
        ),
    )
    await db.commit()
    saved = (await db.execute(select(Turn).where(Turn.id == turn_id))).scalar_one()
    assert saved.status == TurnStatus.COMPLETED
    assert sum("COUNCIL_FACT" in system for system in provider.systems) >= 2
