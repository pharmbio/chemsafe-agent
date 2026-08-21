"""Config for the SOP RAG ensemble (BM25 + dense), production paths.

Two ensemble modes share the same sparse (BM25) + dense (Chroma) topology;
the dense engine is swappable:

  mode="basic"          → MultiVectorRetriever + text-embedding-3-small
  mode="parent_child"   → ParentDocumentRetriever + text-embedding-3-large
                          (child chunk_size=400, overlap=50)

At runtime the retriever reads from MEMORY_ROOT/sop_documents/<mode>/. Source
databases live under analysis/rag_sop/ and are copied in by sop_indexer.py
when (re)building the index.
"""

from __future__ import annotations

from pathlib import Path

from app.config import MEMORY_ROOT, OPENAI_API_KEY, REPO_ROOT

ID_KEY = "doc_id"

# Production data lives under MEMORY_ROOT (typically <repo>/persistence/memory).
MEMORY_DIR = MEMORY_ROOT
SOP_MEMORY_DIR = MEMORY_DIR / "sop_documents"

# Original PDFs (used only for indexing pipelines upstream).
SOP_DATA_DIR = REPO_ROOT / "analysis" / "rag_sop" / "SOPs"

# --- Source locations (read-only, used by the copy step in sop_indexer) ---

_ANALYSIS_ROOT = REPO_ROOT / "analysis" / "rag_sop"

# basic_rag sources
BASIC_SOURCE_CHROMA_DIR = _ANALYSIS_ROOT / "basic_rag" / "sop_documents" / "chroma_db"
BASIC_SOURCE_DOCSTORE_DIR = _ANALYSIS_ROOT / "basic_rag" / "sop_documents" / "docstore"

# parent_child winner: text-embedding-3-large / c400_o50.
PC_EMBEDDING_MODEL = "text-embedding-3-large"
PC_CHUNK_SIZE = 400
PC_CHUNK_OVERLAP = 50
PC_CHILD_COLLECTION = (
    f"sop_rag_{PC_EMBEDDING_MODEL}_c{PC_CHUNK_SIZE}_o{PC_CHUNK_OVERLAP}"
)
PC_SOURCE_CHROMA_DIR = (
    _ANALYSIS_ROOT
    / "parent_child_rag_investigation"
    / "sop_documents"
    / "chroma_db"
    / PC_EMBEDDING_MODEL
    / f"c{PC_CHUNK_SIZE}_o{PC_CHUNK_OVERLAP}"
)
PC_SOURCE_DOCSTORE_DIR = (
    _ANALYSIS_ROOT / "parent_child_rag_investigation" / "sop_documents" / "docstore"
)

# --- Local destinations (what the retriever actually reads) --------------

MODES = ("basic", "parent_child")

MODE_PATHS = {
    "basic": {
        "root": SOP_MEMORY_DIR / "basic",
        "chroma_dir": SOP_MEMORY_DIR / "basic" / "chroma_db",
        "docstore_dir": SOP_MEMORY_DIR / "basic" / "docstore",
        "bm25_corpus": SOP_MEMORY_DIR / "basic" / "bm25_corpus.pkl",
        "collection_name": "sop_rag",
        "embedding_model": "text-embedding-3-small",
        # MultiVectorRetriever; docstore is a bare LocalFileStore whose values
        # are JSON-encoded Documents.
        "retriever_kind": "multivector",
    },
    "parent_child": {
        "root": SOP_MEMORY_DIR / "parent_child",
        "chroma_dir": SOP_MEMORY_DIR / "parent_child" / "chroma_db",
        "docstore_dir": SOP_MEMORY_DIR / "parent_child" / "docstore",
        "bm25_corpus": SOP_MEMORY_DIR / "parent_child" / "bm25_corpus.pkl",
        "collection_name": PC_CHILD_COLLECTION,
        "embedding_model": PC_EMBEDDING_MODEL,
        "chunk_size": PC_CHUNK_SIZE,
        "chunk_overlap": PC_CHUNK_OVERLAP,
        # ParentDocumentRetriever; docstore wrapped via create_kv_docstore()
        # so values are LangChain-serialized Documents.
        "retriever_kind": "parent_document",
    },
}

# EnsembleRetriever weights (sparse BM25 + dense Chroma) and BM25 hyperparams.
# rank_bm25 defaults: k1=1.5, b=0.75.
ENSEMBLE_CONFIG = {
    "bm25_weight": 0.4,
    "dense_weight": 0.6,
    "bm25_k1": 1.5,
    "bm25_b": 0.75,
    "rrf_c": 60,
}

RETRIEVAL_CONFIG = {
    "search_type": "similarity",
    "default_score_threshold": 0.3,
    # Initial candidates pulled from each arm (BM25 and dense) before fusion.
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
    for d in (SOP_MEMORY_DIR, paths["root"], paths["chroma_dir"], paths["docstore_dir"]):
        Path(d).mkdir(parents=True, exist_ok=True)
