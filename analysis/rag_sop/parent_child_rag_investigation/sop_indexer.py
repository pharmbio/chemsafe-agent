"""Re-embed children at a new chunk_size without re-parsing any PDFs.

Strategy
--------
The expensive parts of the original pipeline (Unstructured hi_res PDF parsing
and GPT image captioning) produce *parent* documents that live in a
`LocalFileStore` docstore at `../parent_child_rag/sop_documents/docstore/`.
Parents are independent of child chunk_size.

This script:
  1. Reads parents directly from that existing docstore.
  2. For each requested (chunk_size, chunk_overlap), splits parents with a
     fresh RecursiveCharacterTextSplitter, embeds the children, and writes
     them to a per-variant Chroma collection under
     `./sop_documents/chroma_db/c{chunk_size}_o{chunk_overlap}/`.
  3. Reuses parent ids from the source docstore so child→parent linkage is
     preserved without any docstore writes.

Only OpenAI embedding cost is incurred per variant. No PDF parsing, no image
captioning, no docstore mutation.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
from tempfile import gettempdir
from typing import Iterable, List, Sequence, Tuple

TMP_ROOT = Path(gettempdir()) / "safechem-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("NUMBA_CACHE_DIR", str(TMP_ROOT / "numba-cache"))
os.environ.setdefault("MPLCONFIGDIR", str(TMP_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(TMP_ROOT / "xdg-cache"))

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
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
OPENAI_API_KEY = _CONFIG.OPENAI_API_KEY
DEFAULT_CHUNK_SIZES = _CONFIG.DEFAULT_CHUNK_SIZES
default_overlap_for = _CONFIG.default_overlap_for
collection_name_for = _CONFIG.collection_name_for
chroma_path_for = _CONFIG.chroma_path_for
ensure_directories = _CONFIG.ensure_directories


def _require_openai_api_key() -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured; cannot embed children.")
    return OPENAI_API_KEY


def load_parents_from_docstore() -> Tuple[List[str], List[Document]]:
    """Read every parent Document from the source docstore.

    Returns parallel lists of (id, Document). Ids are the keys used by the
    original ParentDocumentRetriever, so reusing them keeps child→parent
    linkage consistent with the original pipeline's docstore.
    """
    if not SOURCE_DOCSTORE_DIR.exists():
        raise FileNotFoundError(
            f"Source docstore not found at {SOURCE_DOCSTORE_DIR}. "
            "Run ../parent_child_rag/sop_indexer.py first."
        )

    file_store = LocalFileStore(str(SOURCE_DOCSTORE_DIR))
    docstore = create_kv_docstore(file_store)

    ids = list(file_store.yield_keys())
    if not ids:
        raise RuntimeError(f"Source docstore at {SOURCE_DOCSTORE_DIR} is empty.")

    docs = docstore.mget(ids)
    paired = [(i, d) for i, d in zip(ids, docs) if d is not None]
    if not paired:
        raise RuntimeError("Failed to decode any parent documents from the docstore.")

    pids, pdocs = zip(*paired)
    print(f"Loaded {len(pdocs)} parent documents from {SOURCE_DOCSTORE_DIR}")
    return list(pids), list(pdocs)


def _split_children(
    parent_ids: Sequence[str],
    parents: Sequence[Document],
    splitter: RecursiveCharacterTextSplitter,
) -> List[Document]:
    """Mirror ParentDocumentRetriever.add_documents child generation.

    Each child carries metadata[ID_KEY]=<parent_id> plus the parent's own
    metadata, so retrieval can look up the parent by id.
    """
    children: List[Document] = []
    for pid, parent in zip(parent_ids, parents):
        sub_docs = splitter.split_documents([parent])
        for sub in sub_docs:
            md = dict(sub.metadata or {})
            md[ID_KEY] = pid
            children.append(Document(page_content=sub.page_content, metadata=md))
    return children


def reindex_variant(
    chunk_size: int,
    chunk_overlap: int | None = None,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    overwrite: bool = False,
) -> None:
    """Embed children for one (chunk_size, chunk_overlap, embedding_model) variant."""
    if chunk_overlap is None:
        chunk_overlap = default_overlap_for(chunk_size)
    if chunk_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap ({chunk_overlap}) must be smaller than chunk_size ({chunk_size})."
        )

    ensure_directories(chunk_size, chunk_overlap, embedding_model)
    api_key = _require_openai_api_key()

    collection = collection_name_for(chunk_size, chunk_overlap, embedding_model)
    persist_path = chroma_path_for(chunk_size, chunk_overlap, embedding_model)

    print(
        f"\n=== Variant embedding={embedding_model} "
        f"chunk_size={chunk_size} chunk_overlap={chunk_overlap} ==="
    )
    print(f"Collection: {collection}")
    print(f"Chroma path: {persist_path}")

    embeddings = OpenAIEmbeddings(model=embedding_model, api_key=api_key)
    vectorstore = Chroma(
        collection_name=collection,
        embedding_function=embeddings,
        persist_directory=str(persist_path),
    )

    existing = vectorstore._collection.count()
    if existing > 0:
        if not overwrite:
            print(
                f"Skipping: collection already has {existing} embeddings. "
                f"Pass --overwrite to rebuild."
            )
            return
        print(f"Overwrite requested; deleting existing collection ({existing} embeddings)...")
        vectorstore.delete_collection()
        vectorstore = Chroma(
            collection_name=collection,
            embedding_function=embeddings,
            persist_directory=str(persist_path),
        )

    parent_ids, parents = load_parents_from_docstore()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    children = _split_children(parent_ids, parents, splitter)
    print(f"Built {len(children)} child chunks from {len(parents)} parents.")

    # Chroma single-upsert cap is 5461. Batch children directly.
    BATCH = 4000
    for i in range(0, len(children), BATCH):
        batch = children[i : i + BATCH]
        vectorstore.add_documents(batch)
        print(f"  - embedded {min(i + BATCH, len(children))}/{len(children)} children")

    print(f"Done. Collection '{collection}' now has {vectorstore._collection.count()} embeddings.")


def reindex_many(
    chunk_sizes: Iterable[int],
    overlap: int | None = None,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    overwrite: bool = False,
) -> None:
    for cs in chunk_sizes:
        ov = overlap if overlap is not None else default_overlap_for(cs)
        reindex_variant(
            chunk_size=cs,
            chunk_overlap=ov,
            embedding_model=embedding_model,
            overwrite=overwrite,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-embed children at one or more chunk sizes, reusing the "
            "parents already stored by ../parent_child_rag."
        )
    )
    parser.add_argument(
        "--chunk-size", type=int, action="append",
        help="Child chunk_size. Repeat for multiple variants. "
             f"Default sweep: {DEFAULT_CHUNK_SIZES}.",
    )
    parser.add_argument(
        "--chunk-overlap", type=int, default=None,
        help="Override overlap (applied to every chunk_size). "
             "Default = chunk_size * 0.125.",
    )
    parser.add_argument(
        "--embedding-model", type=str, default=DEFAULT_EMBEDDING_MODEL,
        help=f"OpenAI embedding model id. Default: {DEFAULT_EMBEDDING_MODEL}. "
             "Each model gets its own Chroma collection/path.",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Delete and rebuild a variant's collection if it already exists.",
    )
    args = parser.parse_args()

    chunk_sizes = args.chunk_size if args.chunk_size else DEFAULT_CHUNK_SIZES
    reindex_many(
        chunk_sizes,
        overlap=args.chunk_overlap,
        embedding_model=args.embedding_model,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
