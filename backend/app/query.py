"""Query pipeline for the Scientific Research Agent.

This module is the main entry point for answering user questions against the
pre-built vector index of research papers.

Public surface
--------------
  initialize_query_engine() -> QueryEngine
      Load (or build) the vector index and return a configured query engine.

  query_question(question, query_engine) -> dict
      Ask one question; receive a structured dict with ``answer`` and ``sources``.

Return schema of ``query_question``
------------------------------------
{
  "answer":  str  — grounded, cited answer text (or fallback message),
  "sources": [
    {
      "paper":        str   — paper name from chunk metadata,
      "page":         int | str — page number (int when available, else "unknown"),
      "score":        float — retrieval similarity score,
      "text_preview": str   — first 200 chars of the matching chunk
    },
    …
  ]
}

Citation format injected by the LLM
------------------------------------
The grounding prompt instructs the model to place inline citations in the
form ``[paper_name, page X]`` for every factual claim.

Confidence handling
-------------------
- Empty retrieval  → return "insufficient information" message immediately
                     without calling the LLM.
- top_score < 0.3  → prepend a low-confidence disclaimer to the LLM answer.
- LLM API error    → return a user-friendly error message; always log.

Every query (success or failure) is logged as a JSON file to ``LOGS_DIR``
via ``logging_utils.log_query()``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import uuid
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.response_synthesizers import get_response_synthesizer
from llama_index.core.schema import NodeWithScore
from llama_index.llms.openai import OpenAI

from app.config import (
    LOGS_DIR,
    LLM_MODEL,
    LOW_CONFIDENCE_THRESHOLD,
    SIMILARITY_TOP_K,
    get_config_snapshot,
)
from app.index import load_or_build_index
from app.logging_utils import _build_chunk_log_entry, log_query

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Message returned when there is nothing to retrieve.
_INSUFFICIENT_INFO_MSG = (
    "I don't have sufficient information in the provided papers to answer this question."
)

# Low-confidence disclaimer prepended to the answer when top retrieval score
# falls below LOW_CONFIDENCE_THRESHOLD.
_LOW_CONFIDENCE_DISCLAIMER = (
    "[Note: The retrieved passages have low similarity to your question. "
    "The following answer may not be fully reliable.]\n\n"
)

# ---------------------------------------------------------------------------
# Grounding prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a precise scientific research assistant.  You answer questions ONLY
using the context passages provided below — never from prior knowledge.

Rules:
1. Cite every factual claim with an inline citation in the format
   [paper_name, page X].  Use the exact paper name from the metadata.
2. If a piece of information comes from multiple sources, cite each one.
3. If the provided context does not contain enough information to answer the
   question, respond with exactly:
   "I don't have sufficient information in the provided papers to answer this question."
4. Do NOT speculate, infer, or add information beyond what is in the context.
5. Write in clear, concise prose.
"""


# ---------------------------------------------------------------------------
# Engine initialisation
# ---------------------------------------------------------------------------


def initialize_query_engine(
    papers_dir=None,
    storage_dir=None,
) -> RetrieverQueryEngine:
    """Load (or build) the vector index and assemble a ``RetrieverQueryEngine``.

    This function is intentionally side-effect free beyond loading the index:
    it creates no files and makes no API calls other than what is required to
    load the LlamaIndex index.

    Args:
        papers_dir:  Override for the PDF directory.  ``None`` uses config default.
        storage_dir: Override for the index storage directory.  ``None`` uses default.

    Returns:
        A ``RetrieverQueryEngine`` wired with GPT-4o-mini and the grounding
        system prompt.

    Raises:
        ValueError: If ``OPENAI_API_KEY`` is unset or the index cannot be built.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Add it to your .env file or set it as "
            "an environment variable before running the query pipeline."
        )

    # Load (or build) the vector index from Phase 2.
    index: VectorStoreIndex = load_or_build_index(
        papers_dir=papers_dir,
        storage_dir=storage_dir,
    )

    # Retriever: fetch top-K most similar chunks.
    retriever = index.as_retriever(similarity_top_k=SIMILARITY_TOP_K)

    # LLM: GPT-4o-mini with the grounding system prompt.
    llm = OpenAI(
        model=LLM_MODEL,
        api_key=api_key,
        system_prompt=_SYSTEM_PROMPT,
        temperature=0.0,  # deterministic for research accuracy
    )

    # Response synthesiser: compact — concatenates context then calls LLM once.
    response_synthesizer = get_response_synthesizer(
        llm=llm,
        response_mode="compact",
    )

    engine = RetrieverQueryEngine(
        retriever=retriever,
        response_synthesizer=response_synthesizer,
    )

    logger.info(
        "Query engine initialised (model=%s, top_k=%d, threshold=%.2f).",
        LLM_MODEL,
        SIMILARITY_TOP_K,
        LOW_CONFIDENCE_THRESHOLD,
    )
    return engine


# ---------------------------------------------------------------------------
# Citation helpers
# ---------------------------------------------------------------------------


def _format_sources(nodes: list[NodeWithScore]) -> list[dict[str, Any]]:
    """Convert a list of ``NodeWithScore`` objects into a serialisable sources list.

    Missing metadata fields are handled gracefully: ``paper_name`` falls back
    to ``"Unknown Paper"``, ``page_number`` falls back to ``"unknown"``, and a
    warning is logged so the issue is surfaced without crashing.

    Args:
        nodes: Raw retrieved nodes from LlamaIndex.

    Returns:
        List of source dicts with ``paper``, ``page``, ``score``, ``text_preview``.
    """
    sources: list[dict[str, Any]] = []

    for node_with_score in nodes:
        node = node_with_score.node
        score: float = node_with_score.score or 0.0
        meta: dict[str, Any] = node.metadata or {}
        text: str = node.get_content() or ""

        paper_name: str = meta.get("paper_name", "")
        page_number = meta.get("page_number", None)

        if not paper_name:
            logger.warning(
                "Chunk is missing 'paper_name' metadata — citing as 'Unknown Paper'."
            )
            paper_name = "Unknown Paper"

        if page_number is None:
            logger.warning(
                "Chunk from '%s' is missing 'page_number' metadata.", paper_name
            )
            page_number = "unknown"

        sources.append(
            {
                "paper": paper_name,
                "page": page_number,
                "score": score,
                "text_preview": text[:200],
            }
        )

    return sources


def _top_score(nodes: list[NodeWithScore]) -> float | None:
    """Return the highest similarity score from *nodes*, or ``None`` if empty."""
    if not nodes:
        return None
    return max((n.score or 0.0) for n in nodes)


# ---------------------------------------------------------------------------
# Core query function
# ---------------------------------------------------------------------------


def query_question(
    question: str,
    query_engine: RetrieverQueryEngine,
) -> dict[str, Any]:
    """Answer *question* using the pre-built query engine.

    Workflow:
    1. Retrieve top-K chunks from the vector index.
    2. Guard against empty retrieval — return fallback without calling LLM.
    3. If top score < ``LOW_CONFIDENCE_THRESHOLD``, note disclaimer.
    4. Call LLM to generate a grounded, cited answer.
    5. Log all details to ``LOGS_DIR``.
    6. Return structured result dict.

    Args:
        question:     The user's natural-language question (non-empty string).
        query_engine: Engine returned by ``initialize_query_engine()``.

    Returns:
        Dict with keys ``"answer"`` (str) and ``"sources"`` (list of dicts).
    """
    if not question or not question.strip():
        return {"answer": "Please provide a non-empty question.", "sources": []}

    t_start = time.perf_counter()
    query_id = str(uuid.uuid4())
    config_snapshot = get_config_snapshot()
    answer: str = _INSUFFICIENT_INFO_MSG
    nodes: list[NodeWithScore] = []
    sources: list[dict[str, Any]] = []
    top_scr: float | None = None

    try:
        # Step 1: Retrieve matching chunks (single retrieval call).
        nodes = query_engine.retriever.retrieve(question)
        top_scr = _top_score(nodes)

        # Step 2: Guard — empty retrieval.
        if not nodes:
            logger.info("No chunks retrieved for question — returning fallback.")
            # answer stays as _INSUFFICIENT_INFO_MSG; sources stays empty
        else:
            # Step 3: Low-confidence check.
            low_confidence = (top_scr is not None) and (top_scr < LOW_CONFIDENCE_THRESHOLD)

            # Step 4: Synthesize answer using already-retrieved nodes (no second retrieval).
            response = query_engine._response_synthesizer.synthesize(question, nodes=nodes)
            answer = str(response).strip()

            if not answer:
                answer = _INSUFFICIENT_INFO_MSG

            if low_confidence:
                logger.info(
                    "Low retrieval confidence (top_score=%.3f < %.2f) — prepending disclaimer.",
                    top_scr,
                    LOW_CONFIDENCE_THRESHOLD,
                )
                answer = _LOW_CONFIDENCE_DISCLAIMER + answer

            # Step 5a: Build structured sources from retrieved nodes.
            raw_nodes = getattr(response, "source_nodes", None)
            response_nodes: list[NodeWithScore] = raw_nodes if raw_nodes is not None else nodes
            sources = _format_sources(response_nodes)

    except Exception as exc:  # noqa: BLE001
        # Any API or unexpected failure: log, return user-friendly message.
        logger.exception("Query pipeline error for question %r: %s", question, exc)
        answer = "Unable to generate answer at this time. Please try again."
        sources = []

    finally:
        processing_time_ms = int((time.perf_counter() - t_start) * 1000)

        # Build chunk log entries from whatever nodes we managed to retrieve.
        # Include rank (1-based index, 1 = highest score).
        chunk_logs = [
            _build_chunk_log_entry(
                text=n.node.get_content() or "",
                metadata=n.node.metadata or {},
                score=n.score or 0.0,
                rank=idx + 1,
            )
            for idx, n in enumerate(nodes)
        ]

        # Step 5b: Log the query regardless of success or failure.
        log_query(
            query_id=query_id,
            question=question,
            answer=answer,
            model=LLM_MODEL,
            top_score=top_scr,
            processing_time_ms=processing_time_ms,
            config=config_snapshot,
            retrieved_chunks=chunk_logs,
            logs_dir=LOGS_DIR,
        )

    return {"answer": answer, "sources": sources}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Scientific Research Agent — Query CLI",
    )
    parser.add_argument(
        "question",
        nargs="?",
        help="Question to ask.  If omitted, an interactive prompt is shown.",
    )
    args = parser.parse_args()

    # Get question from argument or interactive prompt.
    question_text: str = args.question or input("Enter your question: ").strip()

    if not question_text:
        print("No question provided. Exiting.")
        sys.exit(1)

    print("\nInitialising query engine …")
    engine = initialize_query_engine()

    print(f"\nAsking: {question_text!r}\n")
    result = query_question(question_text, engine)

    # ---- Print answer ----
    print("=" * 70)
    print("ANSWER")
    print("=" * 70)
    print(result["answer"])

    # ---- Print sources ----
    if result["sources"]:
        print("\n" + "=" * 70)
        print(f"SOURCES  ({len(result['sources'])} retrieved chunk(s))")
        print("=" * 70)
        for i, src in enumerate(result["sources"], start=1):
            print(f"\n[{i}] {src['paper']}, page {src['page']}  (score: {src['score']:.3f})")
            print(f"    Preview: {src['text_preview'][:120]} …")
    else:
        print("\n(No source chunks retrieved.)")

    print()
