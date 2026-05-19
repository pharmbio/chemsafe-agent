from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


PARENT_CHILD_RAG_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = PARENT_CHILD_RAG_DIR.parent


def _find_repo_root() -> Path:
    """Find the nearest repository root that contains app/config.py."""
    for candidate in PARENT_CHILD_RAG_DIR.parents:
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

DATA_DIR = WORKSPACE_ROOT
MEMORY_DIR = PARENT_CHILD_RAG_DIR / "sop_documents"

SOP_DATA_DIR = DATA_DIR / "SOPs"

SOP_MEMORY_DIR = MEMORY_DIR
CHROMA_PERSIST_PATH = SOP_MEMORY_DIR / "chroma_db"
DOCSTORE_DIR = SOP_MEMORY_DIR / "docstore"

COLLECTION_NAME = "sop_rag"
ID_KEY = "doc_id"

# Unstructured parsing produces parent-level chunks (text/tables/images merged).
PDF_PROCESSING_CONFIG = {
    "strategy": "hi_res",
    "infer_table_structure": True,
    "extract_image_block_types": ["Image"],
    "extract_image_block_to_payload": True,
    "chunking_strategy": "by_title",
    "max_characters": 10000,
    "combine_text_under_n_chars": 2000,
    "new_after_n_chars": 6000,
}

# ParentDocumentRetriever child splitter — embeds small chunks, returns big parents.
CHILD_SPLITTER_CONFIG = {
    "chunk_size": 400,
    "chunk_overlap": 50,
}

LLM_CONFIG = {
    "image_description_model": "gpt-5.1",
    "rag_response_model": "gpt-5.1",
}

RETRIEVAL_CONFIG = {
    "search_type": "similarity",
    "default_score_threshold": 0.3,
    "max_results": 6,
    # Used when search_type is "mmr" (must be present in search_kwargs for MMR).
    "fetch_k": 20,
    "lambda_mult": 0.5,
}


def ensure_directories() -> None:
    for directory in (SOP_DATA_DIR, SOP_MEMORY_DIR, CHROMA_PERSIST_PATH, DOCSTORE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
