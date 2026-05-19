"""Config for the parent-child child-chunk-size investigation.

Reuses the parent docstore produced by ../parent_child_rag (so PDFs are not
re-parsed and images are not re-described). Each (chunk_size, chunk_overlap)
variant gets its own Chroma collection so multiple variants coexist on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


INVESTIGATION_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = INVESTIGATION_DIR.parent


def _find_repo_root() -> Path:
    for candidate in INVESTIGATION_DIR.parents:
        if (candidate / "app" / "config.py").exists():
            return candidate
    return WORKSPACE_ROOT


REPO_ROOT = _find_repo_root()
ENV_PATH = REPO_ROOT / ".env"

if load_dotenv is not None:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Source of parents: the original pipeline's persisted docstore.
# Parents are invariant to child chunk_size, so we read them as-is.
PARENT_PIPELINE_MEMORY_DIR = WORKSPACE_ROOT / "parent_child_rag" / "sop_documents"
SOURCE_DOCSTORE_DIR = PARENT_PIPELINE_MEMORY_DIR / "docstore"

# Per-variant Chroma stores live under this folder.
MEMORY_DIR = INVESTIGATION_DIR / "sop_documents"
CHROMA_ROOT = MEMORY_DIR / "chroma_db"

ID_KEY = "doc_id"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# Default child sizes to sweep over. Override on the CLI.
DEFAULT_CHUNK_SIZES = [100, 200, 400, 600, 800, 1000, 1200]
# overlap = round(chunk_size * ratio); 0.125 matches the 400/50 baseline.
DEFAULT_OVERLAP_RATIO = 0.125

RETRIEVAL_CONFIG = {
    "search_type": "similarity",
    "default_score_threshold": 0.3,
    "max_results": 6,
    "fetch_k": 20,
    "lambda_mult": 0.5,
}


def default_overlap_for(chunk_size: int) -> int:
    return max(0, int(round(chunk_size * DEFAULT_OVERLAP_RATIO)))


def _embed_slug(embedding_model: str) -> str:
    """Filesystem/collection-safe slug for an embedding model id."""
    return embedding_model.replace("/", "_").replace(":", "_")


def collection_name_for(chunk_size: int, chunk_overlap: int, embedding_model: str) -> str:
    return f"sop_rag_{_embed_slug(embedding_model)}_c{chunk_size}_o{chunk_overlap}"


def chroma_path_for(chunk_size: int, chunk_overlap: int, embedding_model: str) -> Path:
    return CHROMA_ROOT / _embed_slug(embedding_model) / f"c{chunk_size}_o{chunk_overlap}"


def ensure_directories(chunk_size: int, chunk_overlap: int, embedding_model: str) -> None:
    for directory in (
        MEMORY_DIR,
        CHROMA_ROOT,
        chroma_path_for(chunk_size, chunk_overlap, embedding_model),
    ):
        directory.mkdir(parents=True, exist_ok=True)
