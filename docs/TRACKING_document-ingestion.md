# Phase 1: Document Ingestion & Processing

**Overall Progress:** `100%`

## TLDR

Build a PDF ingestion pipeline that extracts text from 10 research papers in `papers/`, splits the text into 512-token chunks with 100-token overlap using LlamaIndex SentenceSplitter, and attaches metadata (paper filename, page number) to each chunk. Output is a list of LlamaIndex Document objects ready for Phase 2 embedding.

## Exploration Summary

**Current state:** The repo has no `backend/` directory yet. 10 research papers exist in `papers/`. Python 3.12.3 is available but no virtual environment or dependencies are installed. The worktree is at `/tmp/sra-document-ingestion`.

**Dependencies:** `pymupdf` (PDF extraction), `llama-index-core` (SentenceSplitter and Document objects), `python-dotenv` (env loading, used more in Phase 2).

**Downstream consumers:** Phase 2 (embedding) expects `List[Document]` with `.text` and `.metadata` containing `paper_name` (str) and `page_number` (int). Phase 3 (query) uses these metadata fields for citation formatting.

**Key files to create:**
- `backend/app/__init__.py` -- package init
- `backend/app/config.py` -- paths, chunk size constants
- `backend/app/ingestion.py` -- core extraction and chunking logic
- `backend/requirements.txt` -- pinned dependencies

**Edge cases identified:**
- Empty or corrupt PDFs should be skipped with a logged warning
- Page numbers must be 1-indexed (human-readable)
- Multi-page chunks use the starting page number
- Large PDFs (up to 8 MB) processed sequentially to manage memory
- All 10 papers are text-based; OCR is out of scope

## Critical Decisions

- **LlamaIndex Document objects (not TextNode):** Output `Document` objects since they carry metadata natively and are the standard input for LlamaIndex indexing in Phase 2.
- **Page-level extraction then per-page chunking:** Extract text page-by-page from PyMuPDF, then chunk each paper's full text while tracking which page each chunk starts on. This preserves page number accuracy.
- **Filename as paper identifier:** Use the PDF filename (e.g., `2016_NRDP_pancreaticcancer.pdf`) as `paper_name` in metadata. Simple and sufficient for 10 papers.
- **Python logging module for warnings:** Use stdlib `logging` at INFO level for console output. No file logging in Phase 1.
- **No minimum chunk size filter:** Include all chunks regardless of length; tuning deferred to Phase 5.

## Tasks

- [x] ✅ **Step 1: Project scaffolding**
  - [x] ✅ Create directory structure: `backend/app/`, `backend/storage/`, `backend/logs/`
  - [x] ✅ Create `backend/app/__init__.py` (empty package init)
  - [x] ✅ Create `backend/requirements.txt` with `pymupdf`, `llama-index-core`, `python-dotenv`
  - [x] ✅ Update `.gitignore` to include `venv/`, `*.pyc`, `__pycache__/`, `.env`, `backend/storage/`, `backend/logs/`

- [x] ✅ **Step 2: Configuration module (`backend/app/config.py`)**
  - [x] ✅ Define `PAPERS_DIR` path pointing to `papers/` at project root
  - [x] ✅ Define chunking constants: `CHUNK_SIZE = 512`, `CHUNK_OVERLAP = 100`
  - [x] ✅ Define `STORAGE_DIR` and `LOGS_DIR` paths for future phases

- [x] ✅ **Step 3: PDF extraction logic (`backend/app/ingestion.py` -- extraction)**
  - [x] ✅ Implement `extract_text_by_page(pdf_path: Path) -> list[tuple[int, str]]` using PyMuPDF -- returns list of (page_number, text) tuples with 1-indexed page numbers
  - [x] ✅ Add validation: skip non-PDF files, handle corrupt/empty PDFs with try/except and logging
  - [x] ✅ Implement `get_pdf_files(papers_dir: Path) -> list[Path]` to discover all `.pdf` files in the directory

- [x] ✅ **Step 4: Chunking with metadata (`backend/app/ingestion.py` -- chunking)**
  - [x] ✅ Implement `chunk_paper(pages: list[tuple[int, str]], paper_name: str) -> list[Document]` using LlamaIndex SentenceSplitter (512 tokens, 100 overlap)
  - [x] ✅ Attach metadata `{"paper_name": str, "page_number": int}` to each Document, where page_number is the starting page of the chunk
  - [x] ✅ Handle edge case: skip chunks with empty/whitespace-only text

- [x] ✅ **Step 5: Orchestration function (`backend/app/ingestion.py` -- main)**
  - [x] ✅ Implement `ingest_papers(papers_dir: Path | None = None) -> list[Document]` that ties together discovery, extraction, chunking for all papers
  - [x] ✅ Add summary logging: "Processed X papers, generated Y chunks, Z warnings"
  - [x] ✅ Add `if __name__ == "__main__"` block that runs ingestion and prints sample output (chunk count per paper, sample chunk text and metadata)

- [x] ✅ **Step 6: Virtual environment and dependency installation**
  - [x] ✅ Create venv at `backend/venv/` and install dependencies from `requirements.txt`
  - [x] ✅ Verify imports work: `python -c "import fitz; from llama_index.core import Document"`

- [x] ✅ **Step 7: End-to-end validation**
  - [x] ✅ Run `python -m app.ingestion` from `backend/` on all 10 papers
  - [x] ✅ Verify: all 10 papers processed without errors
  - [x] ✅ Verify: chunk count is reasonable (expect hundreds of chunks total) — 571 chunks generated
  - [x] ✅ Verify: spot-check metadata on a few chunks (correct paper_name, valid page_number)
  - [x] ✅ Verify: no empty chunks in output — 0 empty chunks confirmed

## Validation Results

```
Processed 10 paper(s), generated 571 chunk(s), 0 warning(s).

Chunks per paper:
  2010-0409.pdf: 88 chunks
  2016_NRDP_pancreaticcancer.pdf: 106 chunks
  40814_2019_Article_466.pdf: 23 chunks
  41467_2021_Article_27765.pdf: 72 chunks
  41467_2023_Article_36344.pdf: 48 chunks
  bph0171-0849.pdf: 38 chunks
  fonc-12-991850.pdf: 29 chunks
  gcr2_5ap0005.pdf: 28 chunks
  nihms277358.pdf: 58 chunks
  nihms98189.pdf: 81 chunks

Sample first chunk:
  paper_name : 2010-0409.pdf
  page_number: 1
  text[:200] : 'Pancreatic cancer\nSearch date August 2009\n...'

Empty chunks: 0
```
