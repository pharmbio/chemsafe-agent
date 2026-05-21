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

import numpy as np
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Dict, List, Tuple

TMP_ROOT = Path(gettempdir()) / "safechem-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(TMP_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(TMP_ROOT / "xdg-cache"))

from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_classic.retrievers.multi_vector import MultiVectorRetriever
from langchain_classic.storage import LocalFileStore, create_kv_docstore
from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_core.stores import BaseStore
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


CURRENT_DIR = Path(__file__).resolve().parent


def min_max_scaling(scores: List[float]) -> List[float]:
    if not scores:
        return []
    arr = np.asarray(scores, dtype=float)
    s_min = float(arr.min())
    s_max = float(arr.max())
    if s_max == s_min:
        return [1.0] * len(scores)
    return ((arr - s_min) / (s_max - s_min)).tolist()


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
        mode: str = "parent_child",
        score_threshold: float | None = None,
        max_results: int | None = None,
        fetch_k: int | None = None,
        search_type: str | None = None,
        bm25_weight: float | None = None,
        dense_weight: float | None = None,
        bm25_k1: float | None = None,
        bm25_b: float | None = None,
        fuse_func: str = "rrf",
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

        self.fuse_func = fuse_func
        self.rrf_c = ENSEMBLE_CONFIG["rrf_c"]

        self.bm25_retriever: BM25Retriever | None = None
        self.dense_retriever: Any = None
        self.dense_vectorstore: Chroma | None = None
        self.dense_docstore: BaseStore[str, Document] | None = None
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

        return retriever, count, vectorstore, docstore

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
        dense_retriever, dense_count, vectorstore, docstore = self._build_dense_retriever()
        self.dense_vectorstore = vectorstore
        self.dense_docstore = docstore

        bm25_docs = self._load_bm25_corpus()
        bm25_retriever = BM25Retriever.from_documents(
            bm25_docs,
            bm25_params={"k1": self.bm25_k1, "b": self.bm25_b},
        )
        bm25_retriever.k = self.fetch_k

        self.bm25_retriever = bm25_retriever
        self.dense_retriever = dense_retriever

        print(
            f"Manual ensemble retriever ready (mode={self.mode}) — "
            f"dense={dense_count} ({self._paths['retriever_kind']}, "
            f"{self._paths['embedding_model']}), "
            f"sparse={len(bm25_docs)} BM25 docs (k1={self.bm25_k1}, b={self.bm25_b}), "
            f"weights=[bm25={self.bm25_weight}, dense={self.dense_weight}], "
            f"fuse_func={self.fuse_func}, "
            f"fetch_k={self.fetch_k}, max_results={self.max_results}"
        )

    @staticmethod
    def _doc_key(doc: Document) -> str:
        """Stable identity for fusion dedup; prefer ID_KEY, fall back to content."""
        meta = doc.metadata or {}
        key = meta.get(ID_KEY)
        if key is not None:
            return str(key)
        return f"__content__::{hash(doc.page_content)}"

    def fuse(
        self,
        ranked_lists: List[List[Document]] | None = None,
        weights: List[float] | None = None,
        scored_lists: List[List[Tuple[Document, float]]] | None = None,
    ) -> List[Document]:
        """Fuse multiple per-retriever result lists into one ordered list.

        Rank-based fusers (rrf/borda/log_rank/condorcet) consume `ranked_lists`.
        Score-based fusers (combsum) consume `scored_lists` (each entry a
        list of (Document, raw_score) tuples, higher = better).
        """
        if weights is None:
            raise ValueError("weights is required.")

        if self.fuse_func in ("combsum", "isrc", "log_odds"):
            if scored_lists is None:
                raise ValueError(f"fuse_func='{self.fuse_func}' requires scored_lists.")
            if len(scored_lists) != len(weights):
                raise ValueError("scored_lists and weights must have the same length.")
            if self.fuse_func == "combsum":
                return self._weighted_combsum(scored_lists, weights)
            if self.fuse_func == "isrc":
                return self._weighted_isrc(scored_lists, weights)
            return self._weighted_log_odds(scored_lists, weights)

        if ranked_lists is None:
            raise ValueError(f"fuse_func='{self.fuse_func}' requires ranked_lists.")
        if len(ranked_lists) != len(weights):
            raise ValueError("ranked_lists and weights must have the same length.")

        if self.fuse_func == "rrf":
            return self._weighted_rrf(ranked_lists, weights)
        elif self.fuse_func == "borda":
            return self._weighted_borda(ranked_lists, weights)
        elif self.fuse_func == "log_rank":
            return self._weighted_log_rank(ranked_lists, weights)
        elif self.fuse_func == "condorcet":
            return self._weighted_condorcet(ranked_lists, weights)
        elif self.fuse_func == "linear":
            # Placeholder: weighted linear combination of normalized scores.
            raise NotImplementedError("fuse_func='linear' not implemented yet.")
        else:
            raise ValueError(f"Unknown fuse_func '{self.fuse_func}'.")

    def _weighted_rrf(
        self,
        ranked_lists: List[List[Document]],
        weights: List[float],
    ) -> List[Document]:
        """Weighted Reciprocal Rank Fusion.

        score(d) = sum_i  w_i / (c + rank_i(d))   (rank starts at 1)
        """
        c = self.rrf_c
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}

        for docs, w in zip(ranked_lists, weights):
            for rank, doc in enumerate(docs, start=1):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w / (c + rank)
                if key not in first_seen:
                    first_seen[key] = doc

        ordered_keys = sorted(scores, key=scores.get, reverse=True)
        return [first_seen[k] for k in ordered_keys]

    def _weighted_borda(
        self,
        ranked_lists: List[List[Document]],
        weights: List[float],
    ) -> List[Document]:
        """Weighted Borda count fusion.

        For each ranker i, a doc at rank r (1-indexed) contributes
        w_i * (fetch_k - r + 1). Docs absent from a ranker get 0 from it.
        """
        n = self.fetch_k
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}

        for docs, w in zip(ranked_lists, weights):
            for rank, doc in enumerate(docs, start=1):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w * (n - rank + 1)
                if key not in first_seen:
                    first_seen[key] = doc

        ordered_keys = sorted(scores, key=scores.get, reverse=True)
        return [first_seen[k] for k in ordered_keys]

    def _weighted_log_rank(
        self,
        ranked_lists: List[List[Document]],
        weights: List[float],
    ) -> List[Document]:
        """Weighted log-rank fusion.

        score(d) = sum_i  -w_i * log10(rank_i(d))   (rank starts at 1)

        Rank 1 contributes 0; deeper ranks contribute increasingly negative
        amounts, so higher score = better. Docs absent from a ranker get 0
        from it.
        """
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}

        for docs, w in zip(ranked_lists, weights):
            for rank, doc in enumerate(docs, start=1):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) - w * float(np.log10(rank))
                if key not in first_seen:
                    first_seen[key] = doc

        ordered_keys = sorted(scores, key=scores.get, reverse=True)
        return [first_seen[k] for k in ordered_keys]

    def _weighted_condorcet(
        self,
        ranked_lists: List[List[Document]],
        weights: List[float],
    ) -> List[Document]:
        """Weighted Condorcet fusion.

        Pairwise weighted vote:
            Condorcet(d_i, d_j) = sum_r  w_r * 1[rank_r(d_i) < rank_r(d_j)]

        d_i wins the pair (d_i, d_j) iff Condorcet(d_i, d_j) > Condorcet(d_j, d_i).
        Each doc's final score = number of pairwise wins.

        Docs missing from a ranker are treated as ranked worse than any present
        doc in that ranker (rank = +inf), contributing 0 to its own vote there.
        """
        first_seen: Dict[str, Document] = {}
        rank_maps: List[Dict[str, int]] = []
        for docs in ranked_lists:
            rmap: Dict[str, int] = {}
            for rank, doc in enumerate(docs, start=1):
                key = self._doc_key(doc)
                if key not in rmap:
                    rmap[key] = rank
                if key not in first_seen:
                    first_seen[key] = doc
            rank_maps.append(rmap)

        keys = list(first_seen.keys())
        wins: Dict[str, int] = {k: 0 for k in keys}

        for a_idx in range(len(keys)):
            for b_idx in range(a_idx + 1, len(keys)):
                a, b = keys[a_idx], keys[b_idx]
                vote_a = 0.0
                vote_b = 0.0
                for rmap, w in zip(rank_maps, weights):
                    ra = rmap.get(a, np.inf)
                    rb = rmap.get(b, np.inf)
                    if ra < rb:
                        vote_a += w
                    elif rb < ra:
                        vote_b += w
                if vote_a > vote_b:
                    wins[a] += 1
                elif vote_b > vote_a:
                    wins[b] += 1

        ordered_keys = sorted(keys, key=lambda k: wins[k], reverse=True)
        return [first_seen[k] for k in ordered_keys]

    def _weighted_combsum(
        self,
        scored_lists: List[List[Tuple[Document, float]]],
        weights: List[float],
    ) -> List[Document]:
        """Weighted CombSUM over min-max normalized scores.

            score(d) = sum_i  w_i * normalized_score_i(d)

        Each arm's raw scores are min-max scaled to [0, 1] independently, then
        summed with the arm's weight. Docs absent from an arm contribute 0
        from that arm.
        """
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}

        for scored, w in zip(scored_lists, weights):
            if not scored:
                continue
            raw = [s for _, s in scored]
            normalized = min_max_scaling(raw)
            for (doc, _), ns in zip(scored, normalized):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w * ns
                if key not in first_seen:
                    first_seen[key] = doc

        ordered_keys = sorted(scores, key=scores.get, reverse=True)
        return [first_seen[k] for k in ordered_keys]

    def _weighted_isrc(
        self,
        scored_lists: List[List[Tuple[Document, float]]],
        weights: List[float],
    ) -> List[Document]:
        """Inverse-Square Rank with Score (ISRC).

            score(d) = sum_i  w_i * normalized_score_i(d) / rank_i(d)^2

        Each arm's raw scores are min-max scaled to [0, 1] independently;
        rank starts at 1 (best). Docs absent from an arm contribute 0 from it.
        """
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}

        for scored, w in zip(scored_lists, weights):
            if not scored:
                continue
            raw = [s for _, s in scored]
            normalized = min_max_scaling(raw)
            for rank, ((doc, _), ns) in enumerate(zip(scored, normalized), start=1):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w * ns / (rank * rank)
                if key not in first_seen:
                    first_seen[key] = doc

        ordered_keys = sorted(scores, key=scores.get, reverse=True)
        return [first_seen[k] for k in ordered_keys]

    def _weighted_log_odds(
        self,
        scored_lists: List[List[Tuple[Document, float]]],
        weights: List[float],
    ) -> List[Document]:
        """Weighted log-odds fusion.

            score(d) = sum_i  w_i * log( ns_i(d) / (1 - ns_i(d)) )

        where ns_i is the min-max normalized score from arm i. Normalized
        values are clipped into (eps, 1-eps) to keep the log and denominator
        finite when min-max produces exactly 0 or 1.
        """
        eps = 1e-6
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}

        for scored, w in zip(scored_lists, weights):
            if not scored:
                continue
            raw = [s for _, s in scored]
            normalized = min_max_scaling(raw)
            clipped = np.clip(np.asarray(normalized, dtype=float), eps, 1.0 - eps)
            log_odds = np.log(clipped / (1.0 - clipped))
            for (doc, _), lo in zip(scored, log_odds):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w * float(lo)
                if key not in first_seen:
                    first_seen[key] = doc

        ordered_keys = sorted(scores, key=scores.get, reverse=True)
        return [first_seen[k] for k in ordered_keys]

    def _bm25_with_scores(self, query: str) -> List[Tuple[Document, float]]:
        """Top fetch_k (Document, BM25 score) pairs.

        BM25Retriever doesn't expose scored results, but its `vectorizer`
        (rank_bm25 BM25Okapi) and `preprocess_func` are public attributes; we
        reuse them to recover the scores the retriever computes internally.
        """
        retriever = self.bm25_retriever
        if retriever is None:
            raise RuntimeError("BM25 retriever not initialized.")
        processed = retriever.preprocess_func(query)
        all_scores = np.asarray(retriever.vectorizer.get_scores(processed), dtype=float)
        top_idx = np.argsort(all_scores)[::-1][: self.fetch_k]
        return [(retriever.docs[int(i)], float(all_scores[int(i)])) for i in top_idx]

    def _dense_with_scores(self, query: str) -> List[Tuple[Document, float]]:
        """Top fetch_k (parent Document, similarity score) pairs.

        ParentDocumentRetriever / MultiVectorRetriever don't surface per-parent
        scores, so we call the vectorstore's `similarity_search_with_score`
        directly on the child collection, convert distance → similarity
        (-distance, higher = better), aggregate per parent doc_id by max
        similarity, then load parents from the docstore.
        """
        vectorstore = self.dense_vectorstore
        docstore = self.dense_docstore
        if vectorstore is None or docstore is None:
            raise RuntimeError("Dense vectorstore/docstore not initialized.")

        # Over-fetch children so distinct parents can reach fetch_k after dedup.
        k_children = max(self.fetch_k * 4, self.fetch_k)
        children = vectorstore.similarity_search_with_score(query, k=k_children)

        parent_best: Dict[str, float] = {}
        parent_order: List[str] = []
        for child_doc, distance in children:
            parent_id = (child_doc.metadata or {}).get(ID_KEY)
            if parent_id is None:
                continue
            sim = -float(distance)
            if parent_id not in parent_best:
                parent_best[parent_id] = sim
                parent_order.append(parent_id)
            elif sim > parent_best[parent_id]:
                parent_best[parent_id] = sim
            if len(parent_order) >= self.fetch_k:
                break

        parent_ids = parent_order[: self.fetch_k]
        parent_docs = docstore.mget(parent_ids)

        out: List[Tuple[Document, float]] = []
        for pid, pdoc in zip(parent_ids, parent_docs):
            if pdoc is None:
                continue
            out.append((pdoc, parent_best[pid]))
        return out

    def query(self, query: str) -> List[Document]:
        if self.bm25_retriever is None or self.dense_retriever is None:
            raise RuntimeError("Retriever not initialized.")

        if self.fuse_func in ("combsum", "isrc", "log_odds"):
            bm25_scored = self._bm25_with_scores(query)
            dense_scored = self._dense_with_scores(query)
            fused = self.fuse(
                weights=[self.bm25_weight, self.dense_weight],
                scored_lists=[bm25_scored, dense_scored],
            )
        else:
            bm25_results = self.bm25_retriever.invoke(query)[: self.fetch_k]
            dense_results = self.dense_retriever.invoke(query)[: self.fetch_k]
            fused = self.fuse(
                ranked_lists=[bm25_results, dense_results],
                weights=[self.bm25_weight, self.dense_weight],
            )
        return fused[: self.max_results]
