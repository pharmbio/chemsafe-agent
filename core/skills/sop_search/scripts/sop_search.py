from __future__ import annotations
import os
from typing import List
from backend.utils.sop_rag.sop_retriever import SOPRetriever




def _format_sop_results(documents) -> str:
    """Format SOP documents for display."""
    result_lines: List[str] = []
    for idx, doc in enumerate(documents, start=1):
        filename = "Unknown"
        if hasattr(doc, "metadata"):
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
    score_threshold: float = 0.3,
    max_results: int = 12,
) -> str:
    """Search SOP documents for protocols and regulatory procedures."""
    retriever = SOPRetriever(
        score_threshold=score_threshold,
        max_results=max_results,
    )

    try:
        documents = retriever.retriever.invoke(query)
        documents = retriever._convert_bytes_to_docs(documents)

        if not documents:
            return f"No SOP content found for query '{query}'."

        return _format_sop_results(documents)

    except Exception as exc:  # pragma: no cover - external service call
        return f"Error retrieving SOP content for '{query}': {exc}. Please try again."


__all__ = [
    "sop_search",
]
