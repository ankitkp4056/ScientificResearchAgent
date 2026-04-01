# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Added
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
