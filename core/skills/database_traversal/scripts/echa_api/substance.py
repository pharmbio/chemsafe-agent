import json
from .echa_client import get_client
from .parser import select_best_cas


async def get_substance_info(substance_index):
    """Resolve a substance index to its core identifiers.

    Returns a JSON string with CAS/EC numbers, chemical and IUPAC names,
    molecular formula, InChI/SMILES and a link back to the ECHA record. On a
    miss the JSON carries an ``error`` key instead.
    """
    client = get_client()
    data = await client.get_substance_info(substance_index)

    if not data:
        return json.dumps(
            {"error": f"Substance not found for index: {substance_index}"},
            indent=2,
        )

    cas_list = data.get("casNumber") or []
    iupac_names = data.get("iupacName") or []
    ec_names = data.get("ecName") or []
    index_numbers = data.get("indexNumber") or []

    # rml* fields are ECHA's chosen primary values; fall back to the lists.
    primary_cas = data.get("rmlCas", "") or select_best_cas(cas_list)

    result = {
        "substance_index": substance_index,
        "cas_number": primary_cas,
        "ec_number": data.get("rmlEc", ""),
        "chemical_name": data.get("rmlName", ""),
        "iupac_name": data.get("rmlIupac", ""),
        "molecular_formula": data.get("rmlMolFormula", ""),
        "inchi": data.get("rmlInchi", ""),
        "smiles": data.get("rmlSmiles", ""),
        "index_number": index_numbers[0] if index_numbers else "",
        "all_cas_numbers": cas_list,
        "all_ec_names": ec_names,
        "all_iupac_names": iupac_names[:10],
        "substance_url": f"https://chem.echa.europa.eu/substance-information/{substance_index}",
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


async def list_dossiers(substance_index, status="Active", max_results=10):
    """List REACH registration dossiers for a substance, newest first.

    Returns a JSON string with the total available count and up to
    ``max_results`` dossier records, or an ``error`` key if none were found.
    """
    client = get_client()
    data = await client.get_dossier_list(substance_index, status=status)

    if not data:
        return json.dumps(
            {"error": f"No dossier data for substance {substance_index} (status={status})"},
            indent=2,
        )

    dossiers = []
    for d in data.get("items", []):
        asset_id = d.get("assetExternalId", "")
        if not asset_id:
            continue

        reach_info = d.get("reachDossierInfo", {}) or {}
        dossiers.append({
            "asset_id": asset_id,
            "registration_number": d.get("registrationNumber", ""),
            "subtype": reach_info.get("dossierSubtype", ""),
            "role": reach_info.get("registrationRole", ""),
            "status": d.get("registrationStatus", status),
            "last_updated": d.get("lastUpdatedDate", ""),
            "registration_date": d.get("registrationDate", ""),
            "dossier_url": f"https://chem.echa.europa.eu/html-pages-prod/{asset_id}/index.html",
        })

    total_available = len(dossiers)
    dossiers.sort(key=lambda x: x.get("last_updated", ""), reverse=True)
    returned = dossiers[:max_results]

    return json.dumps(
        {
            "total_available": total_available,
            "returned": len(returned),
            "truncated": total_available > len(returned),
            "dossiers": returned,
        },
        ensure_ascii=False,
        indent=2,
    )
