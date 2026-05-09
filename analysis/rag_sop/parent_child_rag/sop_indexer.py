import os
import argparse
import importlib.util
from pathlib import Path
from tempfile import gettempdir
from typing import List, Any

# Keep transient caches in a writable temp location when run directly.
TMP_ROOT = Path(gettempdir()) / "safechem-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("NUMBA_CACHE_DIR", str(TMP_ROOT / "numba-cache"))
os.environ.setdefault("MPLCONFIGDIR", str(TMP_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(TMP_ROOT / "xdg-cache"))

from unstructured.partition.pdf import partition_pdf
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
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

SOP_DATA_DIR = _CONFIG.SOP_DATA_DIR
CHROMA_PERSIST_PATH = _CONFIG.CHROMA_PERSIST_PATH
COLLECTION_NAME = _CONFIG.COLLECTION_NAME
ID_KEY = _CONFIG.ID_KEY
DOCSTORE_DIR = _CONFIG.DOCSTORE_DIR
PDF_PROCESSING_CONFIG = _CONFIG.PDF_PROCESSING_CONFIG
CHILD_SPLITTER_CONFIG = _CONFIG.CHILD_SPLITTER_CONFIG
LLM_CONFIG = _CONFIG.LLM_CONFIG
OPENAI_API_KEY = _CONFIG.OPENAI_API_KEY
ensure_directories = _CONFIG.ensure_directories


def _require_openai_api_key() -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured; cannot index SOP documents.")
    return OPENAI_API_KEY


def discover_pdf_files(directory: str) -> List[str]:
    directory_path = Path(directory)
    if not directory_path.exists():
        raise FileNotFoundError(f"Directory {directory} does not exist")
    return sorted(str(p) for p in directory_path.glob("*.pdf") if p.is_file())


def _create_image_describer():
    """Chain that turns a base64 image into a textual description."""
    api_key = _require_openai_api_key()
    prompt = ChatPromptTemplate.from_messages([
        ("user", [
            {"type": "text", "text": (
                "Describe this image in detail. It is part of a regulatory/SOP "
                "document. Be specific about figures, diagrams, and tables."
            )},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64,{image}"}},
        ])
    ])
    llm = ChatOpenAI(model=LLM_CONFIG["image_description_model"], api_key=api_key)
    return prompt | llm | StrOutputParser()


def _chunk_to_parent_text(chunk: Any, image_describer) -> str:
    """Merge a CompositeElement (or Table) into a single text blob.

    Tables get HTML; embedded images get LLM descriptions appended.
    """
    parts: List[str] = []

    if "Table" in str(type(chunk)):
        html = getattr(chunk.metadata, "text_as_html", None)
        parts.append(html if html else getattr(chunk, "text", ""))
        return "\n".join(p for p in parts if p)

    # CompositeElement: include its text, plus image descriptions for any
    # Image elements that were grouped into this section.
    if getattr(chunk, "text", None):
        parts.append(chunk.text)

    orig_elements = getattr(chunk.metadata, "orig_elements", None) or []
    images_b64 = [
        el.metadata.image_base64
        for el in orig_elements
        if "Image" in str(type(el)) and getattr(el.metadata, "image_base64", None)
    ]
    if images_b64:
        descriptions = image_describer.batch([{"image": b} for b in images_b64])
        for desc in descriptions:
            parts.append(f"[Image] {desc}")

    return "\n".join(p for p in parts if p)


def build_parent_documents(pdf_files: List[str]) -> List[Document]:
    """Run Unstructured on each PDF and emit one parent Document per chunk."""
    image_describer = _create_image_describer()
    parents: List[Document] = []

    for pdf_file in pdf_files:
        print(f"\nParsing {os.path.basename(pdf_file)}...")
        try:
            chunks = partition_pdf(filename=pdf_file, **PDF_PROCESSING_CONFIG)
        except Exception as exc:
            print(f"  - Failed to parse {pdf_file}: {exc}")
            continue

        filename = os.path.basename(pdf_file)
        kept = 0
        for chunk in chunks:
            text = _chunk_to_parent_text(chunk, image_describer)
            if not text.strip():
                continue
            parents.append(Document(
                page_content=text,
                metadata={"source": filename, "element_type": type(chunk).__name__},
            ))
            kept += 1
        print(f"  - {kept} parent chunks built")

    return parents


def build_retriever() -> ParentDocumentRetriever:
    ensure_directories()
    api_key = _require_openai_api_key()

    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model="text-embedding-3-small", api_key=api_key),
        persist_directory=str(CHROMA_PERSIST_PATH),
    )
    # create_kv_docstore wraps a bytes store so it can persist Documents.
    docstore = create_kv_docstore(LocalFileStore(str(DOCSTORE_DIR)))

    child_splitter = RecursiveCharacterTextSplitter(**CHILD_SPLITTER_CONFIG)

    return ParentDocumentRetriever(
        vectorstore=vectorstore,
        docstore=docstore,
        child_splitter=child_splitter,
        id_key=ID_KEY,
    )


def clear_existing_collection() -> None:
    api_key = _require_openai_api_key()
    try:
        Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=OpenAIEmbeddings(model="text-embedding-3-small", api_key=api_key),
            persist_directory=str(CHROMA_PERSIST_PATH),
        ).delete_collection()
        print("Cleared existing Chroma collection")
    except Exception as exc:
        print(f"No existing collection to clear: {exc}")

    if DOCSTORE_DIR.exists():
        import shutil
        shutil.rmtree(DOCSTORE_DIR)
        print("Cleared existing docstore")


def process_and_index_pdfs(directory: str) -> None:
    clear_existing_collection()

    pdf_files = discover_pdf_files(directory)
    print(f"Found {len(pdf_files)} PDF files to process:")
    for f in pdf_files:
        print(f"  - {os.path.basename(f)}")
    if not pdf_files:
        print("No PDF files found to process")
        return

    parents = build_parent_documents(pdf_files)
    if not parents:
        print("No parent documents extracted; nothing to index.")
        return

    retriever = build_retriever()
    print(f"\nAdding {len(parents)} parent documents (children will be embedded)...")
    # Chroma caps a single upsert at 5461 records. Children expand ~10x from parents,
    # so batch parents to stay under the cap.
    BATCH = 400
    for i in range(0, len(parents), BATCH):
        batch = parents[i : i + BATCH]
        retriever.add_documents(batch, ids=None)
        print(f"  - indexed {min(i + BATCH, len(parents))}/{len(parents)} parents")

    print("\n=== Indexing Complete ===")
    print(f"Parents stored: {len(parents)}")
    print(f"Vector store: {CHROMA_PERSIST_PATH}")
    print(f"Docstore:     {DOCSTORE_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Index SOP PDFs with ParentDocumentRetriever.")
    parser.add_argument("--directory", default=str(SOP_DATA_DIR),
                        help="Directory containing SOP PDF files.")
    args = parser.parse_args()
    process_and_index_pdfs(args.directory)


if __name__ == "__main__":
    main()
