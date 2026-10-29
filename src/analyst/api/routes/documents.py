"""Flow B: upload, list and delete documents."""

from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, status

from analyst.api.deps import WorkspaceDep
from analyst.config import get_settings
from analyst.db.audit import audit
from analyst.documents.ingest import DocumentRecord, delete_document, ingest_document, list_documents

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
async def documents(ws: WorkspaceDep) -> dict[str, list[DocumentRecord]]:
    return {"documents": await list_documents(ws.id)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile, ws: WorkspaceDep) -> dict[str, Any]:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"max upload size is {limit // 1024 // 1024} MB")
    try:
        record = await ingest_document(ws.id, file.filename or "upload.txt", file.content_type, data)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    await audit(
        "upload_document",
        actor="user",
        workspace_id=ws.id,
        detail={
            "document_id": record.id,
            "filename": record.filename,
            "chunks": record.n_chunks,
            "flagged": record.flagged_chunks,
        },
    )
    warning = (
        f"{record.flagged_chunks} chunk(s) look like prompt-injection attempts and are excluded from search."
        if record.flagged_chunks
        else None
    )
    return {"document": record, "warning": warning}


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(document_id: str, ws: WorkspaceDep) -> None:
    if not await delete_document(ws.id, document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
