"""Embedding pipeline and persistent vector index for the Scientific Research Agent.

This module owns the full lifecycle of the LlamaIndex VectorStoreIndex:

  1. Build     — ingest paper chunks, embed via OpenAI, create in-memory index.
  2. Persist   — write the index to disk under STORAGE_DIR.
  3. Load      — restore the index from disk without re-embedding.
  4. Orchestrate — `load_or_build_index` for normal startup,
                   `reindex` for forced full rebuild.

Phase 3 (Query Pipeline) calls `load_or_build_index()` and uses the returned
`VectorStoreIndex` directly.

Phase 4 (Web UI) calls `reindex()` via a `POST /reindex` endpoint.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from llama_index.core import (
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.core import Document
from llama_index.embeddings.openai import OpenAIEmbedding

from app.config import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    PAPERS_DIR,
    STORAGE_DIR,
)
from app.ingestion import ingest_papers

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------


def _get_embed_model() -> OpenAIEmbedding:
    """Return a configured OpenAI embedding model instance.

    Validates that ``OPENAI_API_KEY`` is present in the environment before
    constructing the model — fails fast with a clear error rather than an
    obscure API call failure later.

    Returns:
        An ``OpenAIEmbedding`` instance for ``EMBEDDING_MODEL``.

    Raises:
        ValueError: If ``OPENAI_API_KEY`` is not set.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Add it to your .env file or set it as "
            "an environment variable before running the indexer."
        )

    return OpenAIEmbedding(
        model=EMBEDDING_MODEL,
        dimensions=EMBEDDING_DIMENSIONS,
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Index construction
# ---------------------------------------------------------------------------


def build_index(
    chunks: list[Document],
    embed_model: OpenAIEmbedding,
) -> VectorStoreIndex:
    """Create an in-memory VectorStoreIndex from a list of Document chunks.

    Each document's ``.metadata`` (``paper_name``, ``page_number``) is
    preserved verbatim by LlamaIndex through the embedding stage, which is
    critical for citation support in Phase 3.

    Args:
        chunks:     Non-empty list of LlamaIndex Documents from ``ingest_papers()``.
        embed_model: Configured embedding model (use ``_get_embed_model()``).

    Returns:
        A freshly built ``VectorStoreIndex`` (not yet persisted to disk).

    Raises:
        ValueError: If ``chunks`` is empty (no content to index).
        openai.APIError: On any OpenAI API failure during embedding.
    """
    if not chunks:
        raise ValueError(
            "Cannot build index from an empty chunk list. "
            "Ensure papers exist in the papers directory and can be extracted."
        )

    logger.info("Building VectorStoreIndex from %d chunk(s) ...", len(chunks))
    t0 = time.perf_counter()

    index = VectorStoreIndex.from_documents(
        chunks,
        embed_model=embed_model,
        show_progress=True,
    )

    elapsed = time.perf_counter() - t0
    logger.info("Index built in %.1f s.", elapsed)
    return index


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_index(index: VectorStoreIndex, storage_dir: Path) -> None:
    """Write the index to *storage_dir* using LlamaIndex's native storage format.

    Creates *storage_dir* and any parent directories if they do not exist.
    Overwrites any existing files in place.

    Recovery note: if this process is interrupted mid-write, the on-disk
    state may be corrupt.  The safe recovery path is to delete *storage_dir*
    and call ``reindex()``.

    Args:
        index:       The in-memory index to persist.
        storage_dir: Directory path for index storage (maps to ``STORAGE_DIR``).
    """
    storage_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Persisting index to %s ...", storage_dir)
    index.storage_context.persist(persist_dir=str(storage_dir))
    logger.info("Index persisted successfully.")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_index(
    storage_dir: Path,
    embed_model: OpenAIEmbedding,
) -> Optional[VectorStoreIndex]:
    """Load a previously persisted VectorStoreIndex from *storage_dir*.

    Returns ``None`` (rather than raising) if the storage directory is absent,
    empty, or does not contain a recognizable index — this lets
    ``load_or_build_index`` transparently fall back to building.

    Args:
        storage_dir: Directory where the index was persisted.
        embed_model: Embedding model used when the index was built; must match
                     the original model so similarity scores are meaningful.

    Returns:
        The loaded ``VectorStoreIndex``, or ``None`` if no valid index exists.
    """
    if not storage_dir.exists():
        logger.debug("Storage directory absent — no index to load.")
        return None

    logger.info("Loading index from %s ...", storage_dir)
    t0 = time.perf_counter()

    try:
        if not any(storage_dir.iterdir()):
            logger.debug("Storage directory empty — no index to load.")
            return None
        storage_context = StorageContext.from_defaults(
            persist_dir=str(storage_dir)
        )
        index = load_index_from_storage(
            storage_context,
            embed_model=embed_model,
        )
    except Exception as exc:  # noqa: BLE001
        # Corrupt or incompatible index — log and signal caller to rebuild.
        logger.warning(
            "Failed to load index from %s (%s). "
            "Will rebuild from scratch.",
            storage_dir,
            exc,
        )
        return None

    elapsed = time.perf_counter() - t0
    logger.info("Index loaded in %.2f s.", elapsed)
    return index


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------


def load_or_build_index(
    papers_dir: Optional[Path] = None,
    storage_dir: Optional[Path] = None,
) -> VectorStoreIndex:
    """Return a ready-to-query VectorStoreIndex.

    Startup behaviour:
    - If a valid persisted index exists in *storage_dir*, load it (fast path,
      no API calls).
    - Otherwise, ingest all papers from *papers_dir*, embed them via OpenAI,
      build a new index, and persist it for future runs.

    This is the primary entry point for Phase 3 (Query Pipeline).

    Args:
        papers_dir:  Directory containing PDF files. Defaults to ``PAPERS_DIR``.
        storage_dir: Directory for index persistence. Defaults to ``STORAGE_DIR``.

    Returns:
        A ``VectorStoreIndex`` ready for ``index.as_retriever()``.

    Raises:
        ValueError: If ``OPENAI_API_KEY`` is unset or no papers are available.
    """
    if papers_dir is None:
        papers_dir = PAPERS_DIR
    if storage_dir is None:
        storage_dir = STORAGE_DIR

    embed_model = _get_embed_model()

    # Fast path: load existing index from disk.
    index = load_index(storage_dir, embed_model)
    if index is not None:
        return index

    # Slow path: ingest papers → embed → build → persist.
    logger.info("No cached index found — building from papers in %s.", papers_dir)
    chunks = ingest_papers(papers_dir)
    index = build_index(chunks, embed_model)
    persist_index(index, storage_dir)
    return index


def reindex(
    papers_dir: Optional[Path] = None,
    storage_dir: Optional[Path] = None,
) -> VectorStoreIndex:
    """Force a full re-index: delete existing storage and rebuild from scratch.

    Use this when papers have been added, removed, or updated.  The current
    implementation always performs a complete rebuild (no incremental updates).

    Args:
        papers_dir:  Directory containing PDF files. Defaults to ``PAPERS_DIR``.
        storage_dir: Directory for index persistence. Defaults to ``STORAGE_DIR``.

    Returns:
        A freshly built and persisted ``VectorStoreIndex``.

    Raises:
        ValueError: If ``OPENAI_API_KEY`` is unset or no papers are available.
    """
    if papers_dir is None:
        papers_dir = PAPERS_DIR
    if storage_dir is None:
        storage_dir = STORAGE_DIR

    # Wipe existing storage so there is no risk of stale data.
    if storage_dir.exists():
        logger.info("Removing existing index at %s ...", storage_dir)
        shutil.rmtree(storage_dir)

    embed_model = _get_embed_model()
    chunks = ingest_papers(papers_dir)
    index = build_index(chunks, embed_model)
    persist_index(index, storage_dir)
    return index


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Scientific Research Agent — Embedding & Index CLI",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help=(
            "Force a full rebuild: delete existing storage and re-embed all papers. "
            "Use after adding or updating papers."
        ),
    )
    args = parser.parse_args()

    t_start = time.perf_counter()

    if args.reindex:
        logger.info("--reindex flag set: performing full rebuild.")
        idx = reindex()
        action = "rebuilt"
    else:
        logger.info("Loading or building index (default mode).")
        idx = load_or_build_index()
        action = "ready"

    elapsed_total = time.perf_counter() - t_start

    # Summary
    node_count: int = len(idx.docstore.docs)
    print(f"\n{'=' * 60}")
    print(f"Index {action}.")
    print(f"  Nodes (chunks) : {node_count}")
    print(f"  Storage path   : {STORAGE_DIR}")
    print(f"  Total time     : {elapsed_total:.2f} s")
    print(f"{'=' * 60}\n")

    # Spot-check: print metadata from a sample node to confirm preservation.
    if node_count > 0:
        sample_node = next(iter(idx.docstore.docs.values()))
        meta = getattr(sample_node, "metadata", {})
        print("Sample node metadata (verify paper_name + page_number):")
        print(f"  paper_name : {meta.get('paper_name', '<missing>')}")
        print(f"  page_number: {meta.get('page_number', '<missing>')}")
        print()
