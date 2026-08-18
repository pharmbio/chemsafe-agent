from __future__ import annotations

import os
import sys
import types
import warnings
from pathlib import Path
from typing import Optional

warnings.filterwarnings("ignore")

from rdkit import Chem
from rdkit.Chem import Descriptors

from backend.utils.skill_paths import register_skill_scripts

register_skill_scripts()  # sibling skill scripts import as `scripts.<module>`

from scripts.cheminformatics import (
    compute_descriptors,
    parse_smiles,
    standardize_smiles,
)

# admet-ai (optional, highly recommended)
try:
    from admet_ai import ADMETModel
    ADMET_AI_OK = True
except ImportError:
    ADMET_AI_OK = False

# DeepChem (optional)
try:
    import dgl  # noqa: F401
except Exception:  # FileNotFoundError, ImportError, OSError, ...
    sys.modules["dgl"] = types.ModuleType("dgl")

try:
    import torch
    import deepchem as dc
    # The legacy Keras ``GraphConvModel`` passes ``fused=False`` to
    # ``BatchNormalization``, which Keras 3 removed; use the torch backend model.
    from deepchem.models.torch_models import GraphConvModel as _TorchGraphConvModel
    import numpy as np  # noqa: F401  (used transitively by DeepChem)
    DEEPCHEM_OK = True
except Exception:
    DEEPCHEM_OK = False

# Cache directory for the trained Tox21 model
_CACHE_DIR = Path.home() / ".cache" / "chemical_safety_calc"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 12 Tox21 task names (nuclear receptors + stress response)
TOX21_TASKS = [
    "NR-AR", "NR-AR-LBD", "NR-AhR", "NR-Aromatase",
    "NR-ER", "NR-ER-LBD", "NR-PPAR-gamma",
    "SR-ARE", "SR-ATAD5", "SR-HSE", "SR-MMP", "SR-p53",
]

# GraphConv architecture (torch backend). ``number_input_features`` is required
# by the torch ``GraphConvModel`` and must align with ``graph_conv_layers`` as
# ``[number_atom_features, *graph_conv_layers[:-1]]``. With the default 75 atom
# features and two 64-wide conv layers that is ``[75, 64]``.
_GRAPH_CONV_LAYERS = [64, 64]
_NUMBER_INPUT_FEATURES = [75, 64]



# Drug-likeness rules (Lipinski / Veber / Egan / Ghose)
def calc_drug_likeness(descriptors: dict) -> dict:
    """Return Lipinski / Veber / Egan / Ghose pass-fail flags from a
    descriptor dict produced by ``compute_descriptors``.

    For *drug-likeness* only — not a hazard criterion.
    """
    mw    = descriptors["mw"]
    logP  = descriptors["logp_crippen"]
    hbd   = descriptors["hbd"]
    hba   = descriptors["hba"]
    tpsa  = descriptors["tpsa"]
    rb    = descriptors["rotatable_bonds"]
    natms = descriptors["heavy_atoms"]

    lip_viol = sum([mw > 500, logP > 5, hbd > 5, hba > 10])

    return {
        "lipinski_violations":      lip_viol,
        "lipinski_rule_of_5_pass":  lip_viol <= 1,
        "veber_oral_bioavail_pass": rb <= 10 and tpsa <= 140,
        "egan_oral_bioavail_pass":  logP <= 5.88 and tpsa <= 131.6,
        "ghose_filter_pass":        (160 <= mw <= 480)
                                    and (-0.4 <= logP <= 5.6)
                                    and (20 <= natms <= 70),
    }



# ADMET-AI (pre-trained Chemprop/MPNN on TDC datasets)
# Reference: Swanson et al. 2023 — github.com/swansonk14/admet_ai

_admet_singleton: Optional[object] = None


def _get_admet_model():
    global _admet_singleton
    if _admet_singleton is None:
        _admet_singleton = ADMETModel()
    return _admet_singleton


# Friendly name -> possible column names in admet-ai output
_ADMET_MAP = {
    # Toxicity
    "AMES_mutagenicity_prob":           ["AMES", "ames"],
    "hERG_inhibition_prob":             ["hERG", "herg", "hERG_Karim"],
    "hepatotoxicity_DILI_prob":         ["DILI", "dili"],
    "LD50_oral_log_mg_per_kg":          ["LD50_Zhu", "ld50_zhu"],
    "skin_sensitization_prob":          ["Skin_Reaction", "skin_reaction"],
    "clinical_toxicity_prob":           ["ClinTox", "clintox"],
    # Absorption
    "caco2_permeability_log_cm_s":      ["Caco2_Wang", "caco2_wang"],
    "oral_bioavailability_prob":        ["Bioavailability_Ma", "bioavailability_ma"],
    "solubility_log_mol_L":             ["Solubility_AqSolDB", "solubility_aqsoldb"],
    "HIA_oral_absorption_prob":         ["HIA_Hou", "hia_hou"],
    # Distribution
    "BBB_penetration_prob":             ["BBB_Martins", "bbb_martins"],
    "plasma_protein_binding_pct":       ["PPBR_AZ", "ppbr_az"],
    "VDss_log_L_per_kg":                ["VDss_Lombardo", "vdss_lombardo"],
    "Pgp_substrate_prob":               ["Pgp_Broccatelli", "pgp_broccatelli"],
    # Metabolism
    "CYP3A4_substrate_prob":            ["CYP3A4_Substrate_CarbonMangels"],
    "CYP3A4_inhibitor_prob":            ["CYP3A4_Veith", "cyp3a4_veith"],
    "CYP2D6_inhibitor_prob":            ["CYP2D6_Veith", "cyp2d6_veith"],
    "CYP2C19_inhibitor_prob":           ["CYP2C19_Veith", "cyp2c19_veith"],
    "CYP2C9_inhibitor_prob":            ["CYP2C9_Veith", "cyp2c9_veith"],
    "CYP1A2_inhibitor_prob":            ["CYP1A2_Veith", "cyp1a2_veith"],
    # Elimination
    "half_life_h":                      ["Half_Life_Obach", "half_life_obach"],
    "clearance_microsome_mL_min_kg":    ["Clearance_Microsome_AZ"],
    "clearance_hepatocyte_mL_min_kg":   ["Clearance_Hepatocyte_AZ"],
}


def calc_admet(canonical_smiles: str) -> dict:
    """Predict ADMET and toxicity endpoints using admet-ai.

    Scale notes:
      *_prob                     : probability [0-1] (binary classifier)
      LD50_oral_log_mg_per_kg    : log10(mg/kg)  =>  convert: 10^value
      caco2_permeability_log_cm_s: log10(cm/s)
      solubility_log_mol_L       : log10(mol/L)
      VDss_log_L_per_kg          : log10(L/kg)
    """
    if not ADMET_AI_OK:
        return {"status": "unavailable", "note": "pip install admet-ai"}
    try:
        model = _get_admet_model()
        df = model.predict(smiles=[canonical_smiles])
        row = df.iloc[0].to_dict()

        out = {}
        for key, candidates in _ADMET_MAP.items():
            for col in candidates:
                if col in row:
                    val = row[col]
                    if hasattr(val, "item"):
                        val = float(val.item())
                    if isinstance(val, float):
                        val = round(val, 4)
                    out[key] = val
                    break

        # Convenience: expose LD50 in mg/kg (not log scale)
        if "LD50_oral_log_mg_per_kg" in out:
            out["LD50_oral_mg_per_kg"] = round(10 ** out["LD50_oral_log_mg_per_kg"], 1)

        return out

    except Exception as e:
        return {"status": "error", "error": str(e)}


# Tox21 (DeepChem GraphConv — trained once, cached to disk)
# 12 regulatory endpoints. First run: downloads ~10 MB, trains ~5 min CPU,
# caches to ~/.cache/chemical_safety_calc/tox21_graphconv/.

_tox21_cache: Optional[tuple] = None  # (model, transformers)


def _build_tox21_model(model_dir: str):
    """Construct the torch-backend GraphConv model used for Tox21.

    Pinned to CPU: DeepChem's torch GraphConv layers call ``.numpy()`` on
    intermediate tensors, which fails on Apple-Silicon ``mps`` devices.
    """
    return _TorchGraphConvModel(
        n_tasks=len(TOX21_TASKS),
        number_input_features=_NUMBER_INPUT_FEATURES,
        graph_conv_layers=_GRAPH_CONV_LAYERS,
        mode="classification",
        batch_size=128,
        model_dir=model_dir,
        device=torch.device("cpu"),
    )


def _load_or_train_tox21() -> tuple:
    global _tox21_cache
    if _tox21_cache is not None:
        return _tox21_cache

    model_dir = str(_CACHE_DIR / "tox21_graphconv")

    if os.path.isdir(model_dir) and os.listdir(model_dir):
        try:
            _, datasets, transformers = dc.molnet.load_tox21(
                featurizer="GraphConv", splitter=None
            )
            model = _build_tox21_model(model_dir)
            model.restore()
            _tox21_cache = (model, transformers)
            return _tox21_cache
        except Exception:
            pass  # cache corrupted -> retrain

    print("[Tox21] First-time setup: downloading data and training model (~5 min on CPU)...")
    print(f"        Will be saved at: {model_dir}")

    tasks, datasets, transformers = dc.molnet.load_tox21(
        featurizer="GraphConv", splitter="scaffold"
    )
    train_ds, _val, _test = datasets

    model = _build_tox21_model(model_dir)
    model.fit(train_ds, nb_epoch=50)
    model.save_checkpoint()
    print("[Tox21] Model trained and cached. Future calls will be fast.")

    _tox21_cache = (model, transformers)
    return _tox21_cache


def calc_tox21(canonical_smiles: str) -> dict:
    """Predict activity for the 12 Tox21 regulatory endpoints.

    Each endpoint returns:
      probability_active : float [0-1]
      prediction         : "active" | "inactive"
    """
    if not DEEPCHEM_OK:
        return {"status": "unavailable", "note": "pip install deepchem"}
    try:
        model, _transformers = _load_or_train_tox21()
        featurizer = dc.feat.ConvMolFeaturizer()
        X = featurizer.featurize([canonical_smiles])
        ds = dc.data.NumpyDataset(X=X, y=None)
        preds = model.predict(ds)  # shape: (1,12) or (1,12,2)

        results = {}
        for i, task in enumerate(TOX21_TASKS):
            prob = float(preds[0, i, 1]) if preds.ndim == 3 else float(preds[0, i])
            results[task] = {
                "probability_active": round(prob, 4),
                "prediction": "active" if prob > 0.5 else "inactive",
            }
        return results

    except Exception as e:
        return {"status": "error", "error": str(e)}



# Ecotoxicology (baseline-narcosis QSARs)
# References:
#   Fish (Fathead minnow, 96h LC50):  Veith et al. 1983
#   Daphnia magna (48h EC50):          Cronin & Dearden 1995
#   Green algae (72h EC50):            Netzeva et al. 2005 (simplified)
#   BCF (bioconcentration):            Meylan et al. 1999
# Applicability domain: non-ionic organics, logP ~0-7, MW ~50-500.
# Reactive, electrophilic, or ionisable compounds may deviate >1 log unit.

def calc_ecotoxicology(descriptors: dict) -> dict:
    """Baseline-narcosis QSAR estimates for fish, daphnia, algae, and BCF."""
    logP = descriptors["logp_crippen"]
    mw   = descriptors["mw"]

    fish_log  = 1.987 - 0.871 * logP
    fish_mmol = 10 ** fish_log
    fish_mgL  = fish_mmol * mw

    daph_log  = 1.498 - 0.785 * logP
    daph_mmol = 10 ** daph_log
    daph_mgL  = daph_mmol * mw

    alga_log  = 1.374 - 0.740 * logP
    alga_mmol = 10 ** alga_log
    alga_mgL  = alga_mmol * mw

    log_bcf = 0.77 * logP - 0.70
    bcf     = 10 ** log_bcf

    def _ghs_aq(val_mgL: float) -> str:
        if val_mgL <= 1:    return "Aquatic Acute 1 - H400 (Very toxic)"
        if val_mgL <= 10:   return "Aquatic Acute 2 - H401 (Toxic)"
        if val_mgL <= 100:  return "Aquatic Acute 3 - H402 (Harmful)"
        return "Not classified (>100 mg/L)"

    return {
        "fish_fathead_minnow": {
            "endpoint":          "96-h LC50",
            "value_mmol_L":      round(fish_mmol, 5),
            "value_mg_L":        round(fish_mgL, 3),
            "log10_LC50_mmol_L": round(fish_log, 3),
            "ghs_class":         _ghs_aq(fish_mgL),
            "model_ref":         "Veith et al. 1983 (baseline narcosis)",
        },
        "daphnia_magna": {
            "endpoint":      "48-h EC50",
            "value_mmol_L":  round(daph_mmol, 5),
            "value_mg_L":    round(daph_mgL, 3),
            "ghs_class":     _ghs_aq(daph_mgL),
            "model_ref":     "Cronin & Dearden 1995 (baseline narcosis)",
        },
        "green_algae": {
            "endpoint":      "72-h EC50",
            "value_mmol_L":  round(alga_mmol, 5),
            "value_mg_L":    round(alga_mgL, 3),
            "ghs_class":     _ghs_aq(alga_mgL),
            "model_ref":     "Netzeva et al. 2005 (simplified)",
        },
        "bioconcentration": {
            "log_BCF":    round(log_bcf, 3),
            "BCF_L_per_kg": round(bcf, 2),
            "REACH_B_flag":  bcf >= 2000,
            "REACH_vB_flag": bcf >= 5000,
            "model_ref":   "Meylan et al. 1999",
        },
        "domain_note": (
            "Baseline narcosis models. Valid for non-ionic organics, logP 0-7. "
            "Reactive or ionisable compounds may deviate >1 log unit."
        ),
    }



# Melting point (empirical QSAR)
# Reference: Karthikeyan et al. 2005, J. Chem. Inf. Model. 45:581-590
# Typical uncertainty: +/-40-60 C. Salts and polymorphs deviate more.

def calc_melting_point(descriptors: dict) -> dict:
    mw    = descriptors["mw"]
    logP  = descriptors["logp_crippen"]
    tpsa  = descriptors["tpsa"]
    hbd   = descriptors["hbd"]
    hba   = descriptors["hba"]
    rings = descriptors["rings"]
    rb    = descriptors["rotatable_bonds"]

    Tm_K = (130.0
            + 0.517 * mw
            - 12.0  * logP
            + 0.350 * tpsa
            + 30.0  * hbd
            + 5.0   * hba
            + 25.0  * rings
            - 3.0   * rb)

    return {
        "melting_point_K":   round(Tm_K, 1),
        "melting_point_C":   round(Tm_K - 273.15, 1),
        "uncertainty":       "+/- 40-60 C (QSAR estimate)",
        "model_ref":         "Karthikeyan et al. 2005 (J. Chem. Inf. Model.)",
        "note": "Salts, co-crystals, polymorphs deviate significantly.",
    }


# Explosivity (SMARTS + oxygen balance)
# Rule-based only — formal GHS classification requires physical testing
# (UN gap test, BAM drop-weight test).

_EXPLOSIVE_SMARTS = {
    "nitro":              "[N+](=O)[O-]",
    "nitroso":            "[c,C][N]=O",
    "organic_peroxide":   "[#6]-O-O-[#6]",
    "hydroperoxide":      "[#6]-O-O",
    "azide":              "[#7]=[N+]=[N-]",
    "diazo":              "[CX3H0]=[N+]=[N-]",
    "fulminate":          "[C]#[N+][O-]",
    "terminal_alkyne":    "[CX2H1]#[CX2]",
    "N_oxide":            "[n,N]->[O]",
    "nitramine":          "[N;R0](-[#7])(=O)",
    "acyl_peroxide":      "[CX3](=O)-O-O-[CX3](=O)",
    "diazonium":          "[#6]-[N+]#N",
    "chlorate_perchlorate": "[Cl](=O)(=O)",
}


def calc_explosivity(mol) -> dict:
    """Detect SMARTS-matched energetic functional groups and compute oxygen
    balance (Pepekin method) as a complementary indicator.
    """
    flags = {}
    for name, smarts in _EXPLOSIVE_SMARTS.items():
        patt = Chem.MolFromSmarts(smarts)
        if patt is not None:
            flags[name] = bool(mol.HasSubstructMatch(patt))

    n_flags = sum(flags.values())

    formula: dict[str, int] = {}
    for atom in mol.GetAtoms():
        sym = atom.GetSymbol()
        formula[sym] = formula.get(sym, 0) + 1
        formula["H"] = formula.get("H", 0) + atom.GetTotalNumHs()

    C  = formula.get("C", 0)
    H  = formula.get("H", 0)
    O  = formula.get("O", 0)
    mw = Descriptors.MolWt(mol)

    ob = round((1600 / mw) * (O - 2 * C - H / 2), 2) if mw > 0 else None

    risk_score = n_flags + (1 if ob is not None and -40 <= ob <= 10 else 0)
    risk_level = "High" if risk_score >= 3 else ("Medium" if risk_score >= 1 else "Low")

    return {
        "explosive_groups_detected":    flags,
        "num_explosive_groups":         n_flags,
        "oxygen_balance_percent":       ob,
        "explosivity_risk_level":       risk_level,
        "ghs_unstable_explosive_flag":  n_flags >= 2,
        "note": (
            "Rule-based analysis (SMARTS). Formal GHS classification "
            "requires physical experiments (UN gap test, BAM drop-weight)."
        ),
    }



# Draft GHS rollup

def classify_ghs(descriptors: dict, admet: dict, eco: dict, expl: dict) -> dict:
    """Draft GHS rollup (screening output, not authoritative).

    Inputs are predicted properties; reviewers must confirm via woe_reasoning
    with experimental evidence and applicability-domain status before any
    SDS, REACH, or regulatory use.
    """
    hazards = []

    ld50 = admet.get("LD50_oral_mg_per_kg")
    if ld50 is not None:
        if   ld50 <= 5:     hazards.append(("Acute Tox. 1 (Oral)", "H300", "Fatal if swallowed"))
        elif ld50 <= 50:    hazards.append(("Acute Tox. 2 (Oral)", "H300", "Fatal if swallowed"))
        elif ld50 <= 300:   hazards.append(("Acute Tox. 3 (Oral)", "H301", "Toxic if swallowed"))
        elif ld50 <= 2000:  hazards.append(("Acute Tox. 4 (Oral)", "H302", "Harmful if swallowed"))

    if admet.get("AMES_mutagenicity_prob", 0) > 0.5:
        hazards.append(("Muta. 2 (suspected)", "H341",
                        "Suspected of causing genetic defects (AMES+)"))

    if admet.get("hERG_inhibition_prob", 0) > 0.5:
        hazards.append(("Cardiotox. / hERG", "H371",
                        "May cause cardiac disorders (hERG inhibition predicted)"))

    if admet.get("hepatotoxicity_DILI_prob", 0) > 0.5:
        hazards.append(("STOT RE 2 (Liver)", "H373",
                        "May cause liver damage through prolonged/repeated exposure"))

    if admet.get("skin_sensitization_prob", 0) > 0.5:
        hazards.append(("Skin Sens. 1", "H317",
                        "May cause an allergic skin reaction"))

    fish_mgL = eco.get("fish_fathead_minnow", {}).get("value_mg_L", 9999)
    daph_mgL = eco.get("daphnia_magna",       {}).get("value_mg_L", 9999)
    alga_mgL = eco.get("green_algae",         {}).get("value_mg_L", 9999)
    min_ec50  = min(fish_mgL, daph_mgL, alga_mgL)

    if   min_ec50 <= 1:   hazards.append(("Aquatic Acute 1", "H400", "Very toxic to aquatic life"))
    elif min_ec50 <= 10:  hazards.append(("Aquatic Acute 2", "H401", "Toxic to aquatic life"))
    elif min_ec50 <= 100: hazards.append(("Aquatic Acute 3", "H402", "Harmful to aquatic life"))

    bio = eco.get("bioconcentration", {})
    if bio.get("REACH_vB_flag"):
        hazards.append(("Aquatic Chronic / vPvB", "H413",
                        "May cause long lasting effects (vB: BCF >= 5000)"))
    elif bio.get("REACH_B_flag"):
        hazards.append(("Aquatic Chronic / PB", "H413",
                        "May cause long lasting effects (B: BCF >= 2000)"))

    if expl.get("ghs_unstable_explosive_flag"):
        hazards.append(("Explo. Unstable", "H200",
                        "Unstable explosive - multiple energetic groups detected"))

    _picto_map = {
        "H200": "GHS01 - Exploding bomb",
        "H300": "GHS06 - Skull & crossbones",
        "H301": "GHS06 - Skull & crossbones",
        "H302": "GHS07 - Exclamation mark",
        "H317": "GHS07 - Exclamation mark",
        "H341": "GHS08 - Health hazard",
        "H371": "GHS08 - Health hazard",
        "H373": "GHS08 - Health hazard",
        "H400": "GHS09 - Environmental hazard",
        "H401": "GHS09 - Environmental hazard",
        "H402": "GHS09 - Environmental hazard",
        "H413": "GHS09 - Environmental hazard",
    }
    pictograms = sorted({_picto_map[h[1]] for h in hazards if h[1] in _picto_map})

    danger_codes = {"H200", "H300", "H301", "H341", "H371", "H373", "H400"}
    used_codes   = {h[1] for h in hazards}
    signal_word  = (
        "DANGER"  if used_codes & danger_codes else
        "WARNING" if hazards else
        "No hazards identified"
    )

    return {
        "draft": True,
        "signal_word": signal_word,
        "num_hazards": len(hazards),
        "hazard_statements": [
            {"class": h[0], "H_code": h[1], "statement": h[2]}
            for h in hazards
        ],
        "ghs_pictograms": pictograms,
        "pbt_assessment": {
            "T_toxic":       ld50 is not None and ld50 < 100,
            "B_bioaccum":    bio.get("REACH_B_flag", False),
            "vB_very_bioaccum": bio.get("REACH_vB_flag", False),
            "note": "P (Persistent) requires biodegradation data (not calculated here).",
        },
        "regulatory_note": (
            "DRAFT / SCREENING ONLY. Derived from ML/QSAR predicted properties. "
            "Authoritative classification must come from woe_reasoning, which "
            "integrates this with experimental + read-across evidence and "
            "applicability-domain status. Do not use directly on an SDS or "
            "in a REACH/GHS dossier."
        ),
    }



# Full-profile orchestrator
def calculate_chemical_safety(smiles: str) -> dict:
    """Compute a full chemical safety and toxicological profile from a SMILES.

    Returns a JSON-serializable nested dict with keys:
      smiles_input, canonical_smiles, physicochemical, drug_likeness,
      admet_toxicity, tox21_endpoints, ecotoxicology, melting_point,
      explosivity, ghs_classification (draft).

    Raises ValueError if the SMILES cannot be parsed.

    Example:
        >>> r = calculate_chemical_safety("CC(=O)Oc1ccccc1C(=O)O")  # Aspirin
        >>> r["ghs_classification"]["signal_word"]
        'No hazards identified'
    """
    mol = parse_smiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: '{smiles}'")

    std = standardize_smiles(smiles)
    canonical = std.canonical_smiles or Chem.MolToSmiles(mol)

    phys  = compute_descriptors(mol)
    drug  = calc_drug_likeness(phys)
    admet = calc_admet(canonical) if ADMET_AI_OK else {
        "status": "unavailable", "note": "pip install admet-ai"
    }
    tox21 = calc_tox21(canonical) if DEEPCHEM_OK else {
        "status": "unavailable", "note": "pip install deepchem"
    }
    eco   = calc_ecotoxicology(phys)
    mp    = calc_melting_point(phys)
    expl  = calc_explosivity(mol)
    ghs   = classify_ghs(phys, admet, eco, expl)

    return {
        "smiles_input":       smiles,
        "canonical_smiles":   canonical,
        "physicochemical":    phys,
        "drug_likeness":      drug,
        "admet_toxicity":     admet,
        "tox21_endpoints":    tox21,
        "ecotoxicology":      eco,
        "melting_point":      mp,
        "explosivity":        expl,
        "ghs_classification": ghs,
    }


__all__ = [
    "calc_drug_likeness",
    "calc_admet",
    "calc_tox21",
    "calc_ecotoxicology",
    "calc_melting_point",
    "calc_explosivity",
    "classify_ghs",
    "calculate_chemical_safety",
]
