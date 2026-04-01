# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Added
- Phase 4: Web UI (`backend/app/api.py`, `frontend/index.html`)
  - FastAPI app with lifespan-based startup — query engine loaded once at boot; startup failure logs the error and leaves server reachable (so `/reindex` still works)
  - `GET /` — serves `frontend/index.html` via `FileResponse`; 404 if file missing
  - `POST /query` — accepts `{"question": str}`, returns `{"answer": str, "sources": [...]}` (Pydantic-validated); 503 if engine not initialised, 500 on pipeline error
  - `POST /reindex` — wipes index, rebuilds from scratch, refreshes in-memory engine; 500 on failure
  - Plain `def` endpoints so FastAPI auto-threads synchronous pipeline calls, keeping the event loop unblocked
  - CORS middleware scoped to `localhost` / `127.0.0.1` on port 8000
  - `frontend/index.html` — single-file vanilla HTML/CSS/JS: question input + Ask button (Enter key supported), answer display with inline citations (paper, page, score, text preview), Re-index button with confirmation dialog, loading spinner, error banner; all user content rendered via `textContent` / `escapeHtml` (XSS-safe)
  - `backend/requirements.txt` — added `fastapi>=0.111.0`, `uvicorn[standard]>=0.29.0`

- Phase 3: Query pipeline (`backend/app/query.py`)
  - `initialize_query_engine(papers_dir, storage_dir)` — loads (or builds) the vector index, wires a `RetrieverQueryEngine` with GPT-4o-mini (`temperature=0.0`) and a grounding system prompt that restricts answers to provided context; fails fast with `ValueError` if `OPENAI_API_KEY` is unset
  - `query_question(question, query_engine)` — single-retrieval flow: fetches top-5 chunks, guards against empty retrieval (returns "insufficient information" without calling the LLM), prepends a low-confidence disclaimer when top score < 0.3, synthesizes a cited answer, returns `{"answer": str, "sources": [{"paper", "page", "score", "text_preview"}]}`
  - Inline citation format injected via system prompt: `[paper_name, page X]`
  - LLM API errors caught and returned as user-friendly message; query is still logged
  - CLI entry point: `python -m app.query [question]` — prints answer and numbered source list; interactive prompt if question omitted
- `backend/app/logging_utils.py` — JSON query logging
  - `log_query(...)` — writes one JSON file per query to `LOGS_DIR`; filename `query_<YYYYMMDD_HHMMSS_ffffff>.json`; auto-creates directory; OSError on write never crashes the pipeline
  - `_build_chunk_log_entry(text, metadata, score)` — builds serialisable chunk dict (200-char preview) for inclusion in log payload
  - Log schema: `timestamp`, `question`, `answer`, `model`, `top_score`, `processing_time_ms`, `retrieved_chunks[]`
- `backend/app/config.py` — added `LLM_MODEL = "gpt-4o-mini"`, `SIMILARITY_TOP_K = 5`, `LOW_CONFIDENCE_THRESHOLD = 0.3`
- `backend/requirements.txt` — added `llama-index-llms-openai>=0.1.0`

- Phase 2: Embedding & persistent index pipeline (`backend/app/index.py`)
  - `_get_embed_model()` — validates `OPENAI_API_KEY` at startup, returns `OpenAIEmbedding` (text-embedding-3-small, 1536 dims); fails fast with a clear error rather than an obscure API failure
  - `build_index(chunks, embed_model)` — creates `VectorStoreIndex` from Phase 1 Document chunks; preserves `paper_name` and `page_number` metadata through embedding for citation support in Phase 3
  - `persist_index(index, storage_dir)` — writes index to `backend/storage/` using LlamaIndex native format; auto-creates directory
  - `load_index(storage_dir, embed_model)` — loads from disk; returns `None` (never raises) for absent, empty, or corrupt storage, allowing transparent fallback to rebuild
  - `load_or_build_index()` — unified entry point for Phase 3: fast disk-load on warm start, full build-and-persist on cold start
  - `reindex()` — wipes `backend/storage/` and rebuilds from scratch; intended for `POST /reindex` in Phase 4
  - CLI `__main__` block: `python -m app.index` for default load-or-build; `--reindex` flag for forced full rebuild; prints node count, storage path, elapsed time, and sample metadata
- `backend/app/config.py` — added `load_dotenv()` at module level and `EMBEDDING_MODEL = "text-embedding-3-small"` / `EMBEDDING_DIMENSIONS = 1536` constants
- `backend/requirements.txt` — added `llama-index-embeddings-openai==0.1.11` and `openai>=1.0.0`

- Phase 1: Document ingestion pipeline (`backend/app/ingestion.py`)
  - `get_pdf_files(papers_dir)` — discovers all `.pdf` files in a directory, sorted for deterministic ordering
  - `extract_text_by_page(pdf_path)` — PyMuPDF text extraction, returns `(page_number, text)` tuples, 1-indexed; logs and skips corrupt/unreadable PDFs
  - `chunk_paper(pages, paper_name)` — splits extracted pages into LlamaIndex `Document` objects using `SentenceSplitter` (512-token chunks, 100-token overlap); attaches `paper_name` and `page_number` metadata to each chunk; uses page-sentinel strategy to track chunk origin page
  - `ingest_papers(papers_dir)` — orchestration entry point; returns flat list of `Document` objects ready for embedding; also usable as a CLI script (`python -m app.ingestion`)
- `backend/app/config.py` — central config: `PAPERS_DIR`, `STORAGE_DIR`, `LOGS_DIR`, `CHUNK_SIZE=512`, `CHUNK_OVERLAP=100`; paths derived relative to file location, overridable via `SRA_PAPERS_DIR` env var
- `backend/app/__init__.py` — package init
- `backend/requirements.txt` — `pymupdf==1.24.3`, `llama-index-core==0.10.40`, `python-dotenv==1.0.1`

### Changed
- `backend/app/config.py` — `load_dotenv()` now runs at import time so `OPENAI_API_KEY` is available before any module reads it
