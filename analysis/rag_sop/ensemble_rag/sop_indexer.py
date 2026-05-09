import os
import argparse
import uuid
import pickle
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
from langchain_community.vectorstores import Chroma

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

SOP_DATA_DIR = _CONFIG.SOP_DATA_DIR
CHROMA_PERSIST_PATH = _CONFIG.CHROMA_PERSIST_PATH
BM25_CORPUS_PATH = _CONFIG.BM25_CORPUS_PATH
COLLECTION_NAME = _CONFIG.COLLECTION_NAME
ID_KEY = _CONFIG.ID_KEY
PDF_PROCESSING_CONFIG = _CONFIG.PDF_PROCESSING_CONFIG
LLM_CONFIG = _CONFIG.LLM_CONFIG
OPENAI_API_KEY = _CONFIG.OPENAI_API_KEY
ensure_directories = _CONFIG.ensure_directories


# Chroma caps a single upsert at 5461 records. Stay well under that.
_VECTORSTORE_BATCH = 400


def _require_openai_api_key() -> str:
    """Return the configured OpenAI API key or raise if missing."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured; cannot index SOP documents.")
    return OPENAI_API_KEY


def discover_pdf_files(directory: str) -> List[str]:
    """Discover all PDF files in the specified directory."""
    directory_path = Path(directory)
    if not directory_path.exists():
        raise FileNotFoundError(f"Directory {directory} does not exist")
    return sorted(str(p) for p in directory_path.glob("*.pdf") if p.is_file())


def _create_table_summarizer():
    """Chain that turns table HTML into a concise text summary."""
    api_key = _require_openai_api_key()
    template = ChatPromptTemplate.from_template(
        """You are an assistant tasked with summarizing tables.
Give a concise text summary that preserves the table's structure
(columns, units, rows), key values, and any thresholds or limits.

Respond only with the summary, no preamble.

Table HTML:

{element}
"""
    )
    llm = ChatOpenAI(
        model=LLM_CONFIG["summarization_model"], api_key=api_key, temperature=0
    )
    return template | llm | StrOutputParser()


def _create_image_describer():
    """Chain that turns a base64-encoded image into a textual description."""
    api_key = _require_openai_api_key()
    prompt = ChatPromptTemplate.from_messages([
        (
            "user",
            [
                {
                    "type": "text",
                    "text": (
                        "Describe this image in detail. It is part of a regulatory "
                        "or SOP document. Be specific about figures, diagrams, "
                        "flowcharts, plots, and any visible text or labels."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,{image}"},
                },
            ],
        )
    ])
    llm = ChatOpenAI(model=LLM_CONFIG["image_description_model"], api_key=api_key)
    return prompt | llm | StrOutputParser()


def _extract_image_b64s(chunk: Any) -> List[str]:
    """Pull base64 image payloads out of a CompositeElement chunk."""
    images: List[str] = []
    orig_elements = getattr(chunk.metadata, "orig_elements", None) or []
    for el in orig_elements:
        if "Image" in type(el).__name__:
            b64 = getattr(el.metadata, "image_base64", None)
            if b64:
                images.append(b64)
    return images


def build_documents(pdf_files: List[str]) -> List[Document]:
    """Parse each PDF and emit one plain-text Document per chunk.

    Tables become LLM summaries; images embedded inside a composite chunk
    become LLM descriptions appended to that chunk's text. The result is
    a flat list of Documents that both BM25 and a dense embedder index
    uniformly.
    """
    table_summarizer = _create_table_summarizer()
    image_describer = _create_image_describer()
    documents: List[Document] = []

    for pdf_file in pdf_files:
        filename = os.path.basename(pdf_file)
        print(f"\nParsing {filename}...")
        try:
            chunks = partition_pdf(filename=pdf_file, **PDF_PROCESSING_CONFIG)
        except Exception as exc:
            print(f"  - Failed to parse {pdf_file}: {exc}")
            continue

        kept = 0
        for chunk in chunks:
            ctype = type(chunk).__name__

            if "Table" in ctype:
                html = getattr(chunk.metadata, "text_as_html", None)
                if not html:
                    continue
                summary = table_summarizer.invoke({"element": html}).strip()
                if not summary:
                    continue
                text = f"[Table summary]\n{summary}"

            elif "CompositeElement" in ctype:
                parts: List[str] = []
                chunk_text = getattr(chunk, "text", None)
                if chunk_text and chunk_text.strip():
                    parts.append(chunk_text.strip())
                images = _extract_image_b64s(chunk)
                if images:
                    descriptions = image_describer.batch(
                        [{"image": b} for b in images]
                    )
                    for desc in descriptions:
                        desc = desc.strip()
                        if desc:
                            parts.append(f"[Image] {desc}")
                text = "\n\n".join(parts)
                if not text.strip():
                    continue

            else:
                continue

            documents.append(Document(
                page_content=text,
                metadata={
                    ID_KEY: str(uuid.uuid4()),
                    "source": filename,
                    "element_type": ctype,
                },
            ))
            kept += 1

        print(f"  - Built {kept} Documents from {filename}")

    return documents


def clear_existing_index() -> None:
    """Drop any prior Chroma collection and remove the pickled BM25 corpus."""
    api_key = _require_openai_api_key()
    try:
        Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=OpenAIEmbeddings(
                model="text-embedding-3-small", api_key=api_key
            ),
            persist_directory=str(CHROMA_PERSIST_PATH),
        ).delete_collection()
        print("Cleared existing Chroma collection")
    except Exception as exc:
        print(f"No existing Chroma collection to clear: {exc}")

    if BM25_CORPUS_PATH.exists():
        BM25_CORPUS_PATH.unlink()
        print("Cleared existing BM25 corpus")


def process_and_index_pdfs(directory: str) -> None:
    """Top-level: parse all PDFs, persist Chroma + BM25 corpus."""
    ensure_directories()
    clear_existing_index()

    pdf_files = discover_pdf_files(directory)
    print(f"Found {len(pdf_files)} PDF files to process:")
    for f in pdf_files:
        print(f"  - {os.path.basename(f)}")
    if not pdf_files:
        print("No PDF files found to process")
        return

    documents = build_documents(pdf_files)
    if not documents:
        print("No documents extracted; nothing to index.")
        return

    print(f"\nIndexing {len(documents)} Documents into Chroma + BM25...")

    # Dense side: persist into Chroma in batches (collection cap is 5461/upsert).
    api_key = _require_openai_api_key()
    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(
            model="text-embedding-3-small", api_key=api_key
        ),
        persist_directory=str(CHROMA_PERSIST_PATH),
    )
    total = len(documents)
    for i in range(0, total, _VECTORSTORE_BATCH):
        batch = documents[i : i + _VECTORSTORE_BATCH]
        # Use our doc_id as the Chroma id so EnsembleRetriever can dedupe on it.
        ids = [d.metadata[ID_KEY] for d in batch]
        vectorstore.add_documents(batch, ids=ids)
        print(f"  - Indexed {min(i + _VECTORSTORE_BATCH, total)}/{total} into Chroma")

    # Sparse side: pickle the corpus so the retriever can rebuild BM25 on load.
    payload = [{"page_content": d.page_content, "metadata": d.metadata} for d in documents]
    with open(BM25_CORPUS_PATH, "wb") as f:
        pickle.dump(payload, f)

    print("\n=== Indexing Complete ===")
    print(f"Documents indexed: {total}")
    print(f"Vector store: {CHROMA_PERSIST_PATH}")
    print(f"BM25 corpus:  {BM25_CORPUS_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Index SOP PDFs into the ensemble (BM25 + Chroma) RAG store."
    )
    parser.add_argument(
        "--directory",
        default=str(SOP_DATA_DIR),
        help="Directory containing SOP PDF files.",
    )
    args = parser.parse_args()
    process_and_index_pdfs(args.directory)


if __name__ == "__main__":
    main()
