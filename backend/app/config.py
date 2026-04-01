"""Configuration constants and paths for the Scientific Research Agent backend."""

import os as _os
from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

# Load .env from the backend directory (or any parent) so that OPENAI_API_KEY
# and other secrets are available as environment variables.
_load_dotenv()

_THIS_FILE = Path(__file__).resolve()

# Project root: backend/app/config.py -> backend/app -> backend -> project root
_PROJECT_ROOT = _THIS_FILE.parent.parent.parent

PAPERS_DIR: Path = Path(_os.environ.get("SRA_PAPERS_DIR", str(_PROJECT_ROOT / "papers")))

# Local storage directories (not committed)
BACKEND_DIR: Path = _THIS_FILE.parent.parent
STORAGE_DIR: Path = BACKEND_DIR / "storage"
LOGS_DIR: Path = BACKEND_DIR / "logs"

# Chunking constants
CHUNK_SIZE: int = 512
CHUNK_OVERLAP: int = 100

# Embedding constants
EMBEDDING_MODEL: str = "text-embedding-3-small"
EMBEDDING_DIMENSIONS: int = 1536

# Query / LLM constants
LLM_MODEL: str = "gpt-4o-mini"
SIMILARITY_TOP_K: int = 5
# Retrieval score below this threshold triggers a low-confidence disclaimer.
LOW_CONFIDENCE_THRESHOLD: float = 0.3


def get_config_snapshot() -> dict:
    """Return a snapshot of all tuning parameters as a dict.

    Used for eval tracking — every query log includes the config that produced
    it, enabling diff mode to correlate metric changes with parameter changes.

    Returns:
        Dict with keys for all tunable configuration constants.
    """
    return {
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "llm_model": LLM_MODEL,
        "similarity_top_k": SIMILARITY_TOP_K,
        "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
    }
