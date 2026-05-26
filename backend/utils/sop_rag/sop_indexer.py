"""Copy source RAG databases into MEMORY_ROOT and (re)build BM25 corpora.

This indexer does not parse PDFs or call embeddings of its own. It copies the
artifacts produced by sibling RAG pipelines under analysis/rag_sop/ and
packages them with a per-mode BM25 corpus, under MEMORY_ROOT/sop_documents/.

Modes
-----
basic         dense engine = basic_rag (MultiVectorRetriever, embedding=small).
parent_child  dense engine = parent_child_rag winner (large, c400_o50).

BM25 corpus
-----------
For each mode the corpus is the *parent* documents the dense retriever
ultimately returns; both arms therefore carry the same doc_id metadata,
which is required for EnsembleRetriever's id_key dedup to work.
"""

from __future__ import annotations

import argparse
import json
import pickle
import shutil
from pathlib import Path
from typing import List, Tuple

from langchain_classic.storage import LocalFileStore, create_kv_docstore
from langchain_core.documents import Document

from backend.utils.sop_rag.config import (
    BASIC_SOURCE_CHROMA_DIR,
    BASIC_SOURCE_DOCSTORE_DIR,
    ID_KEY,
    MODE_PATHS,
    MODES,
    PC_SOURCE_CHROMA_DIR,
    PC_SOURCE_DOCSTORE_DIR,
    ensure_mode_directories,
)


def _source_dirs_for(mode: str) -> Tuple[Path, Path]:
    if mode == "basic":
        return BASIC_SOURCE_CHROMA_DIR, BASIC_SOURCE_DOCSTORE_DIR
    if mode == "parent_child":
        return PC_SOURCE_CHROMA_DIR, PC_SOURCE_DOCSTORE_DIR
    raise ValueError(f"Unknown mode '{mode}'")


def _copytree(src: Path, dst: Path, overwrite: bool) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")
    if dst.exists():
        if not overwrite:
            print(f"  - {dst} already exists; pass --overwrite to replace.")
            return
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"  - copied {src} → {dst}")


def copy_source_databases(mode: str, overwrite: bool = False) -> None:
    """Copy the source Chroma + docstore for `mode` into MEMORY_ROOT."""
    ensure_mode_directories(mode)
    paths = MODE_PATHS[mode]
    src_chroma, src_docstore = _source_dirs_for(mode)

    print(f"\n[copy] mode={mode}")
    _copytree(src_chroma, Path(paths["chroma_dir"]), overwrite=overwrite)
    _copytree(src_docstore, Path(paths["docstore_dir"]), overwrite=overwrite)


def _load_basic_parents(docstore_dir: Path) -> List[Document]:
    """basic_rag stores Documents as raw JSON files under LocalFileStore."""
    store = LocalFileStore(str(docstore_dir))
    keys = list(store.yield_keys())
    raw_values = store.mget(keys)
    docs: List[Document] = []
    for key, value in zip(keys, raw_values):
        if value is None:
            continue
        try:
            payload = json.loads(value.decode("utf-8"))
            content = payload.get("page_content", "")
            metadata = dict(payload.get("metadata", {}))
        except (json.JSONDecodeError, UnicodeDecodeError):
            content = value.decode("utf-8", errors="replace")
            metadata = {}
        metadata.setdefault(ID_KEY, key)
        docs.append(Document(page_content=content, metadata=metadata))
    return docs


def _load_parent_child_parents(docstore_dir: Path) -> List[Document]:
    """parent_child docstore is wrapped with create_kv_docstore()."""
    file_store = LocalFileStore(str(docstore_dir))
    docstore = create_kv_docstore(file_store)
    keys = list(file_store.yield_keys())
    values = docstore.mget(keys)
    docs: List[Document] = []
    for key, doc in zip(keys, values):
        if doc is None:
            continue
        metadata = dict(doc.metadata or {})
        metadata.setdefault(ID_KEY, key)
        docs.append(Document(page_content=doc.page_content, metadata=metadata))
    return docs


def _load_parents_for_mode(mode: str) -> List[Document]:
    docstore_dir = Path(MODE_PATHS[mode]["docstore_dir"])
    if not docstore_dir.exists():
        raise FileNotFoundError(
            f"Docstore not yet copied for mode '{mode}' at {docstore_dir}. "
            f"Run with --copy first."
        )
    if mode == "basic":
        return _load_basic_parents(docstore_dir)
    if mode == "parent_child":
        return _load_parent_child_parents(docstore_dir)
    raise ValueError(f"Unknown mode '{mode}'")


def build_bm25_corpus(mode: str) -> None:
    """Pickle parent (page_content, metadata) tuples for BM25 to rebuild from."""
    parents = _load_parents_for_mode(mode)
    if not parents:
        raise RuntimeError(f"No parent documents found for mode '{mode}'.")

    payload = [
        {"page_content": d.page_content, "metadata": d.metadata} for d in parents
    ]
    out_path = Path(MODE_PATHS[mode]["bm25_corpus"])
    with open(out_path, "wb") as f:
        pickle.dump(payload, f)
    print(f"  - wrote BM25 corpus ({len(payload)} parents) → {out_path}")


def setup_mode(mode: str, overwrite: bool = False) -> None:
    copy_source_databases(mode, overwrite=overwrite)
    print(f"[bm25] mode={mode}")
    build_bm25_corpus(mode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy source DBs into MEMORY_ROOT and (re)build per-mode BM25 corpora."
    )
    parser.add_argument(
        "--mode",
        choices=list(MODES) + ["all"],
        default="all",
        help="Which ensemble mode(s) to set up.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace already-copied Chroma/docstore folders.",
    )
    parser.add_argument(
        "--bm25-only",
        action="store_true",
        help="Skip the copy step and only rebuild the BM25 corpus from already-copied docstores.",
    )
    args = parser.parse_args()

    targets = MODES if args.mode == "all" else (args.mode,)
    for m in targets:
        if args.bm25_only:
            print(f"\n[bm25-only] mode={m}")
            build_bm25_corpus(m)
        else:
            setup_mode(m, overwrite=args.overwrite)

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
