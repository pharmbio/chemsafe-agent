"""Mode-pluggable ensemble retriever (BM25 + dense).

Pick the dense engine with `mode`:

    EnsembleSOPRetriever(mode="basic")          # basic_rag MultiVectorRetriever
    EnsembleSOPRetriever(mode="parent_child")   # parent_child winner
                                                # (text-embedding-3-large, c400/o50)

Both modes return parent documents and dedupe across BM25/dense via the
shared `doc_id` metadata key.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pickle
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Dict, List

TMP_ROOT = Path(gettempdir()) / "safechem-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(TMP_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(TMP_ROOT / "xdg-cache"))

from langchain_classic.retrievers import EnsembleRetriever, ParentDocumentRetriever
from langchain_classic.retrievers.multi_vector import MultiVectorRetriever
from langchain_classic.storage import LocalFileStore, create_kv_docstore
from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_core.stores import BaseStore
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


CURRENT_DIR = Path(__file__).resolve().parent


def _load_local_config():
    config_path = CURRENT_DIR / "config.py"
    spec = importlib.util.spec_from_file_location("ensemble_investigation_config", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONFIG = _load_local_config()

MODES = _CONFIG.MODES
MODE_PATHS = _CONFIG.MODE_PATHS
ID_KEY = _CONFIG.ID_KEY
ENSEMBLE_CONFIG = _CONFIG.ENSEMBLE_CONFIG
RETRIEVAL_CONFIG = _CONFIG.RETRIEVAL_CONFIG
OPENAI_API_KEY = _CONFIG.OPENAI_API_KEY


def _require_openai_api_key() -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured; cannot run SOP retrieval.")
    return OPENAI_API_KEY


def _normalize_search_type(search_type: str | None) -> str:
    if not search_type:
        return RETRIEVAL_CONFIG["search_type"]
    valid = {"similarity", "similarity_score_threshold", "mmr"}
    if search_type not in valid:
        raise ValueError(
            f"Unsupported search_type '{search_type}'. Choose one of {sorted(valid)}."
        )
    return search_type


def _validate_weight(name: str, value: float | None, default: float) -> float:
    weight = default if value is None else value
    if weight < 0:
        raise ValueError(f"{name} must be non-negative.")
    return float(weight)


class _JSONBytesDocstore(BaseStore[str, Document]):
    """Adapter that decodes basic_rag's JSON-encoded LocalFileStore values.

    MultiVectorRetriever expects a `BaseStore[str, Document]`, but basic_rag
    persisted Documents as raw JSON bytes (not LangChain-encoded objects),
    so `create_kv_docstore` would not decode them. This subclass of BaseStore
    decodes on read and is read-only.
    """

    def __init__(self, file_store: LocalFileStore) -> None:
        self._store = file_store

    def mget(self, keys: List[str]) -> List[Document | None]:
        out: List[Document | None] = []
        for raw in self._store.mget(keys):
            if raw is None:
                out.append(None)
                continue
            try:
                payload = json.loads(raw.decode("utf-8"))
                doc = Document(
                    page_content=payload.get("page_content", ""),
                    metadata=dict(payload.get("metadata", {})),
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                doc = Document(page_content=raw.decode("utf-8", errors="replace"))
            out.append(doc)
        return out

    def mset(self, key_value_pairs):  # pragma: no cover
        raise NotImplementedError("Read-only docstore.")

    def mdelete(self, keys):  # pragma: no cover
        raise NotImplementedError("Read-only docstore.")

    def yield_keys(self, *, prefix: str | None = None):
        return self._store.yield_keys(prefix=prefix)


class _IdInjectingDocstore(BaseStore[str, Document]):
    """Wrap a Document-valued BaseStore to inject the lookup key into metadata.

    parent_child_rag stored parent Documents with only {source, element_type}
    in metadata, so EnsembleRetriever's `id_key="doc_id"` dedup fails on hits
    coming back from ParentDocumentRetriever. Wrapping the underlying docstore
    here re-attaches the docstore key as metadata[ID_KEY] on every read,
    without mutating any persisted data.
    """

    def __init__(self, inner: BaseStore[str, Document], id_key: str) -> None:
        self._inner = inner
        self._id_key = id_key

    def mget(self, keys: List[str]) -> List[Document | None]:
        out: List[Document | None] = []
        for key, doc in zip(keys, self._inner.mget(keys)):
            if doc is None:
                out.append(None)
                continue
            metadata = dict(doc.metadata or {})
            metadata.setdefault(self._id_key, key)
            out.append(Document(page_content=doc.page_content, metadata=metadata))
        return out

    def mset(self, key_value_pairs):  # pragma: no cover
        raise NotImplementedError("Read-only docstore.")

    def mdelete(self, keys):  # pragma: no cover
        raise NotImplementedError("Read-only docstore.")

    def yield_keys(self, *, prefix: str | None = None):
        return self._inner.yield_keys(prefix=prefix)


class EnsembleSOPRetriever:
    """BM25 (sparse) + Chroma (dense) ensemble, dense engine selectable by mode."""

    def __init__(
        self,
        mode: str = "basic",
        score_threshold: float | None = None,
        max_results: int | None = None,
        fetch_k: int | None = None,
        search_type: str | None = None,
        bm25_weight: float | None = None,
        dense_weight: float | None = None,
        bm25_k1: float | None = None,
        bm25_b: float | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose one of {MODES}.")
        self.mode = mode
        self._paths = MODE_PATHS[mode]
        self._api_key = _require_openai_api_key()

        self.search_type = _normalize_search_type(search_type)
        self.max_results = (
            RETRIEVAL_CONFIG["max_results"] if max_results is None else max_results
        )
        if self.max_results < 1:
            raise ValueError("max_results must be at least 1.")
        self.fetch_k = (
            RETRIEVAL_CONFIG["fetch_k"] if fetch_k is None else fetch_k
        )
        if self.fetch_k < self.max_results:
            raise ValueError("fetch_k must be >= max_results.")
        self.score_threshold = (
            RETRIEVAL_CONFIG["default_score_threshold"]
            if score_threshold is None
            else score_threshold
        )
        self.bm25_weight = _validate_weight(
            "bm25_weight", bm25_weight, ENSEMBLE_CONFIG["bm25_weight"]
        )
        self.dense_weight = _validate_weight(
            "dense_weight", dense_weight, ENSEMBLE_CONFIG["dense_weight"]
        )
        if self.bm25_weight == 0 and self.dense_weight == 0:
            raise ValueError("At least one of bm25_weight / dense_weight must be > 0.")
        self.bm25_k1 = (
            float(bm25_k1) if bm25_k1 is not None else ENSEMBLE_CONFIG["bm25_k1"]
        )
        if self.bm25_k1 < 0:
            raise ValueError("bm25_k1 must be non-negative.")
        self.bm25_b = (
            float(bm25_b) if bm25_b is not None else ENSEMBLE_CONFIG["bm25_b"]
        )
        if not 0.0 <= self.bm25_b <= 1.0:
            raise ValueError("bm25_b must be in [0, 1].")

        self.retriever: EnsembleRetriever | None = None
        self._initialize()

    def _build_search_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"k": self.fetch_k}
        if self.search_type == "similarity_score_threshold":
            kwargs["score_threshold"] = self.score_threshold
        return kwargs

    def _build_dense_retriever(self):
        chroma_dir: Path = self._paths["chroma_dir"]
        docstore_dir: Path = self._paths["docstore_dir"]
        if not chroma_dir.exists():
            raise FileNotFoundError(
                f"Chroma not found for mode '{self.mode}' at {chroma_dir}. "
                "Run sop_indexer.py first."
            )
        if not docstore_dir.exists():
            raise FileNotFoundError(
                f"Docstore not found for mode '{self.mode}' at {docstore_dir}. "
                "Run sop_indexer.py first."
            )

        vectorstore = Chroma(
            collection_name=self._paths["collection_name"],
            embedding_function=OpenAIEmbeddings(
                model=self._paths["embedding_model"], api_key=self._api_key
            ),
            persist_directory=str(chroma_dir),
        )
        count = vectorstore._collection.count()
        if count == 0:
            raise ValueError(
                f"Chroma collection '{self._paths['collection_name']}' is empty for mode '{self.mode}'."
            )

        search_kwargs = self._build_search_kwargs()
        kind = self._paths["retriever_kind"]

        if kind == "multivector":
            docstore = _JSONBytesDocstore(LocalFileStore(str(docstore_dir)))
            retriever = MultiVectorRetriever(
                vectorstore=vectorstore,
                docstore=docstore,
                id_key=ID_KEY,
                search_type=self.search_type,
                search_kwargs=search_kwargs,
            )
        elif kind == "parent_document":
            docstore = _IdInjectingDocstore(
                create_kv_docstore(LocalFileStore(str(docstore_dir))),
                id_key=ID_KEY,
            )
            # child_splitter is required by the constructor but unused for
            # pure-query usage.
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=self._paths["chunk_size"],
                chunk_overlap=self._paths["chunk_overlap"],
            )
            retriever = ParentDocumentRetriever(
                vectorstore=vectorstore,
                docstore=docstore,
                child_splitter=splitter,
                id_key=ID_KEY,
                search_type=self.search_type,
                search_kwargs=search_kwargs,
            )
        else:
            raise ValueError(f"Unknown retriever_kind '{kind}'")

        return retriever, count

    def _load_bm25_corpus(self) -> List[Document]:
        path: Path = self._paths["bm25_corpus"]
        if not path.exists():
            raise FileNotFoundError(
                f"BM25 corpus not found at {path}. Run sop_indexer.py for mode '{self.mode}'."
            )
        with open(path, "rb") as f:
            payload = pickle.load(f)
        if not isinstance(payload, list) or not payload:
            raise ValueError(
                f"Unexpected BM25 corpus at {path}; rebuild with sop_indexer.py."
            )
        return [
            Document(page_content=item["page_content"], metadata=item["metadata"])
            for item in payload
        ]

    def _initialize(self) -> None:
        dense_retriever, dense_count = self._build_dense_retriever()

        bm25_docs = self._load_bm25_corpus()
        bm25_retriever = BM25Retriever.from_documents(
            bm25_docs,
            bm25_params={"k1": self.bm25_k1, "b": self.bm25_b},
        )
        bm25_retriever.k = self.fetch_k

        self.retriever = EnsembleRetriever(
            retrievers=[bm25_retriever, dense_retriever],
            weights=[self.bm25_weight, self.dense_weight],
            c=ENSEMBLE_CONFIG["rrf_c"],
            id_key=ID_KEY,
        )

        print(
            f"EnsembleRetriever ready (mode={self.mode}) — "
            f"dense={dense_count} ({self._paths['retriever_kind']}, "
            f"{self._paths['embedding_model']}), "
            f"sparse={len(bm25_docs)} BM25 docs (k1={self.bm25_k1}, b={self.bm25_b}), "
            f"weights=[bm25={self.bm25_weight}, dense={self.dense_weight}], "
            f"fetch_k={self.fetch_k}, max_results={self.max_results}"
        )

    def query(self, query: str) -> List[Document]:
        if self.retriever is None:
            raise RuntimeError("Retriever not initialized.")
        results = self.retriever.invoke(query)
        return results[: self.max_results]
