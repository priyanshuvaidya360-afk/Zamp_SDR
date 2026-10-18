"""FastAPI app: HTTP routes and SSE endpoints."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import create_db_and_tables
from app.pipeline.runner import recover_interrupted
from app.routes import batches, runs


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    create_db_and_tables()
    recover_interrupted()
    yield


app = FastAPI(title="Signal API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(batches.router)
app.include_router(runs.router)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
