import json
import re
from .echa_client import get_client

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")

# Score at or above which the leading candidate is treated as a confident hit
# (an exact CAS or exact name match clears this comfortably).
_CONFIDENT = 60


def looks_like_cas(query: str) -> bool:
    """True if the query is shaped like a CAS number (NN...-NN-N)."""
    return bool(_CAS_RE.match(query.strip()))


def _candidate(substance_index: dict) -> dict:
    """Flatten one search hit's ``substanceIndex`` block into a compact record."""
    real_cas = [c for c in (substance_index.get("casNumber") or []) if c and c != "-"]
    return {
        "substance_index": substance_index.get("rmlId", ""),
        "name": substance_index.get("rmlName", ""),
        "ec_number": substance_index.get("rmlEc", ""),
        "cas_number": substance_index.get("rmlCas", ""),
        "all_cas_numbers": real_cas,
        "iupac_names": substance_index.get("iupacName") or [],
    }


def _score(candidate: dict, query: str, is_cas: bool) -> int:
    """Rank a candidate against the query — higher is better.

    Exact CAS / name matches dominate; mono-constituent substances that carry a
    real EC and CAS are nudged above reaction-mass and "releaser" pseudo-entries
    that tend to crowd the top of a raw name search.
    """
    q = query.strip().lower()
    score = 0

    if is_cas:
        if candidate["cas_number"].lower() == q:
            score += 100
        elif q in (c.lower() for c in candidate["all_cas_numbers"]):
            score += 60
    else:
        if candidate["name"].lower() == q:
            score += 100
        elif q in (n.lower() for n in candidate["iupac_names"]):
            score += 50
        elif q in candidate["name"].lower():
            score += 10

    if candidate["ec_number"] and candidate["ec_number"] != "-":
        score += 5
    if candidate["cas_number"] and candidate["cas_number"] != "-":
        score += 5

    return score


async def _search(query: str, max_results: int) -> list[dict]:
    data = await get_client().search_substances(query.strip(), page_size=max_results)
    items = data.get("items", []) if isinstance(data, dict) else []
    candidates = [_candidate(it.get("substanceIndex", {})) for it in items]
    return [c for c in candidates if c["substance_index"]]


async def search_substances(query, max_results=10):
    """Search ECHA for substances matching a name, CAS or EC number.

    Returns a JSON string listing the raw candidates (unranked, as ECHA orders
    them), each already carrying its ``substance_index`` ready to feed the other
    ECHA tools. Use this when you want to see every match; use
    ``resolve_substance_index`` when you want a single best pick.

    Args:
        query: A chemical name, CAS number, or EC number.
        max_results: Maximum number of candidates to return (default 10).

    Returns:
        JSON string: {query, count, candidates: [{substance_index, name,
        ec_number, cas_number, all_cas_numbers, iupac_names}, ...]}
    """
    candidates = await _search(query, max_results)
    return json.dumps(
        {"query": query, "count": len(candidates), "candidates": candidates},
        ensure_ascii=False,
        indent=2,
    )


async def resolve_substance_index(query, max_results=10):
    """Resolve a name or CAS number to a single best ECHA substance index.

    This is the intended entry point for the ECHA toolset. It searches, ranks
    the hits (exact CAS/name matches win; reaction-mass / pseudo entries are
    down-weighted), and returns the best pick plus the ranked alternatives.

    CAS input resolves cleanly. Name input can be ambiguous (isomers, mixtures,
    "…and releasers" entries); when ``ambiguous`` is true, confirm the pick — or
    fall back to searching by CAS — before trusting downstream classification.

    Args:
        query: A chemical name, CAS number, or EC number.
        max_results: Maximum number of candidates to consider (default 10).

    Returns:
        JSON string: {query, query_type, resolved, ambiguous, substance_index,
        best, candidates}. On no match: {resolved: false, reason: "no_match"}.
    """
    q = query.strip()
    is_cas = looks_like_cas(q)
    candidates = await _search(q, max_results)

    if not candidates:
        return json.dumps(
            {
                "query": query,
                "query_type": "cas" if is_cas else "name",
                "resolved": False,
                "reason": "no_match",
                "substance_index": None,
                "best": None,
                "candidates": [],
            },
            ensure_ascii=False,
            indent=2,
        )

    ranked = sorted(candidates, key=lambda c: _score(c, q, is_cas), reverse=True)
    best_score = _score(ranked[0], q, is_cas)
    runner_up_score = _score(ranked[1], q, is_cas) if len(ranked) > 1 else -1
    ambiguous = best_score < _CONFIDENT or best_score == runner_up_score

    return json.dumps(
        {
            "query": query,
            "query_type": "cas" if is_cas else "name",
            "resolved": True,
            "ambiguous": ambiguous,
            "substance_index": ranked[0]["substance_index"],
            "best": ranked[0],
            "candidates": ranked,
        },
        ensure_ascii=False,
        indent=2,
    )
