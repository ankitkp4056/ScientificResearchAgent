# Exploration: Phase 5 — Evaluation & Tuning

**Date:** 2026-04-01  
**Phase:** 5 (Evaluation & Tuning)  
**Status:** Exploration Complete

---

## Scope Summary

Phase 5 implements a two-flow evaluation system to measure and improve system quality:

**Flow 1: Human Review (during normal usage)**
- Minimal local HTML UI served by the backend
- Displays one query at a time with its answer and retrieved chunks
- Allows human reviewers to mark chunks as relevant/not-relevant and citations as correct/incorrect
- Reviews accumulate in `eval/reviews.jsonl`, automatically building an eval dataset (`eval/known_relevance.json`)

**Flow 2: Automated Eval (after parameter changes)**
- Script-based metric collection using structured logs from Phase 3
- Three evaluation layers:
  1. **Retrieval metrics** — Precision@K using script-based checks on known data + LLM judge for unknown chunks
  2. **Citation metrics** — Citation presence (script) + citation correctness (LLM judge)
  3. **Hallucination detection** — LLM judge analyzing claim support
- Diff mode comparing eval runs to track quality impact of config changes
- Saves results to `eval/results/run_<timestamp>.json`

**Supporting infrastructure:**
- Structured logging of all queries with full config snapshots (already partially in place from Phase 3)
- Seed questions (`eval/gold_seed.json`) to bootstrap the eval dataset
- Known-relevance registry (`eval/known_relevance.json`) that grows organically from human reviews

**Key insight:** Flow 1 feeds Flow 2 — every human review adds data for script-based checks, reducing future LLM judge calls.

---

## Existing Project State

### Directory Structure (Current — from worktree at `/tmp/sra-module-5-eval`)

```
/tmp/sra-module-5-eval/
├── backend/
│   ├── app/
│   │   ├── __init__.py              (empty package init)
│   │   ├── config.py                (paths, embedding/chunking constants)
│   │   ├── ingestion.py             (Phase 1: PDF extraction + chunking)
│   │   ├── index.py                 (Phase 2: embedding + persistent index)
│   │   ├── query.py                 (Phase 3: query pipeline + logging)
│   │   ├── logging_utils.py         (Phase 3: JSON query logging)
│   │   ├── api.py                   (Phase 4: FastAPI web server)
│   │   └── [eval modules NOT YET CREATED]
│   ├── requirements.txt              (all Phase 1-4 dependencies)
│   ├── storage/                      [auto-created by Phase 2, not in repo]
│   ├── logs/                         [auto-created by Phase 3: query logs, not in repo]
│   └── venv/                         [Python environment, not in repo]
├── frontend/
│   └── index.html                   (Phase 4: main UI for querying)
│   [no eval HTML yet]
├── papers/                           [input PDFs, not in worktree]
├── eval/                             [Phase 5: all eval data, NOT YET CREATED]
│   ├── evaluate.py                  [NOT YET CREATED: main eval runner]
│   ├── gold_seed.json               [NOT YET CREATED: 3-5 manually written seed questions]
│   ├── known_relevance.json         [NOT YET CREATED: accumulated human review data]
│   ├── reviews.jsonl                [NOT YET CREATED: raw human review logs]
│   ├── results/                     [NOT YET CREATED: eval run outputs]
│   └── [human_review_ui endpoint]   [NOT YET CREATED: Flask/FastAPI route + HTML]
├── docs/
│   ├── MODULE_05_EVAL.md            (Phase 5 specification)
│   ├── EXPLORE_module-5-eval.md     (this file)
│   ├── [TRACKING_module-5-eval.md will be created in planning step]
│   └── [previous EXPLORE/TRACKING files from phases 1-4]
├── .env                             [not in worktree; OPENAI_API_KEY needed]
└── .git/
```

### Current Python Dependencies (`backend/requirements.txt`)

```
pymupdf==1.24.3                      # Phase 1: PDF text extraction
llama-index-core==0.10.40            # Phase 1-2: chunking, indexing, retrieval
llama-index-embeddings-openai==0.1.11 # Phase 2: OpenAI embeddings
llama-index-llms-openai>=0.1.0       # Phase 3: OpenAI LLM plugin
openai>=1.0.0                        # Phase 2-3: OpenAI API client
python-dotenv==1.0.1                # Config: .env loading
fastapi>=0.111.0                     # Phase 4: web server
uvicorn[standard]>=0.29.0            # Phase 4: ASGI runner
```

**No changes needed to requirements.txt** — all Phase 5 needs is OpenAI API (for LLM judge) and stdlib JSON, no new PyPI packages.

### Git State

- **Worktree:** `/tmp/sra-module-5-eval` created from main branch for Phase 5 work
- **Status:** Phases 1–4 already committed; Phase 5 is greenfield

---

## Phase 3 & 4 Output (Input to Phase 5)

### Query Logging Infrastructure (Phase 3)

**Location:** `backend/logs/query_<YYYYMMDD_HHMMSS_ffffff>.json` (one JSON file per query)

**Current schema (from `logging_utils.py`):**
```python
{
  "timestamp": str,          # ISO-8601 UTC
  "question": str,
  "answer": str,
  "model": str,              # e.g., "gpt-4o-mini"
  "top_score": float | null, # highest retrieval similarity score
  "processing_time_ms": int,
  "retrieved_chunks": [
    {
      "text_preview": str,   # first 200 chars of chunk
      "metadata": {          # raw chunk metadata
        "paper_name": str,
        "page_number": int
      },
      "score": float         # similarity score for this chunk
    },
    …
  ]
}
```

**Missing from current log schema (per MODULE_05_EVAL.md spec):**
- `id`: UUID to identify the query (for linking to reviews)
- `config`: snapshot of tuning parameters (chunk_size, top_k, embedding_model, etc.)
- `retrieved_chunks`: lacks `rank` field; should be added to track position

### Query Pipeline Interface (Phase 3, Phase 4)

From `backend/app/query.py`:
- `initialize_query_engine() -> RetrieverQueryEngine` — loads index, returns stateful engine
- `query_question(question: str, query_engine: RetrieverQueryEngine) -> dict` — runs full query, logs automatically

From `backend/app/api.py` (Phase 4):
- `POST /query` endpoint expects `{"question": str}`, calls `query_question()`, returns answer + sources

**Key fact:** Every call to `query_question()` already logs to `backend/logs/` via `log_query()` call at the end of the function.

### Config System (Phase 3)

From `backend/app/config.py`:
```python
CHUNK_SIZE: int = 512
CHUNK_OVERLAP: int = 100
EMBEDDING_MODEL: str = "text-embedding-3-small"
EMBEDDING_DIMENSIONS: int = 1536
LLM_MODEL: str = "gpt-4o-mini"
SIMILARITY_TOP_K: int = 5
LOW_CONFIDENCE_THRESHOLD: float = 0.3
```

These are module-level constants. To support Phase 5 diff mode, we need to:
1. Capture these in every query log (partially done in logs already for model + top_k)
2. Capture them in every eval run result for comparison

---

## Integration Points & Dependencies

### Upstream (Phase 3 & 4)

**What we depend on:**
- Structured query logs in `backend/logs/` (already created by Phase 3)
- Query interface `query_question(question, engine) -> dict` (already stable)
- Config constants in `backend/app/config.py` (already available)
- FastAPI app in `backend/app/api.py` to add new endpoints for eval UI

**What we need to extend:**
- Query logging schema: add `id` (UUID), `config` snapshot, `rank` field to chunks
- No changes to query pipeline logic itself

### Downstream

**No downstream phases.** Phase 5 is the final phase in the development plan.

---

## Key Design Decisions Needed

### 1. UUID Generation for Query Logs

**Issue:** Module spec says reviews link to queries via `query_id`, but current logs don't have IDs.

**Options:**
- **Option A:** Generate UUID in `log_query()` when writing each log file; include in JSON payload. Simple, deterministic.
- **Option B:** Derive UUID from log filename (timestamp-based). More error-prone if filenames change.
- **Option C:** Use the log filename itself as the ID. Already unique, simpler.

**Recommendation:** Option A — add `id` field to log JSON payload. Decouples ID from filename, allows deterministic UUID generation (e.g., `uuid.uuid4()` or hash-based).

### 2. Config Snapshots in Logs

**Issue:** Current logs capture model and top_k, but not chunk_size, embedding_model, etc.

**Options:**
- **Option A:** Add full `config` dict to every log (as per MODULE spec). Always available.
- **Option B:** Lazy-load config from `backend/app/config.py` when analyzing logs. Requires code to know the paths.

**Recommendation:** Option A — extend `log_query()` to accept optional `config` dict, default to reading from `backend/app/config` if not provided. Ensures every log is fully self-contained.

### 3. Seed Questions Format & Storage

**Issue:** Spec calls for 3-5 manually written questions in `eval/gold_seed.json`, but it's not clear:
- Should they be questions only, or full Q&A pairs?
- Should they be marked as "reviewed" by default?

**Options:**
- **Option A:** Just questions, no answers. When first run through the system, they get auto-logged like normal queries. Human reviews them in Flow 1. After review, they join `known_relevance.json`.
- **Option B:** Full Q&A pairs with manual relevance judgments pre-populated. Imported directly into reviews.

**Recommendation:** Option A — seed questions are just questions. They run through the normal pipeline like any other query, get logged, then reviewed in the UI. Simpler, fewer manual steps, and reviews come from a human.

### 4. Known-Relevance Format & Uniqueness

**Issue:** Spec shows `known_relevance.json` keyed by exact question string. But:
- What if the same question is asked twice but with slight wording differences?
- Should we deduplicate reviews per (question, chunk) pair or per exact question?

**Options:**
- **Option A:** Exact question string is the key. Reviewed chunks accumulate per exact question. Different wordings are separate entries.
- **Option B:** Normalize questions (lowercase, strip punctuation, etc.) before using as key.

**Recommendation:** Option A — exact question string is the key. Simpler, avoids normalization bugs. Users can write slightly different versions of the same logical question if they want separate eval data. If needed, post-process to merge similar questions later.

### 5. Eval Flow — Script-Based vs LLM Verdict

**Issue:** For unknown chunks, module spec calls LLM judge. But:
- How do we handle API failures during eval?
- Should eval runs be deterministic (same result on re-run) or can LLM judge introduce variability?

**Options:**
- **Option A:** LLM judge result is final. Cache results so re-running eval on same set of chunks doesn't re-call API.
- **Option B:** No caching. Each eval run may have different LLM judge verdicts if called separately.

**Recommendation:** Option A — add simple result caching in `evaluate.py`. For each (question, chunk) pair judged by LLM, cache the verdict in a separate file. Re-running eval with same config skips cached LLM calls. Reduces costs, makes eval runs reproducible.

### 6. Human Review UI — Where to Serve?

**Issue:** We need a new HTML page for human review. Where should it live and be served?

**Options:**
- **Option A:** Separate endpoint in FastAPI: `GET /review` serves a new HTML page, `POST /review/submit` accepts review data.
- **Option B:** New separate HTML file (e.g., `frontend/review.html`), loaded via new endpoint.
- **Option C:** Extend existing `frontend/index.html` with a "Review" tab that is hidden by default, enabled when `/eval/reviews.jsonl` has data.

**Recommendation:** Option A — new endpoint + inline HTML served from backend. Simpler than multi-file setup, easier to share context (queries/logs) between API and frontend. Can be in a new file or inline in `api.py`. Keep UI minimal per project principles.

### 7. Eval Run Triggering

**Issue:** Automated eval runs are triggered by "after changing a tuning parameter," but:
- Should users manually run `python eval/evaluate.py`, or should there be an API endpoint?
- Should eval runs be saved automatically or only on demand?

**Options:**
- **Option A:** CLI script only: `python eval/evaluate.py` runs eval, saves to `eval/results/run_<timestamp>.json`.
- **Option B:** API endpoint: `POST /eval/run` triggers eval in background, returns job status.

**Recommendation:** Option A — CLI script for now. Simpler, aligns with project scope (local research tool, not a production service). Users who change config run the script manually.

---

## Existing Files & Their Roles

### Backend Modules

| File | Purpose | Phase | Relevance to Phase 5 |
|------|---------|-------|----------------------|
| `backend/app/config.py` | Paths, chunking/embedding/query constants | 1-3 | **MODIFY** — Extend logging to capture full config in each query log |
| `backend/app/ingestion.py` | PDF text extraction, page-level chunking | 1 | No changes needed |
| `backend/app/index.py` | Embedding, persistent VectorStoreIndex, re-index | 2 | No changes needed |
| `backend/app/query.py` | Query pipeline, retrieval, LLM synthesis, citation extraction, logging | 3 | **MODIFY** — Enhance logging to include UUID + full config + rank field |
| `backend/app/logging_utils.py` | JSON query logging to `backend/logs/` | 3 | **MODIFY** — Extend schema to include `id` and `config` fields |
| `backend/app/api.py` | FastAPI web server, `/query` and `/reindex` endpoints | 4 | **EXTEND** — Add new endpoints for human review UI and eval API |

### Frontend Files

| File | Purpose | Phase | Relevance to Phase 5 |
|------|---------|-------|----------------------|
| `frontend/index.html` | Query input, answer display, re-index button | 4 | No changes needed for Phase 5 (create separate review UI) |

### Documentation Files

| File | Purpose | Phase | Relevance to Phase 5 |
|------|---------|-------|----------------------|
| `docs/MODULE_05_EVAL.md` | Phase 5 specification | 5 | Reference (read-only) |
| `docs/DECISIONS.md` | Architectural choices 1-4 | 1-4 | Reference; Phase 5 decisions will be added here |

### New Directories to Create

```
eval/                           # Phase 5 output directory
├── evaluate.py                # Main eval runner (Flow 2 implementation)
├── gold_seed.json             # 3-5 manually written seed questions
├── known_relevance.json       # Accumulated human review data (auto-built)
├── reviews.jsonl              # Raw human review logs (auto-built)
├── cache/                     # LLM judge result cache (optional)
└── results/                   # Eval run outputs
    ├── run_<timestamp_1>.json
    ├── run_<timestamp_2>.json
    └── …
```

---

## Data Flow Summary

### Flow 1: Human Review

```
Query submitted (via /query)
    ↓
Logged to backend/logs/query_*.json (Phase 3)
    ↓
Human visits /review UI (new Phase 5 endpoint)
    ↓
Reviews fetched from logs, displayed one at a time
    ↓
Human marks chunks relevant/irrelevant, citations correct/incorrect
    ↓
Review saved to eval/reviews.jsonl (Phase 5 new)
    ↓
Script (eval/build_known_relevance.py) aggregates reviews into eval/known_relevance.json
```

### Flow 2: Automated Eval

```
User changes config (e.g., chunk_size=400 → chunk_size=300)
    ↓
python eval/evaluate.py [--diff run_001.json]
    ↓
Reads all logs from backend/logs/
    ↓
For each query:
  - Retrieves chunks from known_relevance.json (script check)
  - Unknown chunks → LLM judge for relevance
  - Citation checks (script + LLM)
  - Hallucination check (LLM)
    ↓
Aggregates metrics into eval/results/run_<timestamp>.json
    ↓
If --diff: compares against previous run, prints summary with deltas
```

---

## Edge Cases & Risks

### Edge Case 1: Empty Known-Relevance Dataset

**Risk:** If no human reviews exist yet, script-based eval has no data to work with. LLM judge is called for everything, making eval expensive.

**Mitigation:** Seed questions (Option 3 above). Write 3-5 questions, run through system, review in UI. Takes ~30 min but bootstraps the dataset. Document this in MODULE_05_EVAL.md as a startup task.

### Edge Case 2: Config Drifts Between Queries

**Risk:** If config changes mid-stream (e.g., top_k changes between queries), eval run may evaluate a mixed set of configs. Diff comparison becomes ambiguous.

**Mitigation:** Log full config with every query. When evaluating, group queries by config. If multiple configs present, warn user and suggest re-running queries with consistent config before eval.

### Edge Case 3: LLM Judge Variability

**Risk:** LLM judge results may differ on repeated calls (temperature > 0 or model non-determinism).

**Mitigation:** Use temperature=0 for all LLM judge prompts. Add result caching (Option 5 above). Document that eval results are "best effort" if LLM judge is involved.

### Edge Case 4: Large Log Directories

**Risk:** After many queries, `backend/logs/` may have hundreds of JSON files. Reading all of them for eval could be slow.

**Mitigation:** Keep log reading lazy and incremental. `evaluate.py` can accept a `--since <timestamp>` flag to only process logs after a certain time. Not in scope for initial Phase 5 but feasible extension.

### Edge Case 5: Review Data Corruption

**Risk:** If a reviewer's edit to `reviews.jsonl` breaks JSONL format (e.g., missing newline), entire eval pipeline fails.

**Mitigation:** Validate JSONL format on load. Log parse errors and skip malformed lines with warnings. Don't crash the pipeline.

### Edge Case 6: Citation Verification Complexity

**Risk:** Module spec says "verify citation correctness" but doesn't define what "correct" means precisely. LLM judge must guess.

**Mitigation:** Eval module should include an example citation correctness prompt and document expected behaviors. Test LLM judge on seed questions to verify it produces sensible verdicts before relying on it.

---

## Ambiguities & Questions for Clarification

### Q1: Should eval include query latency metrics?

**Current state:** Query logs already capture `processing_time_ms`.

**Question:** Should eval report on latency trends across runs (e.g., "chunk_size reduction made queries 5% faster")? Or is eval strictly about answer quality?

**Recommendation:** Out of scope for Phase 5 initial. Latency tracking can be added in a later tuning iteration if needed.

### Q2: Should Flow 1 (human review UI) block on loading all logs?

**Current uncertainty:** When user visits `/review`, should we load all query logs into the UI at once, or lazy-load one query at a time from disk?

**Options:**
- Load all logs once on page load (simple, but slow if thousands of queries)
- Load one log per review from disk (more interactive, but more API calls)

**Recommendation:** Start with one-at-a-time. Endpoint: `GET /eval/next-unreviewed-query` returns the next unreviewed log entry. If there are none, return "All reviewed" message.

### Q3: How should reviewed queries be marked/skipped in Flow 1?

**Current uncertainty:** After reviewing a query, should it be hidden from the list, or kept for re-review?

**Options:**
- Once reviewed, never shown again (even if reviewing again manually)
- Always show, but mark as "reviewed" and allow re-review (overwrite previous review)

**Recommendation:** Mark as reviewed but allow re-review. Update the entry in `reviews.jsonl` if re-reviewed. Simpler than trying to hide queries.

### Q4: Should eval runs include per-query breakdowns or just overall metrics?

**Current state:** Module spec shows `per_query` array in eval result.

**Question:** How deep should per-query data go? Just scores, or full reasoning?

**Recommendation:** Keep it lightweight per Phase 5 spec: query text, retrieved chunks with relevance verdicts, citation scores, unsupported claims list. Full LLM judge reasoning can be added later if needed.

### Q5: Diff mode — what metrics matter most?

**Current uncertainty:** Module spec shows basic delta reporting. Should we highlight regressions differently from improvements?

**Options:**
- Simple numeric delta (0.72 → 0.78)
- Highlight regressions with emoji/color (e.g., "precision_at_k: 0.72 → 0.78 ↑")

**Recommendation:** Simple numeric deltas for initial version. If improvements/regressions are vast, the numbers speak for themselves. Emoji output can be added later if needed.

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| No seed questions in gold_seed.json → eval can't bootstrap | MEDIUM | Document seed question creation task in MODULE_05_EVAL; provide 3-5 examples |
| LLM judge API failures during eval | MEDIUM | Wrap API calls in try/except; return partial results + warning |
| Query logs missing `id` or `config` fields → reviews can't link to queries | HIGH | Modify logging_utils.py + query.py early in Phase 5 implementation |
| Human review UI becomes bottleneck | LOW | Pagination/filtering can be added later; start simple with one-query-at-a-time |
| JSONL parsing fragility | LOW | Validate on load, skip malformed lines, log warnings |

---

## Summary: What Needs to Be Built

### Core New Files

1. **`eval/evaluate.py`** — Main eval runner (Flow 2)
   - Load query logs from `backend/logs/`
   - Read `eval/known_relevance.json` and `eval/gold_seed.json`
   - Compute metrics: precision@k, citation presence/correctness, hallucination
   - LLM judge for unknown chunks; result caching
   - Save results to `eval/results/run_<timestamp>.json`
   - Diff mode: compare two run outputs

2. **`eval/build_known_relevance.py`** — Aggregation script
   - Read `eval/reviews.jsonl`
   - Build and save `eval/known_relevance.json`
   - Run automatically after each human review, or on demand

3. **Human review endpoint + HTML** (in `backend/app/api.py` or new module)
   - `GET /eval/next-unreviewed-query` — fetch next unreviewed log entry
   - `POST /eval/submit-review` — save review to `eval/reviews.jsonl`
   - Inline HTML for review UI (minimal, per project style)

4. **`eval/gold_seed.json`** (template)
   - 3-5 manually written seed questions
   - Format: `[{"question": "..."}, ...]`

### Modifications to Existing Files

1. **`backend/app/logging_utils.py`**
   - Extend `log_query()` to include `id` (UUID) and `config` dict
   - Update log JSON schema

2. **`backend/app/query.py`**
   - Pass full `config` dict to `log_query()` when logging each query
   - Add `rank` field to chunk logs (track retrieval position)

3. **`backend/app/config.py`**
   - Add helper function to export all config constants as a dict

4. **`backend/app/api.py`**
   - Add new endpoints for Flow 1: `/eval/next-unreviewed-query`, `/eval/submit-review`
   - Optional: add `/eval/run` endpoint to trigger eval (or keep it CLI-only)

---

## Next Steps

1. **Create a tracking document** (TRACKING_module-5-eval.md) with specific tasks
2. **Clarify ambiguous questions** (Q1-Q5 above) with stakeholders
3. **Begin implementation** with logging schema changes (high priority, blocking other work)
4. **Build Flow 2 (automated eval)** in parallel with Flow 1 UI
5. **Bootstrap seed questions** once UI is ready
6. **Test end-to-end:** Flow 1 → reviews → known_relevance.json → Flow 2 metrics
