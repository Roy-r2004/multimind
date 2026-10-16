"""Document extraction, retrieval, and per-model context budgets. No provider calls."""

from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.db.models import Chat, ChatAttachment, LibraryItem, Strategy, Turn, TurnStatus
from app.llm.orchestrator import TurnContext, TurnOrchestrator
from app.llm.prompt_engine import PromptEngine
from app.core.config import get_settings
from app.services.chat_attachment_text import extract_attachment_text
from app.services.chat_memory_service import chat_memory_service, format_reference_document_manifest
from app.services.chat_service import chat_service, refresh_legacy_attachment_rows
from app.services.document_context import (
    ATTACHMENT_BLOCK_END,
    ATTACHMENT_BLOCK_START,
    DocumentSource,
    assess_legacy_excerpt,
    context_exceeds_budget,
    parse_reference_chat_ids,
    prepare_model_context,
    render_section_findings,
    requests_complete_coverage,
    strip_baked_attachment_block,
)
from app.services.library_service import library_document_excerpt
from tests.conftest import create_other_auth

BEGIN = "FACT_BEGIN_ALPHA"
MIDDLE = "FACT_MIDDLE_BETA"
END = "FACT_END_GAMMA"


def _document_with_facts(pad: int = 4_000) -> str:
    return f"{BEGIN}\n" + ("a " * pad) + f"\n{MIDDLE}\n" + ("b " * pad) + f"\n{END}"


def _prepare(**kwargs):
    defaults = dict(
        model_id="gpt-4.1",
        user_message="What does the file say?",
        fixed_system="System instructions stay intact.",
        references=None,
        requested_output_tokens=256,
        context_window=32_000,
        window_source="caller",
    )
    defaults.update(kwargs)
    return prepare_model_context(**defaults)


def test_library_note_over_100k_keeps_the_ending():
    body = f"{BEGIN}\n" + ("m" * 120_000) + f"\n{END}"
    excerpt, status = library_document_excerpt(body)
    assert status == "ready"
    assert excerpt == body
    assert excerpt.startswith(BEGIN)
    assert excerpt.endswith(END)


def test_plain_text_over_the_old_prefix_keeps_middle_and_end():
    body = _document_with_facts(30_000)
    excerpt, status = extract_attachment_text(body.encode(), ".txt")
    assert status == "ready"
    assert excerpt is not None
    assert BEGIN in excerpt and MIDDLE in excerpt and END in excerpt
    assert "[Content truncated]" not in excerpt


def test_readable_text_is_not_cut_at_a_character_prefix():
    excerpt, status = extract_attachment_text(("A" * 200).encode(), ".txt")
    assert status == "ready"
    assert excerpt == "A" * 200
    assert "[Content truncated]" not in (excerpt or "")


def test_full_window_includes_begin_middle_and_end():
    text = _document_with_facts()
    prepared = _prepare(
        documents=[DocumentSource("notes.txt", text, "ready")],
        context_window=32_000,
    )
    assert prepared.overflow_message is None
    assert prepared.diagnostics["mode"] == "full"
    runtime = prepared.runtime_context or ""
    assert BEGIN in runtime and MIDDLE in runtime and END in runtime
    assert prepared.diagnostics["estimate_method"] == "chars/4"


def test_small_window_retrieves_a_fact_at_the_end():
    text = _document_with_facts(3_000)
    prepared = _prepare(
        user_message=f"Where is {END}?",
        documents=[DocumentSource("notes.txt", text, "ready")],
        context_window=2_400,
        requested_output_tokens=256,
    )
    runtime = prepared.runtime_context or ""
    assert prepared.overflow_message is None
    assert "notes.txt" in runtime
    assert END in runtime
    assert prepared.diagnostics["mode"] in {"retrieved", "full"}


def test_many_files_past_the_old_300k_budget_stay_present_when_the_window_allows():
    documents = []
    for index in range(4):
        documents.append(
            DocumentSource(
                f"file-{index}.pdf",
                f"FILE{index}_BEGIN " + ("x" * 90_000) + f" FILE{index}_END",
                "ready",
            )
        )
    prepared = _prepare(
        user_message="Read every attached file",
        documents=documents,
        context_window=200_000,
        requested_output_tokens=1_024,
    )
    runtime = prepared.runtime_context or ""
    assert prepared.diagnostics["mode"] == "full"
    for index in range(4):
        assert f"file-{index}.pdf" in runtime
        assert f"FILE{index}_BEGIN" in runtime
        assert f"FILE{index}_END" in runtime


def test_ten_files_are_named_when_the_window_cannot_hold_them():
    documents = [
        DocumentSource(f"note-{index}.txt", "UNIQUE" * 8_000, "ready") for index in range(10)
    ]
    prepared = _prepare(
        documents=documents,
        context_window=4_000,
        requested_output_tokens=256,
    )
    runtime = prepared.runtime_context or ""
    assert prepared.overflow_message is None
    for index in range(10):
        assert f"note-{index}.txt" in runtime
    assert "Document content omitted" in runtime or "selected relevant sections" in runtime


def test_late_pdf_page_is_searchable():
    pages = [f"[Page {number}]\nordinary page {number}" for number in range(1, 12)]
    pages.append(f"[Page 12]\n{END}")
    prepared = _prepare(
        user_message=f"Find {END}",
        documents=[DocumentSource("late.pdf", "\n\n".join(pages), "ready")],
        context_window=3_000,
        requested_output_tokens=256,
    )
    assert END in (prepared.runtime_context or "")


def test_docx_table_row_and_spreadsheet_are_addressable():
    docx_text = "Intro\nName\tScore\nAda\t10\n" + ("pad " * 2_000) + f"\n{END}"
    sheet = "Worksheet: Sales\nRegion\tTotal\nEU\t12\n" + ("row " * 2_000) + "\nWorksheet: Archive\n" + END
    prepared = _prepare(
        user_message=f"What is {END} and Ada's score?",
        documents=[
            DocumentSource("tables.docx", docx_text, "ready"),
            DocumentSource("book.xlsx", sheet, "ready"),
        ],
        context_window=64_000,
    )
    runtime = prepared.runtime_context or ""
    assert "Ada\t10" in runtime
    assert "Worksheet: Archive" in runtime
    assert END in runtime


def test_complete_request_plans_every_section_or_reports_the_gap():
    assert requests_complete_coverage("Summarize the entire document")
    text = "SECTION_MARKER_" + ("word " * 20_000)
    prepared = _prepare(
        user_message="Summarize the entire document and extract all requirements",
        documents=[DocumentSource("policy.pdf", text, "ready")],
        context_window=6_000,
        requested_output_tokens=1_024,
    )
    assert prepared.diagnostics["mode"] == "complete_sections"
    covered = "".join(section["text"] for section in prepared.sections)
    covered += "".join(section["text"] for section in prepared.skipped_sections)
    assert text in covered
    assert prepared.sections
    findings = [
        {
            "filename": section["filename"],
            "label": section["label"],
            "text": "noted",
            "failed": False,
        }
        for section in prepared.sections
    ]
    rendered = render_section_findings(prepared, findings)
    assert "complete-document analysis" in rendered
    assert "Sections processed:" in rendered


def test_overflow_does_not_pretend_the_prompt_fits():
    huge_system = "BRAIN " * 20_000
    prepared = _prepare(
        fixed_system=huge_system,
        documents=[DocumentSource("notes.txt", "tiny", "ready")],
        context_window=2_000,
        requested_output_tokens=256,
    )
    assert prepared.overflow_message
    assert "was not sent" in prepared.overflow_message
    assert "tiny" not in prepared.overflow_message
    assert prepared.diagnostics["exceeds_budget"] is True


def test_brain_stays_when_documents_are_reduced():
    brain = "BRAIN_MARKER_UNIQUE " + ("preference " * 200)
    prepared = _prepare(
        fixed_system=brain,
        user_message="What is the capital?",
        documents=[DocumentSource("notes.txt", "x" * 30_000, "ready")],
        context_window=4_000,
        requested_output_tokens=256,
    )
    assert "BRAIN_MARKER_UNIQUE" in brain
    assert prepared.overflow_message is None
    assert "notes.txt" in (prepared.runtime_context or "")


def test_different_windows_change_how_much_document_text_is_sent():
    text = _document_with_facts(2_000)
    wide = _prepare(
        documents=[DocumentSource("notes.txt", text, "ready")],
        context_window=32_000,
    )
    narrow = _prepare(
        documents=[DocumentSource("notes.txt", text, "ready")],
        context_window=2_400,
        requested_output_tokens=256,
    )
    assert wide.diagnostics["mode"] == "full"
    assert len(wide.runtime_context or "") > len(narrow.runtime_context or "")
    assert narrow.diagnostics["window_tokens"] == 2_400
    assert wide.diagnostics["window_source"] == "caller"


def test_unfitted_prompt_exceeds_a_small_window_and_the_fitted_one_does_not():
    text = "y" * 80_000
    raw_system = "System\n" + text
    assert context_exceeds_budget(raw_system, "Question", 8_000, 1_024)
    prepared = _prepare(
        user_message="Question",
        fixed_system="System",
        documents=[DocumentSource("notes.txt", text, "ready")],
        context_window=8_000,
        requested_output_tokens=1_024,
    )
    assert prepared.overflow_message is None
    runtime = prepared.runtime_context or ""
    assert not context_exceeds_budget(
        "System\n" + runtime,
        "Question",
        prepared.window_tokens,
        prepared.reserved_output_tokens,
    )


def test_council_verdict_and_referee_receive_the_same_document_block():
    prepared = _prepare(
        user_message="What is in the note?",
        documents=[DocumentSource("notes.txt", f"Hello {END}", "ready")],
    )
    runtime = prepared.runtime_context
    engine = PromptEngine()
    answers = [{"model_name": "GPT", "text": "The note has a fact.", "failed": False, "error_message": None}]
    council = engine.model_answer_prompt(
        user_message="What is in the note?",
        model_id="gpt-4.1",
        model_name="GPT",
        vendor="OpenAI",
        model_set_name="Research",
        council_runtime_context=runtime,
    )
    verdict = engine.verdict_prompt(
        strategy="Synthesize",
        user_message="What is in the note?",
        model_answers=answers,
        supporting_context=runtime,
        user_brain_context="BRAIN_MARKER_UNIQUE",
    )
    referee = engine.verdict_prompt(
        strategy="Referee",
        user_message="What is in the note?",
        model_answers=answers,
        supporting_context=runtime,
        strict_referee_behavior="Use the supplied source material.",
        user_brain_context="BRAIN_MARKER_UNIQUE",
    )
    for rendered in (council, verdict):
        assert END in rendered
        assert "BRAIN_MARKER_UNIQUE" in rendered or rendered is council
    assert END in referee
    assert "BRAIN_MARKER_UNIQUE" not in referee
    assert END in council


def test_verdict_builder_uses_document_sources_instead_of_a_truncated_bake():
    baked = (
        f"{ATTACHMENT_BLOCK_START}\nAttached file: notes.txt\nContent:\n```text\n"
        f"ONLY_THE_BEGINNING\n{ATTACHMENT_BLOCK_END}"
    )
    ctx = TurnContext(
        turn_id="turn",
        chat_id="chat",
        org_id="org",
        project_id=None,
        user_message="Where is the ending?",
        model_ids=["gpt-4.1"],
        verdict_model_id="gpt-4.1",
        strategy=Strategy.SYNTHESIZE,
        model_set_name="Research",
        council_runtime_context=baked,
        document_sources=[DocumentSource("notes.txt", f"{BEGIN}\n{MIDDLE}\n{END}", "ready")],
    )
    system, reserved = TurnOrchestrator()._build_verdict_request(
        ctx,
        [{"model_name": "GPT", "text": "A short answer.", "failed": False, "error_message": None}],
    )
    assert reserved > 0
    assert END in system
    assert "ONLY_THE_BEGINNING" not in system
    assert strip_baked_attachment_block(baked) is None


def test_partial_and_unreadable_files_are_labeled():
    prepared = _prepare(
        documents=[
            DocumentSource("scan.pdf", None, "empty"),
            DocumentSource("half.txt", "kept prefix", "partial"),
        ],
        context_window=8_000,
    )
    runtime = prepared.runtime_context or ""
    assert "scan.pdf" in runtime
    assert "OCR is not applied" in runtime
    assert "partially extracted" in runtime
    assert "kept prefix" in runtime


@pytest.mark.asyncio
async def test_follow_up_reloads_pdf_docx_xlsx_and_txt(db: AsyncSession, auth: AuthContext):
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Docs")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="Read these",
        model_set_id="research-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.COMPLETED,
    )
    db.add(turn)
    await db.flush()
    specs = [
        ("notes.txt", "text/plain", "TXT_FACT"),
        ("brief.pdf", "application/pdf", "PDF_FACT"),
        ("memo.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "DOCX_FACT"),
        ("grid.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "XLSX_FACT"),
    ]
    for filename, content_type, fact in specs:
        db.add(
            ChatAttachment(
                org_id=auth.org_id,
                chat_id=chat.id,
                uploaded_by_user_id=auth.user.id,
                turn_id=turn.id,
                filename=filename,
                stored_name=filename,
                content_type=content_type,
                size_bytes=len(fact),
                relative_path=f"files/{filename}",
                text_excerpt=fact,
                excerpt_status="ready",
            )
        )
    await db.flush()
    loaded = await chat_memory_service.load_prior_document_attachments(
        db,
        chat.id,
        org_id=auth.org_id,
    )
    found = {row.filename: row.text_excerpt for row in loaded}
    assert found["notes.txt"] == "TXT_FACT"
    assert found["brief.pdf"] == "PDF_FACT"
    assert found["memo.docx"] == "DOCX_FACT"
    assert found["grid.xlsx"] == "XLSX_FACT"


@pytest.mark.asyncio
async def test_prior_documents_stay_inside_the_organization(
    db: AsyncSession, auth: AuthContext
):
    other = await create_other_auth(db)
    chat = Chat(org_id=other.org_id, created_by=other.user.id, title="Other")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="Secret",
        model_set_id="research-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.COMPLETED,
    )
    db.add(turn)
    await db.flush()
    db.add(
        ChatAttachment(
            org_id=other.org_id,
            chat_id=chat.id,
            uploaded_by_user_id=other.user.id,
            turn_id=turn.id,
            filename="secret.pdf",
            stored_name="secret.pdf",
            content_type="application/pdf",
            size_bytes=8,
            relative_path="files/secret.pdf",
            text_excerpt="OTHER_ORG_SECRET",
            excerpt_status="ready",
        )
    )
    await db.flush()
    hidden = await chat_memory_service.load_prior_document_attachments(
        db,
        chat.id,
        org_id=auth.org_id,
    )
    visible = await chat_memory_service.load_prior_document_attachments(
        db,
        chat.id,
        org_id=other.org_id,
    )
    assert hidden == []
    assert visible and visible[0].text_excerpt == "OTHER_ORG_SECRET"
    stored = (
        await db.execute(select(ChatAttachment).where(ChatAttachment.chat_id == chat.id))
    ).scalars().all()
    assert all(row.org_id == other.org_id for row in stored)


def test_spreadsheet_safety_limit_is_partial(monkeypatch):
    monkeypatch.setattr("app.services.chat_attachment_text._XLSX_MAX_SHEETS", 1)
    from openpyxl import Workbook

    workbook = Workbook()
    first = workbook.active
    first.title = "Kept"
    first.append(["alpha"])
    second = workbook.create_sheet("Dropped")
    second.append(["beta"])
    buffer = BytesIO()
    workbook.save(buffer)
    excerpt, status = extract_attachment_text(buffer.getvalue(), ".xlsx")
    assert status == "partial"
    assert excerpt is not None
    assert "Worksheet: Kept" in excerpt
    assert "Worksheet: Dropped" not in excerpt
    assert "[Content truncated]" in excerpt


def _long_document(sections: int = 16) -> str:
    return "".join(f"SECTION_{index:02d}_" + ("w" * 3_900) for index in range(sections))


def test_complete_request_plans_every_section_without_a_call_cap():
    text = _long_document(16)
    prepared = _prepare(
        user_message="Summarize the entire document",
        documents=[DocumentSource("policy.txt", text, "ready")],
        context_window=2_600,
        requested_output_tokens=1_024,
    )
    assert prepared.diagnostics["mode"] == "complete_sections"
    assert prepared.section_total > 12
    indexes = [
        index
        for batch in prepared.sections
        for index in batch["section_indexes"]
    ]
    assert len(indexes) > 12
    assert len(prepared.sections) > 4
    assert prepared.resume_section_index is None
    assert prepared.diagnostics["incomplete"] is True
    runtime = prepared.runtime_context or ""
    assert "automatically" in runtime
    assert "[[multimind_document_cursor" not in runtime
    assert "continue" not in runtime.lower()
    covered = "".join(section["text"] for section in prepared.sections)
    assert text in covered


def test_deployment_call_limit_reports_incomplete_without_asking_the_user(monkeypatch):
    monkeypatch.setattr(get_settings(), "document_analysis_max_calls", 2)
    text = _long_document(16)
    prepared = _prepare(
        user_message="Summarize the entire document",
        documents=[DocumentSource("policy.txt", text, "ready")],
        context_window=2_600,
        requested_output_tokens=1_024,
    )
    assert len(prepared.sections) == 2
    assert prepared.skipped_sections
    assert prepared.diagnostics["incomplete"] is True
    runtime = prepared.runtime_context or ""
    assert "Coverage is incomplete" in runtime
    assert "[[multimind_document_cursor" not in runtime
    assert "reply continue" not in runtime.lower()


def test_unknown_window_names_the_fallback_and_marks_incomplete(monkeypatch):
    monkeypatch.setattr(
        "app.services.document_context.lookup_context_window",
        lambda model_id: (32_768, "conservative_estimate"),
    )
    prepared = prepare_model_context(
        model_id="gpt-4.1",
        user_message="What does the file say?",
        fixed_system="System instructions stay intact.",
        references=None,
        documents=[DocumentSource("notes.txt", "Z" * 200_000, "ready")],
        requested_output_tokens=256,
    )
    runtime = prepared.runtime_context or ""
    assert prepared.window_source == "conservative_estimate"
    assert prepared.diagnostics["incomplete"] is True
    assert "Context window is unknown" in runtime
    assert "Fallback budget: 32768" in runtime
    assert "Coverage is incomplete" in runtime
    assert "Z" * 200_000 not in runtime


def test_smaller_known_window_fails_clearly_instead_of_dropping_text():
    prepared = _prepare(
        documents=[DocumentSource("notes.txt", "KEEP_ME_FACT", "ready")],
        context_window=1_500,
        window_source="provider_metadata",
        requested_output_tokens=4_000,
    )
    assert prepared.overflow_message
    assert "was not sent" in prepared.overflow_message
    assert "KEEP_ME_FACT" not in prepared.overflow_message
    assert "1500" in prepared.overflow_message
    assert prepared.runtime_context is None
    assert prepared.diagnostics["incomplete"] is True


def test_referenced_manifest_does_not_paste_the_old_prefix():
    body = ("A" * 30_000) + "FACT_PAST_30000"
    row = type("Row", (), {})()
    row.filename = "source.txt"
    row.text_excerpt = body
    row.excerpt_status = "ready"
    manifest = format_reference_document_manifest([row], "11111111-1111-1111-1111-111111111111")
    assert "FACT_PAST_30000" not in manifest
    assert "source.txt" in manifest
    assert parse_reference_chat_ids(manifest) == ["11111111-1111-1111-1111-111111111111"]
    prepared = _prepare(
        documents=[DocumentSource("source.txt", body, "ready")],
        context_window=80_000,
        user_message="Where is FACT_PAST_30000?",
    )
    assert "FACT_PAST_30000" in (prepared.runtime_context or "")


@pytest.mark.asyncio
async def test_referenced_chat_loads_text_past_the_old_prefix(
    db: AsyncSession, auth: AuthContext
):
    source = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Source")
    db.add(source)
    await db.flush()
    turn = Turn(
        chat_id=source.id,
        user_message="Read the file",
        model_set_id="research-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.COMPLETED,
    )
    db.add(turn)
    await db.flush()
    body = ("A" * 30_000) + "FACT_PAST_30000"
    db.add(
        ChatAttachment(
            org_id=auth.org_id,
            chat_id=source.id,
            uploaded_by_user_id=auth.user.id,
            turn_id=turn.id,
            filename="source.txt",
            stored_name="source.txt",
            content_type="text/plain",
            size_bytes=len(body),
            relative_path="files/source.txt",
            text_excerpt=body,
            excerpt_status="ready",
        )
    )
    await db.flush()
    handoff = await chat_memory_service.build_continuation_handoff(db, source_chat=source)
    assert "FACT_PAST_30000" not in handoff
    assert parse_reference_chat_ids(handoff) == [source.id]
    rows = await chat_service._referenced_document_rows(db, auth, handoff)
    assert rows and "FACT_PAST_30000" in (rows[0].text_excerpt or "")
    prepared = _prepare(
        documents=[DocumentSource(rows[0].filename, rows[0].text_excerpt, rows[0].excerpt_status)],
        context_window=80_000,
        user_message="What fact is past the old prefix?",
    )
    assert "FACT_PAST_30000" in (prepared.runtime_context or "")
    other = await create_other_auth(db)
    hidden = await chat_service._referenced_document_rows(db, other, handoff)
    assert hidden == []


def test_legacy_excerpt_recovers_only_when_the_source_exists():
    full = ("A" * 1_000) + "LIBRARY_END_FACT"
    prefix = "A" * 1_000
    assert assess_legacy_excerpt(prefix, "ready", source_text=full) == "recoverable"
    assert assess_legacy_excerpt(prefix + "\n[Content truncated]", "partial", source_text=None) == "source_missing"
    assert assess_legacy_excerpt("short complete note", "ready", source_text=None) == "unchanged"
    assert assess_legacy_excerpt("A" * 100_000, "ready", source_text=None) == "source_missing"


@pytest.mark.asyncio
async def test_legacy_library_document_and_missing_upload_are_handled_safely(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(tmp_path))
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Legacy")
    db.add(chat)
    await db.flush()
    full = ("B" * 120) + "RECOVERED_END"
    item = LibraryItem(
        org_id=auth.org_id,
        user_id=auth.user.id,
        item_type="document",
        title="Note",
        content_text=full,
    )
    db.add(item)
    await db.flush()
    library_row = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        library_item_id=item.id,
        filename="note.txt",
        stored_name="note.txt",
        content_type="text/plain",
        size_bytes=10,
        relative_path=f"library-ref/{item.id}",
        text_excerpt="B" * 120,
        excerpt_status="ready",
    )
    missing_row = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        filename="gone.txt",
        stored_name="gone.txt",
        content_type="text/plain",
        size_bytes=100_000,
        relative_path="files/gone.txt",
        text_excerpt="C" * 100_000,
        excerpt_status="ready",
    )
    kept = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        filename="short.txt",
        stored_name="short.txt",
        content_type="text/plain",
        size_bytes=5,
        relative_path="files/short.txt",
        text_excerpt="short",
        excerpt_status="ready",
    )
    db.add_all([library_row, missing_row, kept])
    await db.flush()
    notes = await refresh_legacy_attachment_rows(
        db,
        [library_row, missing_row, kept],
        org_id=auth.org_id,
        user_id=auth.user.id,
    )
    assert library_row.text_excerpt == full
    assert "RECOVERED_END" in (library_row.text_excerpt or "")
    assert library_row.excerpt_status == "ready"
    assert missing_row.text_excerpt == "C" * 100_000
    assert missing_row.excerpt_status == "partial"
    assert "not recovered" in notes[id(missing_row)].lower()
    assert kept.text_excerpt == "short"
    assert kept.excerpt_status == "ready"
    assert id(kept) not in notes


@pytest.mark.asyncio
async def test_legacy_upload_on_disk_is_reextracted(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(root))
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Upload")
    db.add(chat)
    await db.flush()
    relative = f"{auth.org_id}/legacy.txt"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    full = ("D" * 50) + "DISK_END_FACT"
    path.write_text(full, encoding="utf-8")
    row = ChatAttachment(
        org_id=auth.org_id,
        chat_id=chat.id,
        uploaded_by_user_id=auth.user.id,
        filename="legacy.txt",
        stored_name="legacy.txt",
        content_type="text/plain",
        size_bytes=len(full),
        relative_path=relative,
        text_excerpt="D" * 50 + "\n[Content truncated]",
        excerpt_status="partial",
    )
    db.add(row)
    await db.flush()
    notes = await refresh_legacy_attachment_rows(
        db, [row], org_id=auth.org_id, user_id=auth.user.id
    )
    assert notes == {}
    assert row.text_excerpt == full
    assert "DISK_END_FACT" in (row.text_excerpt or "")
    assert row.excerpt_status == "ready"


def _many_facts(count: int = 16, pad: int = 3_400) -> str:
    return "".join(f"FACT_{index:02d}\n" + ("w" * pad) for index in range(count))


def _fitting_window(_model_id: str) -> tuple[int, str]:
    """Large enough for the real answer prompt, small enough that a long file is batched."""
    return 24_000, "provider_metadata"


class _RecordingProvider:
    def __init__(self, *, fail: BaseException | None = None, overflow_over: int = 0) -> None:
        self.systems: list[str] = []
        self.fail = fail
        self.overflow_over = overflow_over

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096, **_kwargs):
        from app.llm.providers import LLMResponse

        self.systems.append(system)
        if self.fail is not None and "one batch" not in system and "Combine these section" not in system:
            raise self.fail
        if self.overflow_over and "one batch" in system and len(system) > self.overflow_over:
            raise RuntimeError("maximum context length exceeded")
        if "one batch" in system or "Combine these section" in system:
            facts = " ".join(dict.fromkeys(__import__("re").findall(r"FACT_\d+|CHECKPOINT_FACT", system)))
            return LLMResponse(text=facts or "none", tokens_input=1, tokens_output=1, cost_usd=0)
        echoed = " ".join(dict.fromkeys(__import__("re").findall(r"FACT_\d+|CHECKPOINT_FACT", system)))
        return LLMResponse(
            text=f"[[multimind_document_cursor start=3 total=9]]\nFINAL_ONLY {echoed}",
            tokens_input=1,
            tokens_output=1,
            cost_usd=0,
        )


async def _run_single(db, auth, provider, *, message: str, documents: list[DocumentSource], brain: str = ""):
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Auto")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message=message,
        model_set_id="research-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.PENDING,
    )
    db.add(turn)
    await db.flush()
    orchestrator = TurnOrchestrator()
    orchestrator._providers = type("Registry", (), {"get_provider": lambda self, _name: provider})()
    await orchestrator.run(
        db,
        TurnContext(
            turn_id=turn.id,
            chat_id=chat.id,
            org_id=auth.org_id,
            project_id=None,
            user_message=message,
            model_ids=["gpt-4.1"],
            verdict_model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            model_set_name="Single",
            user_brain_context=brain or None,
            recent_conversation_context="HISTORY_MARKER stays with the question.",
            document_sources=documents,
        ),
    )
    await db.commit()
    saved = (
        await db.execute(
            select(Turn).where(Turn.id == turn.id).options(
                __import__("sqlalchemy.orm", fromlist=["selectinload"]).selectinload(Turn.model_answers)
            )
        )
    ).scalar_one()
    return saved


@pytest.mark.asyncio
async def test_whole_document_finishes_in_one_turn_without_a_cursor(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "document_analysis_progress_dir", str(tmp_path / "progress"))
    monkeypatch.setattr("app.services.document_context.lookup_context_window", _fitting_window)
    provider = _RecordingProvider()
    text = _many_facts(16, 90_000)
    turn = await _run_single(
        db,
        auth,
        provider,
        message="Summarize the entire document",
        documents=[DocumentSource("policy.txt", text, "ready")],
        brain="BRAIN_MARKER_UNIQUE",
    )
    assert turn.status == TurnStatus.COMPLETED
    answer = turn.model_answers[0].text or ""
    assert "FINAL_ONLY" in answer
    assert "[[multimind_document_cursor" not in answer
    section_calls = [system for system in provider.systems if "one batch" in system]
    final_calls = [system for system in provider.systems if "FINAL" in system or "BRAIN_MARKER_UNIQUE" in system]
    assert len(section_calls) > 4
    assert len(section_calls) > 12
    final = provider.systems[-1]
    assert "BRAIN_MARKER_UNIQUE" in final
    assert "HISTORY_MARKER" in final
    for index in range(16):
        assert f"FACT_{index:02d}" in final
    calls_after = len(provider.systems)
    again = TurnOrchestrator()
    again._providers = type("Registry", (), {"get_provider": lambda self, _name: provider})()
    await again.run(
        db,
        TurnContext(
            turn_id=turn.id,
            chat_id=turn.chat_id,
            org_id=auth.org_id,
            project_id=None,
            user_message=turn.user_message,
            model_ids=["gpt-4.1"],
            verdict_model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            model_set_name="Single",
            document_sources=[DocumentSource("policy.txt", text, "ready")],
        ),
    )
    assert len(provider.systems) == calls_after


@pytest.mark.asyncio
async def test_checkpoint_skips_completed_sections_and_keeps_their_findings(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    from app.services.document_context import save_section_progress

    monkeypatch.setattr(get_settings(), "document_analysis_progress_dir", str(tmp_path / "progress"))
    monkeypatch.setattr("app.services.document_context.lookup_context_window", _fitting_window)
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Resume")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="Summarize the entire document",
        model_set_id="research-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gpt-4.1",
        status=TurnStatus.PENDING,
    )
    db.add(turn)
    await db.flush()
    save_section_progress(
        turn.id,
        "gpt-4.1",
        [
            {
                "filename": "policy.txt",
                "label": "section 1",
                "index": 0,
                "section_indexes": [0],
                "text": "CHECKPOINT_FACT",
                "failed": False,
            }
        ],
    )
    provider = _RecordingProvider()
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
            model_ids=["gpt-4.1"],
            verdict_model_id="gpt-4.1",
            strategy=Strategy.SYNTHESIZE,
            model_set_name="Single",
            document_sources=[DocumentSource("policy.txt", _many_facts(8, 90_000), "ready")],
        ),
    )
    section_calls = [system for system in provider.systems if "one batch" in system]
    assert section_calls
    assert all("FACT_00" not in system for system in section_calls)
    assert "CHECKPOINT_FACT" in provider.systems[-1]
    assert "[[multimind_document_cursor" not in (provider.systems[-1])


@pytest.mark.asyncio
async def test_context_overflow_repacks_a_section(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "document_analysis_progress_dir", str(tmp_path / "progress"))
    monkeypatch.setattr("app.services.document_context.lookup_context_window", _fitting_window)
    provider = _RecordingProvider(overflow_over=3_000)
    turn = await _run_single(
        db,
        auth,
        provider,
        message="Summarize the entire document",
        documents=[DocumentSource("policy.txt", _many_facts(4, 90_000), "ready")],
    )
    assert any(" a" in system or " b" in system for system in provider.systems)
    assert turn.status == TurnStatus.COMPLETED
    assert "[[multimind_document_cursor" not in (turn.model_answers[0].text or "")


@pytest.mark.asyncio
async def test_permanent_provider_failure_does_not_fake_a_complete_answer(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "document_analysis_progress_dir", str(tmp_path / "progress"))
    provider = _RecordingProvider(fail=RuntimeError("upstream exploded"))
    turn = await _run_single(
        db,
        auth,
        provider,
        message="Hello",
        documents=[DocumentSource("note.txt", "short note", "ready")],
    )
    assert turn.status == TurnStatus.FAILED
    assert all((answer.text or "") == "" for answer in turn.model_answers)


@pytest.mark.asyncio
async def test_plain_chat_makes_one_model_call(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "document_analysis_progress_dir", str(tmp_path / "progress"))
    provider = _RecordingProvider()
    turn = await _run_single(db, auth, provider, message="Hello", documents=[])
    assert turn.status == TurnStatus.COMPLETED
    assert len(provider.systems) == 1
    assert "one batch" not in provider.systems[0]


def test_verified_context_window_is_used_for_a_large_document(tmp_path, monkeypatch):
    from app.llm.pricing import get_pricing_service

    monkeypatch.setattr(get_settings(), "openrouter_context_cache_path", str(tmp_path / "missing.json"))
    pricing = get_pricing_service()
    slug = "openai/gpt-4.1"
    previous = pricing._meta_by_slug.get(slug)
    pricing._meta_by_slug[slug] = {"id": slug, "context_length": 200_000}
    text = "Z" * 180_000
    try:
        wide = prepare_model_context(
            model_id="gpt-4.1",
            user_message="What does the file say?",
            fixed_system="System instructions stay intact.",
            references=None,
            documents=[DocumentSource("notes.txt", text, "ready")],
            requested_output_tokens=256,
        )
        assert wide.window_tokens == 200_000
        assert wide.window_source == "provider_metadata"
        assert wide.diagnostics["mode"] == "full"
        assert text in (wide.runtime_context or "")
    finally:
        if previous is None:
            pricing._meta_by_slug.pop(slug, None)
        else:
            pricing._meta_by_slug[slug] = previous

    unknown = prepare_model_context(
        model_id="gpt-4.1",
        user_message="What does the file say?",
        fixed_system="System instructions stay intact.",
        references=None,
        documents=[DocumentSource("notes.txt", text, "ready")],
        requested_output_tokens=256,
    )
    assert unknown.window_source == "conservative_estimate"
    assert unknown.diagnostics["mode"] != "full"
    assert unknown.diagnostics["incomplete"] is True
    assert "Context window is unknown" in (unknown.runtime_context or "")
    assert text not in (unknown.runtime_context or "")


def test_several_documents_keep_end_facts_when_the_window_fits():
    documents = []
    for index in range(3):
        documents.append(
            DocumentSource(
                f"library-{index}.txt",
                ("m" * 2_000) + f"\nEND_FACT_{index}",
                "ready",
            )
        )
    prepared = _prepare(
        user_message="What is at the end of each file?",
        documents=documents,
        context_window=64_000,
    )
    runtime = prepared.runtime_context or ""
    assert prepared.diagnostics["mode"] == "full"
    for index in range(3):
        assert f"END_FACT_{index}" in runtime
