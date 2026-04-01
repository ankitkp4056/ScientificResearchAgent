# Feature Implementation Plan: Phase 4 — Web UI

**Overall Progress:** `100%`

## TLDR

Build a minimal local web interface for the Scientific Research Agent. A FastAPI backend exposes the existing query pipeline and re-index functionality as REST endpoints, and a plain HTML/CSS/JS frontend lets a user ask questions, see cited answers, and trigger re-indexing -- all on localhost with no auth.

## Exploration Summary

**Existing interfaces (Phase 3 output):**
- `query.py` provides `initialize_query_engine()` (returns a `RetrieverQueryEngine`) and `query_question(question, query_engine)` (returns `{"answer": str, "sources": [{"paper", "page", "score", "text_preview"}]}`)
- `index.py` provides `reindex()` which wipes storage, re-ingests all PDFs, embeds, and persists a fresh index
- `config.py` exports `PAPERS_DIR`, `LOGS_DIR`, `SIMILARITY_TOP_K`, `LOW_CONFIDENCE_THRESHOLD`

**New dependencies needed:** `fastapi`, `uvicorn[standard]` -- added to `backend/requirements.txt`

**Files to create:** `backend/app/api.py`, `frontend/index.html`

**Files to modify:** `backend/requirements.txt`

**No changes to existing Phase 1-3 code** -- `config.py`, `ingestion.py`, `index.py`, `query.py`, `logging_utils.py` remain untouched.

**Edge cases identified:**
- Empty question: Phase 3 already returns a graceful message; frontend should also disable submit on empty input
- No papers in directory: `reindex()` raises `ValueError` -- API must catch and return 500 with JSON error
- Missing API key: Let startup fail loudly (fail-fast); runtime errors caught per-endpoint
- Long-running re-index: Blocks for minutes; acceptable for prototype (spinner shown)
- Concurrent re-index: Low risk for single-user localhost; no lock needed for prototype

## Critical Decisions

- **Structured JSON errors:** All error responses use `{"error": "message"}` format with appropriate HTTP status codes (422 for validation, 500 for server errors)
- **Eager engine initialization:** Call `initialize_query_engine()` on FastAPI startup (lifespan event) so config/API-key errors surface immediately
- **Module-level query engine:** Store the engine as a module-level variable in `api.py` -- simple, sufficient for single-user local app
- **No CORS needed:** Frontend served from same origin via FastAPI StaticFiles mount
- **Plain vanilla frontend:** Single HTML file with inline CSS and JS, no framework, no build tools
- **Re-index confirmation:** Use browser `confirm()` dialog for simplicity
- **Re-index button placement:** Below the answer area as a secondary action

## Tasks

- [x] :green_square: **Step 1: Update dependencies**
  - [x] :green_square: Add `fastapi` and `uvicorn[standard]` to `backend/requirements.txt`

- [x] :green_square: **Step 2: Create FastAPI backend (`backend/app/api.py`)**
  - [x] :green_square: Define Pydantic request/response models (`QueryRequest`, `QueryResponse`, `SourceInfo`, `ReindexResponse`, `ErrorResponse`)
  - [x] :green_square: Implement lifespan context manager: call `initialize_query_engine()` on startup and store as module-level variable
  - [x] :green_square: Implement `POST /query` endpoint: validate non-empty question, call `query_question()`, return structured JSON response; catch exceptions and return 500 with `{"error": "..."}`
  - [x] :green_square: Implement `POST /reindex` endpoint: call `reindex()`, return success status with paper/chunk counts; catch exceptions and return 500 with `{"error": "..."}`
  - [x] :green_square: Serve `frontend/index.html` via `GET /` using `FileResponse`
  - [x] :green_square: Add Python `logging` setup consistent with Phases 1-3

- [x] :green_square: **Step 3: Create frontend (`frontend/index.html`)**
  - [x] :green_square: Build HTML structure: question input field, Ask button, answer display area, citations list, Re-index button, status/error message area
  - [x] :green_square: Add inline CSS: clean minimal layout with flexbox, readable typography, distinct styling for citations
  - [x] :green_square: Implement JS: form submit handler that POSTs to `/query`, parses response, renders answer and source citations in the DOM
  - [x] :green_square: Implement JS: Re-index button with `confirm()` dialog, POST to `/reindex`, show loading state and result/error
  - [x] :green_square: Add loading spinner/indicator for both query and re-index operations
  - [x] :green_square: Add client-side validation: disable Ask button when input is empty

- [x] :green_square: **Step 4: Smoke test** (static verification complete; live test requires running server)
  - [x] :green_square: Path resolution verified: `frontend/index.html` correctly resolved from `backend/app/api.py`
  - [x] :green_square: All import names verified against Phase 1-3 source files
  - [x] :green_square: Python AST syntax check passed for `api.py`
  - [x] :green_square: Error handling paths verified: 503 (no engine), 500 (ValueError + generic), 404 (missing HTML), 422 (empty question via Pydantic)
