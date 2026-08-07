"""User Library: folders, labels, uploaded files, and MultiMind Documents."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import LibraryFolder, LibraryItem, LibraryItemLabel, LibraryLabel
from app.schemas.api import (
    LibraryFolderResponse,
    LibraryItemResponse,
    LibraryLabelBrief,
    LibraryLabelResponse,
)
from app.services.attachment_types import (
    validate_attachment_content_type,
    validate_attachment_filename,
)
from app.services.chat_attachment_storage import (
    cleanup_path,
    promote_temp_attachment_file,
    resolve_attachment_path,
    safe_delete_attachment_file,
    stream_upload_to_temp_file,
)
from app.services.chat_attachment_text import (
    ATTACHMENT_TEXT_EXCERPT_MAX,
    extract_attachment_text_from_path,
)

logger = get_logger(__name__)

ITEM_TYPE_FILE = "file"
ITEM_TYPE_DOCUMENT = "document"
_LABEL_NAME_RE = re.compile(r"\s+")


class LibraryService:
    # --- Folders ---

    async def list_folders(
        self, db: AsyncSession, auth: AuthContext
    ) -> list[LibraryFolderResponse]:
        result = await db.execute(
            select(LibraryFolder)
            .where(
                LibraryFolder.org_id == auth.org_id,
                LibraryFolder.user_id == auth.user.id,
            )
            .order_by(LibraryFolder.name.asc(), LibraryFolder.id.asc())
        )
        return [self._folder_response(row) for row in result.scalars().all()]

    async def create_folder(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        name: str,
        parent_id: str | None = None,
    ) -> LibraryFolderResponse:
        cleaned = self._clean_folder_name(name)
        parent = None
        if parent_id:
            parent = await self._get_folder(db, auth, parent_id)
        await self._assert_unique_folder_name(
            db, auth, name=cleaned, parent_id=parent.id if parent else None
        )
        folder = LibraryFolder(
            org_id=auth.org_id,
            user_id=auth.user.id,
            parent_id=parent.id if parent else None,
            name=cleaned,
        )
        db.add(folder)
        await db.flush()
        await db.commit()
        await db.refresh(folder)
        return self._folder_response(folder)

    async def update_folder(
        self,
        db: AsyncSession,
        auth: AuthContext,
        folder_id: str,
        *,
        name: str | None = None,
        parent_id: str | None = None,
        clear_parent: bool = False,
    ) -> LibraryFolderResponse:
        folder = await self._get_folder(db, auth, folder_id)
        new_name = self._clean_folder_name(name) if name is not None else folder.name
        new_parent_id = folder.parent_id
        if clear_parent:
            new_parent_id = None
        elif parent_id is not None:
            if parent_id == folder.id:
                raise ValidationError("A folder cannot be its own parent")
            parent = await self._get_folder(db, auth, parent_id)
            await self._assert_not_descendant(db, auth, folder.id, parent.id)
            new_parent_id = parent.id

        if new_name != folder.name or new_parent_id != folder.parent_id:
            await self._assert_unique_folder_name(
                db,
                auth,
                name=new_name,
                parent_id=new_parent_id,
                exclude_id=folder.id,
            )

        folder.name = new_name
        folder.parent_id = new_parent_id
        folder.updated_at = datetime.now(UTC)
        await db.flush()
        await db.commit()
        await db.refresh(folder)
        return self._folder_response(folder)

    async def delete_folder(
        self, db: AsyncSession, auth: AuthContext, folder_id: str
    ) -> None:
        folder = await self._get_folder(db, auth, folder_id)
        # Items in this folder (and nested via CASCADE on subfolders) become unfiled
        # via SET NULL on folder_id; nested folders CASCADE-delete.
        await db.delete(folder)
        await db.flush()
        await db.commit()

    # --- Labels ---

    async def list_labels(
        self, db: AsyncSession, auth: AuthContext
    ) -> list[LibraryLabelResponse]:
        result = await db.execute(
            select(LibraryLabel)
            .where(
                LibraryLabel.org_id == auth.org_id,
                LibraryLabel.user_id == auth.user.id,
            )
            .order_by(LibraryLabel.name.asc())
        )
        labels = list(result.scalars().all())
        counts: dict[str, int] = {}
        if labels:
            count_rows = await db.execute(
                select(LibraryItemLabel.label_id, func.count())
                .where(LibraryItemLabel.label_id.in_([label.id for label in labels]))
                .group_by(LibraryItemLabel.label_id)
            )
            counts = {lid: int(n) for lid, n in count_rows.all()}
        return [
            LibraryLabelResponse(
                id=label.id,
                name=label.name,
                item_count=counts.get(label.id, 0),
                created_at=label.created_at,
                updated_at=label.updated_at,
            )
            for label in labels
        ]

    async def create_label(
        self, db: AsyncSession, auth: AuthContext, name: str
    ) -> LibraryLabelResponse:
        cleaned = self._clean_label_name(name)
        existing = await db.execute(
            select(LibraryLabel).where(
                LibraryLabel.org_id == auth.org_id,
                LibraryLabel.user_id == auth.user.id,
                LibraryLabel.name == cleaned,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("A library label with this name already exists")
        label = LibraryLabel(
            org_id=auth.org_id, user_id=auth.user.id, name=cleaned
        )
        db.add(label)
        await db.flush()
        await db.commit()
        await db.refresh(label)
        return LibraryLabelResponse(
            id=label.id,
            name=label.name,
            item_count=0,
            created_at=label.created_at,
            updated_at=label.updated_at,
        )

    async def update_label(
        self, db: AsyncSession, auth: AuthContext, label_id: str, name: str
    ) -> LibraryLabelResponse:
        label = await self._get_label(db, auth, label_id)
        cleaned = self._clean_label_name(name)
        if cleaned != label.name:
            clash = await db.execute(
                select(LibraryLabel).where(
                    LibraryLabel.org_id == auth.org_id,
                    LibraryLabel.user_id == auth.user.id,
                    LibraryLabel.name == cleaned,
                    LibraryLabel.id != label.id,
                )
            )
            if clash.scalar_one_or_none() is not None:
                raise ConflictError("A library label with this name already exists")
            label.name = cleaned
            label.updated_at = datetime.now(UTC)
            await db.flush()
            await db.commit()
            await db.refresh(label)
        count = (
            await db.execute(
                select(func.count())
                .select_from(LibraryItemLabel)
                .where(LibraryItemLabel.label_id == label.id)
            )
        ).scalar_one()
        return LibraryLabelResponse(
            id=label.id,
            name=label.name,
            item_count=int(count),
            created_at=label.created_at,
            updated_at=label.updated_at,
        )

    async def delete_label(
        self, db: AsyncSession, auth: AuthContext, label_id: str
    ) -> None:
        label = await self._get_label(db, auth, label_id)
        await db.delete(label)
        await db.flush()
        await db.commit()

    # --- Items ---

    async def list_items(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        q: str | None = None,
        folder_id: str | None = None,
        unfiled: bool = False,
        label_id: str | None = None,
        item_type: str | None = None,
        favorites: bool = False,
        recent: bool = False,
        include_content: bool = False,
        limit: int = 200,
    ) -> list[LibraryItemResponse]:
        stmt = (
            select(LibraryItem)
            .options(selectinload(LibraryItem.labels))
            .where(
                LibraryItem.org_id == auth.org_id,
                LibraryItem.user_id == auth.user.id,
            )
        )
        if favorites:
            stmt = stmt.where(LibraryItem.is_favorite.is_(True))
        if unfiled:
            stmt = stmt.where(LibraryItem.folder_id.is_(None))
        elif folder_id:
            await self._get_folder(db, auth, folder_id)
            stmt = stmt.where(LibraryItem.folder_id == folder_id)
        if item_type:
            if item_type not in {ITEM_TYPE_FILE, ITEM_TYPE_DOCUMENT}:
                raise ValidationError("item_type must be 'file' or 'document'")
            stmt = stmt.where(LibraryItem.item_type == item_type)
        if label_id:
            await self._get_label(db, auth, label_id)
            stmt = stmt.join(LibraryItemLabel).where(
                LibraryItemLabel.label_id == label_id
            )
        if q and q.strip():
            needle = f"%{q.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(LibraryItem.title).like(needle),
                    func.lower(func.coalesce(LibraryItem.original_filename, "")).like(
                        needle
                    ),
                    func.lower(func.coalesce(LibraryItem.content_text, "")).like(needle),
                    func.lower(func.coalesce(LibraryItem.text_excerpt, "")).like(needle),
                    LibraryItem.id.in_(
                        select(LibraryItemLabel.item_id).where(
                            LibraryItemLabel.label_id.in_(
                                select(LibraryLabel.id).where(
                                    LibraryLabel.org_id == auth.org_id,
                                    LibraryLabel.user_id == auth.user.id,
                                    func.lower(LibraryLabel.name).like(needle),
                                )
                            )
                        )
                    ),
                )
            )
        stmt = stmt.order_by(
            LibraryItem.updated_at.desc(), LibraryItem.id.desc()
        ).limit(min(max(limit, 1), 500))
        if recent:
            stmt = stmt.limit(min(50, limit))

        rows = list((await db.execute(stmt)).unique().scalars().all())
        return [
            self._item_response(row, include_content=include_content) for row in rows
        ]

    async def get_item(
        self,
        db: AsyncSession,
        auth: AuthContext,
        item_id: str,
        *,
        include_content: bool = True,
    ) -> LibraryItemResponse:
        item = await self._get_item(db, auth, item_id, load_labels=True)
        return self._item_response(item, include_content=include_content)

    async def create_document(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        title: str,
        content_text: str = "",
        folder_id: str | None = None,
        label_ids: list[str] | None = None,
        label_names: list[str] | None = None,
        is_favorite: bool = False,
    ) -> LibraryItemResponse:
        cleaned_title = self._clean_title(title)
        folder = None
        if folder_id:
            folder = await self._get_folder(db, auth, folder_id)
        labels = await self._resolve_labels(
            db, auth, label_ids=label_ids or [], label_names=label_names or []
        )
        item = LibraryItem(
            org_id=auth.org_id,
            user_id=auth.user.id,
            item_type=ITEM_TYPE_DOCUMENT,
            title=cleaned_title,
            folder_id=folder.id if folder else None,
            is_favorite=is_favorite,
            content_text=content_text or "",
            excerpt_status="ready",
            text_excerpt=(content_text or "")[:ATTACHMENT_TEXT_EXCERPT_MAX] or None,
        )
        db.add(item)
        await db.flush()
        for label in labels:
            db.add(LibraryItemLabel(item_id=item.id, label_id=label.id))
        await db.flush()
        await db.commit()
        return await self.get_item(db, auth, item.id)

    async def upload_file(
        self,
        db: AsyncSession,
        auth: AuthContext,
        upload: UploadFile,
        *,
        title: str | None = None,
        folder_id: str | None = None,
        label_ids: list[str] | None = None,
        label_names: list[str] | None = None,
        is_favorite: bool = False,
    ) -> LibraryItemResponse:
        settings = get_settings()
        folder = None
        if folder_id:
            folder = await self._get_folder(db, auth, folder_id)
        labels = await self._resolve_labels(
            db, auth, label_ids=label_ids or [], label_names=label_names or []
        )

        original_filename, ext = validate_attachment_filename(upload.filename)
        content_type = validate_attachment_content_type(
            upload.content_type, original_filename, ext
        )
        display_title = self._clean_title(title) if title else original_filename

        item_id = str(uuid.uuid4())
        stored_name = f"{item_id}{ext}"
        relative_path = f"{auth.org_id}/{auth.user.id}/{stored_name}"
        tmp_path = None
        dest_path = None

        try:
            tmp_path, size_bytes = await stream_upload_to_temp_file(
                upload,
                max_bytes=settings.library_file_max_bytes,
                extension=ext,
                root=settings.library_file_dir,
            )
            text_excerpt, excerpt_status = extract_attachment_text_from_path(
                tmp_path, ext
            )
            dest_path = resolve_attachment_path(
                relative_path, root=settings.library_file_dir
            )
            promote_temp_attachment_file(tmp_path, dest_path)
            tmp_path = None

            item = LibraryItem(
                id=item_id,
                org_id=auth.org_id,
                user_id=auth.user.id,
                item_type=ITEM_TYPE_FILE,
                title=display_title,
                folder_id=folder.id if folder else None,
                is_favorite=is_favorite,
                original_filename=original_filename,
                content_type=content_type,
                size_bytes=size_bytes,
                stored_name=stored_name,
                relative_path=relative_path,
                text_excerpt=text_excerpt,
                excerpt_status=excerpt_status,
            )
            db.add(item)
            await db.flush()
            for label in labels:
                db.add(LibraryItemLabel(item_id=item.id, label_id=label.id))
            await db.flush()
            await db.commit()
            return await self.get_item(db, auth, item.id)
        except Exception:
            cleanup_path(tmp_path)
            cleanup_path(dest_path)
            raise

    async def update_item(
        self,
        db: AsyncSession,
        auth: AuthContext,
        item_id: str,
        *,
        title: str | None = None,
        content_text: str | None = None,
        folder_id: str | None = None,
        clear_folder: bool = False,
        label_ids: list[str] | None = None,
        is_favorite: bool | None = None,
    ) -> LibraryItemResponse:
        item = await self._get_item(db, auth, item_id, load_labels=True)
        if title is not None:
            item.title = self._clean_title(title)
        if content_text is not None:
            if item.item_type != ITEM_TYPE_DOCUMENT:
                raise ValidationError("Only MultiMind Documents have editable content")
            item.content_text = content_text
            excerpt = content_text[:ATTACHMENT_TEXT_EXCERPT_MAX]
            item.text_excerpt = excerpt or None
            item.excerpt_status = "ready" if excerpt.strip() else "empty"
        if clear_folder:
            item.folder_id = None
        elif folder_id is not None:
            folder = await self._get_folder(db, auth, folder_id)
            item.folder_id = folder.id
        if label_ids is not None:
            await db.execute(
                delete(LibraryItemLabel).where(LibraryItemLabel.item_id == item.id)
            )
            labels = await self._resolve_labels(
                db, auth, label_ids=label_ids, label_names=[]
            )
            for label in labels:
                db.add(LibraryItemLabel(item_id=item.id, label_id=label.id))
        if is_favorite is not None:
            item.is_favorite = is_favorite
        item.updated_at = datetime.now(UTC)
        await db.flush()
        await db.commit()
        return await self.get_item(db, auth, item.id)

    async def delete_item(
        self, db: AsyncSession, auth: AuthContext, item_id: str
    ) -> None:
        item = await self._get_item(db, auth, item_id, load_labels=False)
        relative_path = item.relative_path
        item_type = item.item_type
        await db.delete(item)
        await db.flush()
        await db.commit()
        if item_type == ITEM_TYPE_FILE and relative_path:
            try:
                safe_delete_attachment_file(
                    relative_path, root=get_settings().library_file_dir
                )
            except Exception:
                logger.warning(
                    "library_file_delete_failed",
                    item_id=item_id,
                    relative_path=relative_path,
                    exc_info=True,
                )

    async def resolve_download_path(
        self, db: AsyncSession, auth: AuthContext, item_id: str
    ) -> tuple[LibraryItem, Path]:
        item = await self._get_item(db, auth, item_id, load_labels=False)
        if item.item_type != ITEM_TYPE_FILE or not item.relative_path:
            raise ValidationError("Only uploaded Library files can be downloaded")
        path = resolve_attachment_path(
            item.relative_path, root=get_settings().library_file_dir
        )
        if not path.is_file():
            raise NotFoundError("Library file", item_id)
        return item, path

    # --- Internals ---

    async def _get_folder(
        self, db: AsyncSession, auth: AuthContext, folder_id: str
    ) -> LibraryFolder:
        result = await db.execute(
            select(LibraryFolder).where(
                LibraryFolder.id == folder_id,
                LibraryFolder.org_id == auth.org_id,
                LibraryFolder.user_id == auth.user.id,
            )
        )
        folder = result.scalar_one_or_none()
        if folder is None:
            raise NotFoundError("LibraryFolder", folder_id)
        return folder

    async def _get_label(
        self, db: AsyncSession, auth: AuthContext, label_id: str
    ) -> LibraryLabel:
        result = await db.execute(
            select(LibraryLabel).where(
                LibraryLabel.id == label_id,
                LibraryLabel.org_id == auth.org_id,
                LibraryLabel.user_id == auth.user.id,
            )
        )
        label = result.scalar_one_or_none()
        if label is None:
            raise NotFoundError("LibraryLabel", label_id)
        return label

    async def _get_item(
        self,
        db: AsyncSession,
        auth: AuthContext,
        item_id: str,
        *,
        load_labels: bool,
    ) -> LibraryItem:
        stmt = select(LibraryItem).where(
            LibraryItem.id == item_id,
            LibraryItem.org_id == auth.org_id,
            LibraryItem.user_id == auth.user.id,
        )
        if load_labels:
            stmt = stmt.options(selectinload(LibraryItem.labels))
        result = await db.execute(stmt)
        item = result.scalar_one_or_none()
        if item is None:
            raise NotFoundError("LibraryItem", item_id)
        return item

    async def _assert_unique_folder_name(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        name: str,
        parent_id: str | None,
        exclude_id: str | None = None,
    ) -> None:
        stmt = select(LibraryFolder.id).where(
            LibraryFolder.org_id == auth.org_id,
            LibraryFolder.user_id == auth.user.id,
            LibraryFolder.name == name,
        )
        if parent_id is None:
            stmt = stmt.where(LibraryFolder.parent_id.is_(None))
        else:
            stmt = stmt.where(LibraryFolder.parent_id == parent_id)
        if exclude_id:
            stmt = stmt.where(LibraryFolder.id != exclude_id)
        if (await db.execute(stmt)).scalar_one_or_none() is not None:
            raise ConflictError("A folder with this name already exists here")

    async def _assert_not_descendant(
        self,
        db: AsyncSession,
        auth: AuthContext,
        folder_id: str,
        candidate_parent_id: str,
    ) -> None:
        """Reject moving a folder under one of its descendants."""
        current_id: str | None = candidate_parent_id
        seen: set[str] = set()
        while current_id:
            if current_id == folder_id:
                raise ValidationError("Cannot move a folder into its own subfolder")
            if current_id in seen:
                break
            seen.add(current_id)
            row = (
                await db.execute(
                    select(LibraryFolder.parent_id).where(
                        LibraryFolder.id == current_id,
                        LibraryFolder.org_id == auth.org_id,
                        LibraryFolder.user_id == auth.user.id,
                    )
                )
            ).scalar_one_or_none()
            current_id = row

    async def _resolve_labels(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        label_ids: list[str],
        label_names: list[str],
    ) -> list[LibraryLabel]:
        labels: dict[str, LibraryLabel] = {}
        for label_id in label_ids:
            label = await self._get_label(db, auth, label_id)
            labels[label.id] = label
        for raw_name in label_names:
            cleaned = self._clean_label_name(raw_name)
            existing = (
                await db.execute(
                    select(LibraryLabel).where(
                        LibraryLabel.org_id == auth.org_id,
                        LibraryLabel.user_id == auth.user.id,
                        LibraryLabel.name == cleaned,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = LibraryLabel(
                    org_id=auth.org_id, user_id=auth.user.id, name=cleaned
                )
                db.add(existing)
                await db.flush()
            labels[existing.id] = existing
        return list(labels.values())

    def _clean_title(self, title: str) -> str:
        cleaned = (title or "").strip()
        if not cleaned:
            raise ValidationError("Title is required")
        return cleaned[:255]

    def _clean_folder_name(self, name: str) -> str:
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValidationError("Folder name is required")
        return cleaned[:255]

    def _clean_label_name(self, name: str) -> str:
        cleaned = _LABEL_NAME_RE.sub(" ", (name or "").strip())
        if not cleaned:
            raise ValidationError("Label name is required")
        return cleaned[:120]

    def _folder_response(self, folder: LibraryFolder) -> LibraryFolderResponse:
        return LibraryFolderResponse(
            id=folder.id,
            name=folder.name,
            parent_id=folder.parent_id,
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )

    def _item_response(
        self, item: LibraryItem, *, include_content: bool
    ) -> LibraryItemResponse:
        labels = [
            LibraryLabelBrief(id=label.id, name=label.name)
            for label in sorted(item.labels, key=lambda row: row.name.lower())
        ]
        content = item.content_text if include_content else None
        if item.item_type != ITEM_TYPE_DOCUMENT:
            content = None
        return LibraryItemResponse(
            id=item.id,
            item_type=item.item_type,
            title=item.title,
            folder_id=item.folder_id,
            is_favorite=bool(item.is_favorite),
            original_filename=item.original_filename,
            content_type=item.content_type,
            size_bytes=item.size_bytes,
            excerpt_status=item.excerpt_status,
            content_text=content,
            labels=labels,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


library_service = LibraryService()
