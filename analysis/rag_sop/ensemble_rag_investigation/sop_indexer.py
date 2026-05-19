"""Copy source RAG databases into this folder and (re)build BM25 corpora.

The ensemble investigation is intentionally self-contained: it does not parse
PDFs or call embeddings of its own. Instead it copies the artifacts produced
by sibling RAG pipelines and packages them with a per-mode BM25 corpus.

Modes
-----
basic         dense engine = basic_rag (MultiVectorRetriever, embedding=small).
              Source Chroma:    ../basic_rag/sop_documents/chroma_db
              Source docstore:  ../basic_rag/sop_documents/docstore
parent_child  dense engine = parent_child_rag winner (large, c400_o50).
              Source Chroma:    ../parent_child_rag_investigation/.../c400_o50
              Source docstore:  ../parent_child_rag/sop_documents/docstore

BM25 corpus
-----------
For each mode the corpus is the *parent* documents the dense retriever
ultimately returns. That way EnsembleRetriever's `id_key` dedup works:
both retrievers carry the same doc_id metadata.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pickle
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

from langchain_classic.storage import LocalFileStore, create_kv_docstore
from langchain_core.documents import Document


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
ensure_mode_directories = _CONFIG.ensure_mode_directories

BASIC_SOURCE_CHROMA_DIR = _CONFIG.BASIC_SOURCE_CHROMA_DIR
BASIC_SOURCE_DOCSTORE_DIR = _CONFIG.BASIC_SOURCE_DOCSTORE_DIR
PC_SOURCE_CHROMA_DIR = _CONFIG.PC_SOURCE_CHROMA_DIR
PC_SOURCE_DOCSTORE_DIR = _CONFIG.PC_SOURCE_DOCSTORE_DIR


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
    """Copy the source Chroma + docstore for `mode` into this folder."""
    ensure_mode_directories(mode)
    paths = MODE_PATHS[mode]
    src_chroma, src_docstore = _source_dirs_for(mode)

    print(f"\n[copy] mode={mode}")
    _copytree(src_chroma, paths["chroma_dir"], overwrite=overwrite)
    _copytree(src_docstore, paths["docstore_dir"], overwrite=overwrite)


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
            # Fall back to raw text (some entries are base64 image blobs).
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
    docstore_dir = MODE_PATHS[mode]["docstore_dir"]
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
    out_path: Path = MODE_PATHS[mode]["bm25_corpus"]
    with open(out_path, "wb") as f:
        pickle.dump(payload, f)
    print(f"  - wrote BM25 corpus ({len(payload)} parents) → {out_path}")


def setup_mode(mode: str, overwrite: bool = False) -> None:
    copy_source_databases(mode, overwrite=overwrite)
    print(f"[bm25] mode={mode}")
    build_bm25_corpus(mode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy source DBs into this folder and (re)build per-mode BM25 corpora."
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
