"""Configuration constants and paths for the Scientific Research Agent backend."""

import os as _os
from pathlib import Path

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
