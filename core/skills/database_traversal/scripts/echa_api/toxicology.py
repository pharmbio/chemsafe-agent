import json
from .echa_client import get_client
from .parser import select_best_dossier, parse_section_7, parse_dossier_sections


def _dossier_info(dossier, *, include_type_role=True):
    info = {
        "asset_id": dossier["asset_id"],
        "registration_number": dossier.get("registration_number", ""),
    }
    if include_type_role:
        info["subtype"] = dossier.get("subtype", "")
        info["role"] = dossier.get("role", "")
    return info


async def _resolve_dossier(substance_index):
    """Return (dossier, None) or (None, error_json) for the best dossier."""
    dossier = await select_best_dossier(get_client(), substance_index)
    if dossier:
        return dossier, None
    error = json.dumps(
        {"error": f"No suitable dossier found for substance {substance_index}"},
        indent=2,
    )
    return None, error


async def get_toxicology_summary(substance_index):
    """Get Section 7 summaries and DN(M)EL values only (fast path).

    Skips individual study records, returning just the per-subsection summary
    documents plus any derived no/minimal-effect levels. Returns a JSON string.
    """
    dossier, error = await _resolve_dossier(substance_index)
    if error:
        return error

    data = await parse_section_7(get_client(), dossier["asset_id"], max_studies=0)

    summary_sections = {
        sec: {"summaries": sec_data["summaries"]}
        for sec, sec_data in data.get("sections", {}).items()
        if sec_data.get("summaries")
    }

    return json.dumps(
        {
            "substance_index": substance_index,
            "dossier_info": _dossier_info(dossier),
            "dnmels": data.get("dnmels", []),
            "sections": summary_sections,
        },
        ensure_ascii=False,
        indent=2,
    )


async def get_toxicology_studies(substance_index, section=None, max_studies=50):
    """Get Section 7 study-level records, optionally limited to one subsection.

    Returns a JSON string of study records grouped by subsection, with a count
    per section and a grand total.
    """
    dossier, error = await _resolve_dossier(substance_index)
    if error:
        return error

    data = await parse_section_7(
        get_client(), dossier["asset_id"], target_section=section, max_studies=max_studies
    )

    study_sections = {
        sec: {"study_count": len(sec_data["studies"]), "studies": sec_data["studies"]}
        for sec, sec_data in data.get("sections", {}).items()
        if sec_data.get("studies")
    }

    result = {
        "substance_index": substance_index,
        "dossier_info": _dossier_info(dossier, include_type_role=False),
        "sections": study_sections,
        "total_studies": sum(len(s["studies"]) for s in study_sections.values()),
    }
    if section:
        result["filter_section"] = section

    return json.dumps(result, ensure_ascii=False, indent=2)


async def get_toxicology_full(substance_index):
    """Get the complete Section 7 dataset: summaries, studies and DN(M)ELs.

    Downloads and parses every summary plus up to 100 study records. Slow for
    data-rich substances — prefer the summary/studies tools when possible.
    Returns a JSON string.
    """
    dossier, error = await _resolve_dossier(substance_index)
    if error:
        return error

    data = await parse_section_7(get_client(), dossier["asset_id"], max_studies=100)
    sections = data.get("sections", {})

    return json.dumps(
        {
            "substance_index": substance_index,
            "dossier_info": _dossier_info(dossier),
            "dnmels": data.get("dnmels", []),
            "sections": sections,
            "total_summaries": sum(len(s.get("summaries", [])) for s in sections.values()),
            "total_studies": sum(len(s.get("studies", [])) for s in sections.values()),
        },
        ensure_ascii=False,
        indent=2,
    )


async def get_ecotoxicology_data(substance_index, section=None, max_studies=50):
    """Get environmental fate (Section 5) and ecotoxicology (Section 6) data.

    Returns a JSON string; pass ``section`` (e.g. '5.1.1' or '6.1.1') to narrow
    the scan to one subsection.
    """
    data = await parse_dossier_sections(
        get_client(),
        substance_index,
        ("5", "6"),
        target_section=section,
        max_studies=max_studies,
    )
    return json.dumps(data, ensure_ascii=False, indent=2)
