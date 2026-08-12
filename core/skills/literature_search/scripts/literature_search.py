from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, List, Optional

import requests


LITSENSE_BASE_URL = "https://www.ncbi.nlm.nih.gov/research/litsense2-api/api/"
DEFAULT_TIMEOUT_SECONDS = 30


@dataclass
class LiteraturePassage:
    """Single passage/sentence returned by LitSense2."""

    text: str
    score: float
    pmid: Optional[int]
    pmcid: Optional[str]
    section: Optional[str]
    annotations: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _coerce_passage(raw: dict) -> LiteraturePassage:
    return LiteraturePassage(
        text=str(raw.get("text") or "").strip(),
        score=float(raw.get("score") or 0.0),
        pmid=raw.get("pmid"),
        pmcid=raw.get("pmcid"),
        section=raw.get("section"),
        annotations=list(raw.get("annotations") or []),
    )


def _dedupe(passages: List[LiteraturePassage]) -> List[LiteraturePassage]:
    seen: set[tuple] = set()
    unique: List[LiteraturePassage] = []
    for passage in passages:
        key = (passage.pmid, passage.pmcid, passage.text[:200])
        if key in seen:
            continue
        seen.add(key)
        unique.append(passage)
    return unique


def literature_search(
    query: str,
    *,
    limit: int = 10,
    mode: str = "passages",
    rerank: bool = True,
    min_score: Optional[float] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    base_url: str = LITSENSE_BASE_URL,
) -> List[LiteraturePassage]:
    """Query NCBI LitSense2 for passages or sentences from PubMed/PMC.

    Parameters
    ----------
    query:
        Natural-language query. LitSense2 uses semantic retrieval, so plain
        English works; see SKILL.md for query-construction guidance.
    limit:
        Maximum passages to request from the API before local filtering.
    mode:
        "passages" (paragraph-level, default) or "sentences" (sentence-level).
    rerank:
        Ask LitSense to semantically rerank results before returning.
    min_score:
        Optional local score floor applied after fetch. Does not change the API call.
    timeout:
        Request timeout in seconds.
    base_url:
        Override the LitSense2 base URL (tests / mirrors).

    Returns
    -------
    List of `LiteraturePassage`, deduplicated by (pmid, pmcid, text-prefix).
    Never raises on HTTP or parse errors — returns `[]` so callers can branch on emptiness.
    """

    query = (query or "").strip()
    if not query:
        return []

    path = "sentences/" if mode == "sentences" else "passages/"
    url = base_url.rstrip("/") + "/" + path

    params = {"query": query, "rerank": bool(rerank)}
    if limit is not None:
        params["limit"] = int(limit)

    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []

    if not isinstance(payload, list):
        return []

    passages = [_coerce_passage(item) for item in payload if isinstance(item, dict)]
    if min_score is not None:
        passages = [p for p in passages if p.score >= float(min_score)]
    return _dedupe(passages)


def batch_literature_search(
    queries: List[str],
    *,
    per_query_limit: int = 10,
    mode: str = "passages",
    rerank: bool = True,
    min_score: Optional[float] = None,
) -> dict[str, List[LiteraturePassage]]:
    """Run a set of queries sequentially and return a per-query result map.

    Follow the SKILL.md guidance: build 3–6 angled queries from dimensions
    (substance, endpoint, system, outcome, context) and pass them here so
    downstream code can deduplicate across the full set by PMID.
    """

    results: dict[str, List[LiteraturePassage]] = {}
    for query in queries:
        results[query] = literature_search(
            query,
            limit=per_query_limit,
            mode=mode,
            rerank=rerank,
            min_score=min_score,
        )
    return results


def format_passages_for_prompt(
    passages: List[LiteraturePassage],
    *,
    max_passages: Optional[int] = None,
) -> str:
    """Render passages as a compact block suitable for in-context reasoning.

    The full set is wrapped once in `<document>` tags; individual passages are
    delimited by `--- Passage #n ---` headers inside that single wrapper.
    """
    if not passages:
        return "No literature passages retrieved."

    chunks: List[str] = []
    for idx, passage in enumerate(passages[: max_passages or len(passages)], start=1):
        pmid = passage.pmid if passage.pmid is not None else "n/a"
        pmcid = passage.pmcid or "n/a"
        section = passage.section or "n/a"
        chunks.append(
            f"\n--- Passage #{idx} ---\n"
            f"PMID: {pmid}\n"
            f"PMCID: {pmcid}\n"
            f"Section: {section}\n"
            f"Score: {passage.score:.3f}\n"
            f"Content: {passage.text}\n"
        )
    return "<document>" + "".join(chunks) + "</document>\n"


def dedupe_across_queries(
    per_query_results: dict[str, List[LiteraturePassage]],
) -> List[LiteraturePassage]:
    """Flatten and dedupe results from `batch_literature_search`."""
    merged: List[LiteraturePassage] = []
    for passages in per_query_results.values():
        merged.extend(passages)
    return _dedupe(merged)


__all__ = [
    "LITSENSE_BASE_URL",
    "LiteraturePassage",
    "batch_literature_search",
    "dedupe_across_queries",
    "format_passages_for_prompt",
    "literature_search",
]
