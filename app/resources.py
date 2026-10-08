from __future__ import annotations

from typing import Dict, List

# What the agent draws on, as listed on the /resources page. Logo paths that do
# not exist yet are skipped when the page renders, so a logo can be dropped into
# images/org_logo/ later without touching this file.

DATABASES: List[Dict[str, str]] = [
    {
        "name": "ECHA CHEM",
        "provider": "European Chemicals Agency",
        "logo": "images/org_logo/echa.png",
        "url": "https://chem.echa.europa.eu/",
        "description": (
            "EU regulatory hazard data: harmonised CLP classification, CLP notifications "
            "and REACH registration dossiers"
        ),
    },
    {
        "name": "PubChem",
        "provider": "National Center for Biotechnology Information, NIH",
        "logo": "images/org_logo/pubchem.png",
        "url": "https://pubchem.ncbi.nlm.nih.gov/",
        "description": (
            "Compound records with GHS classification, physical properties, toxicity "
            "and Laboratory Chemical Safety Summaries (LCSS)."
        ),
    },
    {
        "name": "NIOSH",
        "provider": "National Institute for Occupational Safety and Health",
        "logo": "images/org_logo/niosh.png",
        "url": "https://www.cdc.gov/niosh/npg/",
        "description": (
            "Occupational exposure limits (REL, IDLH), personal protective equipment, "
            "respirator recommendations and symptoms by exposure route."
        ),
    },
    {
        "name": "OPCW",
        "provider": "Organisation for the Prohibition of Chemical Weapons",
        "logo": "images/org_logo/opcw.png",
        "url": "https://www.opcw.org/",
        "description": "Chemicals listed in the Schedules of the Chemical Weapons Convention.",
    },
]

MODEL_COLLECTION: Dict[str, str] = {
    "name": "MISTRA SafeChem toolbox",
    "logo": "images/org_logo/safechem.png",
    "url": "https://github.com/pharmbio/ths-models",
    "description": (
        "Each model runs as a web service on SciLifeLab Serve and can be called by any agent/tool."
    ),
}

# Mirrors the model table in skills/qsar_modelling/SKILL.md.
PREDICTIVE_MODELS: List[Dict[str, str]] = [
    {"name": "AHR_agonists", "endpoint": "Aryl hydrocarbon receptor (AhR) agonism"},
    {"name": "CAR_agonist", "endpoint": "Constitutive androstane receptor (CAR) agonism"},
    {"name": "CAR_antagonist", "endpoint": "Constitutive androstane receptor (CAR) antagonism"},
    {"name": "DIO1_inhibition", "endpoint": "Type 1 iodothyronine deiodinase (DIO1) inhibition"},
    {"name": "DIO2_inhibition", "endpoint": "Type 2 iodothyronine deiodinase (DIO2) inhibition"},
    {"name": "DIO3_inhibition", "endpoint": "Type 3 iodothyronine deiodinase (DIO3) inhibition"},
    {"name": "NIS_inhibition", "endpoint": "Sodium/iodide symporter (NIS) inhibition"},
    {"name": "PPAR_delta_agonist", "endpoint": "PPAR-delta agonism"},
    {"name": "PPAR_delta_antagonist", "endpoint": "PPAR-delta antagonism"},
    {"name": "PPAR_gamma_agonist", "endpoint": "PPAR-gamma agonism"},
    {"name": "PPAR_gamma_antagonist", "endpoint": "PPAR-gamma antagonism"},
    {"name": "PXR_agonist", "endpoint": "Pregnane X receptor (PXR) agonism"},
    {"name": "TPO_inhibition", "endpoint": "Thyroid peroxidase (TPO) inhibition"},
    {"name": "TRHR_antagonists", "endpoint": "Thyrotropin-releasing hormone receptor (TRHR) antagonism"},
    {"name": "TR_beta_agonist", "endpoint": "Thyroid hormone receptor beta (TR-beta) agonism"},
    {"name": "TR_beta_antagonist", "endpoint": "Thyroid hormone receptor beta (TR-beta) antagonism"},
    {"name": "TSHR_agonist", "endpoint": "Thyroid-stimulating hormone receptor (TSHR) agonism"},
    {"name": "TSHR_antagonist", "endpoint": "Thyroid-stimulating hormone receptor (TSHR) antagonism"},
    {"name": "TTR_binding", "endpoint": "Transthyretin (TTR) binding"},
]


def model_url(name: str) -> str:
    """Where a model's web API runs; the same rule skills/qsar_modelling/scripts/utils.py uses."""
    return f"https://{name.lower().replace('_', '-')}.serve.scilifelab.se"


# One web service answers for every ADMET-AI endpoint, so its URL is given once
# for the collection rather than per model.
ADMET_AI_COLLECTION: Dict[str, str] = {
    "name": "ADMET-AI",
    "logo": "images/org_logo/admet-ai.png",
    "url": "https://github.com/swansonk14/admet_ai",
    "model_url": "https://admet-ai.serve.scilifelab.se",
    "description": (
        "Chemprop-RDKit graph neural networks trained on Therapeutics Data Commons datasets. "
        "All endpoints run as one web service on SciLifeLab Serve and are predicted in a single call."
    ),
}

# Mirrors the endpoint tables in skills/admet_prediction/SKILL.md, without the
# dataset author in each name. The RDKit physicochemical properties the service
# also returns are computed, not predicted, so they are not listed.
ADMET_AI_MODELS: Dict[str, List[str]] = {
    "Absorption": [
        "HIA", "Bioavailability", "Solubility", "Lipophilicity",
        "HydrationFreeEnergy", "Caco2", "PAMPA", "Pgp",
    ],
    "Distribution": ["Blood-brain barrier", "Plasma protein binding rate", "Volume of distribution at steady state"],
    "Metabolism": [
        "CYP1A2", "CYP2C19", "CYP2C9", "CYP2D6", "CYP3A4",
        "CYP2C9_Substrate", "CYP2D6_Substrate", "CYP3A4_Substrate",
    ],
    "Excretion": ["Clearance_Hepatocyte", "Clearance_Microsome", "Half_Life"],
    "Toxicity": ["hERG", "ClinTox", "AMES", "DILI", "Carcinogens", "LD50", "Skin_Reaction"],
    "Tox21": [
        "Androgen receptor", "Androgen receptor ligand-binding domain", "Aryl hydrocarbon receptor",
        "Aromatase", "Estrogen receptor", "Estrogen receptor ligand-binding domain",
        "Peroxisome proliferator-activated receptor gamma", "Antioxidant response element",
        "ATPase family AAA domain-containing protein 5", "Heat shock factor response element",
        "Mitochondrial membrane potential", "Tumour protein p53",
    ],
}

# Guidelines are grouped by the "organization" key; each group shows this logo once.
GUIDELINE_ORGANIZATIONS: Dict[str, Dict[str, str]] = {
    "ECHA": {"name": "European Chemicals Agency (ECHA)", "logo": "images/org_logo/echa.png"},
    "NIH": {"name": "National Institutes of Health (NIH)", "logo": "images/org_logo/nih.png"},
    "UN": {"name": "United Nations", "logo": "images/org_logo/un.png"},
}

GUIDELINES: List[Dict[str, str]] = [
    {
        "title": "Chemical Hygiene Plan",
        "organization": "NIH",
        "category": "Chemical safety report",
        "url": "https://ors.od.nih.gov/sr/dohs/Documents/chemical-hygiene-plan.pdf",
    },
    {
        "title": "How downstream users can handle exposure scenarios",
        "organization": "ECHA",
        "category": "Chemical safety",
        "url": "https://echa.europa.eu/documents/10162/17250/du_practical_guide_13_en.pdf/2c3bc624-fb3c-4515-a581-87b79d460d38",
    },
    {
        "title": "Globally Harmonized System of Classification and Labelling of Chemicals",
        "organization": "UN",
        "category": "Chemical safety",
        "url": "https://unece.org/sites/default/files/2025-09/GHS%20Rev11e.pdf",
    },
    {
        "title": "Hazard Communication Plan",
        "organization": "NIH",
        "category": "Chemical safety",
        "url": "https://ors.od.nih.gov/sr/dohs/Documents/hazard-communication-plan.pdf",
    },
    {
        "title": "How to act in substance evaluation",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17221/how_to_act_in_substance_evaluation_en.pdf/29e1197a-4d02-840b-03ed-6d02632c12ed",
    },
    {
        "title": "How to submit CLH dossier",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17250/how_to_submit_clh_dossier_en.pdf/a715300e-c40e-b181-e1c2-7dc851eb7b62",
    },
    {
        "title": "How to apply ECHA’s practical guide ‘How to use and report (Q)SARs’ for the assessment of substances under BPR",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17250/how-to-apply-echas-practical-guide_bpr_en.pdf/98fce7a8-046f-f488-422d-a03a075fb1bc",
    },
    {
        "title": "Frameworks for generation of information on intrinsic properties",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17235/information_requirements_r2_en.pdf/1fb0cfa6-8014-4477-a0dd-b33ebe4f1fdc",
    },
    {
        "title": "Information gathering",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17235/information_requirements_r3_en.pdf/41895234-1125-4977-b058-50a98e36fa48",
    },
    {
        "title": "Evaluation of available information",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17235/information_requirements_r4_en.pdf/d6395ad2-1596-4708-ba86-0136686d205e",
    },
    {
        "title": "Adaptation of information requirements",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17235/information_requirements_r5_en.pdf/51ffb7a7-baef-43ef-bac7-501e95b5a1d5",
    },
    {
        "title": "QSARs and grouping of chemicals",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r6_en.pdf/77f49f81-b76d-40ab-8513-4f3a533b6ac9",
    },
    {
        "title": "Endpoint specific guidance",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r7a_en.pdf/e4a2a18f-a2bd-4a04-ac6d-0ea425b2567f",
    },
    {
        "title": "Characterisation of dose [concentration]-response for human health",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r8_en.pdf/e153243a-03f0-44c5-8808-88af66223258",
    },
    {
        "title": "Characterisation of dose [concentration]-response for environment",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r10_en.pdf/bb902be7-a503-4ab7-9036-d866b8ddce69",
    },
    {
        "title": "PBT/vPvB assessment",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r11_en.pdf/a8cce23f-a65a-46d2-ac68-92fee1f9e54f",
    },
    {
        "title": "Use description",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r12_en.pdf/ea8fa5a6-6ba1-47f4-9e47-c7216e180197",
    },
    {
        "title": "Risk management measures and operational conditions",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r13_en.pdf/1f6d95d0-a9cb-479d-889e-f7f528e69fbd",
    },
    {
        "title": "Occupational exposure assessment",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r14_en.pdf/bb14b581-f7ef-4587-a171-17bf4b332378",
    },
    {
        "title": "Consumer exposure assessment",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r15_en.pdf/35e6f804-c84d-4962-acc5-6546dc5d9a55",
    },
    {
        "title": "Uncertainty analysis",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/information_requirements_r19_en.pdf/d5bd6c3f-3383-49df-894e-dea410ba4335",
    },
    {
        "title": "Environmental exposure assessment",
        "organization": "ECHA",
        "category": "Data analysis",
        "url": "https://echa.europa.eu/documents/10162/17224/IR_CSR_R16_V4_FINAL.pdf/b9f0f406-ff5f-4315-908e-e5f83115d6af",
    },
    {
        "title": "Managing Pyrophoric and Water Reactive Chemicals in the Laboratories",
        "organization": "NIH",
        "category": "Chemical safety",
        "url": "https://ors.od.nih.gov/sr/dohs/Documents/managing-pyrophoric-and-water-reactive-chemicals-in-the-laboratories.pdf",
    },
    {
        "title": "Nanotechnology safety and health program",
        "organization": "NIH",
        "category": "Chemical safety",
        "url": "https://ors.od.nih.gov/sr/dohs/Documents/nanotechnology-safety-and-health-program.pdf",
    },
    {
        "title": "Waste disposal guide",
        "organization": "NIH",
        "category": "Chemical safety",
        "url": "https://orf.od.nih.gov/EnvironmentalProtection/WasteDisposal/Documents/NIH-Waste-Disposal-Guide-2022-508Ready.pdf",
    },
    {
        "title": "How to undertake a qualitative human health assessment and document it in a chemical safety report",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17250/pg_15_qualitative-human_health_assessment_documenting_en.pdf/26a645d4-a81e-4223-8ca9-20162ae74e72",
    },
    {
        "title": "How to use and report (Q)SARs",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17250/pg_report_qsars_en.pdf/407dff11-aa4a-4eef-a1ce-9300f8460099",
    },
    {
        "title": "How to report robust study summaries",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17235/pg_report_robust_study_summaries_en.pdf/1e8302c3-98b7-4a50-aa22-f6f02ca54352",
    },
    {
        "title": "Practical guide for SME managers and REACH coordinators",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17250/pg_sme_managers_reach_coordinators_en.pdf/1253d9f9-d1f0-4ca8-9e7a-c81e337e3a7d",
    },
    {
        "title": (
            "How to assess whether a substance is used as an intermediate under strictly "
            "controlled conditions and how to report the information for the intermediate "
            "registration in IUCLID"
        ),
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/2324906/pg16_intermediate_registration_en.pdf/291b6e50-5598-42d3-8a2b-d63d50a68104",
    },
    {
        "title": "How to prepare a downstream user chemical safety report",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17250/pg17_du_csr_final_en.pdf/03aeab25-405a-45a4-9a66-5fa5c2dbfcb2",
    },
    {
        "title": "How to prepare and develop a Substance Identity Profile (SIP)",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17250/practical_guide_how_to_develop_prepare_sip_en.pdf/21576ee0-5ae4-93d7-f834-f1f38e06d026",
    },
    {
        "title": "How to use alternatives to animal testing to fulfil your information requirements for REACH registration",
        "organization": "ECHA",
        "category": "Chemical safety report",
        "url": "https://echa.europa.eu/documents/10162/17250/practical_guide_how_to_use_alternatives_en.pdf/148b30c7-c186-463c-a898-522a888a4404",
    },
]

CHEMINFORMATICS: List[Dict[str, str]] = [
    {
        "name": "RDKit",
        "provider": "Open-source cheminformatics toolkit",
        "logo": "images/org_logo/rdkit.png",
        "logo_size": "large",
        "url": "https://www.rdkit.org/",
        "description": (
            "Structure parsing, structure standardisation, physicochemical descriptors, "
            "substructure matching, fingerprints, similarity, and scaffolds "
        ),
    },
]
