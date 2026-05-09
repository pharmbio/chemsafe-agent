"""Query the parent-child SOP index.

Loads the persisted Chroma store + LocalFileStore docstore and exposes a
small wrapper around `ParentDocumentRetriever` that returns full parent
Documents whose child chunks matched the query.
"""

import os
import importlib.util
from pathlib import Path
from tempfile import gettempdir
from typing import List, Dict, Any

TMP_ROOT = Path(gettempdir()) / "safechem-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(TMP_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(TMP_ROOT / "xdg-cache"))

from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_classic.storage import LocalFileStore, create_kv_docstore

CURRENT_DIR = Path(__file__).resolve().parent


def _load_local_config():
    config_path = CURRENT_DIR / "config.py"
    spec = importlib.util.spec_from_file_location("parent_child_rag_config", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONFIG = _load_local_config()

CHROMA_PERSIST_PATH = _CONFIG.CHROMA_PERSIST_PATH
COLLECTION_NAME = _CONFIG.COLLECTION_NAME
DOCSTORE_DIR = _CONFIG.DOCSTORE_DIR
ID_KEY = _CONFIG.ID_KEY
CHILD_SPLITTER_CONFIG = _CONFIG.CHILD_SPLITTER_CONFIG
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
        raise ValueError(f"Unsupported search_type '{search_type}'. Choose one of {sorted(valid)}.")
    return search_type


class ParentChildRetriever:
    def __init__(
        self,
        score_threshold: float | None = None,
        max_results: int | None = None,
        search_type: str | None = None,
    ):
        self._api_key = _require_openai_api_key()
        self.search_type = _normalize_search_type(search_type)
        self.max_results = max_results if max_results is not None else RETRIEVAL_CONFIG["max_results"]
        self.score_threshold = (
            score_threshold if score_threshold is not None
            else RETRIEVAL_CONFIG["default_score_threshold"]
        )
        self.search_kwargs = self._build_search_kwargs()
        self.retriever: ParentDocumentRetriever | None = None
        self._initialize()

    def _build_search_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"k": self.max_results}
        if self.search_type == "similarity_score_threshold":
            kwargs["score_threshold"] = self.score_threshold
        return kwargs

    def _initialize(self) -> None:
        if not os.path.exists(str(CHROMA_PERSIST_PATH)):
            raise FileNotFoundError(
                f"Vector store not found at {CHROMA_PERSIST_PATH}. "
                "Run sop_indexer.py first to create the index."
            )
        if not os.path.exists(str(DOCSTORE_DIR)):
            raise FileNotFoundError(
                f"Document store not found at {DOCSTORE_DIR}. "
                "Run sop_indexer.py first to create the index."
            )

        vectorstore = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=OpenAIEmbeddings(model="text-embedding-3-small", api_key=self._api_key),
            persist_directory=str(CHROMA_PERSIST_PATH),
        )
        if vectorstore._collection.count() == 0:
            raise ValueError("Vector store is empty. Run sop_indexer.py to index documents.")

        docstore = create_kv_docstore(LocalFileStore(str(DOCSTORE_DIR)))
        child_splitter = RecursiveCharacterTextSplitter(**CHILD_SPLITTER_CONFIG)

        self.retriever = ParentDocumentRetriever(
            vectorstore=vectorstore,
            docstore=docstore,
            child_splitter=child_splitter,
            id_key=ID_KEY,
            search_type=self.search_type,
            search_kwargs=self.search_kwargs,
        )
        print(f"Loaded ParentDocumentRetriever — {vectorstore._collection.count()} child embeddings.")

    def query(self, query: str) -> List[Document]:
        if self.retriever is None:
            raise RuntimeError("Retriever not initialized.")
        return self.retriever.invoke(query)