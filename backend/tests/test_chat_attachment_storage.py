"""Unit tests for chat attachment filesystem safety helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.chat_attachment_storage import (
    UnsafeAttachmentPathError,
    resolve_attachment_path,
    safe_delete_attachment_file,
)


def test_resolve_rejects_traversal(tmp_path: Path):
    root = tmp_path / "attachments"
    root.mkdir()
    with pytest.raises(UnsafeAttachmentPathError):
        resolve_attachment_path("../secret.txt", root=root)
    with pytest.raises(UnsafeAttachmentPathError):
        resolve_attachment_path("org/../../secret.txt", root=root)


def test_safe_delete_missing_file_is_success(tmp_path: Path):
    root = tmp_path / "attachments"
    root.mkdir()
    assert safe_delete_attachment_file("org/chat/missing.txt", root=root) is True


def test_safe_delete_never_touches_outside_file(tmp_path: Path):
    root = tmp_path / "attachments"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    with pytest.raises(UnsafeAttachmentPathError):
        safe_delete_attachment_file("../outside.txt", root=root)
    assert outside.read_text(encoding="utf-8") == "keep"


def test_safe_delete_removes_file_under_root(tmp_path: Path):
    root = tmp_path / "attachments"
    target = root / "org" / "chat" / "a.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    assert safe_delete_attachment_file("org/chat/a.txt", root=root) is True
    assert not target.exists()
