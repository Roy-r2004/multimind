"""Library feature: folders, labels, documents, uploads, search, attach-to-chat."""

from __future__ import annotations

from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext, get_auth_context
from app.db.models import Chat, ChatAttachment, LibraryItem
from app.db.session import get_db
from app.main import create_app
from tests.conftest import create_other_auth


async def _client_for(db: AsyncSession, auth: AuthContext) -> AsyncClient:
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = lambda: auth
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create_chat(db: AsyncSession, auth: AuthContext) -> Chat:
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Library attach chat")
    db.add(chat)
    await db.flush()
    return chat


@pytest.mark.asyncio
async def test_create_and_edit_library_document(db: AsyncSession, auth: AuthContext):
    async with await _client_for(db, auth) as client:
        created = await client.post(
            "/api/v1/library/items/documents",
            json={"title": "Meeting Notes", "content_text": "Discuss Austria centers"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["item_type"] == "document"
        assert body["title"] == "Meeting Notes"
        assert body["content_text"] == "Discuss Austria centers"

        updated = await client.patch(
            f"/api/v1/library/items/{body['id']}",
            json={"title": "Meeting Notes v2", "content_text": "Updated body"},
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Meeting Notes v2"
        assert updated.json()["content_text"] == "Updated body"


@pytest.mark.asyncio
async def test_upload_library_file(db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "library_file_dir", str(tmp_path / "library"))
    async with await _client_for(db, auth) as client:
        response = await client.post(
            "/api/v1/library/items/upload",
            files={"file": ("notes.txt", BytesIO(b"rehab research"), "text/plain")},
            data={"title": "Austria Research"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["item_type"] == "file"
    assert body["title"] == "Austria Research"
    assert body["original_filename"] == "notes.txt"
    assert body["excerpt_status"] == "ready"

    row = (
        await db.execute(select(LibraryItem).where(LibraryItem.id == body["id"]))
    ).scalar_one()
    assert row.text_excerpt == "rehab research"
    assert row.relative_path is not None


@pytest.mark.asyncio
async def test_nested_folders_and_labels(db: AsyncSession, auth: AuthContext):
    async with await _client_for(db, auth) as client:
        root = await client.post("/api/v1/library/folders", json={"name": "Rehab Research"})
        assert root.status_code == 201
        root_id = root.json()["id"]

        child = await client.post(
            "/api/v1/library/folders",
            json={"name": "Austria", "parent_id": root_id},
        )
        assert child.status_code == 201
        child_id = child.json()["id"]

        grandchild = await client.post(
            "/api/v1/library/folders",
            json={"name": "Centers", "parent_id": child_id},
        )
        assert grandchild.status_code == 201

        label = await client.post("/api/v1/library/labels", json={"name": "Research"})
        assert label.status_code == 201
        label_id = label.json()["id"]

        doc = await client.post(
            "/api/v1/library/items/documents",
            json={
                "title": "Centers List",
                "content_text": "Vienna clinic",
                "folder_id": grandchild.json()["id"],
                "label_ids": [label_id],
            },
        )
        assert doc.status_code == 201
        assert doc.json()["folder_id"] == grandchild.json()["id"]
        assert any(row["id"] == label_id for row in doc.json()["labels"])

        folders = await client.get("/api/v1/library/folders")
        assert folders.status_code == 200
        assert len(folders.json()) == 3


@pytest.mark.asyncio
async def test_library_search_favorite_and_delete(db: AsyncSession, auth: AuthContext):
    async with await _client_for(db, auth) as client:
        await client.post(
            "/api/v1/library/items/documents",
            json={
                "title": "Austria Rehab Centers",
                "content_text": "List of centers in Vienna",
                "label_names": ["Important"],
                "is_favorite": True,
            },
        )
        await client.post(
            "/api/v1/library/items/documents",
            json={"title": "Other Notes", "content_text": "unrelated"},
        )

        by_title = await client.get("/api/v1/library/items", params={"q": "Austria"})
        assert by_title.status_code == 200
        assert len(by_title.json()) == 1
        assert by_title.json()[0]["title"] == "Austria Rehab Centers"

        by_content = await client.get("/api/v1/library/items", params={"q": "Vienna"})
        assert len(by_content.json()) == 1

        by_label = await client.get("/api/v1/library/items", params={"q": "Important"})
        assert len(by_label.json()) == 1

        favorites = await client.get("/api/v1/library/items", params={"favorites": True})
        assert len(favorites.json()) == 1

        item_id = favorites.json()[0]["id"]
        deleted = await client.delete(f"/api/v1/library/items/{item_id}")
        assert deleted.status_code == 200
        remaining = await client.get("/api/v1/library/items")
        assert len(remaining.json()) == 1


@pytest.mark.asyncio
async def test_library_org_isolation(db: AsyncSession, auth: AuthContext):
    other = await create_other_auth(db)
    async with await _client_for(db, auth) as client:
        created = await client.post(
            "/api/v1/library/items/documents",
            json={"title": "Private Doc", "content_text": "secret"},
        )
        item_id = created.json()["id"]

    async with await _client_for(db, other) as other_client:
        listed = await other_client.get("/api/v1/library/items")
        assert listed.status_code == 200
        assert listed.json() == []

        fetched = await other_client.get(f"/api/v1/library/items/{item_id}")
        assert fetched.status_code == 404


@pytest.mark.asyncio
async def test_attach_library_item_to_chat_and_reject_cross_org(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "library_file_dir", str(tmp_path / "library"))
    chat = await _create_chat(db, auth)
    other = await create_other_auth(db)
    other_chat = await _create_chat(db, other)

    async with await _client_for(db, auth) as client:
        doc = await client.post(
            "/api/v1/library/items/documents",
            json={"title": "Austria Research", "content_text": "Compare with verdict"},
        )
        item_id = doc.json()["id"]

        attached = await client.post(
            f"/api/v1/chats/{chat.id}/attachments/from-library",
            json={"library_item_id": item_id},
        )
        assert attached.status_code == 201
        body = attached.json()
        assert body["library_item_id"] == item_id
        assert body["filename"].startswith("Austria Research")
        assert "Compare with verdict" in (body["text_excerpt"] or "")

        # Idempotent — no duplicate pending chip
        again = await client.post(
            f"/api/v1/chats/{chat.id}/attachments/from-library",
            json={"library_item_id": item_id},
        )
        assert again.status_code == 201
        assert again.json()["id"] == body["id"]

        pending = await client.get(f"/api/v1/chats/{chat.id}/attachments")
        assert len(pending.json()["items"]) == 1

    row = (
        await db.execute(select(ChatAttachment).where(ChatAttachment.id == body["id"]))
    ).scalar_one()
    assert row.library_item_id == item_id
    assert row.relative_path.startswith("library-ref/")

    async with await _client_for(db, other) as other_client:
        rejected = await other_client.post(
            f"/api/v1/chats/{other_chat.id}/attachments/from-library",
            json={"library_item_id": item_id},
        )
        assert rejected.status_code == 404


@pytest.mark.asyncio
async def test_large_library_document_attachment_preserves_up_to_50k_characters(
    db: AsyncSession, auth: AuthContext
):
    chat = await _create_chat(db, auth)
    content = ("A" * 25_000) + ("B" * 25_000) + ("C" * 10_000)

    async with await _client_for(db, auth) as client:
        document = await client.post(
            "/api/v1/library/items/documents",
            json={"title": "Large document", "content_text": content},
        )
        assert document.status_code == 201

        attached = await client.post(
            f"/api/v1/chats/{chat.id}/attachments/from-library",
            json={"library_item_id": document.json()["id"]},
        )

    assert attached.status_code == 201
    excerpt = attached.json()["text_excerpt"]
    assert len(excerpt) == 50_000
    assert excerpt == content[:50_000]
    assert "B" * 25_000 in excerpt
    assert "C" not in excerpt


@pytest.mark.asyncio
async def test_library_item_detail_returns_document_content_and_file_excerpt(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "library_file_dir", str(tmp_path / "library"))
    async with await _client_for(db, auth) as client:
        doc = await client.post(
            "/api/v1/library/items/documents",
            json={
                "title": "Editable Doc",
                "content_text": "Full document body for viewing",
            },
        )
        assert doc.status_code == 201
        doc_id = doc.json()["id"]

        uploaded = await client.post(
            "/api/v1/library/items/upload",
            files={"file": ("brief.txt", BytesIO(b"Extracted file body"), "text/plain")},
            data={"title": "Brief"},
        )
        assert uploaded.status_code == 201
        file_id = uploaded.json()["id"]

        doc_detail = await client.get(f"/api/v1/library/items/{doc_id}")
        assert doc_detail.status_code == 200
        doc_body = doc_detail.json()
        assert doc_body["item_type"] == "document"
        assert doc_body["content_text"] == "Full document body for viewing"
        assert doc_body["text_excerpt"] == "Full document body for viewing"

        file_detail = await client.get(f"/api/v1/library/items/{file_id}")
        assert file_detail.status_code == 200
        file_body = file_detail.json()
        assert file_body["item_type"] == "file"
        assert file_body["content_text"] is None
        assert file_body["text_excerpt"] == "Extracted file body"
        assert file_body["excerpt_status"] == "ready"

        listed = await client.get("/api/v1/library/items")
        assert listed.status_code == 200
        by_id = {row["id"]: row for row in listed.json()}
        assert by_id[doc_id]["content_text"] is None
        assert by_id[doc_id].get("text_excerpt") is None
        assert by_id[file_id]["content_text"] is None
        assert by_id[file_id].get("text_excerpt") is None


@pytest.mark.asyncio
async def test_library_file_detail_org_isolation(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "library_file_dir", str(tmp_path / "library"))
    other = await create_other_auth(db)
    async with await _client_for(db, auth) as client:
        uploaded = await client.post(
            "/api/v1/library/items/upload",
            files={"file": ("secret.txt", BytesIO(b"org private"), "text/plain")},
        )
        item_id = uploaded.json()["id"]

    async with await _client_for(db, other) as other_client:
        fetched = await other_client.get(f"/api/v1/library/items/{item_id}")
        assert fetched.status_code == 404
        downloaded = await other_client.get(f"/api/v1/library/items/{item_id}/download")
        assert downloaded.status_code == 404
