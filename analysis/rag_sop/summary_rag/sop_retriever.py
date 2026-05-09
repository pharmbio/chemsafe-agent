import os
import argparse
import importlib.util
from base64 import b64decode
from pathlib import Path
from tempfile import gettempdir
from typing import List, Dict, Any

# Keep transient caches in a writable temp location when run directly.
TMP_ROOT = Path(gettempdir()) / "safechem-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(TMP_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(TMP_ROOT / "xdg-cache"))

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.messages import HumanMessage
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from langchain_classic.retrievers.multi_vector import MultiVectorRetriever
from langchain_classic.storage import LocalFileStore

CURRENT_DIR = Path(__file__).resolve().parent


def _load_local_config():
    """Load the sibling config module regardless of the caller's working directory."""
    config_path = CURRENT_DIR / "config.py"
    spec = importlib.util.spec_from_file_location("section_wise_rag_config", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONFIG = _load_local_config()

CHROMA_PERSIST_PATH = _CONFIG.CHROMA_PERSIST_PATH
COLLECTION_NAME = _CONFIG.COLLECTION_NAME
LLM_CONFIG = _CONFIG.LLM_CONFIG
RETRIEVAL_CONFIG = _CONFIG.RETRIEVAL_CONFIG
ID_KEY = _CONFIG.ID_KEY
DOCSTORE_DIR = _CONFIG.DOCSTORE_DIR
OPENAI_API_KEY = _CONFIG.OPENAI_API_KEY


def _require_openai_api_key() -> str:
    """Return the configured OpenAI API key or raise if missing."""
    openai_api_key = OPENAI_API_KEY
    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured; cannot run SOP retrieval.")
    return openai_api_key


def _normalize_search_type(search_type: str | None) -> str:
    """Normalize requested search type against supported MultiVectorRetriever values."""
    if not search_type:
        return RETRIEVAL_CONFIG["search_type"]

    valid_search_types = {"similarity", "similarity_score_threshold", "mmr"}
    if search_type not in valid_search_types:
        raise ValueError(
            f"Unsupported search_type '{search_type}'. "
            f"Choose one of {sorted(valid_search_types)}."
        )
    return search_type


def _validate_score_threshold(score_threshold: float | None) -> float:
    """Validate a score threshold in the inclusive range [0, 1]."""
    threshold = (
        RETRIEVAL_CONFIG["default_score_threshold"]
        if score_threshold is None
        else score_threshold
    )
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("score_threshold must be between 0.0 and 1.0.")
    return threshold


def _validate_max_results(max_results: int | None) -> int:
    """Validate the retrieval candidate ceiling."""
    value = RETRIEVAL_CONFIG["max_results"] if max_results is None else max_results
    if value < 1:
        raise ValueError("max_results must be at least 1.")
    return value

class SummaryRetriever:
    def __init__(
        self,
        score_threshold: float | None = None,
        max_results: int | None = None,
        search_type: str | None = None,
    ):
        self.retriever = None
        self._api_key = _require_openai_api_key()
        self.score_threshold = _validate_score_threshold(score_threshold)
        self.max_results = _validate_max_results(max_results)
        self.search_type = _normalize_search_type(search_type)
        self.search_kwargs = self._build_search_kwargs()
        self._initialize()

    def _build_search_kwargs(self) -> Dict[str, Any]:
        """Build search kwargs for vectorstore-backed retrieval."""
        search_kwargs: Dict[str, Any] = {"k": self.max_results}
        if self.search_type == "similarity_score_threshold":
            search_kwargs["score_threshold"] = self.score_threshold
        return search_kwargs
    
    def _initialize(self):
        """Initialize the MultiVectorRetriever and RAG chain."""
        if not os.path.exists(str(CHROMA_PERSIST_PATH)):
            raise FileNotFoundError(
                f"Vector store not found at {CHROMA_PERSIST_PATH}. "
                "Please run sop_indexer.py first to create the index."
            )
        
        if not os.path.exists(str(DOCSTORE_DIR)):
            raise FileNotFoundError(
                f"Document store not found at {DOCSTORE_DIR}. "
                "Please run sop_indexer.py first to create the index."
            )
        
        # Create vector store
        vectorstore = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=OpenAIEmbeddings(model = "text-embedding-3-small", api_key=self._api_key),
            persist_directory=str(CHROMA_PERSIST_PATH),
        )
        
        # Check if collection has documents
        if vectorstore._collection.count() == 0:
            raise ValueError(
                "Vector store is empty. Please run sop_indexer.py to index documents."
            )
        
        print(f"Loaded vector store with {vectorstore._collection.count()} documents")
        
        # Create docstore
        docstore = LocalFileStore(str(DOCSTORE_DIR))
        
        # Create MultiVectorRetriever
        self.retriever = MultiVectorRetriever(
            vectorstore=vectorstore,
            docstore=docstore,
            id_key=ID_KEY,
            search_kwargs=self.search_kwargs,
            search_type=self.search_type,
        )
        
        print(f"MultiVectorRetriever created with vectorstore and docstore")
        print(f"Docstore contains {len(list(docstore.yield_keys()))} documents")
    
    def _convert_bytes_to_docs(self, retrieved_items: List[Any]) -> List[Any]:
        """Convert bytes from docstore back to Document objects."""
        documents = []
        for item in retrieved_items:
            if isinstance(item, bytes):
                try:
                    # Try to deserialize JSON data with metadata
                    import json
                    doc_dict = json.loads(item.decode('utf-8'))
                    documents.append(Document(
                        page_content=doc_dict['page_content'],
                        metadata=doc_dict['metadata']
                    ))
                except (json.JSONDecodeError, KeyError):
                    # Fallback: treat as plain text
                    content = item.decode('utf-8')
                    documents.append(Document(page_content=content))
            elif hasattr(item, 'page_content'):
                # Already a Document
                documents.append(item)
            else:
                # Handle other cases
                documents.append(Document(page_content=str(item)))
        return documents
    
    def _parse_docs(self, docs: List[Any]) -> Dict[str, List[Any]]:
        """Split base64-encoded images and texts."""
        # First convert bytes to documents if needed
        docs = self._convert_bytes_to_docs(docs)
        
        b64 = []
        text = []
        for doc in docs:
            try:
                # Check if it's a base64 image
                b64decode(doc.page_content)
                b64.append(doc.page_content)
            except Exception:
                text.append(doc)
        return {"images": b64, "texts": text}


    def query(self, query: str) -> List[Document]:
        """Search SOP documents for protocols and regulatory procedures."""
        documents = self.retriever.invoke(query)
        documents = self._convert_bytes_to_docs(documents)
        return documents