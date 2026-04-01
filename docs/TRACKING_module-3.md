# Phase 3 — Query Pipeline Implementation Plan

**Overall Progress:** `100%`

## TLDR

Build the end-to-end query pipeline: user question goes in, top-5 semantically similar chunks are retrieved from the Phase 2 index, GPT-4o-mini generates a grounded answer with inline citations in `[paper_name, page X]` format, and every query is logged as JSON for debugging and future evaluation.

## Exploration Summary

**Existing codebase state:**
- Phase 1 (`backend/app/ingestion.py`) handles PDF extraction and chunking with metadata (`paper_name`, `page_number`).
- Phase 2 (`backend/app/index.py`) provides `load_or_build_index()` returning a `VectorStoreIndex`. Chunks carry full metadata through embedding and storage.
- `backend/app/config.py` already defines `STORAGE_DIR`, `LOGS_DIR`, `PAPERS_DIR`, and embedding constants.
- Current dependencies: `pymupdf`, `llama-index-core`, `llama-index-embeddings-openai`, `openai`, `python-dotenv`.

**Key integration points:**
- Upstream: `index.load_or_build_index()` returns a `VectorStoreIndex`; call `index.as_retriever(similarity_top_k=5)` to get a retriever.
- Retrieved nodes have `.text`, `.metadata` (with `paper_name` and `page_number`), and `.score`.
- Downstream (Phase 4 UI): expects `query_question(question: str) -> dict` returning `{"answer": str, "sources": list}`.

**New dependency needed:** `llama-index-llms-openai` for LlamaIndex OpenAI LLM plugin.

**Edge cases identified:**
- Empty retrieval results -- return "insufficient information" directly without calling LLM.
- Missing metadata on chunks -- cite as `[Unknown Paper, page unknown]` and log a warning.
- OpenAI API failures -- catch exceptions, log error, return user-friendly error message.
- Low-confidence retrieval (top score < 0.3) -- prepend disclaimer but still return best-effort answer.

## Critical Decisions

- **LlamaIndex RetrieverQueryEngine for orchestration** -- avoids rebuilding retrieval + generation plumbing manually; cleaner than a custom pipeline for this prototype.
- **GPT-4o-mini via LlamaIndex LLM abstraction** -- consistent with Phase 2's LlamaIndex embedding abstraction; handles retries and token counting.
- **Metadata-based citations** -- no separate citation extraction model; trust chunk metadata from Phase 1 as the source of truth.
- **Threshold 0.3 for low-confidence** -- tunable later in Phase 5; prepend disclaimer rather than filtering out results.
- **JSON logging to backend/logs/** -- one JSON file per query for easy parsing by Phase 5 evaluation scripts.

## Tasks

- [x] ✅ **Step 1: Update dependencies**
  - [x] ✅ Add `llama-index-llms-openai>=0.1.0` to `backend/requirements.txt`
  - [x] ✅ Install updated dependencies in the virtual environment

- [x] ✅ **Step 2: Add query config constants**
  - [x] ✅ Add LLM model name (`gpt-4o-mini`), `SIMILARITY_TOP_K` (5), and `LOW_CONFIDENCE_THRESHOLD` (0.3) to `backend/app/config.py`

- [x] ✅ **Step 3: Create query logging utility**
  - [x] ✅ Create `backend/app/logging_utils.py` with `log_query()` function
  - [x] ✅ Auto-create `LOGS_DIR` if it does not exist
  - [x] ✅ Write one JSON file per query with fields: `timestamp`, `question`, `retrieved_chunks` (text preview, metadata, score), `answer`, `model`, `top_score`, `processing_time_ms`
  - [x] ✅ Use timestamp-based filenames for uniqueness

- [x] ✅ **Step 4: Build grounding prompt**
  - [x] ✅ Write a system prompt that instructs GPT-4o-mini to answer only from the provided context
  - [x] ✅ Require inline citations in `[paper_name, page X]` format for every claim
  - [x] ✅ Include explicit instruction to respond with "I don't have sufficient information in the provided papers to answer this question." when context is insufficient

- [x] ✅ **Step 5: Implement query pipeline module**
  - [x] ✅ Create `backend/app/query.py`
  - [x] ✅ Implement `initialize_query_engine()` -- load index via `load_or_build_index()`, create retriever with `similarity_top_k=5`, instantiate GPT-4o-mini LLM, assemble `RetrieverQueryEngine` with grounding prompt
  - [x] ✅ Implement `query_question(question, query_engine)` -- call engine, extract answer text and source nodes, format sources list with `paper`, `page`, `score`, `text_preview`
  - [x] ✅ Add citation formatting: build sources list from retrieved node metadata
  - [x] ✅ Add low-confidence handling: check top retrieval score against 0.3 threshold, prepend disclaimer if below
  - [x] ✅ Add empty-retrieval guard: return "insufficient information" message without calling LLM if no chunks retrieved
  - [x] ✅ Wrap LLM call in try/except for API errors; return user-friendly error message on failure
  - [x] ✅ Call `log_query()` after every query (success or failure)

- [x] ✅ **Step 6: Add CLI entry point for testing**
  - [x] ✅ Add `if __name__ == "__main__"` block in `query.py` that accepts a question via command-line argument
  - [x] ✅ Print formatted answer with citations and source details to stdout

- [x] ✅ **Step 7: End-to-end verification**
  - [x] ✅ Test with an in-scope question and verify cited answer is returned
  - [x] ✅ Test with an out-of-scope question and verify "insufficient information" response
  - [x] ✅ Verify JSON logs are created in `backend/logs/` with all expected fields
  - [x] ✅ Verify citation format matches `[paper_name, page X]` pattern
