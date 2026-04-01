# Product Decisions

Key architectural and product decisions made during development, with reasoning.

## 2026-04-01 — LlamaIndex as RAG framework

**Context:** Needed to choose between custom pipeline vs framework for chunking, indexing, retrieval.
**Decision:** Use LlamaIndex for the full pipeline.
**Reasoning:** Prototype speed matters more than fine-grained control. LlamaIndex handles plumbing (chunking, embedding batching, index persistence, retrieval) out of the box. Can always swap components later.
**Alternatives considered:** Fully custom pipeline with just PyMuPDF + OpenAI + numpy. More control but significantly more code for the same result.

## 2026-04-01 — GPT-4o-mini for generation

**Context:** Choosing between GPT-4o, GPT-4o-mini, or local models for answer generation.
**Decision:** GPT-4o-mini.
**Reasoning:** For grounded Q&A from provided context, GPT-4o-mini is sufficient. Cheap and fast for iteration. Easy to swap to GPT-4o later if answer quality needs improvement.
**Alternatives considered:** GPT-4o (overkill for prototype), local models (setup overhead).

## 2026-04-01 — OpenAI text-embedding-3-small for embeddings

**Context:** Choosing between OpenAI embeddings vs local sentence-transformers.
**Decision:** OpenAI text-embedding-3-small.
**Reasoning:** One API key for everything (embeddings + LLM). No local model downloads or torch dependency. With 10 papers, embedding cost is negligible.
**Alternatives considered:** sentence-transformers/all-mpnet-base-v2 (good quality, but adds dependency complexity).

## 2026-04-01 — Folder-based paper ingestion only

**Context:** Whether to build upload UI or use file system.
**Decision:** Papers are added by dropping PDFs into `papers/` directory. No upload UI.
**Reasoning:** Simpler to build, and for a local prototype, dragging files into a folder is fine. Re-index command picks up new papers.
**Alternatives considered:** Upload button in web UI (unnecessary complexity for prototype).

## 2026-04-01 — Persistent index with re-index option

**Context:** Whether to rebuild index on every startup or persist to disk.
**Decision:** Persist to disk, with explicit re-index command to rebuild.
**Reasoning:** Avoids re-embedding on every restart (saves time and API costs). Re-index command ensures new papers can be picked up on demand.
**Alternatives considered:** In-memory only (simpler but wasteful).

## 2026-04-01 — PyMuPDF for PDF text extraction

**Context:** Choosing a PDF parsing library for Phase 1 ingestion.
**Decision:** PyMuPDF (`fitz`) via `pymupdf==1.24.3`.
**Reasoning:** Fast, reliable text extraction with per-page access. Handles corrupt pages gracefully (per-page try/except). No external binaries required. Well-maintained and pip-installable.
**Alternatives considered:** `pdfminer.six` (slower, more complex API), `pypdf` (weaker text extraction quality on multi-column layouts), `pdfplumber` (good but heavier dependency).

## 2026-04-01 — Single-retrieval query architecture (retrieve once, synthesize directly)

**Context:** LlamaIndex's default `RetrieverQueryEngine.query()` internally calls the retriever and then the synthesizer. This makes it hard to inspect retrieved nodes before synthesis (needed for confidence scoring and logging) without triggering a second retrieval.
**Decision:** Call `retriever.retrieve()` once, inspect the nodes directly (confidence check, logging), then call `response_synthesizer.synthesize()` with the pre-fetched nodes — bypassing the engine's internal retrieve-then-synthesize path.
**Reasoning:** Guarantees exactly one embedding/retrieval API call per query. Allows the empty-retrieval guard and low-confidence check to run before the LLM is invoked, avoiding wasted API spend. Keeps logging deterministic (logged nodes are the same nodes the LLM saw).
**Alternatives considered:** Using `query_engine.query()` and inspecting `response.source_nodes` after the fact (retrieval still happens; no way to short-circuit before LLM call); subclassing `RetrieverQueryEngine` to override internal methods (more invasive, harder to maintain).

## 2026-04-01 — FastAPI as the web layer with same-origin frontend serving

**Context:** Phase 4 needed a minimal HTTP server to expose the query pipeline to a browser UI. Options were a bare stdlib `http.server`, Flask, or FastAPI.
**Decision:** FastAPI, serving `frontend/index.html` from `GET /` via `FileResponse` so the frontend and API share the same origin.
**Reasoning:** Same-origin serving eliminates CORS for the primary use case (browser loads page from the same server it queries). FastAPI's Pydantic validation catches malformed requests before they reach the pipeline. Lifespan context manager surfaces config errors at startup. Auto-threading of plain `def` handlers avoids blocking the event loop without requiring the pipeline to be rewritten as async.
**Alternatives considered:** Flask (no native async or lifespan; would need `threading` manually for non-blocking handlers); bare `http.server` (no routing, no validation, too much boilerplate); serving frontend from a separate dev server (simpler tooling split but adds CORS complexity and an extra process).

## 2026-04-01 — Single-file vanilla HTML/CSS/JS frontend (no build tooling)

**Context:** The project scope is a local research tool; the UI only needs a question input, answer display with citations, and a re-index trigger.
**Decision:** One file (`frontend/index.html`) with inline CSS and inline JS. No framework, no bundler, no npm.
**Reasoning:** Zero build steps — the file is served directly. Browser-native `fetch`, `textContent`, and DOM APIs are sufficient for the interaction model. `escapeHtml` helper prevents XSS without a library. Keeping it in one file makes it trivially reviewable and editable.
**Alternatives considered:** React/Vue (unnecessary overhead for this scope); separate CSS/JS files (marginal organisation gain, extra HTTP round-trips, added setup).

## 2026-04-01 — Page-sentinel chunking strategy

**Context:** SentenceSplitter operates on a flat string, losing page boundary information. Each chunk needs a `page_number` metadata field for citations.
**Decision:** Prepend `[PAGE n]` sentinel lines to each page's text before concatenation, then strip sentinels from the final chunk text while using them to recover the originating page number.
**Reasoning:** Keeps chunking logic in a single pass through the full document (better sentence splitting across page boundaries) while still preserving page attribution per chunk. Fallback to `start_char_idx` covers edge cases where no sentinel appears in a chunk.
**Alternatives considered:** Chunk each page independently (breaks sentences at page boundaries, degrades retrieval quality); post-hoc page lookup via character offset only (fragile without sentinels as anchors).

## 2026-04-01 — Two-flow evaluation architecture (human-in-the-loop + automated metrics)

**Context:** Need to evaluate RAG quality (retrieval relevance, citation correctness, hallucination) but don't have ground-truth data initially. Also need to track metric changes after tuning (chunk size, top-k, model swaps).
**Decision:** Build two complementary flows: (1) human review UI for organically building eval data (chunk relevance, citation correctness) via reviewing real queries; (2) automated eval runner that computes script-based metrics (Precision@K, citation presence) for known data and falls back to LLM judge (GPT-4o-mini) for unknown data, with diff mode to compare runs.
**Reasoning:** Human reviews are slow but high-quality — use them to bootstrap a ground-truth dataset. Script-based metrics are cheap and deterministic once you have ground truth. LLM judge fills the gap for new queries (eval can run even with zero reviews) but is cached to avoid redundant API calls. Diff mode correlates config changes with metric changes, making tuning iterations faster. This hybrid approach balances cost, coverage, and quality.
**Alternatives considered:** Full manual eval (doesn't scale, blocks iteration); pure LLM judge (expensive, non-deterministic, no human oversight); script-only with fixed test set (requires upfront test creation, misses organic edge cases).

## 2026-04-01 — UUID and full config snapshot in every query log

**Context:** Eval flow needs stable query IDs (to track which queries have been reviewed) and config provenance (to understand which parameter set produced a given answer, enabling diff mode).
**Decision:** Generate UUID for each query in `query_question()`, call `get_config_snapshot()` to capture all tuning parameters, include both in the log entry written by `log_query()`.
**Reasoning:** UUID decouples ID from filename (allows renames, makes referencing stable). Full config snapshot per log makes each log self-contained (no need to reconstruct historical config states). Logging UUID and config has zero cost at query time (just dict creation, no API calls) and unlocks two critical eval features: review tracking and config-aware diffing.
**Alternatives considered:** Use filename as ID (fragile if logs are moved/renamed); store config separately (requires external file and timestamp correlation, complicates diff logic); omit config (makes diff mode impossible).

## 2026-04-01 — Exact question string as key in known_relevance.json

**Context:** Aggregating human reviews into a ground-truth dataset. Need to match future queries against reviewed queries to decide script vs LLM judge.
**Decision:** Use the raw question string (no normalization, no fuzzy matching) as the key in `known_relevance.json`.
**Reasoning:** Simple, deterministic, and bug-free. Normalization (lowercasing, stemming, semantic similarity) adds complexity and false-positive risk (slightly different questions might retrieve different chunks, so treating them as the same is misleading). For a local research tool, having multiple entries for paraphrased questions is acceptable — reviewers can re-review if they want, and the eval runner just treats it as a new question.
**Alternatives considered:** Normalize questions (lower/strip/stem) for fuzzy matching (adds complexity, false positives); use embedding similarity to find "close enough" questions (too slow, non-deterministic, overkill for local tool).

## 2026-04-01 — LLM judge caching with prompt hashing

**Context:** Eval runner calls LLM judge for unknown chunks, citation correctness, and hallucination detection. Same prompts appear across eval runs (especially if re-evaluating after config changes that don't affect logs).
**Decision:** Hash each LLM judge prompt (sha256), cache the response in `eval/cache/llm_judge_cache.json`, skip API call if cache hit.
**Reasoning:** Dramatic cost and speed improvement for repeated eval runs. With temperature=0, responses are deterministic, so caching is safe. Prompt hashing (vs storing full prompt as key) keeps cache file compact and collision-free. Cache is per-project (not per-run), so tuning iterations reuse results for unchanged queries.
**Alternatives considered:** No caching (wasteful, slow); cache per eval run (doesn't help tuning iterations); cache by (question, chunk) pair only (misses citation/hallucination prompts, which differ in structure).

## 2026-04-01 — Inline _rebuild_known_relevance in API endpoint (no separate script)

**Context:** Need to aggregate `reviews.jsonl` into `known_relevance.json` after each review submission so the eval runner sees fresh data.
**Decision:** Implement `_rebuild_known_relevance()` as an inline helper in `backend/app/api.py` and call it synchronously at the end of `POST /eval/submit-review`.
**Reasoning:** Simplest integration — no separate process, no polling, no stale data. Aggregation is fast (JSONL parse + dict build, typically <100ms even for hundreds of reviews), so blocking the POST response is acceptable. Keeps the build logic co-located with the review submission logic, easier to maintain. If aggregation fails, the review is still saved to `reviews.jsonl`, so no data loss.
**Alternatives considered:** Separate `eval/build_known_relevance.py` CLI script run manually (user friction, stale data risk); background task/queue (overkill for local tool); rebuild on eval runner startup (too late — eval starts before new reviews are aggregated).
