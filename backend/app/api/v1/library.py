"""Library API — folders, labels, items (files + MultiMind Documents)."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    LibraryDocumentCreateRequest,
    LibraryFolderCreateRequest,
    LibraryFolderResponse,
    LibraryFolderUpdateRequest,
    LibraryItemResponse,
    LibraryItemUpdateRequest,
    LibraryLabelCreateRequest,
    LibraryLabelResponse,
    LibraryLabelUpdateRequest,
    MessageResponse,
)
from app.services.library_service import library_service

router = APIRouter()


@router.get("/folders", response_model=list[LibraryFolderResponse])
async def list_folders(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.list_folders(db, auth)


@router.post(
    "/folders",
    response_model=LibraryFolderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    data: LibraryFolderCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.create_folder(
        db, auth, name=data.name, parent_id=data.parent_id
    )


@router.patch("/folders/{folder_id}", response_model=LibraryFolderResponse)
async def update_folder(
    folder_id: UUID,
    data: LibraryFolderUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.update_folder(
        db,
        auth,
        str(folder_id),
        name=data.name,
        parent_id=data.parent_id,
        clear_parent=data.clear_parent,
    )


@router.delete("/folders/{folder_id}", response_model=MessageResponse)
async def delete_folder(
    folder_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await library_service.delete_folder(db, auth, str(folder_id))
    return MessageResponse(message="Folder deleted")


@router.get("/labels", response_model=list[LibraryLabelResponse])
async def list_labels(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.list_labels(db, auth)


@router.post(
    "/labels",
    response_model=LibraryLabelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_label(
    data: LibraryLabelCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.create_label(db, auth, data.name)


@router.patch("/labels/{label_id}", response_model=LibraryLabelResponse)
async def update_label(
    label_id: UUID,
    data: LibraryLabelUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.update_label(db, auth, str(label_id), data.name)


@router.delete("/labels/{label_id}", response_model=MessageResponse)
async def delete_label(
    label_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await library_service.delete_label(db, auth, str(label_id))
    return MessageResponse(message="Label deleted")


@router.get("/items", response_model=list[LibraryItemResponse])
async def list_items(
    q: str | None = Query(default=None),
    folder_id: str | None = Query(default=None),
    unfiled: bool = Query(default=False),
    label_id: str | None = Query(default=None),
    item_type: str | None = Query(default=None),
    favorites: bool = Query(default=False),
    recent: bool = Query(default=False),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.list_items(
        db,
        auth,
        q=q,
        folder_id=folder_id,
        unfiled=unfiled,
        label_id=label_id,
        item_type=item_type,
        favorites=favorites,
        recent=recent,
        include_content=False,
    )


@router.post(
    "/items/documents",
    response_model=LibraryItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    data: LibraryDocumentCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.create_document(
        db,
        auth,
        title=data.title,
        content_text=data.content_text,
        folder_id=data.folder_id,
        label_ids=data.label_ids,
        label_names=data.label_names,
        is_favorite=data.is_favorite,
    )


@router.post(
    "/items/upload",
    response_model=LibraryItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    folder_id: str | None = Form(default=None),
    is_favorite: bool = Form(default=False),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.upload_file(
        db,
        auth,
        file,
        title=title,
        folder_id=folder_id or None,
        is_favorite=is_favorite,
    )


@router.get("/items/{item_id}", response_model=LibraryItemResponse)
async def get_item(
    item_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.get_item(db, auth, str(item_id), include_content=True)


@router.patch("/items/{item_id}", response_model=LibraryItemResponse)
async def update_item(
    item_id: UUID,
    data: LibraryItemUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await library_service.update_item(
        db,
        auth,
        str(item_id),
        title=data.title,
        content_text=data.content_text,
        folder_id=data.folder_id,
        clear_folder=data.clear_folder,
        label_ids=data.label_ids,
        is_favorite=data.is_favorite,
    )


@router.delete("/items/{item_id}", response_model=MessageResponse)
async def delete_item(
    item_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await library_service.delete_item(db, auth, str(item_id))
    return MessageResponse(message="Item deleted")


@router.get("/items/{item_id}/download")
async def download_item(
    item_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    item, path = await library_service.resolve_download_path(db, auth, str(item_id))
    filename = item.original_filename or item.title
    return FileResponse(
        path,
        media_type=item.content_type or "application/octet-stream",
        filename=filename,
    )
