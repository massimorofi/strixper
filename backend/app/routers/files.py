"""File upload, download and workspace browsing for the AI-Chat tab.

  POST   /api/v1/files/upload              multipart upload -> file record
  GET    /api/v1/files                   list indexed files
  GET    /api/v1/files/{file_id}         download a stored file
  GET    /api/v1/files/{file_id}/preview open inline (images, text)
  DELETE /api/v1/files/{file_id}         delete a stored file

  GET    /api/v1/workspace/list          browse the workspace tree
  GET    /api/v1/workspace/read          read a text file
  DELETE /api/v1/workspace               delete a file / empty directory

Uploads are stored under ``<workspace>/uploads`` and are addressed by an
opaque ``file_id`` thereafter. The browser never supplies a path to fetch
a stored file, so there is no path to guess at; the workspace routes are
the deliberate exception and are confined by ``FileStore.resolve``.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..config import settings
from ..services.file_store import (
    FileStore,
    FileStoreError,
    UPLOADS_DIR,
    _head,
    _mime_from_ext,
)
from ..services import vision_helper

router = APIRouter(tags=["files"])

# Refuse to stream a preview of anything larger than this; a 2 GB download
# rendered as an "inline preview" is a denial-of-service with extra steps.
MAX_PREVIEW_BYTES = 10 * 1024 * 1024

INLINE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/bmp",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
}


def _store() -> FileStore:
    from ..services.file_store import get_store

    return get_store()


def _fail(exc: Exception, status: int = 400) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    subdir: str = Form(default=UPLOADS_DIR),
) -> dict[str, Any]:
    """Store an uploaded file and return its metadata.

    Images are validated against the engine's real constraints (pixel
    budget and dimension multiple) at upload time rather than at send
    time, so a bad image is rejected where the user can see which file it
    was.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")

    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"file is {len(raw) / 1_048_576:.1f} MB, the limit is "
                f"{settings.max_file_size_mb} MB"
            ),
        )

    sniffed = vision_helper.sniff_mime(raw)
    is_image = sniffed is not None and sniffed.startswith("image/")

    if is_image:
        if sniffed not in settings.allowed_image_types:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"{sniffed} images are not accepted; allowed: "
                    f"{', '.join(settings.allowed_image_types)}"
                ),
            )
        try:
            vision_helper.validate_image(
                raw,
                allowed_types=settings.allowed_image_types,
                max_pixels=settings.image_max_pixels,
                size_multiple=settings.image_size_multiple,
                max_bytes=settings.max_image_size_mb * 1024 * 1024,
            )
        except vision_helper.ImageValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        if "*" not in settings.allowed_file_types:
            ext = os.path.splitext(file.filename or "")[1].lower()
            allowed = {
                t if t.startswith(".") else _ext_for_mime(t)
                for t in settings.allowed_file_types
            }
            if ext not in allowed:
                raise HTTPException(
                    status_code=415,
                    detail=f"{ext or 'unknown'} files are not accepted",
                )

    try:
        record = _store().save(
            raw,
            filename=file.filename or "upload",
            subdir=subdir or UPLOADS_DIR,
            declared_mime=file.content_type,
        )
    except FileStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        **record.to_dict(),
        "download_url": f"/api/v1/files/{record.id}",
        "preview_url": f"/api/v1/files/{record.id}/preview",
    }


@router.get("/files")
async def list_files() -> dict[str, Any]:
    """List every file the store knows about."""
    return {"files": _store().list()}


@router.get("/files/{file_id}")
async def download_file(file_id: str) -> FileResponse:
    """Download a stored file as an attachment."""
    try:
        record = _store().require(file_id)
    except FileStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    full = os.path.join(_store().root, record.path)
    return FileResponse(
        path=full,
        filename=record.name,
        media_type=record.mime_type or "application/octet-stream",
        content_disposition_type="attachment",
    )


@router.get("/files/{file_id}/preview")
async def preview_file(file_id: str) -> FileResponse:
    """Serve a stored file inline (images and text render in the browser)."""
    try:
        record = _store().require(file_id)
    except FileStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if record.size > MAX_PREVIEW_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{record.name} is too large to preview; download it instead",
        )
    full = os.path.join(_store().root, record.path)
    media = record.mime_type or "application/octet-stream"
    # Only genuinely inline-safe types; everything else is forced to download.
    disposition = "inline" if media in INLINE_TYPES else "attachment"
    return FileResponse(
        path=full,
        filename=record.name,
        media_type=media,
        content_disposition_type=disposition,
    )


@router.delete("/files/{file_id}")
async def delete_file(file_id: str) -> dict[str, Any]:
    """Delete a stored file."""
    try:
        deleted = _store().delete(file_id)
    except FileStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"unknown file id: {file_id}")
    return {"deleted": True, "file_id": file_id}


@router.get("/workspace/list")
async def workspace_list(path: str = Query(default=".")) -> dict[str, Any]:
    """List one directory of the workspace."""
    try:
        return _store().browse(path)
    except FileStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workspace/read")
async def workspace_read(path: str = Query(...)) -> dict[str, Any]:
    """Read a text file from the workspace."""
    try:
        return _store().read_text(path)
    except FileStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workspace/preview")
async def workspace_preview(path: str = Query(...)) -> FileResponse:
    """Serve a workspace file inline (images and text render in the browser).

    Path-addressed, unlike the upload routes: the workspace is where the
    agent puts its own output, so there is no upload record to hang an id
    on. Confinement is ``FileStore.resolve``'s job, not the caller's.
    """
    try:
        full = _store().resolve(path)
    except FileStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail=f"not a file: {path}")
    size = os.path.getsize(full)
    if size > MAX_PREVIEW_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{path} is too large to preview; download it instead",
        )
    media = vision_helper.sniff_mime(_head(full)) or _mime_from_ext(os.path.basename(path))
    disposition = "inline" if media in INLINE_TYPES else "attachment"
    return FileResponse(
        path=full,
        filename=os.path.basename(path),
        media_type=media or "application/octet-stream",
        content_disposition_type=disposition,
    )


@router.get("/workspace/download")
async def workspace_download(path: str = Query(...)) -> FileResponse:
    """Download a file from the workspace."""
    try:
        full = _store().resolve(path)
    except FileStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail=f"not a file: {path}")
    media = vision_helper.sniff_mime(_head(full)) or _mime_from_ext(os.path.basename(path))
    return FileResponse(
        path=full,
        filename=os.path.basename(path),
        media_type=media or "application/octet-stream",
        content_disposition_type="attachment",
    )


@router.delete("/workspace")
async def workspace_delete(path: str = Query(...)) -> dict[str, Any]:
    """Delete a file, or an empty directory, inside the workspace."""
    try:
        action = _store().delete_path(path)
    except FileStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": True, "path": path, "kind": action}


_EXT_FOR_MIME = {
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/json": ".json",
    "text/markdown": ".md",
    "text/x-python": ".py",
    "text/yaml": ".yaml",
    "application/x-yaml": ".yaml",
}


def _ext_for_mime(mime: str) -> str:
    return _EXT_FOR_MIME.get(mime, f".{mime.split('/')[-1]}" if "/" in mime else "")
