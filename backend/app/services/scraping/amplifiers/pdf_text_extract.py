"""Safe PDF text extraction with a graceful fallback."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO


@dataclass(frozen=True)
class PdfTextExtraction:
    text: str
    parser: str


def extract_pdf_text(payload: bytes) -> PdfTextExtraction:
    if not payload:
        return PdfTextExtraction(text="", parser="empty")
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(BytesIO(payload))
        parts = [page.extract_text() or "" for page in reader.pages]
        return PdfTextExtraction(text="\n".join(part for part in parts if part).strip(), parser="pypdf")
    except Exception:
        return PdfTextExtraction(text="", parser="unavailable")
