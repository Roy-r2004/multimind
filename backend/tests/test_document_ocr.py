"""Scanned-page OCR uses the OpenRouter vision payload and stays off with the flag."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.models import CostRecord, ProcessedDocument, ProcessedSection, UsageKind
from app.llm.providers import LLMResponse, OpenRouterProvider
from app.services.chat_vision import VisionImage
from app.services.document_processing import (
    default_ocr_pages,
    prepare_stored_file,
    should_defer_extraction,
)
from tests.conftest import create_other_auth
from tests.test_chat_attachments import _pdf_with_pages

OCR_MODEL = "google/gemini-3-flash-preview"


class _VisionProvider:
    def __init__(self, fail_pages: set[int] | None = None) -> None:
        self.fail_pages = fail_pages or set()
        self.pages: list[int] = []
        self.models: list[str] = []
        self.payloads: list[list] = []

    async def complete(self, *, model: str, images=None, **_kwargs):
        page = int(images[0].filename.removeprefix("page-").removesuffix(".png"))
        self.pages.append(page)
        self.models.append(model)
        self.payloads.append(images)
        if page in self.fail_pages:
            raise RuntimeError("vision unavailable")
        return LLMResponse(
            text=f"OCR_PAGE_{page}",
            tokens_input=5,
            tokens_output=3,
            cost_usd=0.02,
        )


def _registry(provider: _VisionProvider):
    return type("Registry", (), {"get_provider": lambda self, _name: provider})()


def _enable(monkeypatch, provider: _VisionProvider) -> None:
    monkeypatch.setattr(get_settings(), "large_documents_enabled", True)
    monkeypatch.setattr(get_settings(), "document_ocr_model", OCR_MODEL)
    monkeypatch.setattr(
        "app.services.document_processing.render_pdf_page_png",
        lambda _path, page: f"png-{page}".encode(),
    )
    monkeypatch.setattr(
        "app.llm.providers.get_provider_registry",
        lambda: _registry(provider),
    )


def test_ocr_model_default_can_be_overridden():
    assert Settings.model_fields["document_ocr_model"].default == OCR_MODEL


def test_flag_off_does_not_start_ocr_or_raise_the_upload_limit(monkeypatch):
    monkeypatch.setattr(get_settings(), "large_documents_enabled", False)
    assert should_defer_extraction(".pdf") is False
    assert get_settings().chat_attachment_max_bytes == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_openrouter_payload_carries_the_rendered_page(monkeypatch):
    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "SCANNED LINE"}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "cost": 0.01},
            }

        @property
        def text(self):
            return ""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers=None, json=None):
            captured["json"] = json
            captured["authorization"] = headers["Authorization"]
            return _FakeResponse()

    monkeypatch.setattr("app.llm.providers.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    image = VisionImage(
        filename="page-4.png",
        media_type="image/png",
        data_base64="UE5H",
    )
    response = await OpenRouterProvider().complete(
        system="Transcribe the page.",
        user="Transcribe page 4.",
        model=OCR_MODEL,
        images=[image],
    )
    body = captured["json"]
    assert body["model"] == OCR_MODEL
    content = body["messages"][1]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,UE5H"
    assert captured["authorization"] == "Bearer test-key"
    assert response.text == "SCANNED LINE"


@pytest.mark.asyncio
async def test_native_scanned_and_mixed_pdfs(db, auth, tmp_path, monkeypatch):
    provider = _VisionProvider()
    _enable(monkeypatch, provider)

    native = tmp_path / "native.pdf"
    native.write_bytes(_pdf_with_pages(["NATIVE_PAGE_FACT"]))
    native_doc = await prepare_stored_file(
        db,
        org_id=auth.org_id,
        path=native,
        extension=".pdf",
        filename="native.pdf",
        ocr_pages=default_ocr_pages,
    )
    assert provider.pages == []
    assert native_doc.status == "ready"

    scanned = tmp_path / "scanned.pdf"
    scanned.write_bytes(_pdf_with_pages(["", ""]))
    scanned_doc = await prepare_stored_file(
        db,
        org_id=auth.org_id,
        path=scanned,
        extension=".pdf",
        filename="scanned.pdf",
        ocr_pages=default_ocr_pages,
    )
    assert provider.pages[-2:] == [1, 2]
    assert provider.models[-2:] == [OCR_MODEL, OCR_MODEL]
    assert provider.payloads[-1][0].data_base64
    assert scanned_doc.pages_done == scanned_doc.pages_total == 2
    scanned_sections = (
        await db.execute(
            select(ProcessedSection)
            .where(ProcessedSection.document_id == scanned_doc.id)
            .order_by(ProcessedSection.section_index)
        )
    ).scalars().all()
    assert [section.origin for section in scanned_sections] == ["ocr", "ocr"]
    assert [section.page_number for section in scanned_sections] == [1, 2]
    assert "OCR_PAGE_2" in scanned_sections[1].text

    provider.pages.clear()
    mixed = tmp_path / "mixed.pdf"
    mixed.write_bytes(_pdf_with_pages(["NATIVE_PAGE_FACT", ""]))
    mixed_doc = await prepare_stored_file(
        db,
        org_id=auth.org_id,
        path=mixed,
        extension=".pdf",
        filename="mixed.pdf",
        ocr_pages=default_ocr_pages,
    )
    assert provider.pages == [2]
    mixed_sections = (
        await db.execute(
            select(ProcessedSection)
            .where(ProcessedSection.document_id == mixed_doc.id)
            .order_by(ProcessedSection.section_index)
        )
    ).scalars().all()
    assert mixed_sections[0].origin == "native"
    assert "NATIVE_PAGE_FACT" in mixed_sections[0].text
    assert mixed_sections[1].origin == "ocr"
    assert mixed_sections[1].page_number == 2


@pytest.mark.asyncio
async def test_ocr_failure_marks_the_page_unread_and_records_successful_cost(
    db, auth, tmp_path, monkeypatch
):
    provider = _VisionProvider(fail_pages={2})
    _enable(monkeypatch, provider)
    path = tmp_path / "partial.pdf"
    path.write_bytes(_pdf_with_pages(["", ""]))
    document = await prepare_stored_file(
        db,
        org_id=auth.org_id,
        path=path,
        extension=".pdf",
        filename="partial.pdf",
        chat_id=None,
        ocr_pages=default_ocr_pages,
    )
    assert document.status == "partial"
    assert document.unread_pages == [2]
    sections = (
        await db.execute(
            select(ProcessedSection)
            .where(ProcessedSection.document_id == document.id)
            .order_by(ProcessedSection.page_number)
        )
    ).scalars().all()
    assert sections[0].origin == "ocr"
    assert sections[1].origin == "unread"
    assert "OCR_PAGE_1" in sections[0].text
    assert "Unreadable page" in sections[1].text
    costs = (await db.execute(select(CostRecord).where(CostRecord.org_id == auth.org_id))).scalars().all()
    ocr_costs = [row for row in costs if row.kind == UsageKind.DOCUMENT_PROCESSING and row.metadata_ and row.metadata_.get("stage") == "ocr"]
    assert len(ocr_costs) == 1
    assert ocr_costs[0].model_id == OCR_MODEL
    assert ocr_costs[0].turn_id is None
    assert ocr_costs[0].cost_usd == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_ocr_results_stay_inside_the_organization(db, auth, tmp_path, monkeypatch):
    provider = _VisionProvider()
    _enable(monkeypatch, provider)
    path = tmp_path / "shared.pdf"
    path.write_bytes(_pdf_with_pages([""]))
    own = await prepare_stored_file(
        db, org_id=auth.org_id, path=path, extension=".pdf", filename="shared.pdf", ocr_pages=default_ocr_pages
    )
    other = await create_other_auth(db)
    theirs = await prepare_stored_file(
        db, org_id=other.org_id, path=path, extension=".pdf", filename="shared.pdf", ocr_pages=default_ocr_pages
    )
    assert own.id != theirs.id
    leaked = (
        await db.execute(
            select(ProcessedDocument).where(
                ProcessedDocument.org_id == other.org_id,
                ProcessedDocument.id == own.id,
            )
        )
    ).scalar_one_or_none()
    assert leaked is None
    own_cost = (
        await db.execute(select(CostRecord).where(CostRecord.org_id == auth.org_id))
    ).scalars().all()
    other_cost = (
        await db.execute(select(CostRecord).where(CostRecord.org_id == other.org_id))
    ).scalars().all()
    assert own_cost and other_cost
    assert {row.org_id for row in own_cost} == {auth.org_id}


def test_compose_gives_api_and_worker_the_ocr_model_without_a_secret():
    root = Path(__file__).resolve().parents[2]
    for name in ("docker-compose.yml", "docker-compose.prod.yml", "docker-compose.ui-test.yml"):
        text = (root / name).read_text(encoding="utf-8")
        assert "DOCUMENT_OCR_MODEL: ${DOCUMENT_OCR_MODEL:-google/gemini-3-flash-preview}" in text
        assert "OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}" in text
        assert "sk-or-" not in text
        assert "sk-" not in text
