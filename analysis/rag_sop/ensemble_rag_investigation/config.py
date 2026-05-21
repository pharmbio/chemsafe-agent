"""Config for the ensemble investigation.

Two ensemble modes share the same sparse (BM25) + dense (Chroma) topology;
the dense engine is swappable:

  mode="basic"          → basic_rag (MultiVectorRetriever, text-embedding-3-small)
  mode="parent_child"   → parent_child_rag investigation winner
                          (ParentDocumentRetriever, text-embedding-3-large,
                          child chunk_size=400, overlap=50)

Source databases are *copied* into this folder by `sop_indexer.py` so the
ensemble is self-contained at query time.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


ENSEMBLE_INVESTIGATION_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = ENSEMBLE_INVESTIGATION_DIR.parent


def _find_repo_root() -> Path:
    for candidate in ENSEMBLE_INVESTIGATION_DIR.parents:
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

ID_KEY = "doc_id"
SOP_DATA_DIR = WORKSPACE_ROOT / "SOPs"

# Local, encapsulated store for both ensemble modes.
MEMORY_DIR = ENSEMBLE_INVESTIGATION_DIR / "sop_documents"

# --- Source locations (read-only, used by the copy step) ------------------

# basic_rag sources
BASIC_SOURCE_CHROMA_DIR = WORKSPACE_ROOT / "basic_rag" / "sop_documents" / "chroma_db"
BASIC_SOURCE_DOCSTORE_DIR = WORKSPACE_ROOT / "basic_rag" / "sop_documents" / "docstore"

# parent_child winner: text-embedding-3-large / c400_o50 from the investigation,
# with parents from the original parent_child_rag pipeline (chunk-size invariant).
PC_EMBEDDING_MODEL = "text-embedding-3-large"
PC_CHUNK_SIZE = 400
PC_CHUNK_OVERLAP = 50
PC_CHILD_COLLECTION = (
    f"sop_rag_{PC_EMBEDDING_MODEL}_c{PC_CHUNK_SIZE}_o{PC_CHUNK_OVERLAP}"
)
PC_SOURCE_CHROMA_DIR = (
    WORKSPACE_ROOT
    / "parent_child_rag_investigation"
    / "sop_documents"
    / "chroma_db"
    / PC_EMBEDDING_MODEL
    / f"c{PC_CHUNK_SIZE}_o{PC_CHUNK_OVERLAP}"
)
PC_SOURCE_DOCSTORE_DIR = (
    WORKSPACE_ROOT / "parent_child_rag_investigation" / "sop_documents" / "docstore"
)

# --- Local destinations (what the retriever actually reads) --------------

MODES = ("basic", "parent_child")

MODE_PATHS = {
    "basic": {
        "root": MEMORY_DIR / "basic",
        "chroma_dir": MEMORY_DIR / "basic" / "chroma_db",
        "docstore_dir": MEMORY_DIR / "basic" / "docstore",
        "bm25_corpus": MEMORY_DIR / "basic" / "bm25_corpus.pkl",
        "collection_name": "sop_rag",
        "embedding_model": "text-embedding-3-small",
        # basic_rag uses MultiVectorRetriever; the docstore is a bare
        # LocalFileStore whose values are JSON-encoded Documents.
        "retriever_kind": "multivector",
    },
    "parent_child": {
        "root": MEMORY_DIR / "parent_child",
        "chroma_dir": MEMORY_DIR / "parent_child" / "chroma_db",
        "docstore_dir": MEMORY_DIR / "parent_child" / "docstore",
        "bm25_corpus": MEMORY_DIR / "parent_child" / "bm25_corpus.pkl",
        "collection_name": PC_CHILD_COLLECTION,
        "embedding_model": PC_EMBEDDING_MODEL,
        "chunk_size": PC_CHUNK_SIZE,
        "chunk_overlap": PC_CHUNK_OVERLAP,
        # parent_child uses ParentDocumentRetriever; the docstore is wrapped
        # via create_kv_docstore() so values are LangChain-serialized Documents.
        "retriever_kind": "parent_document",
    },
}

# EnsembleRetriever weights (sparse BM25 + dense Chroma) and BM25 candidate cap.
# bm25_k1 / bm25_b are the Okapi BM25 hyperparameters (term-frequency saturation
# and length-normalization, respectively). rank_bm25 defaults: k1=1.5, b=0.75.
ENSEMBLE_CONFIG = {
    "bm25_weight": 0.5,
    "dense_weight": 0.5,
    "bm25_k1": 1.5,
    "bm25_b": 0.75,
    "rrf_c": 60,
}

RETRIEVAL_CONFIG = {
    "search_type": "similarity",
    "default_score_threshold": 0.3,
    # Initial candidates pulled from each arm (BM25 and dense) before RRF fusion.
    "fetch_k": 20,
    # Final number of fused results returned to the caller.
    "max_results": 5,
}

LLM_CONFIG = {
    "rag_response_model": "gpt-5.1",
}


def ensure_mode_directories(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"Unknown mode '{mode}'. Choose one of {MODES}.")
    paths = MODE_PATHS[mode]
    for d in (MEMORY_DIR, paths["root"], paths["chroma_dir"], paths["docstore_dir"]):
        d.mkdir(parents=True, exist_ok=True)
