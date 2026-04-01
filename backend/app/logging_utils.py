"""JSON query logging utility for the Scientific Research Agent.

Every query (successful or failed) is written to a dedicated JSON file under
``LOGS_DIR``.  Each file is named with a UTC timestamp for uniqueness and
easy chronological sorting.

Log schema per file
-------------------
{
  "timestamp":          str   — ISO-8601 UTC time the log was written,
  "question":           str   — the original user question,
  "answer":             str   — the generated answer (or error message),
  "model":              str   — LLM model used (e.g. "gpt-4o-mini"),
  "top_score":          float | null — highest retrieval similarity score,
  "processing_time_ms": int   — wall-clock ms from question receipt to answer,
  "retrieved_chunks": [
    {
      "text_preview": str  — first 200 chars of the chunk text,
      "metadata":     dict — raw chunk metadata (paper_name, page_number, …),
      "score":        float — similarity score for this chunk
    },
    …
  ]
}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import LOGS_DIR

logger = logging.getLogger(__name__)

# Maximum characters to include in a chunk text preview.
_TEXT_PREVIEW_LEN: int = 200


def log_query(
    *,
    question: str,
    answer: str,
    model: str,
    top_score: float | None,
    processing_time_ms: int,
    retrieved_chunks: list[dict[str, Any]],
    logs_dir: Path = LOGS_DIR,
) -> Path:
    """Write a structured JSON log entry for one query to *logs_dir*.

    Auto-creates *logs_dir* if it does not exist.  Uses a UTC timestamp in
    the filename to guarantee uniqueness across concurrent processes.

    Args:
        question:           The original user question text.
        answer:             The answer returned to the user (or error message).
        model:              LLM model identifier used for generation.
        top_score:          Highest retrieval similarity score, or ``None``
                            if retrieval returned nothing.
        processing_time_ms: Wall-clock milliseconds from question receipt to
                            answer delivery.
        retrieved_chunks:   List of dicts with keys ``text_preview``,
                            ``metadata``, and ``score`` — built by the caller
                            from raw LlamaIndex ``NodeWithScore`` objects.
        logs_dir:           Directory to write logs into.  Defaults to the
                            project-level ``LOGS_DIR`` from config.

    Returns:
        The ``Path`` of the JSON file that was written.
    """
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(tz=timezone.utc)
    # Filename: query_<YYYYMMDD_HHMMSS_ffffff>.json
    filename = f"query_{now.strftime('%Y%m%d_%H%M%S_%f')}.json"
    log_path = logs_dir / filename

    payload: dict[str, Any] = {
        "timestamp": now.isoformat(),
        "question": question,
        "answer": answer,
        "model": model,
        "top_score": top_score,
        "processing_time_ms": processing_time_ms,
        "retrieved_chunks": retrieved_chunks,
    }

    try:
        log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.debug("Query log written to %s", log_path)
    except OSError as exc:
        # Logging failures must never crash the query pipeline.
        logger.warning("Failed to write query log to %s: %s", log_path, exc)

    return log_path


def _build_chunk_log_entry(
    text: str,
    metadata: dict[str, Any],
    score: float,
) -> dict[str, Any]:
    """Return a serialisable dict representing one retrieved chunk.

    Trims *text* to ``_TEXT_PREVIEW_LEN`` characters for readability.

    Args:
        text:     Full text of the retrieved chunk.
        metadata: Metadata dict from the LlamaIndex node (paper_name, page_number, …).
        score:    Similarity score for this chunk.

    Returns:
        A dict with ``text_preview``, ``metadata``, and ``score``.
    """
    return {
        "text_preview": text[:_TEXT_PREVIEW_LEN],
        "metadata": metadata,
        "score": score,
    }
