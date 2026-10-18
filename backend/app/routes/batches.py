"""Batch upload and preview. Spec: docs/GUIDE.md "Input: the prospect CSV" and T04."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session

from app import ingest
from app.db import get_session
from app.models import Batch, BatchStatus

router = APIRouter(prefix="/batches", tags=["batches"])


class MappingUpdate(BaseModel):
    mapping: dict[str, str | None]


def _store(batch: Batch, result: ingest.ParseResult) -> None:
    counts = result.counts
    batch.headers = result.headers
    batch.source_rows = result.source_rows
    batch.column_mapping = result.mapping
    batch.rows = [r.model_dump() for r in result.rows]
    batch.row_count = counts["total"]
    batch.valid_count = counts["valid"]
    batch.invalid_count = counts["invalid"]
    batch.duplicate_count = counts["duplicate"]


def _preview_response(batch: Batch) -> dict[str, Any]:
    result = ingest.ParseResult(
        headers=batch.headers,
        source_rows=batch.source_rows,
        mapping=batch.column_mapping,
        rows=[ingest.RowResult(**r) for r in batch.rows],
    )
    return {
        "batch_id": batch.id,
        "filename": batch.filename,
        "status": batch.status,
        "preview": result.preview(),
    }


def _get_batch(session: Session, batch_id: int) -> Batch:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, f"Batch {batch_id} not found.")
    return batch


@router.post("", status_code=201)
async def upload_batch(file: UploadFile, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Validate a prospect CSV and return the preview. Spends no API credits."""
    content = await file.read(ingest.MAX_BYTES + 1)
    filename = file.filename or "upload.csv"
    try:
        result = ingest.parse_upload(content, filename)
    except ingest.CsvRejected as error:
        raise HTTPException(422, str(error)) from None
    batch = Batch(filename=filename, status=BatchStatus.validating)
    _store(batch, result)
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return _preview_response(batch)


@router.get("/{batch_id}/preview")
def get_preview(batch_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return _preview_response(_get_batch(session, batch_id))


@router.patch("/{batch_id}/mapping")
def update_mapping(
    batch_id: int, update: MappingUpdate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """Re-validate every row with a user-corrected column mapping."""
    batch = _get_batch(session, batch_id)
    if batch.status != BatchStatus.validating:
        raise HTTPException(409, "This batch has already started; its mapping can't change.")
    try:
        result = ingest.revalidate(batch.headers, batch.source_rows, update.mapping)
    except ingest.CsvRejected as error:
        raise HTTPException(422, str(error)) from None
    _store(batch, result)
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return _preview_response(batch)
