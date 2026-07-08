import atexit
import asyncio
import json
import logging
from .substance import get_substance_info, list_dossiers
from .clp_clf import get_clp_classification
from .harmonised_clf import get_harmonised_classification
from .reach_clf import get_reach_ghs, get_reach_pbt
from .toxicology import (
    get_toxicology_summary,
    get_toxicology_studies,
    get_toxicology_full,
    get_ecotoxicology_data,
)
from .parser import get_hcode_mapping_markdown, get_hcode_mapping_json
from .echa_client import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


@atexit.register
def _release_client():
    """Best-effort close of the shared httpx pool on interpreter exit."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    try:
        if loop.is_running():
            loop.create_task(get_client().close())
        else:
            loop.run_until_complete(get_client().close())
    except Exception:
        pass


async def tool_get_substance_info(substance_index: str) -> str:
    """Get basic information for a chemical substance from ECHA CHEM database.

    Retrieves CAS number, EC number, chemical names, IUPAC name,
    and molecular formula for a given substance index.

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002' for Formaldehyde)

    Returns:
        JSON with substance identifiers and names
    """
    return await get_substance_info(substance_index)


async def tool_list_dossiers(substance_index: str, status: str = "Active", max_results: int = 10) -> str:
    """List REACH registration dossiers for a substance.

    Returns all REACH registration dossiers including registration numbers,
    types (Article 10-full, Article 18), and registrant roles.
    Sorted by last updated date (newest first). Defaults to 10 results.

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002')
        status: Registration status filter: 'Active' or 'Not active'
        max_results: Maximum number of dossiers to return (default 10)

    Returns:
        JSON with dossier list including asset IDs and registration details
    """
    return await list_dossiers(substance_index, status, max_results)


async def tool_get_clp_classification(substance_index: str, max_results: int = 5) -> str:
    """Get CLP notification (industry self-classification) data.

    Retrieves all CLP self-classifications notified by industry under the
    CLP Regulation. Includes hazard categories, H-codes, signal words,
    pictograms, SCL, and M-factors.
    Sorted by notification percentage (most common first). Defaults to top 5.

    For the official EU harmonised classification, use echa_get_harmonised_classification.

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002')
        max_results: Maximum number of classification entries to return (default 5)

    Returns:
        JSON with all notification classifications and their details
    """
    return await get_clp_classification(substance_index, max_results)


async def tool_get_harmonised_classification(substance_index: str) -> str:
    """Get harmonised classification (Annex VI, CLP Regulation).

    Returns the official EU classification adopted by the European Commission.
    Not all substances have harmonised classifications. Includes hazard categories,
    H-codes, SCL, M-factors, ATE values, and regulatory notes.

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002')

    Returns:
        JSON with harmonised classification data or indication that none exists
    """
    return await get_harmonised_classification(substance_index)


async def tool_get_reach_ghs(substance_index: str) -> str:
    """Get GHS classification from REACH registration dossier (Section 2.1).

    Retrieves the registrant's own GHS hazard classification from their
    REACH dossier. This may differ from CLP notifications.

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002')

    Returns:
        JSON with GHS classification entries from lead dossiers
    """
    return await get_reach_ghs(substance_index)


async def tool_get_reach_pbt(substance_index: str) -> str:
    """Get PBT assessment from REACH registration dossier (Section 2.3).

    Retrieves PBT/vPvB assessment data including PBT status and
    conclusions on Persistence, Bioaccumulation, and Toxicity properties.

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002')

    Returns:
        JSON with PBT assessment summaries and study conclusions
    """
    return await get_reach_pbt(substance_index)


async def tool_get_toxicology_summary(substance_index: str) -> str:
    """Get toxicology summary and DN(M)ELs from REACH dossier (Section 7).

    Returns ONLY summary documents and DNEL/DMEL values. This is much
    faster than the full query. Use this for a quick overview of
    toxicological endpoints.

    Sections: 7.1-7.10 (toxicokinetics, acute tox, irritation,
    sensitisation, repeated dose, genotox, carcinogenicity,
    reproductive tox, neurotox/immunotox, human data)

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002')

    Returns:
        JSON with DN(M)ELs and summary data per section
    """
    return await get_toxicology_summary(substance_index)


async def tool_get_toxicology_studies(substance_index: str, section: str = None, max_studies: int = 50) -> str:
    """Get individual toxicology study records from REACH dossier.

    Returns study-level data with species, route, effect levels, and conclusions.
    Can be filtered to a specific subsection (e.g., '7.2' for acute toxicity).

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002')
        section: Optional section filter (e.g., '7.2' for acute toxicity)
        max_studies: Maximum number of studies to parse (default 50)

    Returns:
        JSON with study records per section
    """
    return await get_toxicology_studies(substance_index, section, max_studies)


async def tool_get_toxicology_full(substance_index: str) -> str:
    """Get COMPLETE toxicology data from REACH dossier (Section 7).

    Downloads and parses ALL summaries and studies (up to 400).
    WARNING: This can be very slow for data-rich substances.

    For faster alternatives:
    - echa_get_toxicology_summary: summaries + DNELs only
    - echa_get_toxicology_studies: studies with optional section filter

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002')

    Returns:
        Complete JSON with DN(M)ELs, summaries, and studies
    """
    return await get_toxicology_full(substance_index)


async def tool_get_ecotoxicology_data(
    substance_index: str,
    section: str = None,
    max_studies: int = 50,
) -> str:
    """Get environmental fate and ecotoxicological data from REACH dossier.

    Covers Section 5 environmental fate/pathways and Section 6 ecotoxicological
    information, including degradation, bioaccumulation, adsorption/desorption,
    aquatic toxicity, sediment toxicity, terrestrial toxicity, and PNEC summaries.

    Args:
        substance_index: ECHA substance index (e.g., '100.000.002')
        section: Optional Section 5/6 subsection filter (e.g., '5.1.1' or '6.1.1')
        max_studies: Maximum number of study documents to parse (default 50)

    Returns:
        JSON with Section 5/6 summaries and study records
    """
    return await get_ecotoxicology_data(substance_index, section, max_studies)


async def resource_hcode_mapping() -> str:
    """GHS Hazard Category to H-code mapping table.

    Reference table mapping GHS hazard category short codes
    (e.g., 'Acute Tox. 4 (Oral)') to H statement codes (e.g., 'H302').
    Covers physical, health, and environmental hazards.
    """
    return get_hcode_mapping_markdown()


async def resource_hcode_mapping_json() -> str:
    """GHS Hazard Category to H-code mapping as JSON dict."""
    return json.dumps(get_hcode_mapping_json(), indent=2)
