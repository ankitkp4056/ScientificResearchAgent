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

## 2026-04-01 — Page-sentinel chunking strategy

**Context:** SentenceSplitter operates on a flat string, losing page boundary information. Each chunk needs a `page_number` metadata field for citations.
**Decision:** Prepend `[PAGE n]` sentinel lines to each page's text before concatenation, then strip sentinels from the final chunk text while using them to recover the originating page number.
**Reasoning:** Keeps chunking logic in a single pass through the full document (better sentence splitting across page boundaries) while still preserving page attribution per chunk. Fallback to `start_char_idx` covers edge cases where no sentinel appears in a chunk.
**Alternatives considered:** Chunk each page independently (breaks sentences at page boundaries, degrades retrieval quality); post-hoc page lookup via character offset only (fragile without sentinels as anchors).
