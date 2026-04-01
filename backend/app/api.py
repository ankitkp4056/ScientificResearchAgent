"""FastAPI application for the Scientific Research Agent Web UI.

This module exposes the Phase 3 query pipeline and re-index functionality as
REST endpoints, and serves the frontend HTML from the same origin (no CORS
needed for same-origin requests).

Endpoints
---------
  GET  /           — Serves frontend/index.html
  POST /query      — Ask a question; returns answer + citations
  POST /reindex    — Force a full re-index of the papers directory

Run from inside backend/:
  uvicorn app.api:app --host 127.0.0.1 --port 8000

Startup behaviour
-----------------
  The query engine is initialised once during the FastAPI lifespan startup
  event and stored as a module-level variable.  Any configuration error (e.g.
  missing OPENAI_API_KEY) surfaces immediately at startup rather than at first
  request.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.index import reindex as _reindex
from app.query import initialize_query_engine, query_question

# ---------------------------------------------------------------------------
# Logging — follow the same format used by Phases 1-3
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# backend/app/api.py  →  backend/  →  (project root)/frontend/index.html
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_FRONTEND_DIR = _BACKEND_DIR.parent / "frontend"
_INDEX_HTML = _FRONTEND_DIR / "index.html"

# ---------------------------------------------------------------------------
# Module-level query engine (initialised in lifespan, replaced on re-index)
# ---------------------------------------------------------------------------

_query_engine = None  # type: Any  # RetrieverQueryEngine after startup


# ---------------------------------------------------------------------------
# Lifespan: initialise engine on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager.

    Initialises the query engine once when the server starts so that any
    configuration errors (missing API key, empty papers directory) are
    surfaced immediately rather than on the first request.
    """
    global _query_engine
    logger.info("Startup: initialising query engine ...")
    try:
        _query_engine = initialize_query_engine()
        logger.info("Query engine ready.")
    except Exception as exc:  # noqa: BLE001
        # Log the error but allow the server to start so that the /reindex
        # endpoint is still reachable (useful when papers haven't been indexed yet).
        logger.error("Failed to initialise query engine at startup: %s", exc)
        _query_engine = None

    yield  # server runs here

    # Shutdown: nothing to clean up.
    logger.info("Shutdown: query engine released.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Scientific Research Agent",
    description="Local research paper Q&A with citations.",
    version="0.4.0",
    lifespan=lifespan,
)

# CORS: allow requests from localhost on any port (useful during development
# when the frontend is served by a different dev server).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    """Request body for POST /query."""

    question: str = Field(..., min_length=1, description="The research question to ask.")


class SourceInfo(BaseModel):
    """A single retrieved source chunk used to ground the answer."""

    paper: str
    page: int | str
    score: float
    text_preview: str


class QueryResponse(BaseModel):
    """Response from POST /query."""

    answer: str
    sources: list[SourceInfo]


class ReindexResponse(BaseModel):
    """Response from POST /reindex."""

    status: str
    message: str


class ErrorResponse(BaseModel):
    """Structured JSON error returned on 4xx / 5xx responses."""

    error: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/", response_class=FileResponse, summary="Serve the frontend UI")
async def serve_index():
    """Return the frontend HTML page.

    FastAPI's FileResponse handles ETag and Last-Modified headers
    automatically so browsers can cache the page.

    Raises:
        HTTPException 404: If ``frontend/index.html`` does not exist.
    """
    if not _INDEX_HTML.exists():
        raise HTTPException(
            status_code=404,
            detail="Frontend file not found. Ensure frontend/index.html exists relative to the project root.",
        )
    return FileResponse(str(_INDEX_HTML), media_type="text/html")


@app.post(
    "/query",
    response_model=QueryResponse,
    responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Ask a question about the indexed papers",
)
def query_endpoint(request: QueryRequest) -> QueryResponse:
    """Answer a natural-language question using the indexed research papers.

    The engine retrieves the top-K most relevant chunks and synthesises a
    grounded answer with inline citations.  If the engine has not been
    initialised (e.g. startup failed), a 503 is returned.

    Running as a plain ``def`` so FastAPI dispatches it in a thread pool,
    preventing the synchronous query pipeline from blocking the event loop.

    Args:
        request: JSON body containing the ``question`` field.

    Returns:
        JSON with ``answer`` (str) and ``sources`` (list of source objects).

    Raises:
        HTTPException 503: If the query engine is not initialised.
        HTTPException 500: On unexpected pipeline errors.
    """
    if _query_engine is None:
        raise HTTPException(
            status_code=503,
            detail="Query engine is not initialised. "
                   "Check logs for startup errors or run POST /reindex first.",
        )

    try:
        result: dict[str, Any] = query_question(request.question, _query_engine)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in /query: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Query pipeline error: {exc}",
        ) from exc

    return QueryResponse(
        answer=result["answer"],
        sources=[SourceInfo(**src) for src in result.get("sources", [])],
    )


@app.post(
    "/reindex",
    response_model=ReindexResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Force a full re-index of all papers",
)
def reindex_endpoint() -> ReindexResponse:
    """Wipe the existing index and rebuild it from all PDFs in the papers directory.

    After a successful re-index the module-level query engine is replaced with
    a freshly initialised one so subsequent /query calls use the updated index.

    Use this endpoint whenever papers have been added, removed, or updated.

    Running as a plain ``def`` so FastAPI dispatches it in a thread pool,
    preventing the long-running re-index operation from blocking the event loop.

    Returns:
        JSON with ``status`` ("success") and a human-readable ``message``.

    Raises:
        HTTPException 500: If re-indexing fails (e.g. no papers, API error).
    """
    global _query_engine

    logger.info("POST /reindex — starting full rebuild ...")
    try:
        _reindex()
        # Re-initialise the query engine so it uses the freshly built index.
        _query_engine = initialize_query_engine()
        logger.info("Re-index complete; query engine refreshed.")
    except ValueError as exc:
        # Typically: no papers in directory, or missing API key.
        logger.error("Re-index failed (ValueError): %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Re-index failed with unexpected error: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Re-index error: {exc}",
        ) from exc

    return ReindexResponse(
        status="success",
        message="Papers re-indexed successfully. Query engine refreshed.",
    )
