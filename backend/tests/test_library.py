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
async def test_library_document_markdown_lists_persist_unchanged(
    db: AsyncSession, auth: AuthContext
):
    markdown = "Shopping\n\n- Milk\n- Bread\n\n1. Call\n2. Measure"
    async with await _client_for(db, auth) as client:
        created = await client.post(
            "/api/v1/library/items/documents",
            json={"title": "Lists", "content_text": markdown},
        )
        fetched = await client.get(f"/api/v1/library/items/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["content_text"] == markdown
    assert fetched.json()["text_excerpt"] == markdown


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
async def test_rename_folder_preserves_identity_parent_items_and_children(
    db: AsyncSession, auth: AuthContext
):
    async with await _client_for(db, auth) as client:
        parent = await client.post("/api/v1/library/folders", json={"name": "Parent"})
        folder = await client.post(
            "/api/v1/library/folders",
            json={"name": "Old", "parent_id": parent.json()["id"]},
        )
        folder_id = folder.json()["id"]
        child = await client.post(
            "/api/v1/library/folders",
            json={"name": "Child", "parent_id": folder_id},
        )
        document = await client.post(
            "/api/v1/library/items/documents",
            json={"title": "Doc", "folder_id": folder_id},
        )

        renamed = await client.patch(
            f"/api/v1/library/folders/{folder_id}", json={"name": "  New name  "}
        )
        assert renamed.status_code == 200
        assert renamed.json()["id"] == folder_id
        assert renamed.json()["name"] == "New name"
        assert renamed.json()["parent_id"] == parent.json()["id"]

        folders = {row["id"]: row for row in (await client.get("/api/v1/library/folders")).json()}
        assert folders[child.json()["id"]]["parent_id"] == folder_id
        fetched_document = await client.get(
            f"/api/v1/library/items/{document.json()['id']}"
        )
        assert fetched_document.json()["folder_id"] == folder_id

        blank = await client.patch(
            f"/api/v1/library/folders/{folder_id}", json={"name": "   "}
        )
        assert blank.status_code == 400

        sibling = await client.post(
            "/api/v1/library/folders",
            json={"name": "Sibling", "parent_id": parent.json()["id"]},
        )
        duplicate = await client.patch(
            f"/api/v1/library/folders/{sibling.json()['id']}", json={"name": "New name"}
        )
        assert duplicate.status_code == 409

        other_parent = await client.post("/api/v1/library/folders", json={"name": "Other"})
        same_elsewhere = await client.post(
            "/api/v1/library/folders",
            json={"name": "New name", "parent_id": other_parent.json()["id"]},
        )
        assert same_elsewhere.status_code == 201


@pytest.mark.asyncio
async def test_folder_rename_and_delete_enforce_ownership(
    db: AsyncSession, auth: AuthContext
):
    other = await create_other_auth(db)
    async with await _client_for(db, auth) as client:
        folder = await client.post("/api/v1/library/folders", json={"name": "Private"})
    async with await _client_for(db, other) as other_client:
        renamed = await other_client.patch(
            f"/api/v1/library/folders/{folder.json()['id']}", json={"name": "Stolen"}
        )
        deleted = await other_client.delete(
            f"/api/v1/library/folders/{folder.json()['id']}"
        )
    assert renamed.status_code == 404
    assert deleted.status_code == 404


@pytest.mark.asyncio
async def test_delete_folder_allows_only_empty_folders(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    library_root = tmp_path / "library"
    monkeypatch.setattr(get_settings(), "library_file_dir", str(library_root))
    chat = await _create_chat(db, auth)

    async with await _client_for(db, auth) as client:
        document_folder = await client.post(
            "/api/v1/library/folders", json={"name": "Documents"}
        )
        document = await client.post(
            "/api/v1/library/items/documents",
            json={
                "title": "Protected",
                "content_text": "attachment snapshot",
                "folder_id": document_folder.json()["id"],
                "label_names": ["Keep"],
                "is_favorite": True,
            },
        )
        attachment = await client.post(
            f"/api/v1/chats/{chat.id}/attachments/from-library",
            json={"library_item_id": document.json()["id"]},
        )
        rejected_document = await client.delete(
            f"/api/v1/library/folders/{document_folder.json()['id']}"
        )
        assert rejected_document.status_code == 409
        assert "not empty" in rejected_document.json()["message"].lower()

        file_folder = await client.post(
            "/api/v1/library/folders", json={"name": "Uploads"}
        )
        uploaded = await client.post(
            "/api/v1/library/items/upload",
            files={"file": ("safe.txt", BytesIO(b"keep me"), "text/plain")},
            data={"folder_id": file_folder.json()["id"]},
        )
        uploaded_row = (
            await db.execute(select(LibraryItem).where(LibraryItem.id == uploaded.json()["id"]))
        ).scalar_one()
        physical_path = library_root / uploaded_row.relative_path
        assert physical_path.is_file()
        rejected_file = await client.delete(
            f"/api/v1/library/folders/{file_folder.json()['id']}"
        )
        assert rejected_file.status_code == 409
        assert physical_path.is_file()

        parent = await client.post("/api/v1/library/folders", json={"name": "Tree"})
        child = await client.post(
            "/api/v1/library/folders",
            json={"name": "Empty child", "parent_id": parent.json()["id"]},
        )
        sibling = await client.post("/api/v1/library/folders", json={"name": "Sibling"})
        rejected_child = await client.delete(
            f"/api/v1/library/folders/{parent.json()['id']}"
        )
        assert rejected_child.status_code == 409

        deleted_child = await client.delete(
            f"/api/v1/library/folders/{child.json()['id']}"
        )
        deleted_parent = await client.delete(
            f"/api/v1/library/folders/{parent.json()['id']}"
        )
        assert deleted_child.status_code == 200
        assert deleted_parent.status_code == 200
        remaining_folders = (await client.get("/api/v1/library/folders")).json()
        assert any(row["id"] == sibling.json()["id"] for row in remaining_folders)

        kept_document = await client.get(f"/api/v1/library/items/{document.json()['id']}")
        assert kept_document.status_code == 200
        assert kept_document.json()["folder_id"] == document_folder.json()["id"]
        assert kept_document.json()["is_favorite"] is True
        assert any(label["name"] == "Keep" for label in kept_document.json()["labels"])

    attachment_row = (
        await db.execute(select(ChatAttachment).where(ChatAttachment.id == attachment.json()["id"]))
    ).scalar_one()
    assert attachment_row.text_excerpt == "attachment snapshot"
    assert attachment_row.library_item_id == document.json()["id"]


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
async def test_reattach_refreshes_pending_library_document_snapshot(
    db: AsyncSession, auth: AuthContext
):
    chat = await _create_chat(db, auth)
    async with await _client_for(db, auth) as client:
        document = await client.post(
            "/api/v1/library/items/documents",
            json={"title": "Tasks", "content_text": "- Version A"},
        )
        item_id = document.json()["id"]
        first = await client.post(
            f"/api/v1/chats/{chat.id}/attachments/from-library",
            json={"library_item_id": item_id},
        )
        await client.patch(
            f"/api/v1/library/items/{item_id}",
            json={"content_text": "1. Version B\n2. Current"},
        )
        refreshed = await client.post(
            f"/api/v1/chats/{chat.id}/attachments/from-library",
            json={"library_item_id": item_id},
        )

    assert refreshed.status_code == 201
    assert refreshed.json()["id"] == first.json()["id"]
    assert refreshed.json()["text_excerpt"] == "1. Version B\n2. Current"
    rows = (
        await db.execute(
            select(ChatAttachment).where(
                ChatAttachment.chat_id == chat.id,
                ChatAttachment.library_item_id == item_id,
                ChatAttachment.turn_id.is_(None),
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_large_library_document_attachment_preserves_up_to_100k_characters(
    db: AsyncSession, auth: AuthContext
):
    chat = await _create_chat(db, auth)
    content = ("A" * 50_000) + ("B" * 50_000) + ("C" * 10_000)

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
    assert len(excerpt) == 100_000
    assert excerpt == content[:100_000]
    assert "B" * 50_000 in excerpt
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
