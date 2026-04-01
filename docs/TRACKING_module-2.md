# Phase 2: Embedding & Persistent Index — Implementation Plan

**Overall Progress:** `100%`

## TLDR

Build an OpenAI embedding pipeline that converts Phase 1 document chunks into vectors, stores them in a LlamaIndex VectorStoreIndex persisted to disk, supports fast startup loading from disk, and provides a re-index command for full rebuilds.

## Exploration Summary

**Existing codebase state:**
- Phase 1 delivers `ingest_papers()` in `backend/app/ingestion.py`, returning `list[Document]` with metadata `{"paper_name": str, "page_number": int}` per chunk (~512 tokens, 100-token overlap).
- `backend/app/config.py` already defines `STORAGE_DIR = BACKEND_DIR / "storage"` (line 15).
- Current dependencies: `pymupdf==1.24.3`, `llama-index-core==0.10.40`, `python-dotenv==1.0.1`.
- Missing dependency: `llama-index-embeddings-openai` (OpenAI embedding plugin for LlamaIndex).
- `backend/app/index.py` does not exist yet.
- `backend/storage/` directory does not exist yet (auto-created on persist).

**Integration points:**
- Phase 3 (Query Pipeline) will call `load_or_build_index()` to get a `VectorStoreIndex`, then use `index.as_retriever(similarity_top_k=5)`.
- Phase 4 (Web UI) will call `reindex()` via a `POST /reindex` endpoint.
- Metadata (`paper_name`, `page_number`) must be preserved through embedding for citation support in Phase 3.

**Key risks identified:**
- API key misconfiguration: validate `OPENAI_API_KEY` before building.
- Metadata preservation: LlamaIndex preserves metadata by design, but should verify.
- Corrupt index on interrupted persist: document that delete + rebuild is the recovery path.

## Critical Decisions

- **Embedding model:** OpenAI `text-embedding-3-small` (1536 dimensions) -- spec requirement, cost-negligible for 10 papers.
- **Index type:** LlamaIndex in-memory `VectorStoreIndex` with disk persistence -- no external vector DB needed.
- **Storage path:** `backend/storage/` as already defined in `config.py`.
- **No incremental indexing:** Re-index is always a full rebuild (out of scope for Phase 2).
- **Embedding config constants:** Add `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` to `config.py` for clarity rather than hardcoding in index.py.

## Tasks

- [x] :green_square: **Step 1: Update dependencies**
  - [x] :green_square: Add `llama-index-embeddings-openai==0.1.11` and `openai>=1.0.0` to `backend/requirements.txt`
  - [x] :green_square: Installed in venv; `from llama_index.embeddings.openai import OpenAIEmbedding` verified OK

- [x] :green_square: **Step 2: Update config.py with embedding constants**
  - [x] :green_square: Added `EMBEDDING_MODEL = "text-embedding-3-small"` constant
  - [x] :green_square: Added `EMBEDDING_DIMENSIONS = 1536` constant
  - [x] :green_square: Added `load_dotenv()` call at module level so `OPENAI_API_KEY` is loaded from `.env`

- [x] :green_square: **Step 3: Create `backend/app/index.py` -- core indexing module**
  - [x] :green_square: `_get_embed_model()` — validates API key, returns configured `OpenAIEmbedding`
  - [x] :green_square: `build_index(chunks, embed_model)` — creates `VectorStoreIndex` with progress display
  - [x] :green_square: `persist_index(index, storage_dir)` — writes index to disk, auto-creates dir
  - [x] :green_square: `load_index(storage_dir, embed_model)` — returns `None` for absent/corrupt index (safe fallback)
  - [x] :green_square: `load_or_build_index(papers_dir, storage_dir)` — fast-path load, then build+persist on miss
  - [x] :green_square: `reindex(papers_dir, storage_dir)` — `shutil.rmtree` + full rebuild
  - [x] :green_square: Error handling: `ValueError` for missing key, `except Exception` with rebuild fallback for corrupt index
  - [x] :green_square: Logging throughout with `logging.getLogger(__name__)`
  - [x] :green_square: Full type hints on all public functions

- [x] :green_square: **Step 4: Add CLI entry point to `index.py`**
  - [x] :green_square: `if __name__ == "__main__"` block with `argparse`
  - [x] :green_square: `--reindex` flag triggers `reindex()`; default triggers `load_or_build_index()`
  - [x] :green_square: Prints node count, storage path, total elapsed time, and sample metadata

- [x] :green_square: **Step 5: Update .gitignore**
  - [x] :green_square: `backend/storage/` already present in `.gitignore` — no change needed

- [x] :green_square: **Step 6: Verify end-to-end pipeline**
  - [x] :green_square: `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `STORAGE_DIR` constants verified correct
  - [x] :green_square: `_get_embed_model()` raises `ValueError` when `OPENAI_API_KEY` unset — verified
  - [x] :green_square: `load_index()` returns `None` for absent storage dir — verified
  - [x] :green_square: `--reindex` flag present in CLI `--help` output — verified
  - [x] :green_square: Ingestion pipeline produces 571 chunks from 10 papers; metadata preserved — verified
  - Note: Live embedding (build + persist + load-from-disk) requires `OPENAI_API_KEY` to be set in `.env`. Add the key and run `python -m app.index` from `backend/` to complete the full end-to-end test.
