"""Fit attached documents to one model's context window.

Token figures are estimates from ``estimate_tokens`` (characters / 4), not a
provider tokenizer. Diagnostics contain counts and filenames only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.llm.catalog import estimate_tokens, get_model
from app.llm.pricing import get_pricing_service

logger = get_logger(__name__)

SAFETY_MARGIN_TOKENS = 512
CONSERVATIVE_CONTEXT_WINDOW = 32_768
MIN_OUTPUT_TOKENS = 256
SECTION_OUTPUT_TOKENS = 800
COVERAGE_SECTION_CHARS = 4_000
CHUNK_TARGET_CHARS = 3_500
CHUNK_OVERLAP_CHARS = 300
REFERENCE_FLOOR_TOKENS = 800
ESTIMATE_METHOD = "chars/4"

ATTACHMENT_BLOCK_START = "<<<MULTIMIND_ATTACHMENTS>>>"
ATTACHMENT_BLOCK_END = "<<<END_MULTIMIND_ATTACHMENTS>>>"
_BLOCK_RE = re.compile(
    r"\n*<<<MULTIMIND_ATTACHMENTS>>>.*?<<<END_MULTIMIND_ATTACHMENTS>>>\n*",
    re.DOTALL,
)
_TERM_RE = re.compile(r"[a-z0-9_]{3,}")
_PAGE_SPLIT_RE = re.compile(r"(?=\[Page \d+\])")
_SHEET_SPLIT_RE = re.compile(r"(?=Worksheet: )")
_CURSOR_RE = re.compile(r"\[\[multimind_document_cursor start=(\d+) total=(\d+)\]\]")
_REFERENCE_CHAT_RE = re.compile(
    r"\[\[multimind_reference_chat id=([0-9a-fA-F-]{36})\]\]"
)
_CONTINUE_RE = re.compile(r"\b(continue|resume)\b", re.IGNORECASE)
_COMPLETE_RE = re.compile(
    r"("
    r"\b(?:entire|whole|full)\s+(?:document|documents|file|files|pdf|attachment|attachments)\b"
    r"|read the entire"
    r"|read all (?:documents|files|pages|sections|attachments)"
    r"|summar(?:ize|ise) (?:the )?(?:entire|whole|all)"
    r"|all sections"
    r"|extract all"
    r"|every requirement"
    r"|all requirements"
    r"|compare (?:all|the documents|every)"
    r"|every table"
    r"|analy[sz]e every"
    r"|every worksheet"
    r"|all worksheets"
    r"|every page"
    r"|all pages"
    r"|find every"
    r"|find all"
    r")",
    re.IGNORECASE,
)
_STOP = frozenset(
    """
    the and for that with this from your have was are not but you all any can
    about into document file attached please what when where which who how
    """.split()
)

_UNAVAILABLE = {
    "empty": "(No readable text found. This file may be scanned, empty, or image-based. OCR is not applied.)",
    "failed": "(Content unavailable; extraction failed.)",
    "image": "(Image attached. Extracted text is unavailable unless image analysis was stored.)",
}


class DocumentContextOverflow(Exception):
    """The assembled prompt cannot fit the model window. The provider is not called."""


@dataclass
class DocumentSource:
    filename: str
    text: str | None = None
    status: str = "ready"
    recovery_note: str | None = None


@dataclass
class _Chunk:
    filename: str
    label: str
    text: str
    order: int


@dataclass
class PreparedModelContext:
    runtime_context: str | None
    runtime_token_budget: int
    document_token_budget: int
    reference_context: str | None
    reserved_output_tokens: int
    window_tokens: int
    window_source: str
    sections: list[dict] = field(default_factory=list)
    skipped_sections: list[dict] = field(default_factory=list)
    overflow_message: str | None = None
    diagnostics: dict = field(default_factory=dict)
    resume_section_index: int | None = None
    section_total: int = 0


def requests_complete_coverage(question: str) -> bool:
    return bool(_COMPLETE_RE.search(question or ""))


def requests_document_continuation(question: str) -> bool:
    text = (question or "").strip()
    if not text:
        return False
    if text.lower() in {"continue", "resume"}:
        return True
    return bool(_CONTINUE_RE.search(text) and re.search(r"\b(document|section|analysis|file|attachment)\b", text, re.I))


def parse_document_cursor(text: str | None) -> int | None:
    matches = list(_CURSOR_RE.finditer(text or ""))
    if not matches:
        return None
    return int(matches[-1].group(1))


def parse_reference_chat_ids(text: str | None) -> list[str]:
    seen: list[str] = []
    for match in _REFERENCE_CHAT_RE.finditer(text or ""):
        chat_id = match.group(1)
        if chat_id not in seen:
            seen.append(chat_id)
    return seen


def strip_document_cursors(text: str | None) -> str:
    """Remove internal progress markers so they never appear in a user-visible answer."""
    return _CURSOR_RE.sub("", text or "").strip()


def is_context_overflow_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(
        phrase in message
        for phrase in (
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
            "input is too long",
            "prompt is too long",
            "exceeds the context",
            "exceeds its context",
            "was not sent",
        )
    )


def _analysis_call_limit() -> int:
    from app.core.config import get_settings

    return max(int(get_settings().document_analysis_max_calls), 1)


def reference_chat_marker(chat_id: str) -> str:
    return f"[[multimind_reference_chat id={chat_id}]]"


def excerpt_looks_truncated(excerpt: str | None, status: str | None) -> bool:
    text = excerpt or ""
    if (status or "") == "partial":
        return True
    if "[Content truncated]" in text:
        return True
    return len(text) == 100_000


def assess_legacy_excerpt(
    excerpt: str | None,
    status: str | None,
    *,
    source_text: str | None,
) -> str:
    """Return unchanged, recoverable, or source_missing. Never treats a missing source as recovered."""
    text = excerpt or ""
    source = source_text
    longer_source = bool(source) and len(source) > len(text) and (
        not text or source.startswith(text.split("[Content truncated]", 1)[0].rstrip())
    )
    if not excerpt_looks_truncated(text, status) and not longer_source:
        return "unchanged"
    if source:
        return "recoverable"
    return "source_missing"


def lookup_context_window(model_id: str) -> tuple[int, str]:
    """Return (window, source). Missing catalog metadata uses a conservative window."""
    service = get_pricing_service()
    model = get_model(model_id)
    metadata = service.get_slug_metadata(model.provider_model) or {}
    value = metadata.get("context_length")
    if not isinstance(value, int) or value <= 0:
        service.load_persisted_context_lengths()
        metadata = service.get_slug_metadata(model.provider_model) or {}
        value = metadata.get("context_length")
    if isinstance(value, int) and value > 0:
        return value, "provider_metadata"
    return CONSERVATIVE_CONTEXT_WINDOW, "conservative_estimate"


def output_reserve(window_tokens: int, requested_output_tokens: int) -> int | None:
    room = window_tokens - SAFETY_MARGIN_TOKENS - 1_024
    if room < MIN_OUTPUT_TOKENS:
        return None
    return min(max(requested_output_tokens, MIN_OUTPUT_TOKENS), room)


def strip_baked_attachment_block(text: str | None) -> str | None:
    if not text:
        return None
    stripped = _BLOCK_RE.sub("\n\n", text).strip()
    return stripped or None


def sources_from_attachment_rows(
    rows: list,
    recovery_notes: dict | None = None,
) -> list[DocumentSource]:
    notes = recovery_notes or {}
    sources: list[DocumentSource] = []
    for row in rows:
        sources.append(
            DocumentSource(
                filename=getattr(row, "filename", None) or "file",
                text=getattr(row, "text_excerpt", None),
                status=(getattr(row, "excerpt_status", None) or "failed"),
                recovery_note=notes.get(id(row)),
            )
        )
    return sources


def context_exceeds_budget(
    system: str,
    user: str,
    window_tokens: int,
    reserved_output_tokens: int,
) -> bool:
    estimated = estimate_tokens(system) + estimate_tokens(user)
    return estimated + reserved_output_tokens + SAFETY_MARGIN_TOKENS > window_tokens


def render_stored_attachment_block(
    attachments: list,
    recovery_notes: dict | None = None,
) -> str | None:
    """Persist the full extracted text. Fitting to a model window happens at call time."""
    if not attachments:
        return None
    body = _render_documents(
        sources_from_attachment_rows(attachments, recovery_notes),
        coverage="stored",
    )
    if not body.strip():
        return None
    return f"{ATTACHMENT_BLOCK_START}\n{body}\n{ATTACHMENT_BLOCK_END}"


def prepare_model_context(
    *,
    model_id: str,
    user_message: str,
    fixed_system: str,
    references: str | None,
    documents: list[DocumentSource],
    requested_output_tokens: int,
    context_window: int | None = None,
    window_source: str | None = None,
    runtime_token_budget: int | None = None,
    plan_complete_sections: bool = True,
    section_cursor: int = 0,
    completed_section_indexes: set[int] | None = None,
) -> PreparedModelContext:
    if context_window is None:
        context_window, resolved_source = lookup_context_window(model_id)
    else:
        resolved_source = window_source or "caller"
    reserved = output_reserve(context_window, requested_output_tokens)
    question_tokens = estimate_tokens(user_message) if user_message else 0
    fixed_tokens = estimate_tokens(fixed_system) if fixed_system else 0
    if reserved is None:
        message = _overflow_message(
            model_id,
            context_window,
            resolved_source,
            fixed_tokens,
            requested_output_tokens,
        )
        prepared = PreparedModelContext(
            runtime_context=None,
            runtime_token_budget=0,
            document_token_budget=0,
            reference_context=None,
            reserved_output_tokens=0,
            window_tokens=context_window,
            window_source=resolved_source,
            overflow_message=message,
        )
        prepared.diagnostics = _diagnostics(
            prepared,
            model_id=model_id,
            mode="overflow",
            fixed_tokens=fixed_tokens,
            question_tokens=question_tokens,
            reference_tokens=0,
            document_tokens=0,
            files=[],
        )
        _log(prepared)
        return prepared

    if runtime_token_budget is None:
        runtime_token_budget = (
            context_window
            - reserved
            - SAFETY_MARGIN_TOKENS
            - fixed_tokens
            - question_tokens
        )
    if runtime_token_budget < 0:
        message = _overflow_message(
            model_id,
            context_window,
            resolved_source,
            fixed_tokens + question_tokens,
            reserved,
        )
        prepared = PreparedModelContext(
            runtime_context=None,
            runtime_token_budget=runtime_token_budget,
            document_token_budget=0,
            reference_context=None,
            reserved_output_tokens=reserved,
            window_tokens=context_window,
            window_source=resolved_source,
            overflow_message=message,
        )
        prepared.diagnostics = _diagnostics(
            prepared,
            model_id=model_id,
            mode="overflow",
            fixed_tokens=fixed_tokens,
            question_tokens=question_tokens,
            reference_tokens=0,
            document_tokens=0,
            files=[],
        )
        _log(prepared)
        return prepared

    reference_text, reference_tokens = _fit_references(references, runtime_token_budget, documents)
    document_budget = max(runtime_token_budget - reference_tokens, 0)
    complete = plan_complete_sections and requests_complete_coverage(user_message)
    block, mode, sections, skipped, file_stats = _fit_documents(
        documents,
        document_budget,
        user_message,
        complete=complete,
        section_chars=_section_chars(context_window, user_message),
        section_cursor=max(section_cursor, 0),
        completed_section_indexes=completed_section_indexes or set(),
    )
    if mode != "none":
        block = _join_runtime(
            _window_status_line(context_window, resolved_source, mode),
            block,
        )
    runtime = _join_runtime(reference_text, block)
    document_tokens = estimate_tokens(block) if block else 0
    prepared = PreparedModelContext(
        runtime_context=runtime,
        runtime_token_budget=runtime_token_budget,
        document_token_budget=document_budget,
        reference_context=reference_text,
        reserved_output_tokens=reserved,
        window_tokens=context_window,
        window_source=resolved_source,
        sections=sections,
        skipped_sections=skipped,
        resume_section_index=skipped[0]["index"] if skipped else None,
        section_total=_section_total(sections, skipped, file_stats),
    )
    prepared.diagnostics = _diagnostics(
        prepared,
        model_id=model_id,
        mode=mode,
        fixed_tokens=fixed_tokens,
        question_tokens=question_tokens,
        reference_tokens=reference_tokens,
        document_tokens=document_tokens,
        files=file_stats,
    )
    _log(prepared)
    return prepared


def render_section_findings(
    prepared: PreparedModelContext,
    findings: list[dict],
) -> str:
    """Assemble section notes so the final call stays inside the runtime budget."""
    status = _complete_status(
        prepared.sections,
        prepared.skipped_sections,
        findings,
        resume_section_index=prepared.resume_section_index,
        section_total=prepared.section_total,
    )
    parts = [status]
    used = estimate_tokens(status)
    budget = max(prepared.document_token_budget, 0)
    omitted = 0
    for finding in findings:
        block = (
            f"[{finding.get('filename', 'file')} | {finding.get('label', 'section')}]\n"
            f"{finding.get('text') or ''}"
        )
        cost = estimate_tokens(block) + 2
        if used + cost > budget:
            omitted += 1
            continue
        parts.append(block)
        used += cost
    if omitted:
        parts.append(
            f"[Section findings omitted to fit the model context budget] {omitted} section note(s) were not copied into the final prompt."
        )
    if prepared.skipped_sections:
        labels = ", ".join(
            f"{item['filename']} {item['label']}" for item in prepared.skipped_sections[:8]
        )
        extra = len(prepared.skipped_sections) - min(len(prepared.skipped_sections), 8)
        suffix = f" and {extra} more" if extra else ""
        parts.append(
            f"[Sections not processed] {len(prepared.skipped_sections)} section(s) remain: {labels}{suffix}."
        )
    return _join_runtime(prepared.reference_context, "\n\n".join(parts))


def cap_answer_texts(texts: list[str], budget_tokens: int) -> list[str]:
    if not texts:
        return []
    if budget_tokens <= 0:
        return [
            "[Council answer omitted: the verdict model context budget was exhausted]"
            for _ in texts
        ]
    share = max(budget_tokens // len(texts), 1)
    capped: list[str] = []
    marker = "\n[Council answer truncated to fit the verdict model context budget]"
    for text in texts:
        if estimate_tokens(text) <= share:
            capped.append(text)
            continue
        keep = max(share * 4 - len(marker), 0)
        capped.append((text or "")[:keep].rstrip() + marker)
    return capped


def cursor_after_section_reads(prepared: PreparedModelContext, findings: list[dict]) -> str | None:
    """Internal progress only. User-visible answers must not include this marker."""
    starts: list[int] = []
    for finding in findings:
        if finding.get("failed") and isinstance(finding.get("index"), int):
            starts.append(finding["index"])
    if prepared.resume_section_index is not None:
        starts.append(prepared.resume_section_index)
    if not starts or prepared.section_total <= 0:
        return None
    return document_cursor_marker(min(starts), prepared.section_total)


def document_cursor_marker(start: int, total: int) -> str:
    return f"[[multimind_document_cursor start={start} total={total}]]"


def _section_total(sections: list[dict], skipped: list[dict], file_stats: list[dict]) -> int:
    for group in (sections, skipped, file_stats):
        if group and group[0].get("section_total"):
            return int(group[0]["section_total"])
    return 0


def _window_status_line(window_tokens: int, window_source: str, mode: str) -> str:
    if window_source == "conservative_estimate":
        known = (
            f"Context window is unknown. Fallback budget: {window_tokens} estimated tokens "
            f"({ESTIMATE_METHOD})."
        )
    else:
        known = (
            f"Context window: {window_tokens} estimated tokens "
            f"({window_source}, {ESTIMATE_METHOD})."
        )
    if mode in {"full", "none"}:
        return known
    return known + " Coverage is incomplete. Text outside this budget was not sent."


def _section_chars(window_tokens: int, question: str) -> int:
    section_budget = window_tokens - SECTION_OUTPUT_TOKENS - SAFETY_MARGIN_TOKENS
    section_budget -= estimate_tokens(question) if question else 0
    section_budget -= 400
    return max(1_500, section_budget * 4)


def _fit_references(
    references: str | None,
    runtime_budget: int,
    documents: list[DocumentSource],
) -> tuple[str | None, int]:
    text = (references or "").strip()
    if not text:
        return None, 0
    tokens = estimate_tokens(text)
    minimum_documents = 200 if documents else 0
    if tokens + minimum_documents <= runtime_budget:
        return text, tokens
    reserve = 120 if documents else 0
    allowed = runtime_budget - reserve
    if documents:
        allowed = min(allowed, max(REFERENCE_FLOOR_TOKENS, (runtime_budget * 2) // 5))
    if tokens <= allowed:
        return text, tokens
    if allowed <= 0:
        note = "[Referenced context omitted]\nThe referenced conversation did not fit this model's context budget."
        return note, estimate_tokens(note)
    keep = max(allowed * 4 - 160, 0)
    trimmed = (
        text[:keep].rstrip()
        + "\n[Referenced context truncated]\nLater referenced text was omitted so document content could fit this model's context budget."
    )
    return trimmed, estimate_tokens(trimmed)


def _fit_documents(
    documents: list[DocumentSource],
    token_budget: int,
    question: str,
    *,
    complete: bool,
    section_chars: int,
    section_cursor: int = 0,
    completed_section_indexes: set[int] | None = None,
) -> tuple[str | None, str, list[dict], list[dict], list[dict]]:
    if not documents:
        return None, "none", [], [], []
    full = _render_documents(documents, coverage="full")
    file_stats = [_file_stat(source, coverage="pending") for source in documents]
    if token_budget <= 0:
        omitted = _omitted_manifest(documents, "The model context budget had no room for document text.")
        return omitted, "omitted", [], [], [_file_stat(source, coverage="omitted") for source in documents]
    if estimate_tokens(full) <= token_budget:
        for stat in file_stats:
            stat["coverage"] = "full" if stat["status"] in {"ready", "partial"} else stat["status"]
            stat["included_chars"] = stat["extracted_chars"]
        return full, "full", [], [], file_stats
    if complete:
        width = max(section_chars, 1_500)
        sections = _plan_sections(documents, width)
        total = len(sections)
        for stat in file_stats:
            stat["coverage"] = "complete_sections"
            stat["section_total"] = total
        done = completed_section_indexes or set()
        pending = [
            section
            for section in sections
            if section["index"] >= section_cursor and section["index"] not in done
        ]
        if not pending:
            notice = _complete_plan_notice(
                documents, [], [], total=total, resume=None, finished=True
            )
            return notice, "complete_sections", [], [], file_stats
        batches = _pack_batches(pending, width)
        limit = _analysis_call_limit()
        planned = batches[:limit]
        run_indexes = {index for batch in planned for index in batch["section_indexes"]}
        skipped = [section for section in pending if section["index"] not in run_indexes]
        for batch in planned:
            batch["section_total"] = total
        for section in skipped:
            section["section_total"] = total
        notice = _complete_plan_notice(
            documents,
            planned,
            skipped,
            total=total,
            resume=skipped[0]["index"] if skipped else None,
            finished=False,
        )
        return notice, "complete_sections", planned, skipped, file_stats

    retrieved, stats = _render_retrieved(documents, token_budget, question)
    return retrieved, "retrieved", [], [], stats


def _render_retrieved(
    documents: list[DocumentSource],
    token_budget: int,
    question: str,
) -> tuple[str, list[dict]]:
    terms = [term for term in _TERM_RE.findall(question.lower()) if term not in _STOP]
    usable = [source for source in documents if (source.text or "").strip()]
    passive = [source for source in documents if source not in usable]
    header = (
        "[Document context status]\n"
        f"Token estimates use {ESTIMATE_METHOD}, not a provider tokenizer.\n"
        "Selected sections were retrieved from the full extracted text, including the beginning, middle, and end.\n"
        "Do not claim the entire document was read."
    )
    stats: list[dict] = []
    parts = [header]
    used = estimate_tokens(header)
    share_count = max(len(usable), 1)
    share = max((token_budget - used) // share_count, 0)
    for source in usable:
        chunks = _chunks(source)
        chosen = _choose_chunks(chunks, terms, share)
        included = sum(len(chunk.text) for chunk in chosen)
        stats.append(
            {
                "filename": source.filename,
                "status": source.status,
                "extracted_chars": len(source.text or ""),
                "included_chars": included,
                "coverage": "retrieved" if chosen else "omitted",
                "chunks_selected": len(chosen),
                "chunks_total": len(chunks),
            }
        )
        if not chosen:
            parts.append(
                f"Attached file: {source.filename}\n"
                "[Document content omitted]\n"
                "No section of this file fit the remaining context budget."
            )
            continue
        body = "\n\n".join(
            f"[{source.filename} | {chunk.label}]\n{chunk.text}" for chunk in chosen
        )
        note = (
            f"Attached file: {source.filename}\n"
            f"Coverage: selected relevant sections {len(chosen)} of {len(chunks)}. "
            "Other extracted sections were not copied into this prompt.\n"
            f"Content:\n```text\n{body}\n```"
        )
        if used + estimate_tokens(note) > token_budget and parts:
            parts.append(
                f"Attached file: {source.filename}\n"
                "[Document content omitted]\n"
                "This file's selected sections did not fit the remaining context budget."
            )
            stats[-1]["coverage"] = "omitted"
            stats[-1]["included_chars"] = 0
            continue
        parts.append(note)
        used += estimate_tokens(note)
    for source in passive:
        stats.append(_file_stat(source, coverage=source.status or "unavailable"))
        parts.append(_unavailable_block(source))
    return "\n\n".join(parts), stats


def _choose_chunks(chunks: list[_Chunk], terms: list[str], token_budget: int) -> list[_Chunk]:
    if not chunks or token_budget <= 0:
        return []
    scored: list[tuple[int, int, _Chunk]] = []
    any_hit = False
    for chunk in chunks:
        score = sum(chunk.text.lower().count(term) for term in set(terms)) if terms else 0
        if score:
            any_hit = True
        scored.append((score, chunk.order, chunk))
    if terms and any_hit:
        ranked = sorted(scored, key=lambda item: (-item[0], item[1]))
    else:
        picks = []
        indexes = {0, len(chunks) // 2, len(chunks) - 1}
        for index in sorted(indexes):
            picks.append((1, chunks[index].order, chunks[index]))
        for chunk in chunks:
            if chunk.order not in indexes:
                picks.append((0, chunk.order, chunk))
        ranked = picks
    chosen: list[_Chunk] = []
    used = 0
    chosen_orders: set[int] = set()
    for _score, _order, chunk in ranked:
        cost = estimate_tokens(chunk.text) + estimate_tokens(chunk.label) + 8
        if used + cost > token_budget:
            continue
        chosen.append(chunk)
        chosen_orders.add(chunk.order)
        used += cost
    chosen.sort(key=lambda chunk: chunk.order)
    return chosen


def _chunks(source: DocumentSource) -> list[_Chunk]:
    text = source.text or ""
    pieces = _structural_pieces(text)
    chunks: list[_Chunk] = []
    order = 0
    for label, body in pieces:
        body = body.strip()
        if not body:
            continue
        if len(body) <= CHUNK_TARGET_CHARS:
            chunks.append(_Chunk(source.filename, label, body, order))
            order += 1
            continue
        start = 0
        part = 1
        while start < len(body):
            end = min(len(body), start + CHUNK_TARGET_CHARS)
            chunks.append(
                _Chunk(source.filename, f"{label} chars {start}-{end} part {part}", body[start:end], order)
            )
            order += 1
            part += 1
            if end >= len(body):
                break
            start = max(end - CHUNK_OVERLAP_CHARS, start + 1)
    return chunks


def _structural_pieces(text: str) -> list[tuple[str, str]]:
    if "[Page " in text:
        parts = [part.strip() for part in _PAGE_SPLIT_RE.split(text) if part.strip()]
        return [(part.split("\n", 1)[0][:40], part) for part in parts]
    if "Worksheet:" in text:
        parts = [part.strip() for part in _SHEET_SPLIT_RE.split(text) if part.strip()]
        return [(part.split("\n", 1)[0][:80], part) for part in parts]
    return [("document", text)]


def _plan_sections(documents: list[DocumentSource], section_chars: int) -> list[dict]:
    sections: list[dict] = []
    width = max(section_chars, 1_500)
    for source in documents:
        text = source.text or ""
        if not text.strip():
            continue
        start = 0
        part = 1
        while start < len(text):
            end = min(len(text), start + width)
            newline = text.rfind("\n", start + width // 2, end)
            if newline > start and end < len(text):
                end = newline
            sections.append(
                {
                    "filename": source.filename,
                    "label": f"section {part} chars {start}-{end}",
                    "text": text[start:end],
                    "index": len(sections),
                }
            )
            part += 1
            if end >= len(text):
                break
            start = end
    return sections


def _render_documents(documents: list[DocumentSource], *, coverage: str) -> str:
    lines = [
        "[Document context status]",
        f"Token estimates use {ESTIMATE_METHOD}, not a provider tokenizer.",
    ]
    if coverage == "full":
        lines.append("Each file below is the stored extraction included in full for this call.")
    elif coverage == "stored":
        lines.append("Stored extraction for this turn. A later model call may select sections if the model window is smaller.")
    blocks = ["\n".join(lines)]
    for source in documents:
        text = (source.text or "").strip()
        if source.status == "partial" and text:
            note = f"\n{source.recovery_note}" if source.recovery_note else ""
            blocks.append(
                f"Attached file: {source.filename}\n"
                "Extraction: partially extracted. The text below is everything that was stored. "
                "It is not the complete original file."
                f"{note}\n"
                f"Content:\n```text\n{text}\n```"
            )
        elif source.status in {"ready", "partial"} and text:
            label = "fully extracted; included in full" if coverage != "stored" else "fully extracted"
            blocks.append(
                f"Attached file: {source.filename}\n"
                f"Extraction: {label}.\n"
                f"Content:\n```text\n{text}\n```"
            )
        elif text and source.status == "image":
            blocks.append(
                f"Attached file: {source.filename}\n"
                "Extraction: image analysis stored.\n"
                f"Content:\n```text\n{text}\n```"
            )
        else:
            blocks.append(_unavailable_block(source))
    return "\n\n".join(blocks)


def _unavailable_block(source: DocumentSource) -> str:
    note = _UNAVAILABLE.get(source.status, f"(Content unavailable; excerpt status: {source.status})")
    if source.recovery_note:
        note = f"{note}\n{source.recovery_note}"
    return f"Attached file: {source.filename}\n{note}"


def _omitted_manifest(documents: list[DocumentSource], reason: str) -> str:
    lines = [
        "[Document context status]",
        reason,
        "Do not claim these documents were read.",
    ]
    for source in documents:
        lines.append(
            f"Attached file: {source.filename}\n"
            f"[Document content omitted] status={source.status} extracted_chars={len(source.text or '')}"
        )
    return "\n".join(lines)


def _pack_batches(sections: list[dict], call_chars: int) -> list[dict]:
    batches: list[dict] = []
    current: list[dict] = []
    size = 0
    limit = max(call_chars, 1)
    for section in sections:
        text_len = len(section["text"])
        if current and size + text_len > limit:
            batches.append(_batch_from(current))
            current = []
            size = 0
        current.append(section)
        size += text_len
    if current:
        batches.append(_batch_from(current))
    return batches


def _batch_from(sections: list[dict]) -> dict:
    names = {section["filename"] for section in sections}
    return {
        "filename": sections[0]["filename"] if len(names) == 1 else "multiple files",
        "label": f"sections {sections[0]['index'] + 1}-{sections[-1]['index'] + 1}",
        "text": "".join(section["text"] for section in sections),
        "index": sections[0]["index"],
        "section_indexes": [section["index"] for section in sections],
    }


def _complete_plan_notice(
    documents: list[DocumentSource],
    planned: list[dict],
    skipped: list[dict],
    *,
    total: int,
    resume: int | None,
    finished: bool,
) -> str:
    lines = [
        "[Document context status]",
        "Complete-document analysis was requested and the stored extraction does not fit one model call.",
        f"Extracted sections: {total}. This turn reads {len(planned)} batch(es) automatically.",
        f"Sections remaining after the batches above: {len(skipped)}.",
    ]
    if finished:
        lines.append("No further extracted sections remain.")
    elif resume is not None:
        lines.append(
            "Coverage is incomplete. The deployment call limit stopped the remaining sections. "
            "Do not claim the document was fully analyzed."
        )
    else:
        lines.append(
            "Every remaining extracted section is included in this turn. "
            "Coverage is incomplete until those section reads succeed."
        )
    lines.append("Files: " + ", ".join(source.filename for source in documents))
    return "\n".join(lines)


def _complete_status(
    planned: list[dict],
    skipped: list[dict],
    findings: list[dict],
    *,
    resume_section_index: int | None,
    section_total: int,
) -> str:
    failed = sum(1 for finding in findings if finding.get("failed"))
    remaining = len(skipped)
    total = section_total or (len(planned) + remaining)
    if remaining or failed:
        coverage = (
            "Coverage is incomplete for this complete-document analysis. "
            "Do not claim a complete analysis."
        )
    else:
        coverage = "Coverage: complete-document analysis processed every remaining extracted section."
    return "\n".join(
        [
            "[Document context status]",
            coverage,
            f"Sections processed: {len(findings)} of {total}.",
            f"Section calls that failed: {failed}.",
            f"Sections not processed: {remaining}.",
        ]
    )


def _file_stat(source: DocumentSource, *, coverage: str) -> dict:
    return {
        "filename": source.filename,
        "status": source.status,
        "extracted_chars": len(source.text or ""),
        "included_chars": len(source.text or "") if coverage == "full" else 0,
        "coverage": coverage,
    }


def _join_runtime(references: str | None, documents: str | None) -> str | None:
    parts = [part.strip() for part in (references, documents) if part and part.strip()]
    return "\n\n".join(parts) or None


def _overflow_message(
    model_id: str,
    window_tokens: int,
    window_source: str,
    fixed_tokens: int,
    reserved_output_tokens: int,
) -> str:
    if window_source == "conservative_estimate":
        window_line = (
            f"Its context window is unknown. Fallback budget: {window_tokens} estimated tokens. "
            "The prompt was not sent."
        )
    else:
        window_line = (
            f"Its context window is {window_tokens} tokens ({window_source}). "
            "The prompt was not sent."
        )
    return (
        f"The prompt for {model_id} was not sent. "
        f"{window_line} "
        f"System instructions and the current question use about {fixed_tokens} estimated tokens, "
        f"and {reserved_output_tokens} tokens are reserved for the reply. "
        f"Estimates use {ESTIMATE_METHOD}."
    )


def _diagnostics(
    prepared: PreparedModelContext,
    *,
    model_id: str,
    mode: str,
    fixed_tokens: int,
    question_tokens: int,
    reference_tokens: int,
    document_tokens: int,
    files: list[dict],
) -> dict:
    return {
        "model_id": model_id,
        "window_tokens": prepared.window_tokens,
        "window_source": prepared.window_source,
        "reserved_output_tokens": prepared.reserved_output_tokens,
        "estimated_input_tokens": fixed_tokens + question_tokens + reference_tokens + document_tokens,
        "fixed_tokens": fixed_tokens,
        "question_tokens": question_tokens,
        "reference_tokens": reference_tokens,
        "document_tokens": document_tokens,
        "mode": mode,
        "estimate_method": ESTIMATE_METHOD,
        "files": files,
        "sections_planned": len(prepared.sections),
        "sections_skipped": len(prepared.skipped_sections),
        "section_total": prepared.section_total,
        "resume_section_index": prepared.resume_section_index,
        "exceeds_budget": prepared.overflow_message is not None,
        "incomplete": mode not in {"full", "none"}
        or bool(prepared.skipped_sections)
        or any(stat.get("status") == "partial" for stat in files),
    }


def _log(prepared: PreparedModelContext) -> None:
    payload = dict(prepared.diagnostics)
    files = payload.get("files") or []
    payload["files"] = [
        {
            "filename": item.get("filename"),
            "status": item.get("status"),
            "extracted_chars": item.get("extracted_chars"),
            "included_chars": item.get("included_chars"),
            "coverage": item.get("coverage"),
            "chunks_selected": item.get("chunks_selected"),
            "chunks_total": item.get("chunks_total"),
        }
        for item in files
    ]
    logger.info("document_context_budget", **payload)


def _progress_path(turn_id: str, model_id: str) -> Path:
    from app.core.config import get_settings

    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", model_id or "model")
    return Path(get_settings().document_analysis_progress_dir) / turn_id / f"{safe_model}.json"


def load_section_progress(turn_id: str, model_id: str) -> list[dict]:
    path = _progress_path(turn_id, model_id)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    findings = payload.get("findings") if isinstance(payload, dict) else None
    return [item for item in findings or [] if isinstance(item, dict)]


def save_section_progress(turn_id: str, model_id: str, findings: list[dict]) -> None:
    path = _progress_path(turn_id, model_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"findings": findings}), encoding="utf-8")
    except OSError:
        logger.warning("document_progress_save_failed", turn_id=turn_id, model_id=model_id)


def clear_section_progress(turn_id: str) -> None:
    from app.core.config import get_settings

    root = Path(get_settings().document_analysis_progress_dir) / turn_id
    if not root.exists():
        return
    for child in root.glob("*.json"):
        try:
            child.unlink()
        except OSError:
            continue
    try:
        root.rmdir()
    except OSError:
        return


def completed_section_indexes(findings: list[dict]) -> set[int]:
    done: set[int] = set()
    for finding in findings:
        if finding.get("failed"):
            continue
        indexes = finding.get("section_indexes")
        if isinstance(indexes, list):
            done.update(index for index in indexes if isinstance(index, int))
        elif isinstance(finding.get("index"), int):
            done.add(finding["index"])
    return done
