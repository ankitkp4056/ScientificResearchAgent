#!/usr/bin/env python3
"""Automated evaluation runner for the Scientific Research Agent.

Usage:
    python eval/evaluate.py                        # run eval
    python eval/evaluate.py --diff run_001.json    # compare against previous run

Metrics:
  - Layer 1: Precision@K (script-based, uses known_relevance.json)
  - Layer 2: LLM judge for unknown chunks
  - Layer 3: Citation presence (script-based)
  - Layer 4: Citation correctness (LLM judge)
  - Layer 5: Hallucination detection (LLM judge)

Results are saved to eval/results/run_<timestamp>.json with full config snapshot
and per-query breakdowns.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add backend to path so we can import config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import get_config_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOGS_DIR = Path(__file__).resolve().parent.parent / "backend" / "logs"
KNOWN_RELEVANCE_PATH = Path(__file__).resolve().parent / "known_relevance.json"
CACHE_DIR = Path(__file__).resolve().parent / "cache"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# LLM judge uses OpenAI with temperature=0 for determinism
LLM_JUDGE_MODEL = "gpt-4o-mini"
LLM_JUDGE_TEMPERATURE = 0.0

# ---------------------------------------------------------------------------
# LLM Judge helpers
# ---------------------------------------------------------------------------


def _get_openai_client():
    """Return an OpenAI client instance."""
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")

    return OpenAI(api_key=api_key)


def _cache_key(prompt: str) -> str:
    """Generate a stable cache key for LLM judge results."""
    import hashlib

    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _load_cache() -> dict[str, Any]:
    """Load cached LLM judge results from cache directory."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "llm_judge_cache.json"

    if cache_file.exists():
        try:
            with cache_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load cache, starting fresh")

    return {}


def _save_cache(cache: dict[str, Any]):
    """Save LLM judge cache to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "llm_judge_cache.json"

    try:
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Failed to save cache: %s", exc)


def _call_llm_judge(prompt: str, cache: dict[str, Any]) -> str:
    """Call OpenAI API with the given prompt, using cache if available.

    Args:
        prompt: The full prompt text.
        cache: LLM judge cache dict (will be updated if cache miss).

    Returns:
        The model's response text.
    """
    cache_k = _cache_key(prompt)
    if cache_k in cache:
        logger.debug("Cache hit for LLM judge call")
        return cache[cache_k]

    client = _get_openai_client()

    try:
        response = client.chat.completions.create(
            model=LLM_JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=LLM_JUDGE_TEMPERATURE,
        )
        result = (response.choices[0].message.content or "").strip()
        cache[cache_k] = result
        return result
    except Exception as exc:
        logger.error("LLM judge API call failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Layer 1 & 2: Precision@K (script + LLM judge)
# ---------------------------------------------------------------------------


def _load_known_relevance() -> dict[str, Any]:
    """Load known_relevance.json."""
    if not KNOWN_RELEVANCE_PATH.exists():
        return {}

    try:
        with KNOWN_RELEVANCE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load known_relevance.json: %s", exc)
        return {}


def _evaluate_chunk_relevance(
    question: str,
    chunk: dict[str, Any],
    known_relevance: dict[str, Any],
    cache: dict[str, Any],
) -> tuple[bool, str]:
    """Evaluate if a chunk is relevant to the question.

    Args:
        question: The user question.
        chunk: Chunk dict with metadata and text_preview.
        known_relevance: Loaded known_relevance.json data.
        cache: LLM judge cache.

    Returns:
        Tuple of (is_relevant: bool, judged_by: "human" | "llm").
    """
    paper = chunk["metadata"].get("paper_name", "")
    page = chunk["metadata"].get("page_number", "")

    # Check known_relevance first (script-based)
    if question in known_relevance:
        relevant_chunks = known_relevance[question].get("relevant_chunks", [])
        irrelevant_chunks = known_relevance[question].get("irrelevant_chunks", [])

        for ref in relevant_chunks:
            if ref["paper"] == paper and str(ref["page"]) == str(page):
                return True, "human"

        for ref in irrelevant_chunks:
            if ref["paper"] == paper and str(ref["page"]) == str(page):
                return False, "human"

    # Unknown chunk — use LLM judge
    prompt = f"""Question: {question}

Chunk: {chunk['text_preview']}

Source: {paper}, page {page}

Is this chunk relevant to answering the question?
Answer YES or NO. One sentence explanation."""

    response = _call_llm_judge(prompt, cache)

    # Parse YES/NO from response
    is_relevant = "YES" in response.upper() and "NO" not in response.upper().split("YES")[0]

    return is_relevant, "llm"


# ---------------------------------------------------------------------------
# Layer 3: Citation presence (script-based)
# ---------------------------------------------------------------------------


def _check_citation_presence(answer: str) -> bool:
    """Check if the answer contains at least one citation in [paper, page X] format.

    Args:
        answer: The generated answer text.

    Returns:
        True if at least one citation is found, False otherwise.
    """
    # Regex to match [paper_name, page X] or [paper_name, X]
    pattern = r"\[([^,]+),\s*(?:page\s*)?(\d+)\]"
    matches = re.findall(pattern, answer, re.IGNORECASE)
    return len(matches) > 0


# ---------------------------------------------------------------------------
# Layer 4: Citation correctness (LLM judge)
# ---------------------------------------------------------------------------


def _evaluate_citation_correctness(
    answer: str,
    retrieved_chunks: list[dict[str, Any]],
    cache: dict[str, Any],
) -> tuple[int, int]:
    """Evaluate citation correctness using LLM judge.

    Args:
        answer: The generated answer with citations.
        retrieved_chunks: List of retrieved chunk dicts.
        cache: LLM judge cache.

    Returns:
        Tuple of (correct_count, total_count).
    """
    # Extract citations from answer
    citation_pattern = r"\[([^,]+),\s*(?:page\s*)?(\d+)\]"
    citations = re.findall(citation_pattern, answer, re.IGNORECASE)

    if not citations:
        return 0, 0

    correct_count = 0
    total_count = len(citations)

    for paper_name, page_num in citations:
        paper_name = paper_name.strip()

        # Find matching chunk
        matching_chunk = None
        for chunk in retrieved_chunks:
            chunk_paper = chunk["metadata"].get("paper_name", "")
            chunk_page = str(chunk["metadata"].get("page_number", ""))

            if paper_name.lower() in chunk_paper.lower() and page_num == chunk_page:
                matching_chunk = chunk
                break

        if not matching_chunk:
            # Citation not found in retrieved chunks — mark as incorrect
            continue

        # Extract claim around citation
        claim = _extract_claim_near_citation(answer, paper_name, page_num)

        # LLM judge: does chunk support claim?
        prompt = f"""Claim from answer: {claim}

Cited chunk: {matching_chunk['text_preview']}

Source: {paper_name}, page {page_num}

Does this chunk support the claim? Answer YES, PARTIALLY, or NO.
One sentence explanation."""

        response = _call_llm_judge(prompt, cache)

        if "YES" in response.upper():
            correct_count += 1

    return correct_count, total_count


def _extract_claim_near_citation(answer: str, paper: str, page: str) -> str:
    """Extract the sentence or clause containing the citation.

    Simple heuristic: find the citation and return surrounding context.

    Args:
        answer: Full answer text.
        paper: Paper name from citation.
        page: Page number from citation.

    Returns:
        String containing the claim near the citation.
    """
    citation_str = f"[{paper}, page {page}]"
    idx = answer.find(citation_str)

    if idx == -1:
        citation_str = f"[{paper}, {page}]"
        idx = answer.find(citation_str)

    if idx == -1:
        return answer[:200]  # Fallback: first 200 chars

    # Extract sentence containing citation (approx)
    start = max(0, idx - 100)
    end = min(len(answer), idx + len(citation_str) + 100)

    return answer[start:end]


# ---------------------------------------------------------------------------
# Layer 5: Hallucination detection (LLM judge)
# ---------------------------------------------------------------------------


def _evaluate_hallucination(
    answer: str,
    retrieved_chunks: list[dict[str, Any]],
    cache: dict[str, Any],
) -> tuple[list[dict[str, Any]], float]:
    """Detect unsupported claims in the answer using LLM judge.

    Args:
        answer: The generated answer.
        retrieved_chunks: List of retrieved chunks.
        cache: LLM judge cache.

    Returns:
        Tuple of (claims_list, support_score).
        claims_list is a list of dicts with "claim" and "verdict" keys.
        support_score is the fraction of supported claims (0.0 to 1.0).
    """
    chunks_text = "\n\n".join([
        f"[{c['metadata'].get('paper_name', '')}, page {c['metadata'].get('page_number', '')}]: {c['text_preview']}"
        for c in retrieved_chunks
    ])

    prompt = f"""Retrieved chunks:
{chunks_text}

Generated answer:
{answer}

List each factual claim in the answer.
For each claim, state whether it is SUPPORTED, PARTIALLY SUPPORTED, or UNSUPPORTED by the retrieved chunks.

Format your response as JSON:
{{
  "claims": [
    {{"claim": "...", "verdict": "SUPPORTED"}},
    {{"claim": "...", "verdict": "UNSUPPORTED"}}
  ]
}}"""

    response = _call_llm_judge(prompt, cache)

    # Parse JSON from response
    try:
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = response

        data = json.loads(json_str)
        claims = data.get("claims", [])

        if not claims:
            return [], 1.0

        supported_count = sum(1 for c in claims if c.get("verdict") == "SUPPORTED")
        support_score = supported_count / len(claims)

        return claims, support_score

    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Failed to parse hallucination detection response: %s", exc)
        return [], 0.0  # Conservative default: treat parse failure as unknown support


# ---------------------------------------------------------------------------
# Main eval runner
# ---------------------------------------------------------------------------


def run_evaluation() -> dict[str, Any]:
    """Run full evaluation on all query logs.

    Returns:
        Dict with config, timestamp, overall metrics, and per_query breakdowns.
    """
    logger.info("Starting evaluation run...")

    config = get_config_snapshot()
    known_relevance = _load_known_relevance()
    cache = _load_cache()

    if not LOGS_DIR.exists():
        logger.error("Logs directory not found: %s", LOGS_DIR)
        return {}

    log_files = sorted(LOGS_DIR.glob("query_*.json"))
    logger.info("Found %d query logs", len(log_files))

    per_query_results = []
    overall_metrics = {
        "precision_at_k": 0.0,
        "precision_at_k_known_only": 0.0,
        "citation_presence": 0.0,
        "citation_correctness": 0.0,
        "hallucination_score": 0.0,
        "queries_evaluated": 0,
        "chunks_judged_by_llm": 0,
        "chunks_judged_by_script": 0,
    }

    total_relevant = 0
    total_chunks = 0
    total_known_relevant = 0
    total_known_chunks = 0
    total_citations_correct = 0
    total_citations = 0
    total_with_citations = 0
    total_support_score = 0.0
    llm_chunk_count = 0
    script_chunk_count = 0

    for log_file in log_files:
        try:
            with log_file.open("r", encoding="utf-8") as f:
                log_data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping malformed log file %s: %s", log_file, exc)
            continue

        question = log_data.get("question", "")
        answer = log_data.get("answer", "")
        retrieved_chunks = log_data.get("retrieved_chunks", [])

        if not question or not retrieved_chunks:
            continue

        # Layer 1 & 2: Precision@K
        chunk_relevance = []
        for chunk in retrieved_chunks:
            is_relevant, judged_by = _evaluate_chunk_relevance(
                question, chunk, known_relevance, cache
            )
            chunk_relevance.append({"relevant": is_relevant, "judged_by": judged_by})

            if is_relevant:
                total_relevant += 1

            total_chunks += 1

            if judged_by == "llm":
                llm_chunk_count += 1
            else:
                script_chunk_count += 1

                if is_relevant:
                    total_known_relevant += 1
                total_known_chunks += 1

        # Layer 3: Citation presence
        has_citations = _check_citation_presence(answer)
        if has_citations:
            total_with_citations += 1

        # Layer 4: Citation correctness
        correct_cit, total_cit = _evaluate_citation_correctness(answer, retrieved_chunks, cache)
        total_citations_correct += correct_cit
        total_citations += total_cit

        # Layer 5: Hallucination
        claims, support_score = _evaluate_hallucination(answer, retrieved_chunks, cache)
        total_support_score += support_score

        per_query_results.append({
            "question": question,
            "retrieved_chunks": [
                {
                    "paper": c["metadata"].get("paper_name", ""),
                    "page": c["metadata"].get("page_number", ""),
                    "relevant": chunk_relevance[idx]["relevant"],
                    "judged_by": chunk_relevance[idx]["judged_by"],
                }
                for idx, c in enumerate(retrieved_chunks)
            ],
            "citations_correct": correct_cit == total_cit if total_cit > 0 else True,
            "unsupported_claims": [c for c in claims if c.get("verdict") == "UNSUPPORTED"],
            "support_score": support_score,
        })

        overall_metrics["queries_evaluated"] += 1

    # Compute overall metrics
    if overall_metrics["queries_evaluated"] > 0:
        overall_metrics["precision_at_k"] = total_relevant / total_chunks if total_chunks > 0 else 0.0
        overall_metrics["precision_at_k_known_only"] = (
            total_known_relevant / total_known_chunks if total_known_chunks > 0 else 0.0
        )
        overall_metrics["citation_presence"] = total_with_citations / overall_metrics["queries_evaluated"]
        overall_metrics["citation_correctness"] = (
            total_citations_correct / total_citations if total_citations > 0 else 1.0
        )
        overall_metrics["hallucination_score"] = 1.0 - (total_support_score / overall_metrics["queries_evaluated"])
        overall_metrics["chunks_judged_by_llm"] = llm_chunk_count
        overall_metrics["chunks_judged_by_script"] = script_chunk_count

    # Save cache
    _save_cache(cache)

    result = {
        "config": config,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall": overall_metrics,
        "per_query": per_query_results,
    }

    # Save result
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_filename = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    result_path = RESULTS_DIR / result_filename

    with result_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info("Evaluation complete. Results saved to %s", result_path)

    return result


# ---------------------------------------------------------------------------
# Diff mode
# ---------------------------------------------------------------------------


def diff_runs(current_result: dict[str, Any], previous_path: Path):
    """Compare current eval run against a previous run and print diff.

    Args:
        current_result: Current run result dict.
        previous_path: Path to previous run JSON file.
    """
    try:
        with previous_path.open("r", encoding="utf-8") as f:
            previous_result = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load previous run %s: %s", previous_path, exc)
        return

    print("\n" + "=" * 70)
    print("DIFF: Current vs Previous")
    print("=" * 70)

    # Config changes
    current_config = current_result.get("config", {})
    previous_config = previous_result.get("config", {})

    config_changes = []
    for key in set(current_config.keys()) | set(previous_config.keys()):
        curr_val = current_config.get(key)
        prev_val = previous_config.get(key)
        if curr_val != prev_val:
            config_changes.append(f"  {key}: {prev_val} → {curr_val}")

    if config_changes:
        print("\nConfig changes:")
        for change in config_changes:
            print(change)
    else:
        print("\nNo config changes")

    # Overall metrics
    current_overall = current_result.get("overall", {})
    previous_overall = previous_result.get("overall", {})

    print("\nOverall metrics:")

    metrics_to_compare = [
        ("precision_at_k", "Precision@K", False),
        ("precision_at_k_known_only", "Precision@K (known only)", False),
        ("citation_presence", "Citation presence", False),
        ("citation_correctness", "Citation correctness", False),
        ("hallucination_score", "Hallucination score", True),  # Lower is better
    ]

    for key, label, lower_is_better in metrics_to_compare:
        curr = current_overall.get(key, 0.0)
        prev = previous_overall.get(key, 0.0)
        delta = curr - prev

        if delta == 0:
            arrow = "—"
        elif (delta > 0 and not lower_is_better) or (delta < 0 and lower_is_better):
            arrow = "↑"
        else:
            arrow = "↓"

        print(f"  {label:30s} {prev:.3f} → {curr:.3f}  ({delta:+.3f}) {arrow}")

    # Per-query changes (summary)
    current_queries = {q["question"]: q for q in current_result.get("per_query", [])}
    previous_queries = {q["question"]: q for q in previous_result.get("per_query", [])}

    print("\nPer-query changes:")
    for question in current_queries:
        if question not in previous_queries:
            print(f"  NEW: {question[:60]}...")
            continue

        curr_q = current_queries[question]
        prev_q = previous_queries[question]

        # Compare precision (fraction of relevant chunks)
        if not curr_q["retrieved_chunks"] or not prev_q["retrieved_chunks"]:
            continue
        curr_prec = sum(1 for c in curr_q["retrieved_chunks"] if c["relevant"]) / len(curr_q["retrieved_chunks"])
        prev_prec = sum(1 for c in prev_q["retrieved_chunks"] if c["relevant"]) / len(prev_q["retrieved_chunks"])

        if abs(curr_prec - prev_prec) > 0.05:
            print(f"  {question[:50]}... — precision {prev_prec:.2f} → {curr_prec:.2f}")

    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Automated eval runner for Scientific Research Agent"
    )
    parser.add_argument(
        "--diff",
        type=str,
        metavar="PREVIOUS_RUN.json",
        help="Compare current run against a previous run (path to previous run JSON)",
    )
    args = parser.parse_args()

    result = run_evaluation()

    if not result:
        logger.error("Evaluation failed")
        sys.exit(1)

    # Print summary
    print("\n" + "=" * 70)
    print("EVALUATION SUMMARY")
    print("=" * 70)
    print(f"Queries evaluated: {result['overall']['queries_evaluated']}")
    print(f"Precision@K: {result['overall']['precision_at_k']:.3f}")
    print(f"Precision@K (known only): {result['overall']['precision_at_k_known_only']:.3f}")
    print(f"Citation presence: {result['overall']['citation_presence']:.3f}")
    print(f"Citation correctness: {result['overall']['citation_correctness']:.3f}")
    print(f"Hallucination score: {result['overall']['hallucination_score']:.3f}")
    print(f"Chunks judged by script: {result['overall']['chunks_judged_by_script']}")
    print(f"Chunks judged by LLM: {result['overall']['chunks_judged_by_llm']}")
    print("=" * 70 + "\n")

    # Diff mode
    if args.diff:
        diff_path = Path(args.diff)
        if not diff_path.is_absolute():
            diff_path = RESULTS_DIR / diff_path

        if not diff_path.exists():
            logger.error("Previous run file not found: %s", diff_path)
            sys.exit(1)

        diff_runs(result, diff_path)


if __name__ == "__main__":
    main()
