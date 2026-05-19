"""Query a parent-child SOP index variant.

Loads a per-variant Chroma collection (one chunk_size/overlap combo) plus the
shared parent docstore from ../parent_child_rag, and returns full parent
Documents whose child chunks matched the query.
"""

from __future__ import annotations

import os
import importlib.util
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Dict, List

TMP_ROOT = Path(gettempdir()) / "safechem-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(TMP_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(TMP_ROOT / "xdg-cache"))

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_classic.storage import LocalFileStore, create_kv_docstore


CURRENT_DIR = Path(__file__).resolve().parent


def _load_local_config():
    config_path = CURRENT_DIR / "config.py"
    spec = importlib.util.spec_from_file_location("pc_investigation_config", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONFIG = _load_local_config()

SOURCE_DOCSTORE_DIR = _CONFIG.SOURCE_DOCSTORE_DIR
ID_KEY = _CONFIG.ID_KEY
DEFAULT_EMBEDDING_MODEL = _CONFIG.DEFAULT_EMBEDDING_MODEL
RETRIEVAL_CONFIG = _CONFIG.RETRIEVAL_CONFIG
OPENAI_API_KEY = _CONFIG.OPENAI_API_KEY
default_overlap_for = _CONFIG.default_overlap_for
collection_name_for = _CONFIG.collection_name_for
chroma_path_for = _CONFIG.chroma_path_for


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


class ParentChildRetriever:
    """Variant-aware ParentDocumentRetriever wrapper.

    Pick the variant by passing `chunk_size` (and optionally `chunk_overlap`).
    The docstore is shared across variants (read-only); only the Chroma
    collection differs.
    """

    def __init__(
        self,
        chunk_size: int,
        chunk_overlap: int | None = None,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        score_threshold: float | None = None,
        max_results: int | None = None,
        lambda_mult: float | None = None,
        fetch_k: int | None = None,
        search_type: str | None = None,
    ):
        self._api_key = _require_openai_api_key()
        self.chunk_size = chunk_size
        self.chunk_overlap = (
            chunk_overlap if chunk_overlap is not None else default_overlap_for(chunk_size)
        )
        self.embedding_model = embedding_model

        self.search_type = _normalize_search_type(search_type)
        self.max_results = (
            max_results if max_results is not None else RETRIEVAL_CONFIG["max_results"]
        )
        self.score_threshold = (
            score_threshold
            if score_threshold is not None
            else RETRIEVAL_CONFIG["default_score_threshold"]
        )
        self.lambda_mult = (
            lambda_mult if lambda_mult is not None else RETRIEVAL_CONFIG["lambda_mult"]
        )
        self.fetch_k = fetch_k if fetch_k is not None else RETRIEVAL_CONFIG["fetch_k"]

        self.search_kwargs = self._build_search_kwargs()
        self.retriever: ParentDocumentRetriever | None = None
        self._initialize()

    def _build_search_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"k": self.max_results}
        if self.search_type == "similarity_score_threshold":
            kwargs["score_threshold"] = self.score_threshold
        elif self.search_type == "mmr":
            kwargs["fetch_k"] = self.fetch_k
            kwargs["lambda_mult"] = self.lambda_mult
        return kwargs

    def _initialize(self) -> None:
        persist_path = chroma_path_for(self.chunk_size, self.chunk_overlap, self.embedding_model)
        collection = collection_name_for(self.chunk_size, self.chunk_overlap, self.embedding_model)

        if not persist_path.exists():
            raise FileNotFoundError(
                f"Vector store not found at {persist_path}. "
                f"Run sop_indexer.py --chunk-size {self.chunk_size} "
                f"--chunk-overlap {self.chunk_overlap} "
                f"--embedding-model {self.embedding_model} first."
            )
        if not SOURCE_DOCSTORE_DIR.exists():
            raise FileNotFoundError(
                f"Source docstore not found at {SOURCE_DOCSTORE_DIR}. "
                "Run ../parent_child_rag/sop_indexer.py first."
            )

        vectorstore = Chroma(
            collection_name=collection,
            embedding_function=OpenAIEmbeddings(model=self.embedding_model, api_key=self._api_key),
            persist_directory=str(persist_path),
        )
        if vectorstore._collection.count() == 0:
            raise ValueError(
                f"Collection '{collection}' is empty. Run sop_indexer.py for this variant."
            )

        docstore = create_kv_docstore(LocalFileStore(str(SOURCE_DOCSTORE_DIR)))
        # child_splitter is required by the constructor but only used for
        # `add_documents` (we are query-only here).
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )

        self.retriever = ParentDocumentRetriever(
            vectorstore=vectorstore,
            docstore=docstore,
            child_splitter=child_splitter,
            id_key=ID_KEY,
            search_type=self.search_type,
            search_kwargs=self.search_kwargs,
        )
        print(
            f"Loaded variant embed={self.embedding_model} "
            f"c{self.chunk_size}_o{self.chunk_overlap} — "
            f"{vectorstore._collection.count()} child embeddings."
        )

    def query(self, query: str) -> List[Document]:
        if self.retriever is None:
            raise RuntimeError("Retriever not initialized.")
        return self.retriever.invoke(query)
