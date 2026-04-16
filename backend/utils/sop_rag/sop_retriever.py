import os
import argparse
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

import sys

# Add repo root to sys.path so the module works both directly and via root-level wrapper.
CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[2]
repo_root_str = str(REPO_ROOT)
if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)

from backend.utils.sop_rag.config import (
    CHROMA_PERSIST_PATH,
    COLLECTION_NAME,
    LLM_CONFIG,
    RETRIEVAL_CONFIG,
    ID_KEY,
    DOCSTORE_PATH,
)
from app.config import OPENAI_API_KEY


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

class SOPRetriever:
    def __init__(
        self,
        score_threshold: float | None = None,
        max_results: int | None = None,
        search_type: str | None = None,
    ):
        self.retriever = None
        self.rag_chain = None
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
        
        docstore_dir = DOCSTORE_PATH.parent / "docstore"
        if not os.path.exists(str(docstore_dir)):
            raise FileNotFoundError(
                f"Document store not found at {docstore_dir}. "
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
        docstore = LocalFileStore(str(docstore_dir))
        
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
        
        # Create RAG chain
        self.rag_chain = self._create_rag_chain(self.retriever)
    
    def _create_rag_chain(self, retriever):
        """Create the complete RAG chain for question answering."""
        return {
            "context": retriever | RunnableLambda(self._parse_docs),
            "question": RunnablePassthrough(),
        } | RunnablePassthrough().assign(
            response=(
                RunnableLambda(self._build_prompt)
                | ChatOpenAI(model=LLM_CONFIG["rag_response_model"], api_key=self._api_key)
                | StrOutputParser()
            )
        )
    
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
    
    def _build_prompt(self, kwargs: Dict[str, Any]) -> ChatPromptTemplate:
        """Build a prompt with context and question for RAG queries."""
        docs_by_type = kwargs["context"]
        user_question = kwargs["question"]

        context_text = ""
        if len(docs_by_type["texts"]) > 0:
            for text_element in docs_by_type["texts"]:
                context_text += text_element.page_content + "\n\n"

        prompt_template = f"""
Answer the question based only on the following context, which can include text, tables, and the below image.
Context: {context_text}
Question: {user_question}
"""

        prompt_content = [{"type": "text", "text": prompt_template}]

        if len(docs_by_type["images"]) > 0:
            for image in docs_by_type["images"]:
                prompt_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image}"},
                })

        return ChatPromptTemplate.from_messages([HumanMessage(content=prompt_content)])
    
    def query(self, question: str) -> Dict[str, Any]:
        """Query the RAG system with a question."""
        if self.rag_chain is None:
            raise ValueError("RAG chain not initialized")
        
        response = self.rag_chain.invoke(question)
        return response
    
    def get_sources(self, question: str) -> List[str]:
        """Get source documents for a question without generating response."""
        
        # First check what's in vectorstore (summaries)
        summary_docs = self.retriever.vectorstore.as_retriever(
            search_type=self.search_type,
            search_kwargs=self.search_kwargs,
        ).invoke(question)
        
        # Then get full documents from MultiVectorRetriever (docstore)
        docs = self.retriever.invoke(question)
        
        docs = self._convert_bytes_to_docs(docs)
        
        sources = []
        for doc in docs:
            if hasattr(doc, 'metadata') and 'filename' in doc.metadata:
                sources.append(os.path.basename(doc.metadata['filename']))
        
        return list(set(sources))  # Remove duplicates

def main(query: str | None = None):
    """Main execution function for testing."""
    if query is None:
        parser = argparse.ArgumentParser(description="Query the local SOP RAG store.")
        parser.add_argument("query", nargs="*", help="Question to ask about the indexed SOPs.")
        parser.add_argument(
            "--score-threshold",
            type=float,
            default=None,
            help="Minimum similarity relevance score between 0.0 and 1.0.",
        )
        parser.add_argument(
            "--max-results",
            type=int,
            default=None,
            help="Maximum number of candidate documents to return after thresholding.",
        )
        args = parser.parse_args()
        query = " ".join(args.query).strip() if args.query else input("Query: ").strip()
        score_threshold = args.score_threshold
        max_results = args.max_results
    else:
        score_threshold = None
        max_results = None

    try:
        retriever = SOPRetriever(
            score_threshold=score_threshold,
            max_results=max_results,
        )
        
        print(f"\nQuery: {query}")
        print("-" * 50)
            
        try:
            response = retriever.query(query)
            print(f"Response: {response['response']}")
                
            # Show sources with text content
            sources = retriever.get_sources(query)
            if sources:
                print(f"\nSources:")
                for source in sorted(sources):
                    print(f"  - {source}")
                
                
                # Get and display the actual retrieved documents (full content from docstore)
                docs = retriever.retriever.invoke(query)
                docs = retriever._convert_bytes_to_docs(docs)
                
                print(f"\nRetrieved Content:")
                for i, doc in enumerate(docs, 1):
                    filename = os.path.basename(doc.metadata.get('filename', 'Unknown')) if hasattr(doc, 'metadata') else 'Unknown'
                    print(f"\n--- Document {i} ---")
                    print(f"Source: {filename}")
                    print(f"Original Text: {doc.page_content}")  
                
        except Exception as e:
            print(f"Error processing query: {e}")
            
        print("\n" + "="*60)
    
    except Exception as e:
        print(f"Error initializing retriever: {e}")
        print("Please run sop_indexer.py first to create the index.")

if __name__ == "__main__":
    main()
