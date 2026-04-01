# Exploration: Phase 4 — Web UI

**Date:** 2026-04-01  
**Phase:** 4 (Web UI)  
**Status:** Exploration Complete

---

## Scope Summary

Phase 4 delivers a minimal local web interface that exposes the Phase 3 query pipeline as a REST API and serves a single-page HTML/CSS/JS frontend. Users can:

1. Ask natural-language questions via a text input
2. See cited answers with source paper names and page numbers
3. Trigger a full re-index of papers on demand (with confirmation)
4. Experience graceful loading states and error handling

Key deliverables:
- **FastAPI backend API** with two endpoints: `POST /query` and `POST /reindex`, plus `GET /` serving static frontend
- **Simple HTML/CSS/JS frontend** — no framework, plain vanilla JavaScript
- **Integration with Phase 3:** Call `query_question()` from `query.py` for `/query`, call `reindex()` from `index.py` for `/reindex`
- **No auth, no sessions** — localhost-only, single-user interface

Out of scope:
- Paper upload UI (papers added by copying to `papers/` directory only)
- User sessions or query history
- Streaming/typewriter effect
- Mobile responsiveness or design polish

---

## Existing Project State

### Directory Structure (Worktree at `/tmp/sra-module-4`)

```
backend/
  app/
    __init__.py              (empty package marker)
    config.py                (config constants, env loading, paths)
    ingestion.py             (Phase 1: PDF extraction + chunking)
    index.py                 (Phase 2: embedding + persistent index)
    query.py                 (Phase 3: query pipeline + logging)
    logging_utils.py         (Phase 3: JSON query logging)
    [api.py NOT YET CREATED] (Phase 4: FastAPI app goes here)
  requirements.txt           (Phase 1 + Phase 2 + Phase 3 deps; missing fastapi/uvicorn)
  storage/                   (Phase 2 output: persisted VectorStoreIndex, auto-created)
  logs/                      (Phase 3 output: query logs as JSON, auto-created)
  venv/                      (Python environment, not in repo)

[frontend/ NOT YET CREATED] (Phase 4: static HTML/CSS/JS)

papers/                      (input PDFs, not in worktree but exists in main)
docs/
  MODULE_04_UI.md            (Phase 4 specification)
  EXPLORE_module-4.md        (this file)
  [TRACKING_module-4.md will be created in planning step]

.env                         (not in worktree; OPENAI_API_KEY needed for runtime)
```

### Python Dependencies (Current `backend/requirements.txt`)

```
pymupdf==1.24.3                      # Phase 1: PDF extraction
llama-index-core==0.10.40            # Phase 1 & 2: chunking, indexing, retrieval
llama-index-embeddings-openai==0.1.11 # Phase 2: OpenAI embedding model
llama-index-llms-openai>=0.1.0       # Phase 3: OpenAI LLM plugin for query engine
openai>=1.0.0                        # Phase 2 & 3: OpenAI API client
python-dotenv==1.0.1                # Config: .env loading
```

**Missing for Phase 4:**
- `fastapi` — lightweight async web framework
- `uvicorn` — ASGI server to run FastAPI

### Git State

- **Worktree:** `/tmp/sra-module-4` created from main branch for Phase 4 work
- **Status:** Phases 1–3 already implemented and committed; Phase 4 is greenfield

---

## Phase 3 Output — Interfaces Phase 4 Depends On

### From `backend/app/query.py`

**Function: `initialize_query_engine(papers_dir=None, storage_dir=None) -> RetrieverQueryEngine`**

- **Purpose:** Load (or build) the vector index and return a query engine ready to answer questions
- **Side effects:** May build and persist index on first call; subsequent calls load from disk (fast path)
- **Raises:** `ValueError` if `OPENAI_API_KEY` is not set or index cannot be built
- **Return type:** LlamaIndex `RetrieverQueryEngine` — stateful object that owns retriever and response synthesizer

**Function: `query_question(question: str, query_engine: RetrieverQueryEngine) -> dict`**

- **Purpose:** Answer a single question using the provided query engine
- **Input:** Non-empty string question
- **Returns:**
  ```python
  {
    "answer": str,  # Grounded, cited answer or fallback message
    "sources": [
      {
        "paper": str,        # Paper filename (e.g., "2016_NRDP_pancreaticcancer.pdf")
        "page": int | str,   # Page number (int when available, "unknown" otherwise)
        "score": float,      # Retrieval similarity score (0.0–1.0)
        "text_preview": str  # First 200 characters of chunk text
      },
      ...  # Up to 5 entries (SIMILARITY_TOP_K)
    ]
  }
  ```
- **Behavior:**
  - Returns graceful "I don't have sufficient information..." message if retrieval yields nothing
  - Prepends low-confidence disclaimer if top score < 0.3 (LOW_CONFIDENCE_THRESHOLD)
  - Logs query to `backend/logs/` as JSON (filename: `query_<YYYYMMDD_HHMMSS_ffffff>.json`)
  - Never raises on API error; returns user-friendly error message instead
- **Side effects:** Writes JSON log file to `LOGS_DIR`; makes one OpenAI API call (embedding for retrieval + LLM call)

### From `backend/app/index.py`

**Function: `reindex(papers_dir=None, storage_dir=None) -> VectorStoreIndex`**

- **Purpose:** Force a complete rebuild of the index: delete old storage, ingest papers, embed, persist
- **Input:** Optional override for papers and storage directories
- **Returns:** Freshly built and persisted `VectorStoreIndex`
- **Behavior:**
  - Wipes `storage_dir` completely (if it exists) to avoid stale data
  - Discovers all `.pdf` files in `papers_dir`
  - Extracts text page-by-page, chunks into 512-token units with 100-token overlap
  - Embeds chunks via OpenAI text-embedding-3-small
  - Persists index to disk
  - Logs summary (paper count, chunk count, elapsed time)
- **Raises:** `ValueError` if `OPENAI_API_KEY` unset or no papers found
- **Side effects:** Deletes and recreates `storage_dir`; makes multiple OpenAI API calls (one per batch of chunks during embedding)
- **Duration:** Slow (~minutes for typical set of papers due to embedding API latency)

### From `backend/app/config.py`

Key constants available for import:

```python
PAPERS_DIR: Path       # Where PDFs are stored (env SRA_PAPERS_DIR or default to `papers/`)
LOGS_DIR: Path         # Where query logs are written (`backend/logs/`)
SIMILARITY_TOP_K: int  # Number of chunks to retrieve (hardcoded: 5)
LOW_CONFIDENCE_THRESHOLD: float  # Disclaimer threshold (hardcoded: 0.3)
```

---

## Phase 4 Design Decisions & Questions

### API Endpoint Design

**POST /query**

- **Request body:** `{"question": "..."}` (or `{"question": "", ...}` to handle edge cases)
- **Success response (200):**
  ```json
  {
    "answer": "The main risk factors include...",
    "sources": [
      {"paper": "paper.pdf", "page": 3, "score": 0.87, "text_preview": "..."}
    ]
  }
  ```
- **Error response (500):** Simple error message? Or structured JSON with error code?
  - **Decision needed:** Should errors return `{"error": "message"}` or a simple string? For consistency with `/query`, I suggest structured JSON: `{"error": "message"}`

**POST /reindex**

- **Request body:** Empty or `{}` (no parameters needed)
- **Success response (200):**
  ```json
  {
    "status": "success",
    "papers_indexed": 10,
    "chunks_created": 2500
  }
  ```
- **Question:** What if re-indexing fails partway (e.g., OpenAI API timeout)? Should this return 500 with `{"error": "..."}` or 200 with `{"status": "error", "message": "..."}`? I suggest 500 for consistency with HTTP semantics.

**GET /**

- Serves `frontend/index.html` (the single-page app)

### Frontend Architecture

**Single HTML file** (`frontend/index.html`) with embedded CSS and JavaScript (no build tools, no external CSS framework)

Structure:
```html
<html>
  <head>
    <title>Scientific Research Agent</title>
    <style>/* minimal inline CSS */</style>
  </head>
  <body>
    <!-- Text input + Ask button -->
    <!-- Answer display area -->
    <!-- Citations list -->
    <!-- Re-index button -->
    <!-- Loading spinner / status messages -->
    <script>/* vanilla JS for API calls + DOM updates */</script>
  </body>
</html>
```

**Key interactions:**
1. User types a question and clicks "Ask" → POST to `/query` → display answer and citations
2. Loading spinner shown while request in flight
3. User clicks "Re-index Papers" → show confirmation dialog → POST to `/reindex` → show status
4. Error handling: display user-friendly messages if API fails

**Decision:** Should we display the "Re-index" button prominently on the main page, or hide it in a menu/advanced section? For simplicity and single-user local use, I suggest it goes below the answer area as a secondary button.

### Server Startup & Initialization

**Questions:**

1. **Index loading on startup:** Should `api.py` call `initialize_query_engine()` in a startup event handler (eager), or lazily on first query? I suggest eager startup to catch config/API key errors immediately.

2. **Global state:** Store the query engine as a module-level variable in `api.py`, or pass it through dependency injection? For a single-user local app, module-level is simpler.

3. **Logging:** Use FastAPI's logging or Python `logging` module? I suggest Python `logging` for consistency with Phases 1–3.

4. **CORS:** Do we need CORS headers? Only if frontend will be served from a different origin. Since `GET /` serves the frontend and `/query` and `/reindex` will be called from the same origin (localhost:8000), CORS is not needed.

### Deployment & Running

**From `MODULE_04_UI.md`:**

```bash
uvicorn app.api:app --host 127.0.0.1 --port 8000
```

**Questions:**

1. **Hot reload:** Should we pass `--reload` for development? Yes, for iteration speed.
2. **Port:** Hardcoded 8000, or configurable via env? For prototype, 8000 is fine.
3. **Public binding:** Always 127.0.0.1 (localhost), never 0.0.0.0? Yes, the spec says "localhost only, no auth."

---

## Dependency & Integration Points

### Hard Dependencies

1. **Phase 3 query pipeline:** Must call `initialize_query_engine()` and `query_question()` from `query.py`
2. **Phase 2 index:** Must call `reindex()` from `index.py` (which internally calls Phase 1's `ingest_papers()`)
3. **Environment:** `OPENAI_API_KEY` must be set before starting the server
4. **Configuration:** All paths from `config.py` are used (PAPERS_DIR, LOGS_DIR, etc.)

### Optional Dependencies

- Logging to `LOGS_DIR` already happens in Phase 3; Phase 4 just calls Phase 3 functions
- Error handling is already in place in `query_question()` — Phase 4 API just passes through results

### External Packages to Add

```
fastapi          # Web framework
uvicorn[standard] # ASGI server (includes uvicorn + some extras)
```

---

## Edge Cases & Risks Identified

### Edge Case 1: Empty Question

**Current behavior in `query_question()`:** Returns `{"answer": "Please provide a non-empty question.", "sources": []}`

**Phase 4 responsibility:** Frontend should disable "Ask" button if input is empty, but backend should also guard (already does in Phase 3).

### Edge Case 2: No Papers in Directory

**Current behavior in `reindex()`:** Raises `ValueError("No PDF files found")`

**Phase 4 responsibility:** Wrap `reindex()` call in try/except and return 500 with user-friendly error message.

### Edge Case 3: OpenAI API Key Missing or Invalid

**Current behavior:** Both `initialize_query_engine()` and `reindex()` raise `ValueError` if OPENAI_API_KEY not set or API calls fail.

**Phase 4 responsibility:**
- On startup: Let error propagate so server fails loudly (fail-fast).
- On query/reindex: Catch and return 500 error response. Phase 3's `query_question()` already handles API failures gracefully.

### Edge Case 4: Index Corruption or Missing

**Current behavior in `load_or_build_index()`:** If storage is corrupt, logs warning and rebuilds from scratch. Safe.

**Phase 4 responsibility:** None — Phase 3 handles this transparently.

### Edge Case 5: Concurrent Requests

**Risk:** If two requests try to re-index simultaneously, they could corrupt the index.

**Mitigation needed?** For a single-user localhost app, this is low risk. If we want to be safe, we could add a lock around the reindex operation. For now (prototype), acceptable to leave as-is.

### Edge Case 6: Long-Running Re-Index

**Current behavior:** `reindex()` blocks until complete. For 10 papers, ~minutes.

**Frontend consequence:** User sees loading spinner for ~minutes. For prototype, acceptable.

**Future enhancement:** Async re-index with polling, but out of scope.

---

## File Checklist for Phase 4 Implementation

**To Create:**

- [ ] `backend/app/api.py` — FastAPI app with `/query`, `/reindex`, and `GET /` endpoints
- [ ] `frontend/index.html` — Single-page HTML/CSS/JS frontend
- [ ] Update `backend/requirements.txt` to add `fastapi` and `uvicorn`

**To Modify:**

- [ ] `docs/PHASE4_TRACKING.md` — Created during planning step with implementation tasks

**No changes needed to:**

- `backend/app/config.py`, `ingestion.py`, `index.py`, `query.py`, `logging_utils.py` — all Phase 1–3 code is complete and correct

---

## Summary of Ambiguities & Questions for Clarification

1. **Error response format:** Should `/query` and `/reindex` return structured JSON errors (e.g., `{"error": "message"}`) or simple strings? Recommend structured JSON for API consistency.

2. **Re-index error semantics:** Should a failed re-index return HTTP 500 or 200 with a status field? Recommend HTTP 500 for proper error signaling.

3. **Eager vs lazy engine initialization:** Load query engine on server startup (fail-fast) or on first query? Recommend eager.

4. **Frontend design scope:** The spec says "functional over pretty" — minimal styling OK? Any specific CSS framework preference? Recommend plain CSS, no framework.

5. **Re-index confirmation:** Show a browser `confirm()` dialog or a custom modal on the page? Recommend `confirm()` for simplicity.

6. **Concurrent re-index protection:** Add a lock to prevent simultaneous re-index calls, or accept low risk for prototype? Recommend accept low risk.

7. **Logging verbosity:** Use `logging.INFO` or `logging.DEBUG` for API request/response logging? Recommend `INFO` for key milestones, `DEBUG` for verbose traces.

---

## Implementation Notes for Phase 4

### Structure Plan

**`backend/app/api.py`** (~150–200 lines expected):
- Import FastAPI, initialize app, import query/index functions
- Define request/response Pydantic models for type safety
- `/query` endpoint: call `query_question()`, return structured response
- `/reindex` endpoint: call `reindex()`, count chunks in resulting index, return status
- `GET /` endpoint: serve static `frontend/index.html` using `StaticFiles`
- Startup event: load query engine, handle initialization errors

**`frontend/index.html`** (~300–400 lines expected):
- Basic HTML structure: input, button, output area, citations list
- Inline CSS: flexbox layout, minimal styling
- Vanilla JS: form submission handler, fetch API calls, DOM updates, error display

**`backend/requirements.txt`:**
- Add `fastapi` and `uvicorn[standard]`

### Testing Strategy (for Phase 5 Review)

1. Start server, verify `GET http://localhost:8000` serves HTML
2. Submit a question via frontend, verify `/query` API call succeeds
3. Verify answer and citations display correctly
4. Click "Re-index", verify `/reindex` API call succeeds and status updates
5. Test edge cases: empty question, invalid API key, no papers

---

## Conclusion

Phase 4 is well-scoped and straightforward:

- **Minimal complexity:** Two new API endpoints, one HTML file, simple frontend logic
- **Clear interfaces:** Phase 3 and Phase 2 already provide the functions needed
- **Low risk:** No auth, no deployment, local-only
- **One ambiguity to clarify:** Error response format (structured JSON vs strings)

All code and architecture decisions are in place. Phase 4 is ready for planning and implementation.
