import json
from .echa_client import get_client
from .parser import categories_and_hcodes, labelling_summary, pictogram_list


async def get_clp_classification(substance_index, max_results=5):
    """Assemble industry (CLP notification) self-classifications for a substance.

    Notifications are sorted by how many notifiers agree (most common first)
    and truncated to ``max_results``. Each entry is enriched with its hazard
    categories, H-codes, signal word, pictograms, SCLs and M-factors. Returns
    a JSON string, or an ``error`` key when nothing is found.
    """
    client = get_client()

    data = await client.get_clp_classifications(substance_index)
    if not data:
        return json.dumps(
            {"error": f"No CLP classification data found for {substance_index}"},
            indent=2,
        )

    notifications = data.get("items", [])
    if not notifications:
        return json.dumps(
            {"substance_index": substance_index, "total_classifications": 0, "classifications": []},
            indent=2,
        )

    total_available = len(notifications)
    notifications.sort(
        key=lambda x: x.get("substanceNotificationPercentage", 0), reverse=True
    )
    notifications = notifications[:max_results]

    classifications = []
    for note in notifications:
        cid = str(note.get("classificationId", ""))
        if not cid:
            continue

        categories, hcodes = categories_and_hcodes(
            await client.get_clp_classification_detail(cid)
        )
        signal_word, labelling = labelling_summary(await client.get_clp_labelling(cid))
        scl = await client.get_clp_scl(cid)
        m_factors = await client.get_clp_m_factors(cid)

        classifications.append({
            "classification_id": cid,
            "data_source": note.get("dataSource", ""),
            "notification_percentage": note.get("substanceNotificationPercentage", 0),
            "annex_i_compliant": note.get("clpAnnexIComplianceFlag", False),
            "last_update": note.get("lastUpdateDate", ""),
            "hazard_categories": categories,
            "hcodes": hcodes,
            "signal_word": signal_word,
            "pictograms": pictogram_list(await client.get_clp_pictograms(cid)),
            "labelling": labelling,
            "specific_concentration_limits": scl.get("items", []) if scl else [],
            "m_factors": m_factors.get("items", []) if m_factors else [],
        })

    return json.dumps(
        {
            "substance_index": substance_index,
            "total_available": total_available,
            "returned": len(classifications),
            "truncated": total_available > len(classifications),
            "classifications": classifications,
        },
        ensure_ascii=False,
        indent=2,
    )
