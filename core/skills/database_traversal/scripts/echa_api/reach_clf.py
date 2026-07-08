import json
from .echa_client import get_client
from .parser import find_lead_dossiers, parse_section_2_1_ghs, parse_section_2_3_pbt


async def _collect_from_leads(substance_index, extract, payload_key):
    """Run ``extract(client, asset_id)`` over the top lead dossiers.

    Both REACH tools below share this shape: locate the lead-registrant
    dossiers, parse a section out of at most three of them, and wrap the
    per-dossier results under ``payload_key``.
    """
    client = get_client()

    leads = await find_lead_dossiers(client, substance_index)
    if not leads:
        return json.dumps(
            {"error": f"No lead dossiers found for substance {substance_index}"},
            indent=2,
        )

    dossiers = []
    for info in leads[:3]:
        asset_id = info["asset_id"]
        dossiers.append({
            "dossier_info": {
                "asset_id": asset_id,
                "registration_number": info.get("registration_number", ""),
                "subtype": info.get("subtype", ""),
                "role": info.get("role", ""),
            },
            payload_key: await extract(client, asset_id),
        })

    return json.dumps(
        {
            "substance_index": substance_index,
            "dossier_count": len(dossiers),
            "dossiers": dossiers,
        },
        ensure_ascii=False,
        indent=2,
    )


async def get_reach_ghs(substance_index):
    """Get the registrant's own GHS classification from REACH Section 2.1.

    Unlike CLP notifications (any notifier's self-classification), this is the
    classification the lead registrant recorded in their REACH dossier, so the
    two can disagree. Returns a JSON string keyed by lead dossier.
    """
    return await _collect_from_leads(substance_index, parse_section_2_1_ghs, "ghs_entries")


async def get_reach_pbt(substance_index):
    """Get the PBT / vPvB assessment from REACH Section 2.3.

    Covers the overall PBT status and the P/vP, B/vB and T conclusions recorded
    in the lead dossiers. Returns a JSON string keyed by lead dossier.
    """
    return await _collect_from_leads(substance_index, parse_section_2_3_pbt, "pbt_assessment")
