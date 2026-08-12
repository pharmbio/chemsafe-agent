import json
from .echa_client import get_client
from .parser import categories_and_hcodes, labelling_summary, pictogram_list, unwrap_items


async def get_harmonised_classification(substance_index):
    """Assemble the harmonised (Annex VI) classification for a substance.

    This is the legally binding EU classification adopted by the Commission,
    distinct from the industry CLP notifications. Many substances have none —
    in that case ``has_harmonised`` is False and a pointer to
    ``get_clp_classification`` is returned. Otherwise each entry carries hazard
    categories, H-codes, labelling, pictograms, SCLs, M-factors, ATE values and
    regulatory notes.
    """
    client = get_client()

    data = await client.get_harmonised_classifications(substance_index)
    if not data:
        return json.dumps(
            {
                "substance_index": substance_index,
                "has_harmonised": False,
                "message": "No harmonised classification found. This substance may only "
                           "have industry (CLP notification) classifications. "
                           "Use echa_get_clp_classification to check.",
                "classifications": [],
            },
            indent=2,
        )

    classifications = []
    for entry in unwrap_items(data):
        cid = str(entry.get("classificationId", entry.get("id", "")))
        if not cid:
            continue

        categories, hcodes = categories_and_hcodes(
            await client.get_harmonised_classification_detail(cid)
        )
        signal_word, labelling = labelling_summary(await client.get_harmonised_labelling(cid))

        classifications.append({
            "classification_id": cid,
            "index_number": entry.get("indexNumber", ""),
            "hazard_categories": categories,
            "hcodes": hcodes,
            "signal_word": signal_word,
            "pictograms": pictogram_list(await client.get_harmonised_pictograms(cid)),
            "labelling": labelling,
            "specific_concentration_limits": unwrap_items(await client.get_harmonised_scl(cid)),
            "m_factors": unwrap_items(await client.get_harmonised_m_factors(cid)),
            "acute_toxicity_estimates": unwrap_items(await client.get_harmonised_ate(cid)),
            "notes": unwrap_items(await client.get_harmonised_notes(cid)),
        })

    return json.dumps(
        {
            "substance_index": substance_index,
            "has_harmonised": len(classifications) > 0,
            "classifications": classifications,
        },
        ensure_ascii=False,
        indent=2,
    )
