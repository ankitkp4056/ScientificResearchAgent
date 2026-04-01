# Exploration: Phase 1 — Document Ingestion & Processing

**Date:** 2026-04-01  
**Phase:** 1 (Document Ingestion & Processing)  
**Status:** Exploration Complete

---

## Scope Summary

Phase 1 delivers a PDF ingestion and chunking pipeline that converts 10 research papers from `papers/` into metadata-rich text chunks ready for embedding. The pipeline:

- Extracts text from all PDFs in `papers/` using PyMuPDF (fitz), preserving page numbers
- Splits text into 512-token chunks with 100-token overlap using LlamaIndex SentenceSplitter
- Attaches metadata to each chunk: paper filename and page number
- Validates input: skips non-PDFs, logs warnings for empty/corrupt PDFs
- Outputs a list of LlamaIndex `Document` or `TextNode` objects for Phase 2 (embedding)

**Key Deliverables:**
- Python module: `backend/app/ingestion.py` — orchestrates extraction and chunking
- Metadata enrichment ensures citations work end-to-end in later phases
- Testable: run on 10 papers, validate chunk count, spot-check metadata accuracy

---

## Existing Project State

### File Structure (Current)
```
/home/ankitkp4056/Documents/Projects/ankitkp4056/ScientificResearchAgent/
├── papers/                     (10 research papers — all present)
│   ├── 2010-0409.pdf
│   ├── 2016_NRDP_pancreaticcancer.pdf
│   ├── 40814_2019_Article_466.pdf
│   ├── 41467_2021_Article_27765.pdf
│   ├── 41467_2023_Article_36344.pdf
│   ├── bph0171-0849.pdf
│   ├── fonc-12-991850.pdf
│   ├── gcr2_5ap0005.pdf
│   ├── nihms277358.pdf
│   └── nihms98189.pdf
├── docs/
│   ├── DEVELOPMENT_PLAN.md     (full architecture blueprint)
│   ├── MODULE_01_INGESTION.md  (Phase 1 specs)
│   ├── MODULE_02_INDEX.md      (Phase 2 expects chunks with metadata)
│   ├── MODULE_03_QUERY.md      (Phase 3 uses metadata for citations)
│   ├── DECISIONS.md            (key architectural decisions)
│   └── [other module docs]
├── .claude/
│   └── .env                    (OPENAI_API_KEY stored here)
└── [no backend/ directory yet]
```

### Python Environment
- Python 3.12.3 available
- pip3 available at `/usr/bin/pip3`
- No virtual environment yet
- Dependencies not installed (PyMuPDF, llama-index-core verified missing)

### Git State
- Worktree at `/tmp/sra-document-ingestion` created from main branch
- Only initial commit (3d5b4ba) exists
- Worktree is a clean slate ready for Phase 1 development

---

## Dependencies Required

### Python Packages

| Package | Version | Purpose | Source |
|---------|---------|---------|--------|
| `pymupdf` | Latest | PDF text extraction with page-level control | PyPI |
| `llama-index-core` | >=0.10.0 | SentenceSplitter, Document/TextNode objects | PyPI |
| `python-dotenv` | Latest | Load .env for OPENAI_API_KEY (if needed during dev) | PyPI |

**Installation path:** Create `backend/requirements.txt` with pinned versions once dependencies are confirmed.

### External APIs (Configuration Only)
- `OPENAI_API_KEY` from `.claude/.env` — required by Phase 2 (embedding), not Phase 1
- Phase 1 does not call OpenAI; it only produces chunks

### System Requirements
- Python 3.11+
- Disk space for caching chunks in memory during processing (minimal)

---

## Integration Points & Dependencies

### Upstream (Inputs)
- **Papers directory:** `papers/` must exist with PDFs (✓ present, 10 files confirmed)
- **Environment:** No external dependencies; Phase 1 is self-contained

### Downstream (Outputs Used By)
- **Phase 2 (Embedding & Index):**
  - Expects list of LlamaIndex `Document` or `TextNode` objects
  - Each chunk must have `.text` and `.metadata` with keys: `paper_name`, `page_number`
  - Module doc: `docs/MODULE_02_INDEX.md` — confirms metadata schema
  
- **Phase 3 (Query Pipeline):**
  - Uses metadata (paper_name, page_number) for citation formatting
  - Grounding prompt assumes metadata is reliable
  - Module doc: `docs/MODULE_03_QUERY.md` — citation system depends on Phase 1 metadata

- **Phase 4 (Web UI):**
  - Indirectly depends on Phase 1 via Phase 2 & 3
  - No direct interaction

### Data Flow
```
papers/*.pdf
    ↓
[Phase 1: Ingestion] ← NEW CODE
    ↓ (chunks with metadata)
Phase 2: Embedding & Index
    ↓ (persisted vector index)
Phase 3: Query Pipeline
    ↓ (retrieved chunks + citations)
Phase 4: Web UI
```

---

## Key Implementation Decisions

### 1. **Chunk Size: 512 tokens, 100 overlap**
- **Rationale:** Balanced for research paper density (moderate chunk size avoids both over-fragmentation and missing context)
- **Implication:** Actual byte size varies by paper; token count is what matters
- **Risk:** Will need to verify LlamaIndex SentenceSplitter's token counting is accurate

### 2. **Text Extraction Method: `page.get_text()` (default PyMuPDF)**
- **Rationale:** Default extraction is sufficient for text-based research PDFs
- **Out of Scope:** OCR for scanned PDFs (all 10 papers appear to be text-based)
- **Implication:** If a paper is scanned/image-only, it will be skipped with a warning

### 3. **Page Number Tracking: 1-indexed, starting page per chunk**
- **Rationale:** Human-readable (page 1, not page 0); starting page for multi-page chunks ensures consistent citations
- **Implication:** Module doc specifies this; must implement carefully to avoid off-by-one errors

### 4. **Metadata Schema: `{"paper_name": str, "page_number": int}`**
- **Rationale:** Minimal but sufficient; paper_name is filename (e.g., `2016_NRDP_pancreaticcancer.pdf`)
- **Implication:** Downstream phases assume this schema; no section headers or other metadata in Phase 1
- **Future:** Section headers are explicitly out-of-scope but could be added in Phase 5 (tuning)

### 5. **Validation: Skip non-PDFs, log warnings for empty PDFs**
- **Rationale:** Robust error handling for edge cases
- **Implication:** Empty chunks should not appear in output; need to filter or skip
- **Logging:** Summary: "Processed X papers, generated Y chunks, Z warnings"

---

## Edge Cases & Risks

### Risks (High Priority)

1. **Token Counting Accuracy**
   - LlamaIndex SentenceSplitter uses its own tokenizer
   - **Risk:** Actual chunk size may differ from 512 tokens; could impact downstream retrieval
   - **Mitigation:** Test on a real paper, inspect chunk sizes in tokens, compare with LlamaIndex docs

2. **Metadata Mutation During Chunking**
   - If a chunk spans multiple pages, which page number do we use?
   - **Module spec:** "starting page"
   - **Risk:** Ambiguity could lead to incorrect citations
   - **Mitigation:** Confirm with LlamaIndex docs that metadata is preserved correctly; add unit test

3. **Empty/Corrupt PDFs**
   - Some papers might extract zero text (scanned, corrupted, or image-heavy)
   - **Risk:** Silent failure or corrupted index
   - **Mitigation:** Explicitly check for empty text, log warning, skip file

4. **Large PDF Handling**
   - Some papers are 7-8 MB (e.g., `2016_NRDP_pancreaticcancer.pdf` is 7.1 MB)
   - **Risk:** Memory overhead during extraction; could be slow
   - **Mitigation:** Process PDFs sequentially (no parallelization in Phase 1); profile memory usage

5. **Paper Filename as Unique Identifier**
   - If filenames are not unique or are modified, citations break
   - **Risk:** Downstream phases assume filename is stable
   - **Mitigation:** Document this assumption; validate at index time

### Edge Cases (Medium Priority)

1. **Duplicate Papers**
   - If the same paper is added with different filenames, chunks will appear twice in the index
   - **Mitigation:** Not handled in Phase 1; document as a known limitation

2. **Special Characters in Filenames**
   - PDFs with special characters (e.g., non-ASCII) might cause issues
   - **Current papers:** All have ASCII filenames, so low risk
   - **Mitigation:** Use `.name` attribute of Path object; Python 3 handles this well

3. **Overlapping Text at Chunk Boundaries**
   - With 100-token overlap, adjacent chunks will share text
   - **Risk:** Double-counting in retrieval (same text appears in top-k)
   - **Mitigation:** LlamaIndex handles this; verify in Phase 2 tests

---

## Files to Create/Modify

### New Files (Phase 1 Specific)

| File Path | Type | Purpose |
|-----------|------|---------|
| `backend/app/__init__.py` | Python | Package init |
| `backend/app/ingestion.py` | Python | Main ingestion logic (PDF extraction + chunking) |
| `backend/app/config.py` | Python | Configuration (paths, chunk size, etc.) |
| `backend/requirements.txt` | Text | Python dependencies (pymupdf, llama-index-core, etc.) |
| `backend/README.md` | Markdown | Backend setup instructions |

### Directories to Create

| Path | Purpose |
|------|---------|
| `backend/app/` | Application code |
| `backend/storage/` | Persistent index (auto-created by Phase 2, but mentioned in config) |
| `backend/logs/` | Query logs (Phase 3, but directory structure established in config) |

### Documentation

| File | Purpose |
|------|---------|
| `docs/PHASE1_TRACKING.md` | Created during `/create-plan`; tracks tasks and progress |
| `docs/EXPLORE_document-ingestion.md` | This file; exploration findings |

### Modified Files

| File | Change | Reason |
|------|--------|--------|
| `.gitignore` | Add `venv/`, `*.pyc`, `.env`, `backend/storage/`, `backend/logs/` | Standard Python project hygiene |
| `CHANGELOG.md` | Add entry for Phase 1 completion | Track project history |

---

## Testing Strategy (Preview)

Phase 1 testing focuses on:

1. **Unit Level:**
   - Test PDF extraction on one sample PDF
   - Verify page numbers are correct
   - Confirm metadata is attached

2. **Integration Level:**
   - Run ingestion on all 10 papers
   - Count chunks produced
   - Spot-check a few chunks (text content, metadata, page number)

3. **Validation:**
   - Manually open a cited PDF page and verify the chunk text appears there
   - Print sample output in JSON format (for Phase 2 to consume)

**How to Run (Phase 1 Complete):**
```bash
cd backend/
python -m app.ingestion
# Output: summary of chunks, sample chunks with metadata
```

---

## Open Questions / Clarifications Needed

1. **TextNode vs Document:**
   - LlamaIndex offers both; which should Phase 1 output?
   - **Assumption:** Using `Document` objects; confirm in execution

2. **Error Handling Verbosity:**
   - For empty PDFs, should we print to stdout, use logging module, or both?
   - **Assumption:** Use Python logging module with INFO level; log to console

3. **Chunk Filtering:**
   - Should we filter out very short chunks (e.g., < 50 tokens)?
   - **Spec:** No mention of minimum chunk size
   - **Assumption:** Include all chunks, even short ones; tuning in Phase 5

4. **Batch Processing:**
   - Process all papers in one function call, or provide per-paper interface?
   - **Assumption:** Single function that processes all; can be called with optional filter

---

## Summary

**Phase 1 is well-scoped and straightforward.** The module doc provides clear specifications. Key risks are around metadata correctness and edge-case handling. The 10 papers are ready, the Python environment is available, and dependencies are well-known.

**Ready for execution.** All clarifications needed can be resolved during implementation or via the LlamaIndex documentation. No blocking unknowns.

---

## Quick Reference — Files to Create

```
backend/
├── app/
│   ├── __init__.py
│   ├── config.py           (paths, chunk size)
│   └── ingestion.py        (main logic)
├── requirements.txt
└── README.md
```

**Entry Point:** `backend/app/ingestion.py` with function `ingest_papers() -> List[Document]`

**Output Format:** List of LlamaIndex Document objects with text, metadata (paper_name, page_number)

**Validation:** Run on all 10 papers; output chunk count and sample
