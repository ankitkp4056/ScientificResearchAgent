# Feature Implementation Plan: Phase 5 — Evaluation & Tuning

**Overall Progress:** `100%`

## TLDR

Build a two-flow evaluation system: (1) a human review HTML UI that lets reviewers mark chunk relevance and citation correctness on real queries, organically growing an eval dataset, and (2) an automated CLI eval runner that computes Precision@K, citation presence/correctness, and hallucination scores using script checks for known data and an LLM judge for unknown data, with diff mode to compare runs after config changes.

## Exploration Summary

**Existing files to modify:**
- `backend/app/logging_utils.py` — Current `log_query()` writes one JSON file per query to `backend/logs/`. Missing: `id` (UUID), `config` snapshot, `rank` field on chunks. These are blocking prerequisites for both flows.
- `backend/app/query.py` — Calls `log_query()` in a `finally` block. Needs to pass UUID, config dict, and rank-annotated chunks. Also the source of `_format_sources()` and `_build_chunk_log_entry()`.
- `backend/app/config.py` — Module-level constants (CHUNK_SIZE, CHUNK_OVERLAP, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, LLM_MODEL, SIMILARITY_TOP_K, LOW_CONFIDENCE_THRESHOLD). Needs a helper to export all as a dict.
- `backend/app/api.py` — FastAPI app with `/`, `/query`, `/reindex`. Add new eval endpoints here.

**New files to create:**
- `eval/evaluate.py` — Automated eval runner (Flow 2)
- `eval/build_known_relevance.py` — Aggregates `reviews.jsonl` into `known_relevance.json`
- `eval/gold_seed.json` — 3-5 seed questions (just question strings)
- `eval/known_relevance.json` — Auto-built from human reviews (starts empty)
- `eval/reviews.jsonl` — Raw human review data (starts empty)
- `eval/cache/` — LLM judge result cache directory
- `eval/results/` — Eval run output directory
- Human review HTML served from a new endpoint

**Key integration points:**
- Query logs in `backend/logs/query_*.json` are the input for both flows
- `eval/known_relevance.json` is built by Flow 1 and consumed by Flow 2
- LLM judge uses OpenAI API (same key as query pipeline), temperature=0
- Eval runner is CLI-only (`python eval/evaluate.py`), not an API endpoint

**Edge cases identified:**
- Empty known-relevance dataset means all chunks go to LLM judge (expensive) — mitigated by seed questions
- JSONL parse errors in reviews.jsonl — skip malformed lines with warnings
- LLM judge variability — use temperature=0 and cache results per (question, chunk) pair
- Config drift between queries — log full config per query, warn if mixed configs in eval

## Critical Decisions

- Decision 1: UUID via `uuid.uuid4()` in `log_query()` — decouples ID from filename, simple and reliable
- Decision 2: Full config snapshot in every log entry — makes logs self-contained, supports diff mode
- Decision 3: Seed questions are plain questions, not Q&A pairs — run through pipeline normally, then review in UI
- Decision 4: Exact question string as key in `known_relevance.json` — no normalization, avoids bugs
- Decision 5: LLM judge results cached per (question, chunk) pair — makes eval reproducible and cheaper
- Decision 6: Human review UI served via new FastAPI endpoints with inline HTML — keeps it simple, single server
- Decision 7: Eval runner is CLI-only (`python eval/evaluate.py`) — no API endpoint needed
- Decision 8: One query at a time in review UI; reviewed queries stay visible but marked
- Decision 9: No latency metrics in eval — strictly answer quality
- Decision 10: Diff mode uses visual markers (arrows) for improvements/regressions

## Tasks

- [x] **Step 1: Extend structured logging with UUID, config snapshot, and rank**
  - [x] Add `get_config_snapshot() -> dict` helper to `backend/app/config.py` that returns all tuning parameters as a dict
  - [x] Extend `log_query()` in `backend/app/logging_utils.py` to accept and write `id` (UUID) and `config` (dict) fields; add `rank` field to chunk log entries via `_build_chunk_log_entry()`
  - [x] Update `query_question()` in `backend/app/query.py` to generate a UUID, call `get_config_snapshot()`, pass both to `log_query()`, and include rank (index+1) in chunk log entries
  - [x] Verify existing `/query` endpoint still works with the extended logging (no API contract changes)

- [x] **Step 2: Create eval directory structure and seed data**
  - [x] Create `eval/` directory with subdirectories `cache/` and `results/`
  - [x] Create `eval/gold_seed.json` with 3-5 placeholder seed questions (generic research questions that work with any paper set)
  - [x] Create empty `eval/known_relevance.json` (initial `{}`)
  - [x] Create empty `eval/reviews.jsonl` file

- [x] **Step 3: Build the human review API endpoints**
  - [x] Add `GET /eval/review` endpoint to `backend/app/api.py` that serves the review HTML page (inline HTML response)
  - [x] Add `GET /eval/next-query` endpoint that reads query logs from `backend/logs/`, checks `eval/reviews.jsonl` for already-reviewed IDs, and returns the next unreviewed query (or signals "all reviewed")
  - [x] Add `GET /eval/query/{query_id}` endpoint to fetch a specific query log by ID (for re-review)
  - [x] Add `POST /eval/submit-review` endpoint that validates the review payload and appends it to `eval/reviews.jsonl`

- [x] **Step 4: Build the human review HTML UI**
  - [x] Create the review page HTML with the layout from the module spec: question, answer, retrieved chunks with Relevant/Not Relevant buttons, citations with Correct/Incorrect buttons, notes field, Submit/Skip buttons
  - [x] Add JavaScript to fetch the next unreviewed query on page load, render it, and handle button state (toggle relevant/irrelevant, correct/incorrect)
  - [x] Add JavaScript to POST the review payload on Submit, show confirmation, and auto-load the next query
  - [x] Add Skip button behavior (loads next query without saving), and handle "all reviewed" state
  - [x] Style with minimal CSS consistent with existing frontend

- [x] **Step 5: Build the known-relevance aggregation script**
  - [x] Create `eval/build_known_relevance.py` that reads `eval/reviews.jsonl`, aggregates chunk judgments per exact question string, and writes `eval/known_relevance.json`
  - [x] Handle JSONL parse errors gracefully (skip malformed lines, log warnings)
  - [x] Handle re-reviews: if same query_id appears multiple times, use the latest review
  - [x] Wire aggregation to run automatically after each review submission (call from the `/eval/submit-review` endpoint)

- [x] **Step 6: Build the automated eval runner — metric computation**
  - [x] Create `eval/evaluate.py` with CLI argument parsing (`--diff <previous_run.json>` optional)
  - [x] Implement log loading: read all query logs from `backend/logs/`, parse UUID, config, chunks
  - [x] Implement Layer 1 — Precision@K (script-based): for each query with known-relevance data, compute known_hits, known_misses, unknown counts
  - [x] Implement Layer 2 — LLM judge for unknown chunks: prompt OpenAI with question+chunk, parse YES/NO response, cache result per (question, chunk) pair in `eval/cache/`
  - [x] Implement Layer 3 — Citation presence (script-based): regex/parse check that answer contains at least one citation in `[paper_name, page X]` format
  - [x] Implement Layer 4 — Citation correctness (LLM judge): prompt with claim+chunk, parse YES/PARTIALLY/NO, cache result
  - [x] Implement Layer 5 — Hallucination detection (LLM judge): prompt with all chunks+answer, extract claims and SUPPORTED/PARTIALLY SUPPORTED/UNSUPPORTED verdicts, compute support_score

- [x] **Step 7: Build eval run output and diff mode**
  - [x] Implement eval result aggregation: compute overall metrics (precision_at_k, precision_at_k_known_only, citation_presence, citation_correctness, hallucination_score, queries_evaluated, chunks_judged_by_llm, chunks_judged_by_script) and per_query breakdowns
  - [x] Save results to `eval/results/run_<timestamp>.json` with config snapshot and all metrics
  - [x] Implement diff mode (`--diff`): load previous run, compare config changes, compute metric deltas, print formatted summary with arrows for improvements/regressions and per-query change details

- [x] **Step 8: Integration testing and end-to-end verification**
  - [x] Verify extended logging: run a query, confirm log file contains id, config, rank fields
  - [x] Verify review UI flow: open /eval/review, see a query, mark chunks, submit, confirm reviews.jsonl is populated
  - [x] Verify known_relevance build: confirm known_relevance.json is updated after review submission
  - [x] Verify eval runner: run `python eval/evaluate.py` with at least one reviewed query, confirm results file is created with correct metrics structure
  - [x] Verify diff mode: run eval twice (with a config change if possible), confirm diff output shows deltas correctly
