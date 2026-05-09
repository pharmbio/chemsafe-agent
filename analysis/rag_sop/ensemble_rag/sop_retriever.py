import os
import pickle
import importlib.util
from pathlib import Path
from tempfile import gettempdir
from typing import List, Dict, Any

# Keep transient caches in a writable temp location when run directly.
TMP_ROOT = Path(gettempdir()) / "safechem-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(TMP_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(TMP_ROOT / "xdg-cache"))

from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever

CURRENT_DIR = Path(__file__).resolve().parent


def _load_local_config():
    """Load the sibling config module regardless of the caller's working directory."""
    config_path = CURRENT_DIR / "config.py"
    spec = importlib.util.spec_from_file_location("ensemble_rag_config", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONFIG = _load_local_config()

CHROMA_PERSIST_PATH = _CONFIG.CHROMA_PERSIST_PATH
BM25_CORPUS_PATH = _CONFIG.BM25_CORPUS_PATH
COLLECTION_NAME = _CONFIG.COLLECTION_NAME
ID_KEY = _CONFIG.ID_KEY
LLM_CONFIG = _CONFIG.LLM_CONFIG
RETRIEVAL_CONFIG = _CONFIG.RETRIEVAL_CONFIG
ENSEMBLE_CONFIG = _CONFIG.ENSEMBLE_CONFIG
OPENAI_API_KEY = _CONFIG.OPENAI_API_KEY


def _require_openai_api_key() -> str:
    """Return the configured OpenAI API key or raise if missing."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured; cannot run SOP retrieval.")
    return OPENAI_API_KEY


def _normalize_search_type(search_type: str | None) -> str:
    """Normalize the dense-side search type against supported Chroma values."""
    if not search_type:
        return RETRIEVAL_CONFIG["search_type"]
    valid = {"similarity", "similarity_score_threshold", "mmr"}
    if search_type not in valid:
        raise ValueError(
            f"Unsupported search_type '{search_type}'. Choose one of {sorted(valid)}."
        )
    return search_type


def _validate_score_threshold(score_threshold: float | None) -> float:
    threshold = (
        RETRIEVAL_CONFIG["default_score_threshold"]
        if score_threshold is None
        else score_threshold
    )
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("score_threshold must be between 0.0 and 1.0.")
    return threshold


def _validate_max_results(max_results: int | None) -> int:
    value = RETRIEVAL_CONFIG["max_results"] if max_results is None else max_results
    if value < 1:
        raise ValueError("max_results must be at least 1.")
    return value


def _validate_weight(name: str, value: float | None, default: float) -> float:
    weight = default if value is None else value
    if weight < 0:
        raise ValueError(f"{name} must be non-negative.")
    return float(weight)


class EnsembleSOPRetriever:
    """Sparse + dense retrieval over the persisted SOP index.

    Combines a BM25Retriever (rebuilt from the pickled corpus on every
    instantiation) with a Chroma-backed dense retriever via
    `EnsembleRetriever`. Results are fused with weighted RRF.
    """

    def __init__(
        self,
        score_threshold: float | None = None,
        max_results: int | None = None,
        search_type: str | None = None,
        bm25_weight: float | None = None,
        dense_weight: float | None = None,
        bm25_k: int | None = None,
    ) -> None:
        self._api_key = _require_openai_api_key()
        self.search_type = _normalize_search_type(search_type)
        self.max_results = _validate_max_results(max_results)
        self.score_threshold = _validate_score_threshold(score_threshold)
        self.bm25_weight = _validate_weight(
            "bm25_weight", bm25_weight, ENSEMBLE_CONFIG["bm25_weight"]
        )
        self.dense_weight = _validate_weight(
            "dense_weight", dense_weight, ENSEMBLE_CONFIG["dense_weight"]
        )
        if self.bm25_weight == 0 and self.dense_weight == 0:
            raise ValueError("At least one of bm25_weight / dense_weight must be > 0.")
        self.bm25_k = bm25_k if bm25_k is not None else ENSEMBLE_CONFIG["bm25_k"]
        if self.bm25_k < 1:
            raise ValueError("bm25_k must be at least 1.")
        self.search_kwargs = self._build_search_kwargs()
        self.retriever: EnsembleRetriever | None = None
        self._initialize()

    def _build_search_kwargs(self) -> Dict[str, Any]:
        """Build search kwargs for the dense (Chroma) retriever."""
        kwargs: Dict[str, Any] = {"k": self.max_results}
        if self.search_type == "similarity_score_threshold":
            kwargs["score_threshold"] = self.score_threshold
        return kwargs

    def _load_bm25_corpus(self) -> List[Document]:
        with open(BM25_CORPUS_PATH, "rb") as f:
            payload = pickle.load(f)
        if not isinstance(payload, list):
            raise ValueError(
                f"Unexpected BM25 corpus format at {BM25_CORPUS_PATH}. "
                "Re-run sop_indexer.py to rebuild it."
            )
        return [
            Document(page_content=item["page_content"], metadata=item["metadata"])
            for item in payload
        ]

    def _initialize(self) -> None:
        if not os.path.exists(str(CHROMA_PERSIST_PATH)):
            raise FileNotFoundError(
                f"Vector store not found at {CHROMA_PERSIST_PATH}. "
                "Run sop_indexer.py first to create the index."
            )
        if not BM25_CORPUS_PATH.exists():
            raise FileNotFoundError(
                f"BM25 corpus not found at {BM25_CORPUS_PATH}. "
                "Run sop_indexer.py first to create the index."
            )

        # Dense side: persistent Chroma collection.
        vectorstore = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=OpenAIEmbeddings(
                model="text-embedding-3-small", api_key=self._api_key
            ),
            persist_directory=str(CHROMA_PERSIST_PATH),
        )
        dense_count = vectorstore._collection.count()
        if dense_count == 0:
            raise ValueError(
                "Vector store is empty. Run sop_indexer.py to index documents."
            )
        dense_retriever = vectorstore.as_retriever(
            search_type=self.search_type,
            search_kwargs=self.search_kwargs,
        )

        # Sparse side: rebuild BM25Retriever from the pickled corpus.
        bm25_documents = self._load_bm25_corpus()
        if not bm25_documents:
            raise ValueError(
                f"BM25 corpus at {BM25_CORPUS_PATH} is empty. "
                "Run sop_indexer.py to rebuild it."
            )
        bm25_retriever = BM25Retriever.from_documents(bm25_documents)
        bm25_retriever.k = self.bm25_k

        self.retriever = EnsembleRetriever(
            retrievers=[bm25_retriever, dense_retriever],
            weights=[self.bm25_weight, self.dense_weight],
            c=ENSEMBLE_CONFIG["rrf_c"],
            # Dedupe by our stable doc_id when both retrievers surface the
            # same chunk. Chroma stores doc_id in metadata; BM25 documents
            # carry the same key from the pickled corpus.
            id_key=ID_KEY,
        )

        print(
            f"EnsembleRetriever ready — dense={dense_count} docs (Chroma), "
            f"sparse={len(bm25_documents)} docs (BM25), "
            f"weights=[bm25={self.bm25_weight}, dense={self.dense_weight}]"
        )

    def query(self, query: str) -> List[Document]:
        """Search SOP documents and return the fused top results."""
        if self.retriever is None:
            raise RuntimeError("Retriever not initialized.")
        results = self.retriever.invoke(query)
        return results[: self.max_results]
