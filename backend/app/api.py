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

import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import LOGS_DIR
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
_PROJECT_ROOT = _BACKEND_DIR.parent
_FRONTEND_DIR = _PROJECT_ROOT / "frontend"
_INDEX_HTML = _FRONTEND_DIR / "index.html"
_EVAL_DIR = _PROJECT_ROOT / "eval"
_REVIEWS_PATH = _EVAL_DIR / "reviews.jsonl"
_KNOWN_RELEVANCE_PATH = _EVAL_DIR / "known_relevance.json"

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
# Eval endpoints — models
# ---------------------------------------------------------------------------


class ChunkJudgment(BaseModel):
    """Human judgment on one retrieved chunk."""

    paper: str
    page: int | str
    rank: int
    relevant: bool


class CitationJudgment(BaseModel):
    """Human judgment on one citation."""

    paper: str
    page: int | str
    correct: bool


class ReviewSubmission(BaseModel):
    """Payload for POST /eval/submit-review."""

    query_id: str
    question: str
    chunk_judgments: list[ChunkJudgment]
    citation_judgments: list[CitationJudgment]
    notes: str = ""


class NextQueryResponse(BaseModel):
    """Response from GET /eval/next-query."""

    query_id: str | None
    question: str | None
    answer: str | None
    retrieved_chunks: list[dict[str, Any]] | None
    all_reviewed: bool


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


# ---------------------------------------------------------------------------
# Eval endpoints
# ---------------------------------------------------------------------------


@app.get("/eval/review", response_class=HTMLResponse, summary="Serve human review UI")
def serve_review_ui():
    """Return the human review HTML page.

    Minimal inline HTML that fetches queries via /eval/next-query and submits
    reviews via /eval/submit-review.

    Returns:
        HTMLResponse with the full review page.
    """
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Query Review - Scientific Research Agent</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            background: white;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h1 {
            color: #2c3e50;
            margin-bottom: 10px;
        }
        .subtitle {
            color: #7f8c8d;
            margin-bottom: 30px;
        }
        .section {
            margin-bottom: 30px;
        }
        .section-title {
            font-size: 18px;
            font-weight: 600;
            color: #34495e;
            margin-bottom: 15px;
            padding-bottom: 8px;
            border-bottom: 2px solid #ecf0f1;
        }
        .question-box {
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
            font-size: 16px;
            line-height: 1.6;
        }
        .answer-box {
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
            line-height: 1.8;
            white-space: pre-wrap;
        }
        .chunk-item {
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 15px;
            margin-bottom: 15px;
            background-color: #fafafa;
        }
        .chunk-header {
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 8px;
        }
        .chunk-text {
            color: #555;
            line-height: 1.6;
            margin: 10px 0;
            padding: 10px;
            background: white;
            border-radius: 4px;
        }
        .button-group {
            margin-top: 10px;
        }
        button {
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            margin-right: 8px;
            transition: all 0.2s;
        }
        .btn-relevant {
            background-color: #27ae60;
            color: white;
        }
        .btn-relevant:hover {
            background-color: #229954;
        }
        .btn-not-relevant {
            background-color: #e74c3c;
            color: white;
        }
        .btn-not-relevant:hover {
            background-color: #c0392b;
        }
        .btn-correct {
            background-color: #3498db;
            color: white;
        }
        .btn-correct:hover {
            background-color: #2980b9;
        }
        .btn-incorrect {
            background-color: #e67e22;
            color: white;
        }
        .btn-incorrect:hover {
            background-color: #d35400;
        }
        .btn-active {
            box-shadow: 0 0 0 3px rgba(0,0,0,0.2);
            transform: scale(1.05);
        }
        textarea {
            width: 100%;
            min-height: 80px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-family: inherit;
            font-size: 14px;
            resize: vertical;
        }
        .action-buttons {
            margin-top: 30px;
            display: flex;
            gap: 15px;
        }
        .btn-submit {
            background-color: #2ecc71;
            color: white;
            padding: 12px 30px;
            font-size: 16px;
        }
        .btn-submit:hover {
            background-color: #27ae60;
        }
        .btn-skip {
            background-color: #95a5a6;
            color: white;
            padding: 12px 30px;
            font-size: 16px;
        }
        .btn-skip:hover {
            background-color: #7f8c8d;
        }
        .status-message {
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
            text-align: center;
        }
        .status-success {
            background-color: #d4edda;
            color: #155724;
        }
        .status-info {
            background-color: #d1ecf1;
            color: #0c5460;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #7f8c8d;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Query Review</h1>
        <p class="subtitle">Review retrieved chunks and citations for accuracy</p>

        <div id="status"></div>
        <div id="content"></div>
    </div>

    <script>
        let currentQueryData = null;
        let chunkJudgments = {};
        let citationJudgments = {};

        async function loadNextQuery() {
            try {
                document.getElementById('content').innerHTML = '<div class="loading">Loading next query...</div>';
                const response = await fetch('/eval/next-query');
                const data = await response.json();

                if (data.all_reviewed) {
                    document.getElementById('content').innerHTML = '<div class="status-message status-info">All queries have been reviewed!</div>';
                    return;
                }

                currentQueryData = data;
                chunkJudgments = {};
                citationJudgments = {};
                renderQuery(data);
            } catch (error) {
                document.getElementById('content').innerHTML = '<div class="status-message status-info">Error loading query: ' + error.message + '</div>';
            }
        }

        function renderQuery(data) {
            const content = document.getElementById('content');

            let html = `
                <div class="section">
                    <div class="section-title">Question</div>
                    <div class="question-box">${escapeHtml(data.question)}</div>
                </div>

                <div class="section">
                    <div class="section-title">Answer</div>
                    <div class="answer-box">${escapeHtml(data.answer)}</div>
                </div>

                <div class="section">
                    <div class="section-title">Retrieved Chunks (mark relevance)</div>
            `;

            data.retrieved_chunks.forEach((chunk, idx) => {
                html += `
                    <div class="chunk-item" id="chunk-${idx}">
                        <div class="chunk-header">
                            [${chunk.rank}] ${escapeHtml(chunk.metadata.paper_name)} - Page ${chunk.metadata.page_number} (score: ${chunk.score.toFixed(3)})
                        </div>
                        <div class="chunk-text">${escapeHtml(chunk.text_preview)}</div>
                        <div class="button-group">
                            <button class="btn-relevant" data-chunk-idx="${idx}" data-relevant="true">✓ Relevant</button>
                            <button class="btn-not-relevant" data-chunk-idx="${idx}" data-relevant="false">✗ Not Relevant</button>
                        </div>
                    </div>
                `;
            });

            html += '</div>';

            // Extract citations from answer (simplified - looks for [paper, page X] pattern)
            const citations = extractCitations(data.answer);
            if (citations.length > 0) {
                html += `
                    <div class="section" id="citations-section">
                        <div class="section-title">Citations (mark correctness)</div>
                `;

                citations.forEach((citation, idx) => {
                    html += `
                        <div class="chunk-item" id="citation-${idx}">
                            <div class="chunk-header">${escapeHtml(citation.paper)} - Page ${citation.page}</div>
                            <div class="button-group">
                                <button class="btn-correct" data-cit-idx="${idx}" data-correct="true"
                                        data-paper="${escapeHtml(citation.paper)}" data-page="${citation.page}">✓ Correct</button>
                                <button class="btn-incorrect" data-cit-idx="${idx}" data-correct="false"
                                        data-paper="${escapeHtml(citation.paper)}" data-page="${citation.page}">✗ Incorrect</button>
                            </div>
                        </div>
                    `;
                });

                html += '</div>';
            }

            html += `
                <div class="section">
                    <div class="section-title">Notes (optional)</div>
                    <textarea id="review-notes" placeholder="Add any comments about this query..."></textarea>
                </div>

                <div class="action-buttons">
                    <button class="btn-submit" onclick="submitReview()">Submit Review</button>
                    <button class="btn-skip" onclick="loadNextQuery()">Skip</button>
                </div>
            `;

            content.innerHTML = html;
            attachButtonListeners();
        }

        function attachButtonListeners() {
            // Chunk relevance buttons
            document.querySelectorAll('[data-chunk-idx]').forEach(btn => {
                btn.addEventListener('click', function() {
                    const idx = parseInt(this.dataset.chunkIdx);
                    const relevant = this.dataset.relevant === 'true';
                    const chunk = currentQueryData.retrieved_chunks[idx];
                    const chunkKey = `${chunk.metadata.paper_name}_${chunk.metadata.page_number}_${chunk.rank}`;
                    chunkJudgments[chunkKey] = relevant;

                    const container = document.getElementById(`chunk-${idx}`);
                    container.querySelectorAll('.button-group button').forEach(b => b.classList.remove('btn-active'));
                    this.classList.add('btn-active');
                });
            });

            // Citation correctness buttons
            document.querySelectorAll('[data-cit-idx]').forEach(btn => {
                btn.addEventListener('click', function() {
                    const idx = parseInt(this.dataset.citIdx);
                    const correct = this.dataset.correct === 'true';
                    const paper = this.dataset.paper;
                    const page = this.dataset.page;
                    const citKey = `${paper}_${page}`;
                    citationJudgments[citKey] = { correct, paper, page };

                    const container = document.getElementById(`citation-${idx}`);
                    container.querySelectorAll('.button-group button').forEach(b => b.classList.remove('btn-active'));
                    this.classList.add('btn-active');
                });
            });
        }

        async function submitReview() {
            if (Object.keys(chunkJudgments).length === 0) {
                alert('Please mark at least one chunk as relevant or not relevant.');
                return;
            }

            const chunks = currentQueryData.retrieved_chunks.map(chunk => {
                const chunkKey = `${chunk.metadata.paper_name}_${chunk.metadata.page_number}_${chunk.rank}`;
                const relevant = chunkJudgments[chunkKey];
                if (relevant === undefined) return null;

                return {
                    paper: chunk.metadata.paper_name,
                    page: chunk.metadata.page_number,
                    rank: chunk.rank,
                    relevant: relevant
                };
            }).filter(x => x !== null);

            const citations = Object.values(citationJudgments).map(cit => ({
                paper: cit.paper,
                page: cit.page,
                correct: cit.correct
            }));

            const payload = {
                query_id: currentQueryData.query_id,
                question: currentQueryData.question,
                chunk_judgments: chunks,
                citation_judgments: citations,
                notes: document.getElementById('review-notes').value
            };

            try {
                const response = await fetch('/eval/submit-review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    throw new Error('Failed to submit review');
                }

                showStatus('Review submitted successfully!', 'success');
                setTimeout(loadNextQuery, 1000);
            } catch (error) {
                showStatus('Error submitting review: ' + error.message, 'error');
            }
        }

        function showStatus(message, type) {
            const status = document.getElementById('status');
            status.innerHTML = `<div class="status-message status-${type === 'success' ? 'success' : 'info'}">${message}</div>`;
            setTimeout(() => { status.innerHTML = ''; }, 3000);
        }

        function extractCitations(text) {
            // Simple regex to find [paper_name, page X] patterns
            const regex = /\\[([^,]+),\\s*(?:page\\s*)?([\\d]+)\\]/gi;
            const citations = [];
            let match;
            while ((match = regex.exec(text)) !== null) {
                citations.push({ paper: match[1].trim(), page: match[2] });
            }
            return citations;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Load first query on page load
        loadNextQuery();
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)


@app.get(
    "/eval/next-query",
    response_model=NextQueryResponse,
    summary="Get next unreviewed query"
)
def get_next_query() -> NextQueryResponse:
    """Return the next query that hasn't been reviewed yet.

    Reads all query logs from backend/logs/, checks eval/reviews.jsonl for
    already-reviewed query IDs, and returns the first unreviewed one.

    Returns:
        NextQueryResponse with query data or all_reviewed=True if none remain.
    """
    logs_dir = Path(LOGS_DIR)
    if not logs_dir.exists():
        return NextQueryResponse(
            query_id=None,
            question=None,
            answer=None,
            retrieved_chunks=None,
            all_reviewed=True,
        )

    # Load reviewed query IDs
    reviews_path = _REVIEWS_PATH
    reviewed_ids = set()
    if reviews_path.exists():
        with reviews_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    review = json.loads(line)
                    reviewed_ids.add(review.get("query_id"))
                except json.JSONDecodeError:
                    logger.warning("Malformed JSONL line in reviews.jsonl: %s", line[:100])

    # Find first unreviewed query log
    log_files = sorted(logs_dir.glob("query_*.json"))
    for log_file in log_files:
        try:
            with log_file.open("r", encoding="utf-8") as f:
                log_data = json.load(f)

            query_id = log_data.get("id")
            if query_id and query_id not in reviewed_ids:
                return NextQueryResponse(
                    query_id=query_id,
                    question=log_data.get("question"),
                    answer=log_data.get("answer"),
                    retrieved_chunks=log_data.get("retrieved_chunks", []),
                    all_reviewed=False,
                )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read log file %s: %s", log_file, exc)
            continue

    # All reviewed
    return NextQueryResponse(
        query_id=None,
        question=None,
        answer=None,
        retrieved_chunks=None,
        all_reviewed=True,
    )


@app.get(
    "/eval/query/{query_id}",
    summary="Get specific query by ID"
)
def get_query_by_id(query_id: str) -> dict[str, Any]:
    """Fetch a specific query log by its UUID.

    Args:
        query_id: UUID of the query to fetch.

    Returns:
        Dict with query log data.

    Raises:
        HTTPException 404: If query ID not found.
    """
    logs_dir = Path(LOGS_DIR)
    if not logs_dir.exists():
        raise HTTPException(status_code=404, detail="Logs directory not found")

    for log_file in logs_dir.glob("query_*.json"):
        try:
            with log_file.open("r", encoding="utf-8") as f:
                log_data = json.load(f)

            if log_data.get("id") == query_id:
                return log_data
        except (json.JSONDecodeError, OSError):
            continue

    raise HTTPException(status_code=404, detail=f"Query {query_id} not found")


@app.post(
    "/eval/submit-review",
    summary="Submit a human review"
)
def submit_review(review: ReviewSubmission) -> dict[str, str]:
    """Save a human review to eval/reviews.jsonl and rebuild known_relevance.json.

    Args:
        review: ReviewSubmission payload with judgments.

    Returns:
        Dict with status message.

    Raises:
        HTTPException 500: If writing fails.
    """
    from datetime import datetime, timezone

    reviews_path = _REVIEWS_PATH
    _EVAL_DIR.mkdir(parents=True, exist_ok=True)

    review_entry = {
        "query_id": review.query_id,
        "question": review.question,
        "chunk_judgments": [j.model_dump() for j in review.chunk_judgments],
        "citation_judgments": [j.model_dump() for j in review.citation_judgments],
        "notes": review.notes,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        with reviews_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(review_entry, ensure_ascii=False) + "\n")

        # Rebuild known_relevance.json after each review
        _rebuild_known_relevance()

        logger.info("Review submitted for query %s", review.query_id)
    except OSError as exc:
        logger.error("Failed to write review: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to save review: {exc}") from exc

    return {"status": "success", "message": "Review submitted"}


def _rebuild_known_relevance():
    """Rebuild eval/known_relevance.json from eval/reviews.jsonl.

    Aggregates chunk judgments per exact question string. If a query appears
    multiple times (re-review), uses the latest review.

    This function is called automatically after each review submission.
    """
    reviews_path = _REVIEWS_PATH
    known_path = _KNOWN_RELEVANCE_PATH

    if not reviews_path.exists():
        known_path.write_text("{}", encoding="utf-8")
        return

    # Map: query_id -> review_entry (latest only)
    reviews_by_id: dict[str, dict] = {}

    with reviews_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                review = json.loads(line)
                query_id = review.get("query_id")
                if query_id:
                    reviews_by_id[query_id] = review
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed review line: %s", exc)

    # Aggregate by exact question string
    known_relevance: dict[str, dict] = {}

    for review in reviews_by_id.values():
        question = review.get("question")
        if not question:
            continue

        if question not in known_relevance:
            known_relevance[question] = {
                "relevant_chunks": [],
                "irrelevant_chunks": [],
            }

        for judgment in review.get("chunk_judgments", []):
            chunk_ref = {"paper": judgment["paper"], "page": judgment["page"]}

            if judgment["relevant"]:
                if chunk_ref not in known_relevance[question]["relevant_chunks"]:
                    known_relevance[question]["relevant_chunks"].append(chunk_ref)
            else:
                if chunk_ref not in known_relevance[question]["irrelevant_chunks"]:
                    known_relevance[question]["irrelevant_chunks"].append(chunk_ref)

    known_path.write_text(json.dumps(known_relevance, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Rebuilt known_relevance.json with %d questions", len(known_relevance))
