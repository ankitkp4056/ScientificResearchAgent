# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Added
- Phase 1: Document ingestion pipeline (`backend/app/ingestion.py`)
  - `get_pdf_files(papers_dir)` — discovers all `.pdf` files in a directory, sorted for deterministic ordering
  - `extract_text_by_page(pdf_path)` — PyMuPDF text extraction, returns `(page_number, text)` tuples, 1-indexed; logs and skips corrupt/unreadable PDFs
  - `chunk_paper(pages, paper_name)` — splits extracted pages into LlamaIndex `Document` objects using `SentenceSplitter` (512-token chunks, 100-token overlap); attaches `paper_name` and `page_number` metadata to each chunk; uses page-sentinel strategy to track chunk origin page
  - `ingest_papers(papers_dir)` — orchestration entry point; returns flat list of `Document` objects ready for embedding; also usable as a CLI script (`python -m app.ingestion`)
- `backend/app/config.py` — central config: `PAPERS_DIR`, `STORAGE_DIR`, `LOGS_DIR`, `CHUNK_SIZE=512`, `CHUNK_OVERLAP=100`; paths derived relative to file location, overridable via `SRA_PAPERS_DIR` env var
- `backend/app/__init__.py` — package init
- `backend/requirements.txt` — `pymupdf==1.24.3`, `llama-index-core==0.10.40`, `python-dotenv==1.0.1`
