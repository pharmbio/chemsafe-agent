from __future__ import annotations

import os
from typing import List

from backend.utils.sop_rag.sop_retriever import EnsembleSOPRetriever


def _format_sop_results(documents) -> str:
    """Format SOP documents for display."""
    result_lines: List[str] = []
    for idx, doc in enumerate(documents, start=1):
        filename = "Unknown"
        if hasattr(doc, "metadata") and doc.metadata:
            filename = os.path.basename(doc.metadata.get("filename", "Unknown"))

        result_lines.extend(
            [
                f"\n--- Document {idx} ---\n",
                f"Source: {filename}\n",
                f"Content: {getattr(doc, 'page_content', '')}\n",
            ]
        )

    return "".join(result_lines)


def sop_search(
    query: str,
    mode: str = "parent_child",
    score_threshold: float = 0,
    max_results: int = 5,
) -> str:
    """Search SOP documents via the BM25 + dense ensemble retriever."""
    try:
        retriever = EnsembleSOPRetriever(
            mode=mode,
            score_threshold=score_threshold,
            max_results=max_results,
            fetch_k=50,
            fuse_func="combsum",
            bm25_b=0.3,
            bm25_k1=0.8,
            dense_weight=0.6,
            bm25_weight=0.4
        )
        documents = retriever.query(query)

        if not documents:
            return f"No SOP content found for query '{query}'."

        return _format_sop_results(documents)

    except Exception as exc:  # pragma: no cover - external service call
        return f"Error retrieving SOP content for '{query}': {exc}. Please try again."


__all__ = [
    "sop_search",
]
