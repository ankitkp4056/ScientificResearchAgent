# Exploration: Phase 3 — Query Pipeline

**Date:** 2026-04-01  
**Phase:** 3 (Query Pipeline)  
**Status:** Exploration Complete

---

## Scope Summary

Phase 3 implements the complete end-to-end query pipeline: user asks a question → system retrieves top-5 semantically similar chunks → LLM generates a cited answer. The phase delivers:

- **Semantic Retrieval:** Use LlamaIndex `VectorStoreIndex.as_retriever(similarity_top_k=5)` to find most relevant chunks with similarity scores
- **Context Construction:** Format retrieved chunks and their metadata into a context string suitable for LLM input
- **LLM Generation:** Call GPT-4o-mini with a grounding prompt that enforces citation formatting and prevents hallucination
- **Citation Formatting:** Extract `paper_name` and `page_number` from chunk metadata and format as `[paper_name, page X]` inline in the answer
- **Query Logging:** Log every query to `backend/logs/` as JSON with: timestamp, question, retrieved chunks (with scores), final answer, and model name
- **Low-Confidence Handling:** When top retrieval score falls below threshold (0.3), prepend a confidence disclaimer while still returning the best-effort answer
- **Graceful Fallback:** When retrieval yields no meaningful results, return "I don't have sufficient information..." message instead of hallucinating

**Key Deliverables:**
- Python module: `backend/app/query.py` — orchestrates retrieval, context building, generation, and citation extraction
- Python module: `backend/app/logging_utils.py` — query logging to JSON in `backend/logs/`
- CLI or script to test the pipeline end-to-end with real questions
- Updated `backend/requirements.txt` with LlamaIndex query engine and OpenAI LLM dependencies
- Query logs demonstrating retrieval scores, citations, and full answer pipeline

---

## Existing Project State

### File Structure (Current — from worktree)

```
/tmp/sra-module-3/
├── backend/
│   ├── app/
│   │   ├── __init__.py              (empty package init)
│   │   ├── config.py                (paths, chunk config, embedding config)
│   │   ├── ingestion.py             (Phase 1: PDF extraction + chunking)
│   │   ├── index.py                 (Phase 2: embedding + persistent index)
│   │   ├── [query.py NOT YET CREATED]
│   │   └── [logging_utils.py NOT YET CREATED]
│   ├── requirements.txt              (contains Phase 1 + Phase 2 deps)
│   ├── storage/                      (Phase 2 output: persisted VectorStoreIndex)
│   ├── logs/                         [auto-created by Phase 3, not yet present]
│   └── venv/                         [not checked into worktree]
├── papers/                           [not present in worktree, but exists in main]
├── docs/
│   ├── MODULE_03_QUERY.md            (Phase 3 specification)
│   ├── MODULE_04_UI.md               (Phase 4 expects query.py interface)
│   ├── DEVELOPMENT_PLAN.md           (overall architecture)
│   ├── DECISIONS.md                  (architectural choices)
│   ├── EXPLORE_module-2.md           (Phase 2 exploration)
│   └── TRACKING_module-2.md          (Phase 2 implementation plan)
└── .env                              [not in worktree; stored in .claude/.env]
```

### Python Environment

- **Python:** 3.11+ (per project requirements)
- **Current dependencies (backend/requirements.txt):**
  - `pymupdf==1.24.3` (Phase 1: PDF extraction)
  - `llama-index-core==0.10.40` (Phase 1 & 2: chunking, indexing)
  - `llama-index-embeddings-openai==0.1.11` (Phase 2: embedding)
  - `openai>=1.0.0` (Phase 2: embedding API)
  - `python-dotenv==1.0.1` (config: .env loading)
- **Missing for Phase 3:**
  - `llama-index-llms-openai` (LlamaIndex OpenAI LLM plugin, for retriever query engine)
  - Possibly others depending on exact retriever engine choice

### Git State

- **Worktree:** `/tmp/sra-module-3` created from main branch for Phase 3 work
- **Status:** Phase 1 (ingestion.py) and Phase 2 (index.py) already committed; Phase 3 code does not exist yet

---

## Phase 2 Output (Input to Phase 3)

### VectorStoreIndex Interface

Phase 2 provides `load_or_build_index()` (in `backend/app/index.py`) which returns a `VectorStoreIndex` object ready for querying. Key properties and methods:

- **Retriever construction:** `index.as_retriever(similarity_top_k=5)` returns a retriever that accepts a query string and returns the top-5 most similar chunks
- **Return format:** Retrieved chunks are `Node` objects with:
  - `.text`: the chunk text (clean, no sentinels)
  - `.metadata`: dict containing `paper_name` (str) and `page_number` (int)
  - `.score` or similarity score accessible via retriever API (LlamaIndex exposes this in response)
- **No re-ranking:** Phase 3 spec out of scope for re-ranking; use top-5 as-is

### VectorStoreIndex Usage Pattern

From Phase 2 code inspection (`backend/app/index.py`):
- Call `load_or_build_index()` once at startup to get the index
- From that point, use `index.as_retriever()` to create a retriever
- LlamaIndex handles metadata preservation through the full pipeline (embedding → storage → loading → retrieval)

---

## Integration Points

### Upstream Dependencies (Phase 2)

- **Input:** `VectorStoreIndex` object from `backend/app/index.load_or_build_index()`
- **Metadata flow:** Chunks carry `paper_name` and `page_number` metadata from Phase 1 through Phase 2 embedding; these are preserved in the retriever output
- **Config usage:** Phase 3 will use `STORAGE_DIR` and `LOGS_DIR` from `backend/app/config.py`

### Downstream Dependencies (Phase 4)

- **Output:** Query function with signature: `query_question(question: str) -> dict` returning `{"answer": str, "sources": list}`
- **Expected interface for Phase 4 (Web UI):**
  - `/query` endpoint (POST) expects `{"question": "..."}` and returns `{"answer": "...", "sources": [{"paper": str, "page": int, "score": float, "text_preview": str}]}`
  - Phase 4 calls the query function and formats the response for HTTP
- **Re-index consistency:** Phase 4 calls `index.reindex()` from Phase 2; Phase 3 must reload the index after re-index happens (or handle in-memory updates)

---

## Key Design Questions & Ambiguities Resolved

### 1. Retriever Engine Architecture

**Question:** Should Phase 3 use LlamaIndex's high-level `RetrieverQueryEngine` (which orchestrates retrieve + generate) or build a custom orchestration?

**Resolved:** Use LlamaIndex `RetrieverQueryEngine` or equivalent for orchestration:
- LlamaIndex provides `RetrieverQueryEngine` that takes a retriever and an LLM
- It automates: retrieve top-k → format context → call LLM → return response + source nodes
- This is cleaner than building retrieval + context construction + LLM call manually
- Alternative (manual): more control but verbose; not justified for a prototype

**Implication:** Phase 3 needs to instantiate and manage a `RetrieverQueryEngine` with:
- Retriever from phase 2 index
- LLM instance (GPT-4o-mini via OpenAI plugin)
- Custom system prompt for grounding

### 2. LLM Choice & API Integration

**Question:** Should Phase 3 use LlamaIndex's LLM abstraction or call OpenAI API directly?

**Resolved:** Use LlamaIndex LLM abstraction (`llama_index.llms.openai.OpenAI`):
- Consistent with Phase 2's use of LlamaIndex embedding abstraction
- Handles token counting, retries, API key management
- Easily swappable later (e.g., to GPT-4o)
- Requires new dependency: `llama-index-llms-openai`

### 3. Citation Extraction Method

**Question:** How does Phase 3 extract citations from retrieved chunks?

**Resolved:** Use chunk metadata (`paper_name`, `page_number`) from the retriever output:
- Each retrieved chunk carries full metadata (preserved through embedding)
- LlamaIndex `RetrieverQueryEngine` also returns source nodes with metadata
- Citation formatting: post-process to extract `[paper_name, page X]` format from metadata
- No additional citation extraction model needed; metadata is the source of truth

**Implication:** The grounding prompt must instruct the LLM to reference the metadata provided in the context, and Phase 3 must verify that the LLM's output citations align with retrieved source metadata (or at minimum, prepare to log mismatches).

### 4. Low-Confidence Threshold

**Question:** How low is "low confidence" for retrieval? What is the threshold?

**Resolved:** Start with 0.3 (as per MODULE_03_QUERY.md spec):
- If top retrieval score < 0.3, prepend disclaimer: "Note: The retrieved context may not directly address your question."
- Still return the best-effort answer with the top-5 chunks
- Not a hard filter-out; just a confidence signal to the user
- Tunable later based on evaluation results (Phase 5)

**Implication:** Phase 3 must track and expose similarity scores from the retriever for decision-making.

### 5. Query Logging Structure

**Question:** Where and how should query logs be stored?

**Resolved:** JSON logs in `backend/logs/` (as per spec):
- One log per query, timestamp-based filename or append to a log file
- Fields per spec: `timestamp`, `question`, `retrieved_chunks`, `answer`, `model`
- Optionally also log: `top_score` (for confidence analysis), processing time
- Format as JSON for easy post-processing (Phase 5 evaluation script will likely parse these)

**Implication:** Phase 3 needs a logging utility (`logging_utils.py`) to serialize queries, retrieved chunks, and answers to JSON.

### 6. Insufficient Information Message

**Question:** What should the exact wording be for the "insufficient information" case?

**Resolved:** Per MODULE_03_QUERY.md spec:
- When LLM detects insufficient context, return: "I don't have sufficient information in the provided papers to answer this question."
- This is enforced via the grounding prompt
- If retrieval returns no results (empty chunk list), also return this message
- Not a code-level filter; the LLM's grounding prompt handles this semantically

**Implication:** The grounding prompt must include explicit instruction about this message.

---

## Critical Decisions

1. **LlamaIndex `RetrieverQueryEngine`** — Use it for orchestration to avoid rebuilding retrieval + generation plumbing.
2. **GPT-4o-mini via LlamaIndex LLM plugin** — Consistent with architecture; per DECISIONS.md, sufficient quality for grounded Q&A.
3. **Metadata-based citations** — No separate citation extraction; trust chunk metadata from Phase 1.
4. **Threshold 0.3 for low-confidence** — Tunable, but explicit in logging and UX.
5. **JSON logging** — For debuggability and Phase 5 evaluation dataset creation.

---

## Dependencies & Package Changes

### New Python Dependencies Required

```
llama-index-llms-openai>=0.1.0          # LlamaIndex OpenAI LLM plugin
```

Existing packages sufficient:
- `openai>=1.0.0` (already in requirements)
- `llama-index-core` (already in requirements)

### Environment Variables (Already Configured)

- `OPENAI_API_KEY` — loaded from `.env` in `config.py` via `python-dotenv`

### Directory Creation

- `backend/logs/` — created on first query, no manual setup needed

---

## Edge Cases & Risks

### 1. Empty Retrieval Results

**Risk:** If retriever returns zero chunks (query is orthogonal to papers), LLM may hallucinate.

**Mitigation:** 
- Grounding prompt explicitly states "If the context is empty or does not contain...".
- Verify retriever returns non-empty list before calling LLM; if empty, return "insufficient information" message directly.

### 2. API Rate Limits or Failures

**Risk:** OpenAI API call for generation fails (rate limit, quota, network error).

**Mitigation:**
- Wrap LLM call in try/except; log the error.
- Return user-friendly error message: "Unable to generate answer at this time. Please try again."
- Do NOT return partial answer if LLM fails; be explicit about the failure.

### 3. Metadata Missing or Malformed

**Risk:** A chunk reaches Phase 3 without proper `paper_name` or `page_number` metadata.

**Mitigation:**
- This should not happen (Phase 1 ensures all chunks have metadata), but Phase 3 should gracefully handle it.
- If metadata is missing, cite as `[Unknown Paper, page unknown]` and log a warning.

### 4. Very Long Context

**Risk:** If top-5 chunks are large, the context string may exceed LLM token limits (~128k for GPT-4o-mini).

**Risk Assessment:** With 512-token chunks and top-5 retrieval, context is roughly 2560 tokens plus prompt overhead, well below limit. Not a concern for Phase 3; if it becomes an issue later, chunking strategies can be tuned (Phase 5 tuning).

### 5. Citation Format Mismatch

**Risk:** LLM's citations in the answer don't match the retrieved chunks' metadata.

**Mitigation:**
- The grounding prompt instructs: "Every claim must include a citation in the format [paper_name, page X]" and provides chunk metadata inline.
- Post-processing in Phase 3 can validate that cited papers/pages are from retrieved chunks (optional validation).
- For Phase 3 MVP, trust the LLM's instruction-following; log mismatches in query logs for Phase 5 analysis.

---

## Testing Strategy

### Phase 3 Testable Outputs

Per MODULE_03_QUERY.md:
1. **CLI or script** that takes a question, returns cited answer + logs
2. **Sample questions:**
   - In-scope question (e.g., "What are the risk factors for pancreatic cancer?") → should retrieve relevant chunks and return cited answer
   - Out-of-scope question (e.g., "What is quantum computing?") → should return "insufficient information" gracefully
   - Edge case (very short question like "cancer") → should still retrieve and answer (semantic search handles this)
3. **Log verification:** Check `backend/logs/` for JSON logs containing retrieved chunks, scores, and final answer
4. **Citation verification:** Manually open cited PDF pages and verify claims exist there

### Entry Point for Testing

- `if __name__ == "__main__"` in `backend/app/query.py` or a standalone test script `backend/test_query.py`
- Accepts command-line question input, prints answer + logs, or runs predefined test suite

---

## File Structure & Modules to Create

```
backend/app/
├── query.py              [NEW] — retriever orchestration, LLM generation, citation extraction
├── logging_utils.py      [NEW] — JSON query logging utilities
├── __init__.py           (no changes needed)
├── config.py             (update LOGS_DIR usage if needed; already exists)
├── ingestion.py          (Phase 1; no changes)
└── index.py              (Phase 2; no changes)

backend/logs/             [auto-created by Phase 3; add to .gitignore if not already]
```

### Expected Interfaces

**`backend/app/query.py` main functions:**
```python
def initialize_query_engine() -> RetrieverQueryEngine:
    """Load index and create query engine with grounding prompt."""
    pass

def query_question(question: str, query_engine: RetrieverQueryEngine) -> dict:
    """Execute query and return {"answer": str, "sources": list}."""
    pass

# CLI entry point for testing
if __name__ == "__main__":
    # Parse args, load engine, query, print results, verify logs
    pass
```

**`backend/app/logging_utils.py` main functions:**
```python
def log_query(question: str, retrieved_chunks: list, answer: str, model: str, scores: list) -> None:
    """Write query log to backend/logs/ as JSON."""
    pass
```

---

## Summary of Ambiguities & Questions

None remaining. All design questions have been resolved based on:
- MODULE_03_QUERY.md specification (authoritative for Phase 3)
- DEVELOPMENT_PLAN.md architecture decisions
- DECISIONS.md prior architectural choices
- Inspection of Phase 1 & 2 code (ingestion.py, index.py)
- Integration requirements from Phase 4 (UI expects specific response format)

**Readiness:** Phase 3 is ready for implementation. No blocking questions or unclear requirements.

---

## Implementation Checklist (for next phase)

- [ ] Update `backend/requirements.txt` with `llama-index-llms-openai`
- [ ] Create `backend/app/logging_utils.py` with query logging
- [ ] Create `backend/app/query.py` with:
  - [ ] `_get_llm_model()` — configure GPT-4o-mini with API key validation
  - [ ] `_build_grounding_prompt()` — system prompt for generation
  - [ ] `initialize_query_engine()` — load index and create `RetrieverQueryEngine`
  - [ ] `query_question()` — main query orchestration
  - [ ] Citation extraction from response + metadata
  - [ ] Low-confidence handling (0.3 threshold)
  - [ ] CLI entry point for testing
- [ ] Test with sample questions
- [ ] Verify logs are created and contain expected fields
- [ ] Spot-check citations against source PDFs

