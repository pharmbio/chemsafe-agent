"""Double-fusion retriever.

Two layers of fusion:

    Inner layer: BM25 (sparse) + Chroma (dense) for a single query, fused with
                 a configurable `fuse_func`. Mirrors `ensemble_rag`'s ensemble
                 (same modes, same fuse functions, same knobs).

    Outer layer: an LLM expands the user's query into N variants; each variant
                 runs through the inner layer; the per-variant fused lists are
                 then combined with a configurable `outer_fuse_func`.

Both layers expose the same set of fuse functions (rrf / borda / log_rank /
condorcet / combsum / isrc / log_odds / cross-encoder model id). Score-based
outer fusion reuses the inner fusion score; cross-encoder outer fusion
rescore the union against the *original* user query.
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
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
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
    spec = importlib.util.spec_from_file_location("double_fusion_config", config_path)
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
LLM_CONFIG = _CONFIG.LLM_CONFIG
DOUBLE_FUSION_CONFIG = _CONFIG.DOUBLE_FUSION_CONFIG
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
    """Decodes basic_rag's JSON-encoded LocalFileStore values into Documents."""

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
    """Re-attaches the docstore key as metadata[id_key] on every read."""

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


# Fuse-function families. The outer and inner layers both pick from this set.
_RANK_FUSERS = ("rrf", "borda", "log_rank", "condorcet")
_SCORE_FUSERS = ("combsum", "isrc", "log_odds")
_CROSS_ENCODER_MODELS = {
    "BAAI/bge-reranker-base",
    "BAAI/bge-reranker-large",
    "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "Qwen/Qwen3-Reranker-0.6B",
}


class DoubleFusionSOPRetriever:
    """Two-layer fusion retriever: variant expansion ▸ (BM25 + dense) ensemble ▸ outer fusion."""

    CROSS_ENCODER_MODELS: set = _CROSS_ENCODER_MODELS

    def __init__(
        self,
        mode: str = "parent_child",
        # Inner-layer (BM25 + dense) layer args
        score_threshold: float | None = None,
        max_results: int | None = None,
        fetch_k: int | None = None,
        search_type: str | None = None,
        bm25_weight: float | None = None,
        dense_weight: float | None = None,
        bm25_k1: float | None = None,
        bm25_b: float | None = None,
        fuse_func: str = "rrf",
        # Outer-layer (across query variants) args
        num_queries: int | None = None,
        outer_fuse_func: str | None = None,
        outer_max_results: int | None = None,
        outer_weights: List[float] | None = None,
        query_llm_model: str | None = None,
        query_llm_temperature: float | None = None,
        query_llm: Any | None = None,
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

        # Outer-layer config
        self.num_queries = (
            DOUBLE_FUSION_CONFIG["num_queries"] if num_queries is None else num_queries
        )
        if self.num_queries < 1:
            raise ValueError("num_queries must be at least 1.")
        self.outer_fuse_func = (
            DOUBLE_FUSION_CONFIG["outer_fuse_func"]
            if outer_fuse_func is None
            else outer_fuse_func
        )
        self.outer_max_results = (
            DOUBLE_FUSION_CONFIG["outer_max_results"]
            if outer_max_results is None
            else outer_max_results
        )
        if self.outer_max_results < 1:
            raise ValueError("outer_max_results must be at least 1.")
        self._outer_weights_override = (
            outer_weights
            if outer_weights is not None
            else DOUBLE_FUSION_CONFIG["outer_weights"]
        )
        self.query_llm_model = (
            query_llm_model
            if query_llm_model is not None
            else LLM_CONFIG.get("query_expansion_model", "gpt-5.4-nano")
        )
        self.query_llm_temperature = (
            query_llm_temperature
            if query_llm_temperature is not None
            else LLM_CONFIG.get("query_expansion_temperature", 0)
        )
        self._query_llm = query_llm  # optional injected LLM (overrides model name)

        self.bm25_retriever: BM25Retriever | None = None
        self.dense_retriever: Any = None
        self.dense_vectorstore: Chroma | None = None
        self.dense_docstore: BaseStore[str, Document] | None = None
        self._cross_encoder: Any = None
        self._initialize()

    # ─────────────────────────────────────────────────────────────────────
    # Initialization
    # ─────────────────────────────────────────────────────────────────────
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
            f"Double-fusion retriever ready (mode={self.mode}) — "
            f"dense={dense_count} ({self._paths['retriever_kind']}, "
            f"{self._paths['embedding_model']}), "
            f"sparse={len(bm25_docs)} BM25 docs (k1={self.bm25_k1}, b={self.bm25_b}), "
            f"inner_fuse={self.fuse_func} weights=[bm25={self.bm25_weight}, dense={self.dense_weight}], "
            f"outer_fuse={self.outer_fuse_func} num_queries={self.num_queries}, "
            f"fetch_k={self.fetch_k}, inner_max={self.max_results}, outer_max={self.outer_max_results}"
        )

    @staticmethod
    def _doc_key(doc: Document) -> str:
        """Stable identity for fusion dedup; prefer ID_KEY, fall back to content."""
        meta = doc.metadata or {}
        key = meta.get(ID_KEY)
        if key is not None:
            return str(key)
        return f"__content__::{hash(doc.page_content)}"


    # fuse method (shared by inner and outer layers)
    def fuse_scored(
        self,
        fuse_func: str,
        ranked_lists: List[List[Document]] | None = None,
        weights: List[float] | None = None,
        scored_lists: List[List[Tuple[Document, float]]] | None = None,
        query: str | None = None,
    ) -> List[Tuple[Document, float]]:
        """Fuse multiple per-source lists into one ordered scored list.

        Rank-based fusers (rrf/borda/log_rank/condorcet) consume `ranked_lists`.
        Score-based fusers (combsum/isrc/log_odds) consume `scored_lists`.
        Cross-encoder reranker fusers consume `ranked_lists` + `query`.
        """
        if fuse_func in _CROSS_ENCODER_MODELS:
            if ranked_lists is None:
                raise ValueError(f"fuse_func='{fuse_func}' requires ranked_lists.")
            if not query:
                raise ValueError(f"fuse_func='{fuse_func}' requires a query.")
            return self._cross_encoder_rerank(ranked_lists, query, fuse_func)

        if weights is None:
            raise ValueError("weights is required.")

        if fuse_func in _SCORE_FUSERS:
            if scored_lists is None:
                raise ValueError(f"fuse_func='{fuse_func}' requires scored_lists.")
            if len(scored_lists) != len(weights):
                raise ValueError("scored_lists and weights must have the same length.")
            if fuse_func == "combsum":
                return self._weighted_combsum(scored_lists, weights)
            if fuse_func == "isrc":
                return self._weighted_isrc(scored_lists, weights)
            return self._weighted_log_odds(scored_lists, weights)

        if ranked_lists is None:
            raise ValueError(f"fuse_func='{fuse_func}' requires ranked_lists.")
        if len(ranked_lists) != len(weights):
            raise ValueError("ranked_lists and weights must have the same length.")

        if fuse_func == "rrf":
            return self._weighted_rrf(ranked_lists, weights)
        if fuse_func == "borda":
            return self._weighted_borda(ranked_lists, weights)
        if fuse_func == "log_rank":
            return self._weighted_log_rank(ranked_lists, weights)
        if fuse_func == "condorcet":
            return self._weighted_condorcet(ranked_lists, weights)
        if fuse_func == "linear":
            raise NotImplementedError("fuse_func='linear' not implemented yet.")
        raise ValueError(f"Unknown fuse_func '{fuse_func}'.")

    def fuse(
        self,
        ranked_lists: List[List[Document]] | None = None,
        weights: List[float] | None = None,
        scored_lists: List[List[Tuple[Document, float]]] | None = None,
        query: str | None = None,
    ) -> List[Document]:
        """Document-only convenience wrapper around `fuse_scored` using `self.fuse_func`."""
        scored = self.fuse_scored(
            self.fuse_func,
            ranked_lists=ranked_lists,
            weights=weights,
            scored_lists=scored_lists,
            query=query,
        )
        return [doc for doc, _ in scored]

    # Rank-based fuse functions
    def _weighted_rrf(self, ranked_lists, weights) -> List[Tuple[Document, float]]:
        c = self.rrf_c
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}
        for docs, w in zip(ranked_lists, weights):
            for rank, doc in enumerate(docs, start=1):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w / (c + rank)
                first_seen.setdefault(key, doc)
        ordered = sorted(scores, key=scores.get, reverse=True)
        return [(first_seen[k], scores[k]) for k in ordered]

    def _weighted_borda(self, ranked_lists, weights) -> List[Tuple[Document, float]]:
        n = self.fetch_k
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}
        for docs, w in zip(ranked_lists, weights):
            for rank, doc in enumerate(docs, start=1):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w * (n - rank + 1)
                first_seen.setdefault(key, doc)
        ordered = sorted(scores, key=scores.get, reverse=True)
        return [(first_seen[k], scores[k]) for k in ordered]

    def _weighted_log_rank(self, ranked_lists, weights) -> List[Tuple[Document, float]]:
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}
        for docs, w in zip(ranked_lists, weights):
            for rank, doc in enumerate(docs, start=1):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) - w * float(np.log10(rank))
                first_seen.setdefault(key, doc)
        ordered = sorted(scores, key=scores.get, reverse=True)
        return [(first_seen[k], scores[k]) for k in ordered]

    def _weighted_condorcet(self, ranked_lists, weights) -> List[Tuple[Document, float]]:
        first_seen: Dict[str, Document] = {}
        rank_maps: List[Dict[str, int]] = []
        for docs in ranked_lists:
            rmap: Dict[str, int] = {}
            for rank, doc in enumerate(docs, start=1):
                key = self._doc_key(doc)
                rmap.setdefault(key, rank)
                first_seen.setdefault(key, doc)
            rank_maps.append(rmap)

        keys = list(first_seen.keys())
        wins: Dict[str, int] = {k: 0 for k in keys}
        for a_idx in range(len(keys)):
            for b_idx in range(a_idx + 1, len(keys)):
                a, b = keys[a_idx], keys[b_idx]
                vote_a = vote_b = 0.0
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
        ordered = sorted(keys, key=lambda k: wins[k], reverse=True)
        return [(first_seen[k], float(wins[k])) for k in ordered]

    # Score-based fuse_functions
    def _weighted_combsum(self, scored_lists, weights) -> List[Tuple[Document, float]]:
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}
        for scored, w in zip(scored_lists, weights):
            if not scored:
                continue
            normalized = min_max_scaling([s for _, s in scored])
            for (doc, _), ns in zip(scored, normalized):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w * ns
                first_seen.setdefault(key, doc)
        ordered = sorted(scores, key=scores.get, reverse=True)
        return [(first_seen[k], scores[k]) for k in ordered]

    def _weighted_isrc(self, scored_lists, weights) -> List[Tuple[Document, float]]:
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}
        for scored, w in zip(scored_lists, weights):
            if not scored:
                continue
            normalized = min_max_scaling([s for _, s in scored])
            for rank, ((doc, _), ns) in enumerate(zip(scored, normalized), start=1):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w * ns / (rank * rank)
                first_seen.setdefault(key, doc)
        ordered = sorted(scores, key=scores.get, reverse=True)
        return [(first_seen[k], scores[k]) for k in ordered]

    def _weighted_log_odds(self, scored_lists, weights) -> List[Tuple[Document, float]]:
        eps = 1e-6
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}
        for scored, w in zip(scored_lists, weights):
            if not scored:
                continue
            normalized = min_max_scaling([s for _, s in scored])
            clipped = np.clip(np.asarray(normalized, dtype=float), eps, 1.0 - eps)
            log_odds = np.log(clipped / (1.0 - clipped))
            for (doc, _), lo in zip(scored, log_odds):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + w * float(lo)
                first_seen.setdefault(key, doc)
        ordered = sorted(scores, key=scores.get, reverse=True)
        return [(first_seen[k], scores[k]) for k in ordered]

    # Cross-encoder reranker
    def _get_cross_encoder(self, model_name: str):
        if self._cross_encoder is None or getattr(self, "_cross_encoder_name", None) != model_name:
            from sentence_transformers import CrossEncoder
            self._cross_encoder = CrossEncoder(model_name)
            self._cross_encoder_name = model_name
        return self._cross_encoder

    def _cross_encoder_rerank(
        self,
        ranked_lists: List[List[Document]],
        query: str,
        model_name: str,
    ) -> List[Tuple[Document, float]]:
        unique_docs: Dict[str, Document] = {}
        for docs in ranked_lists:
            for doc in docs:
                key = self._doc_key(doc)
                unique_docs.setdefault(key, doc)
        if not unique_docs:
            return []
        unique_keys = list(unique_docs.keys())
        pairs = [(query, unique_docs[k].page_content) for k in unique_keys]
        reranker = self._get_cross_encoder(model_name)
        ce_scores = reranker.predict(pairs)
        ce_by_id = {k: float(s) for k, s in zip(unique_keys, ce_scores)}
        ordered = sorted(unique_keys, key=lambda k: ce_by_id[k], reverse=True)
        return [(unique_docs[k], ce_by_id[k]) for k in ordered]


    # Inner layer: BM25 + dense for a single query
    def _bm25_with_scores(self, query: str) -> List[Tuple[Document, float]]:
        retriever = self.bm25_retriever
        if retriever is None:
            raise RuntimeError("BM25 retriever not initialized.")
        processed = retriever.preprocess_func(query)
        all_scores = np.asarray(retriever.vectorizer.get_scores(processed), dtype=float)
        top_idx = np.argsort(all_scores)[::-1][: self.fetch_k]
        return [(retriever.docs[int(i)], float(all_scores[int(i)])) for i in top_idx]

    def _dense_with_scores(self, query: str) -> List[Tuple[Document, float]]:
        vectorstore = self.dense_vectorstore
        docstore = self.dense_docstore
        if vectorstore is None or docstore is None:
            raise RuntimeError("Dense vectorstore/docstore not initialized.")
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

    def _inner_run(self, query: str) -> List[Tuple[Document, float]]:
        """Run the inner BM25 + dense ensemble for a single query and return scored fused docs."""
        if self.bm25_retriever is None or self.dense_retriever is None:
            raise RuntimeError("Retriever not initialized.")

        if self.fuse_func in _SCORE_FUSERS:
            bm25_scored = self._bm25_with_scores(query)
            dense_scored = self._dense_with_scores(query)
            return self.fuse_scored(
                self.fuse_func,
                weights=[self.bm25_weight, self.dense_weight],
                scored_lists=[bm25_scored, dense_scored],
            )
        if self.fuse_func in _CROSS_ENCODER_MODELS:
            bm25_results = self.bm25_retriever.invoke(query)[: self.fetch_k]
            dense_results = self.dense_retriever.invoke(query)[: self.fetch_k]
            return self.fuse_scored(
                self.fuse_func,
                ranked_lists=[bm25_results, dense_results],
                weights=[self.bm25_weight, self.dense_weight],
                query=query,
            )
        bm25_results = self.bm25_retriever.invoke(query)[: self.fetch_k]
        dense_results = self.dense_retriever.invoke(query)[: self.fetch_k]
        return self.fuse_scored(
            self.fuse_func,
            ranked_lists=[bm25_results, dense_results],
            weights=[self.bm25_weight, self.dense_weight],
        )

    def inner_query(self, query: str) -> List[Document]:
        """Public inner-layer query (single-shot ensemble, no variant expansion)."""
        return [d for d, _ in self._inner_run(query)][: self.max_results]


    # Outer layer: LLM query expansion + cross-variant fusion
    def _get_query_llm(self):
        if self._query_llm is None:
            self._query_llm = ChatOpenAI(
                model=self.query_llm_model,
                temperature=self.query_llm_temperature,
                api_key=self._api_key,
            )
        return self._query_llm

    def multi_query_construction(self, query: str) -> List[str]:
        """Generate `num_queries` LLM rephrasings of `query`; the original is always included."""
        llm = self._get_query_llm()
        prompt = (
            "You are a query-rewriting assistant for a chemical-safety SOP retrieval system. "
            "The corpus contains laboratory standard operating procedures, GHS/CLP classification "
            "guidance, REACH/ECHA practical guides, NIH waste-disposal guidance, hazard-communication "
            "and chemical-hygiene plans, and procedures for pyrophoric, water-reactive, and "
            "nanomaterial handling. Retrieval runs as a hybrid of BM25 (lexical) and dense embeddings "
            "(semantic), so your rewrites should help BOTH.\n"
            "\n"
            f"Generate exactly {self.num_queries} alternative versions of the user's question. "
            "Each rewrite must:\n"
            "  • Preserve every specific entity from the original — chemical names, CAS numbers, "
            "hazard classes, regulation names, equipment, and quantities — verbatim. Do not "
            "generalize them away.\n"
            "  • Vary along a DIFFERENT axis from the others. Use a mix of:\n"
            "      - lexical swaps (synonyms, expand/contract acronyms such as SDS ↔ safety data "
            "sheet, PPE ↔ personal protective equipment, GHS ↔ Globally Harmonized System);\n"
            "      - perspective shifts (procedural \"how do I…\", regulatory \"what does <regulation> "
            "require…\", hazard-focused \"what are the risks of…\", control-focused \"what controls "
            "mitigate…\");\n"
            "      - granularity shifts (one more specific, one broader);\n"
            "      - terminology register (lab-bench phrasing vs. formal regulatory phrasing).\n"
            "  • Stay a single self-contained question. No preamble, no numbering, no bullets, "
            "no quotes, no commentary.\n"
            "  • Not be a near-duplicate of the original or of another rewrite.\n"
            "\n"
            f"Output exactly {self.num_queries} lines, one rewrite per line, nothing else.\n"
            "\n"
            f"Original question: {query}"
        )
        response = llm.invoke(prompt)
        lines = response.content.strip().split("\n")
        variants = [line.strip(" -*0123456789.\t") for line in lines if line.strip()]
        if query not in variants:
            variants.append(query)
        return variants

    def _resolve_outer_weights(self, n_variants: int) -> List[float]:
        if self._outer_weights_override is None:
            return [1.0] * n_variants
        weights = list(self._outer_weights_override)
        if len(weights) != n_variants:
            raise ValueError(
                f"outer_weights length ({len(weights)}) does not match number of "
                f"query variants ({n_variants})."
            )
        for w in weights:
            if w < 0:
                raise ValueError("outer_weights entries must be non-negative.")
        return [float(w) for w in weights]

    def query(self, query: str) -> List[Document]:
        """End-to-end double fusion: variants → inner ensemble per variant → outer fuse."""
        variants = self.multi_query_construction(query)

        # Per-variant inner runs. Keep full scored lists so either rank or
        # score outer fusion can consume them.
        per_variant_scored: List[List[Tuple[Document, float]]] = [
            self._inner_run(v) for v in variants
        ]
        per_variant_ranked: List[List[Document]] = [
            [d for d, _ in scored] for scored in per_variant_scored
        ]

        weights = self._resolve_outer_weights(len(variants))

        if self.outer_fuse_func in _CROSS_ENCODER_MODELS:
            fused = self.fuse_scored(
                self.outer_fuse_func,
                ranked_lists=per_variant_ranked,
                query=query,
            )
        elif self.outer_fuse_func in _SCORE_FUSERS:
            fused = self.fuse_scored(
                self.outer_fuse_func,
                scored_lists=per_variant_scored,
                weights=weights,
            )
        else:
            fused = self.fuse_scored(
                self.outer_fuse_func,
                ranked_lists=per_variant_ranked,
                weights=weights,
            )

        return [d for d, _ in fused][: self.outer_max_results]
