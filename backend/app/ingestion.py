"""Document ingestion pipeline for the Scientific Research Agent.

Extracts text from PDFs page-by-page using PyMuPDF, then splits into
512-token chunks with 100-token overlap using LlamaIndex SentenceSplitter.
Each resulting Document carries metadata: paper_name (str) and
page_number (int, 1-indexed, set to the chunk's starting page).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter

from app.config import CHUNK_OVERLAP, CHUNK_SIZE, PAPERS_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def get_pdf_files(papers_dir: Path) -> list[Path]:
    """Return all .pdf files found directly inside *papers_dir*.

    Non-PDF files are silently ignored.  The list is sorted for
    deterministic ordering.
    """
    if not papers_dir.exists():
        logger.warning("Papers directory does not exist: %s", papers_dir)
        return []

    pdfs = sorted(p for p in papers_dir.iterdir() if p.suffix.lower() == ".pdf")
    logger.info("Found %d PDF file(s) in %s", len(pdfs), papers_dir)
    return pdfs


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_text_by_page(pdf_path: Path) -> list[tuple[int, str]]:
    """Extract text from *pdf_path* and return a list of (page_number, text).

    Page numbers are 1-indexed.  Pages with no extractable text are
    included as empty strings (filtered out later if needed).

    Returns an empty list and logs a warning for corrupt or unreadable PDFs.
    """
    pages: list[tuple[int, str]] = []

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not open PDF '%s': %s", pdf_path.name, exc)
        return pages

    try:
        if doc.page_count == 0:
            logger.warning("PDF '%s' has no pages — skipping.", pdf_path.name)
            return pages

        for idx in range(doc.page_count):
            try:
                page = doc.load_page(idx)
                text = page.get_text("text")
                pages.append((idx + 1, text))  # 1-indexed
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to extract page %d from '%s': %s",
                    idx + 1,
                    pdf_path.name,
                    exc,
                )
    finally:
        doc.close()

    return pages


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_paper(
    pages: list[tuple[int, str]],
    paper_name: str,
) -> list[Document]:
    """Split a paper's pages into LlamaIndex Documents with metadata.

    Strategy:
    1. Build a single concatenated text where each page's text is prefixed
       with a sentinel ``[PAGE <n>]`` so the splitter sees the full document.
    2. Run SentenceSplitter on the combined text.
    3. For each resulting chunk, determine the starting page number by
       looking for the most recent ``[PAGE <n>]`` marker at or before the
       chunk's position in the original text.

    Each returned Document has:
      - ``.text``: the chunk text (sentinel lines stripped)
      - ``.metadata``: ``{"paper_name": str, "page_number": int}``

    Empty / whitespace-only chunks are dropped.
    """
    if not pages:
        return []

    # Build combined text with page sentinels and track char offsets
    segments: list[tuple[int, int, str]] = []  # (start_char, page_num, text)
    combined_parts: list[str] = []
    offset = 0

    for page_num, text in pages:
        sentinel = f"[PAGE {page_num}]\n"
        combined_parts.append(sentinel)
        combined_parts.append(text)
        segments.append((offset, page_num, sentinel + text))
        offset += len(sentinel) + len(text)

    combined_text = "".join(combined_parts)

    # Build a mapping: character position -> page number (using sentinel offsets)
    sentinel_positions: list[tuple[int, int]] = []  # (char_offset, page_num)
    cursor = 0
    for page_num, text in pages:
        sentinel = f"[PAGE {page_num}]\n"
        sentinel_positions.append((cursor, page_num))
        cursor += len(sentinel) + len(text)

    def page_at_offset(char_offset: int) -> int:
        """Return the page number active at *char_offset* in the combined text."""
        page = sentinel_positions[0][1]
        for pos, pnum in sentinel_positions:
            if pos <= char_offset:
                page = pnum
            else:
                break
        return page

    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    raw_nodes = splitter.get_nodes_from_documents(
        [Document(text=combined_text)],
        show_progress=False,
    )

    documents: list[Document] = []
    for node in raw_nodes:
        chunk_text: str = node.text  # type: ignore[attr-defined]

        # Strip sentinel lines from the visible chunk text
        cleaned_lines = [
            line
            for line in chunk_text.splitlines()
            if not line.startswith("[PAGE ")
        ]
        cleaned_text = "\n".join(cleaned_lines).strip()

        if not cleaned_text:
            continue  # skip empty chunks

        # Determine starting page: find first sentinel in this chunk
        first_sentinel_page: Optional[int] = None
        for line in chunk_text.splitlines():
            if line.startswith("[PAGE ") and line.endswith("]"):
                try:
                    first_sentinel_page = int(line[6:-1])
                    break
                except ValueError:
                    pass

        # Fall back: find page by character position using node's start_char_idx
        if first_sentinel_page is None:
            start_idx = getattr(node, "start_char_idx", None)
            if start_idx is not None:
                first_sentinel_page = page_at_offset(start_idx)
            else:
                first_sentinel_page = pages[0][0]  # default to first page

        doc = Document(
            text=cleaned_text,
            metadata={
                "paper_name": paper_name,
                "page_number": first_sentinel_page,
            },
        )
        documents.append(doc)

    return documents


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def ingest_papers(papers_dir: Optional[Path] = None) -> list[Document]:
    """Discover, extract, and chunk all PDFs in *papers_dir*.

    Returns a flat list of LlamaIndex Documents ready for embedding.
    Logs a summary: papers processed, total chunks, warnings count.
    """
    if papers_dir is None:
        papers_dir = PAPERS_DIR

    pdf_files = get_pdf_files(papers_dir)
    if not pdf_files:
        logger.warning("No PDF files found — returning empty document list.")
        return []

    all_documents: list[Document] = []
    warning_count = 0
    processed_count = 0

    for pdf_path in pdf_files:
        paper_name = pdf_path.name
        logger.info("Processing '%s' ...", paper_name)

        pages = extract_text_by_page(pdf_path)

        if not pages:
            logger.warning("No pages extracted from '%s' — skipping.", paper_name)
            warning_count += 1
            continue

        # Check that at least one page has non-empty text
        total_text = "".join(text for _, text in pages).strip()
        if not total_text:
            logger.warning("All pages empty in '%s' — skipping.", paper_name)
            warning_count += 1
            continue

        chunks = chunk_paper(pages, paper_name)
        logger.info(
            "  '%s': %d pages -> %d chunks", paper_name, len(pages), len(chunks)
        )
        all_documents.extend(chunks)
        processed_count += 1

    logger.info(
        "Ingestion complete. Processed %d paper(s), generated %d chunk(s), %d warning(s).",
        processed_count,
        len(all_documents),
        warning_count,
    )
    return all_documents


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    logger.info("Starting document ingestion from %s", PAPERS_DIR)
    docs = ingest_papers()

    print(f"\n{'='*60}")
    print(f"Total documents (chunks): {len(docs)}")
    print(f"{'='*60}\n")

    # Per-paper summary
    from collections import defaultdict

    per_paper: dict[str, list[Document]] = defaultdict(list)
    for d in docs:
        per_paper[d.metadata["paper_name"]].append(d)

    print("Chunks per paper:")
    for name, chunks in sorted(per_paper.items()):
        print(f"  {name}: {len(chunks)} chunks")

    # Sample output: first and last chunks
    if docs:
        print(f"\nSample — first chunk:")
        sample = docs[0]
        print(f"  paper_name : {sample.metadata['paper_name']}")
        print(f"  page_number: {sample.metadata['page_number']}")
        print(f"  text[:200] : {sample.text[:200]!r}")

        if len(docs) > 1:
            print(f"\nSample — last chunk:")
            sample = docs[-1]
            print(f"  paper_name : {sample.metadata['paper_name']}")
            print(f"  page_number: {sample.metadata['page_number']}")
            print(f"  text[:200] : {sample.text[:200]!r}")

    # Verify no empty chunks
    empty = [d for d in docs if not d.text.strip()]
    print(f"\nEmpty chunks: {len(empty)} (should be 0)")
