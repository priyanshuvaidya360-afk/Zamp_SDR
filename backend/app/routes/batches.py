"""Batch upload and preview. Spec: docs/GUIDE.md "Input: the prospect CSV" and T04."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app import db, ingest
from app.config import settings
from app.db import get_session
from app.events import HEARTBEAT, bus, format_sse, sse_response
from app.models import Batch, BatchStatus, Run
from app.pipeline import runner
from app.routes.runs import latest_stage_events, run_summary

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


@router.post("/{batch_id}/start", status_code=202)
async def start_batch(batch_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Create one run per valid row and process them in the background (5 at a time)."""
    batch = _get_batch(session, batch_id)
    if batch.status != BatchStatus.validating:
        raise HTTPException(409, "This batch has already started.")
    if batch.valid_count == 0:
        raise HTTPException(422, "No valid rows to run. Fix the file or the column mapping first.")
    run_ids = runner.create_runs(session, batch)
    runner.spawn(runner.run_batch(run_ids))
    return {"batch_id": batch.id, "status": batch.status, "run_ids": run_ids}


@router.get("/{batch_id}")
def get_batch(batch_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """The batch, its runs (with each run's latest stage), and rows that never became runs."""
    batch = _get_batch(session, batch_id)
    runs = session.exec(select(Run).where(Run.batch_id == batch_id).order_by(Run.row_number)).all()
    latest = latest_stage_events(session, [r.id for r in runs])
    return {
        "batch": batch.model_dump(exclude={"source_rows", "rows", "headers"}),
        "progress": runner.batch_progress(session, batch_id),
        "runs": [run_summary(r, latest.get(r.id)) for r in runs],
        "not_run": [
            {"row_number": r["row_number"], "status": r["status"], "reason": r["reason"],
             "name": " ".join(filter(None, (r["data"].get("first_name"), r["data"].get("last_name"))))}
            for r in batch.rows if r["status"] != "valid"
        ],
    }


@router.get("/{batch_id}/events")
async def batch_events(batch_id: int) -> StreamingResponse:
    """SSE: a snapshot of progress, then live stage / run_status / batch_progress events."""
    with Session(db.engine) as session:
        _get_batch(session, batch_id)

    async def stream():
        topic = f"batch:{batch_id}"
        queue = bus.subscribe(topic)
        try:
            with Session(db.engine) as session:
                progress = runner.batch_progress(session, batch_id)
                status = session.get(Batch, batch_id).status
            yield format_sse({**progress, "type": "batch_progress"})
            if status == BatchStatus.done:
                yield format_sse({"type": "end", "batch_id": batch_id})
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), settings.sse_heartbeat_s)
                except TimeoutError:
                    yield HEARTBEAT
                    continue
                yield format_sse(event)
                if event["type"] == "batch_status" and event["status"] == "done":
                    yield format_sse({"type": "end", "batch_id": batch_id})
                    return
        finally:
            bus.unsubscribe(topic, queue)

    return sse_response(stream())


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
