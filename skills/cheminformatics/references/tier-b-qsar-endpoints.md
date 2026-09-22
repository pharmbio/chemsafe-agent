# Tier B reference — QSAR endpoint prediction

Full usage for `scripts/qsar_toolbox.py`. Every value here is a model estimate and
must be reported as predicted, with its applicability domain checked first.

# Tier B — QSAR endpoint prediction

All Tier-B helpers live in `scripts.qsar_toolbox` and consume a canonical SMILES (or, where indicated, a descriptor dict from `compute_descriptors`). Heavy dependencies (`admet-ai`, `deepchem`) are optional — when missing, the relevant helpers return `{"status": "unavailable", "note": "pip install ..."}` instead of raising.

## Drug-likeness Rules (composite)

Use to get Lipinski Ro5, Veber, Egan, and Ghose flags in one call from a descriptor dict.

**Precondition:** call `compute_descriptors` first; pass the returned dict.

```python
from scripts.cheminformatics import compute_descriptors
from scripts.qsar_toolbox import calc_drug_likeness

desc = compute_descriptors(std.canonical_smiles)
drug = calc_drug_likeness(desc)
# -> {"lipinski_violations": ..., "lipinski_rule_of_5_pass": ...,
#     "veber_oral_bioavail_pass": ..., "egan_oral_bioavail_pass": ...,
#     "ghose_filter_pass": ...}
```

Rules:

- **Drug-likeness only — not a hazard criterion.** A failing Ro5 or Veber flag has no place in a hazard classification argument.
- Use the Tier-A `lipinski_flags` helper if you only need the four Ro5 booleans without the Veber/Egan/Ghose composite.

---

## ADMET Prediction (admet-ai)

Use to predict 23 ADMET / toxicity endpoints (AMES, hERG, DILI, LD50, skin sensitization, ClinTox; Caco-2, HIA, bioavailability, aqueous solubility; BBB, plasma protein binding, VDss, P-gp; CYP3A4/2D6/2C19/2C9/1A2 inhibition + CYP3A4 substrate; half-life; microsome / hepatocyte clearance). Backed by Chemprop MPNN models pre-trained on Therapeutics Data Commons (TDC) — no training step required.

**Precondition:** canonical SMILES (run `standardize_smiles` first). Each endpoint has its own published AD that must be checked under [Applicability Domain](#applicability-domain-qsar-principle-3) before the prediction can be weighted.

```python
from scripts.qsar_toolbox import calc_admet, ADMET_AI_OK

if not ADMET_AI_OK:
    raise RuntimeError("admet-ai not installed")

admet = calc_admet(std.canonical_smiles)
# admet["AMES_mutagenicity_prob"], admet["hERG_inhibition_prob"],
# admet["LD50_oral_mg_per_kg"], admet["BBB_penetration_prob"], ...
```

Most endpoints return a `*_prob` in [0, 1]; the dose/permeability/solubility/volume endpoints return log-scaled values (with `LD50_oral_mg_per_kg` also exposed as the un-logged value). For the full unit/scale table, see [`references/reference-tables.md`](references/reference-tables.md#admet-output-scale-conventions).

Rules:

- **First call lazily loads the model** (`_get_admet_model`). Subsequent calls reuse it; do not instantiate `ADMETModel()` manually.
- **Each endpoint is a separate model with a separate AD.** Treat them independently in `woe_reasoning`; do not roll them up except via `classify_ghs`.
- **`*_prob > 0.5` is the helper's binary cutoff** for the draft GHS rollup, but the right cutoff for a hazard call is endpoint- and context-specific. State the threshold you used.
- Reference: Swanson et al. 2023 — github.com/swansonk14/admet_ai.

---

## Tox21 Regulatory Endpoints (DeepChem)

Use to predict activity for the 12 Tox21 endpoints: 7 nuclear receptors (NR-AR, NR-AR-LBD, NR-AhR, NR-Aromatase, NR-ER, NR-ER-LBD, NR-PPAR-gamma) and 5 stress-response pathways (SR-ARE, SR-ATAD5, SR-HSE, SR-MMP, SR-p53). Backed by a DeepChem GraphConv model trained on the Tox21 dataset.

**Precondition:** canonical SMILES. First call downloads ~10 MB of data and trains the model (~5 min on CPU); subsequent calls restore from `~/.cache/chemical_safety_calc/tox21_graphconv/` in <10 s. The cache survives container restarts only if the cache dir is mounted as a volume.

```python
from scripts.qsar_toolbox import calc_tox21

tox21 = calc_tox21(std.canonical_smiles)
# tox21["NR-AR"] == {"probability_active": 0.12, "prediction": "inactive"}
```

Rules:

- **Activity ≠ classification.** A "predicted active" call on, say, NR-ER is one *mechanistic line of evidence* for endocrine disruption — the regulatory call still requires `woe_reasoning` integration with apical endpoint data.
- **The training Tox21 dataset is small and unbalanced.** Treat low-prevalence endpoints (e.g. SR-ATAD5) with extra skepticism and check the model's reliability metrics, not just the prediction.
- **AD is task-specific.** Each of the 12 endpoints has its own training subset; an in-AD call for NR-AR does not imply in-AD for SR-p53.

---

## Ecotoxicology (baseline-narcosis QSARs)

Use to produce predicted aquatic toxicity endpoints (fish 96-h LC50, daphnia 48-h EC50, algae 72-h EC50) and a bioconcentration factor (BCF), with a draft GHS aquatic-hazard classification per organism.

**Precondition:** descriptor dict from `compute_descriptors`. Models are valid for non-ionic organics with logP ~0–7 and MW ~50–500; reactive, electrophilic, or ionisable compounds may deviate >1 log unit — flag explicitly in `woe_reasoning`.

```python
from scripts.cheminformatics import compute_descriptors
from scripts.qsar_toolbox import calc_ecotoxicology

desc = compute_descriptors(std.canonical_smiles)
eco = calc_ecotoxicology(desc)
# eco["fish_fathead_minnow"]["value_mg_L"], eco["bioconcentration"]["BCF_L_per_kg"], ...
```

The three aquatic endpoints (fish, daphnia, algae) plus BCF each come from a published baseline-narcosis QSAR; see [`references/reference-tables.md`](references/reference-tables.md#ecotoxicology-model-references) for the model citations.

Rules:

- **Baseline narcosis is the *floor* of aquatic toxicity** for non-reactive compounds. Reactive electrophiles, surfactants, and ionisable compounds will be more toxic than the prediction; state this as an AD failure.
- **`REACH_B_flag` (BCF ≥ 2000) and `REACH_vB_flag` (BCF ≥ 5000)** are screening triggers for REACH Annex XIII — they are not the B/vB determination, which requires integration with experimental BCF data via `woe_reasoning`.
- Per-organism GHS aquatic classes (`H400` / `H401` / `H402`) are draft — the consolidated GHS call lives in `classify_ghs` and is itself only a draft.

---

## Melting Point (empirical QSAR)

Use to get a quick melting-point estimate when no experimental Tm is available.

**Precondition:** descriptor dict from `compute_descriptors`.

```python
from scripts.qsar_toolbox import calc_melting_point

mp = calc_melting_point(desc)
# mp["melting_point_C"], mp["uncertainty"] == "+/- 40-60 C (QSAR estimate)"
```

Rules:

- **Typical uncertainty is ±40–60 °C** — Karthikeyan et al. 2005 (J. Chem. Inf. Model. 45:581–590). Treat the estimate as order-of-magnitude only.
- **Salts, co-crystals, and polymorphs deviate significantly.** If the input might be any of those, do not rely on the prediction.
- Prefer an experimental Tm from `database_traversal` (PubChem) when available; record the QSAR value only as a sanity check.

---

## Explosivity (SMARTS + oxygen balance)

Use to flag energetic functional groups (nitro, nitroso, organic peroxide, hydroperoxide, azide, diazo, fulminate, terminal alkyne, N-oxide, nitramine, acyl peroxide, diazonium, chlorate/perchlorate) and compute an oxygen-balance indicator.

**Precondition:** RDKit `Mol` (from `parse_smiles`).

```python
from scripts.cheminformatics import parse_smiles
from scripts.qsar_toolbox import calc_explosivity

mol = parse_smiles(std.canonical_smiles)
expl = calc_explosivity(mol)
# expl["explosive_groups_detected"], expl["oxygen_balance_percent"],
# expl["explosivity_risk_level"], expl["ghs_unstable_explosive_flag"]
```

Rules:

- **Rule-based screen only.** Formal GHS classification for explosivity (Class 1 / Unstable Explosive) requires physical testing — UN gap test, BAM drop-weight test — not SMARTS matching.
- **Oxygen balance in [-40 %, +10 %] is a known instability window** (Pepekin method); the helper adds 1 to the risk score when the molecule falls in this band.
- **`ghs_unstable_explosive_flag` triggers at ≥ 2 energetic groups** — this is a screening heuristic, not a regulator-validated threshold.

---

## Draft GHS Rollup (screening)

Use to get a draft GHS hazard summary (signal word, H-codes, pictograms, PBT flags) derived from the Tier-B predictions above. **Screening output only** — every output of `classify_ghs` carries a `"draft": True` marker.

**Precondition:** the descriptor / admet / eco / explosivity dicts from the helpers above.

```python
from scripts.qsar_toolbox import classify_ghs

ghs = classify_ghs(desc, admet, eco, expl)
# ghs["signal_word"] in {"DANGER", "WARNING", "No hazards identified"}
# ghs["hazard_statements"] = [{"class": ..., "H_code": ..., "statement": ...}, ...]
# ghs["ghs_pictograms"], ghs["pbt_assessment"], ghs["regulatory_note"]
```

Rules:

- **`classify_ghs` is a draft / triage output. It must not be placed on an SDS, in a REACH dossier, or in a regulator-facing classification call.** The authoritative classification lives in `woe_reasoning`, which integrates these predictions with experimental + read-across evidence and AD status.
- **AD status is not propagated automatically.** If any of the underlying Tier-B endpoints is out of AD, the corresponding GHS line must be marked as low-confidence in `woe_reasoning`.
- **Persistence (P in PBT) is not computed here.** The `pbt_assessment` field intentionally omits P — biodegradation data must come from experimental sources or a dedicated biodegradation QSAR.

---

## Full Safety Profile (orchestrator)

Use when you want every Tier-B endpoint in one nested dict — for a screening report, the first row of a comparison table, or an initial triage of a new chemical.

```python
from scripts.qsar_toolbox import calculate_chemical_safety

result = calculate_chemical_safety("CC(=O)Oc1ccccc1C(=O)O")  # Aspirin
# Keys: smiles_input, canonical_smiles, physicochemical, drug_likeness,
#       admet_toxicity, tox21_endpoints, ecotoxicology, melting_point,
#       explosivity, ghs_classification
```

Inspecting individual sections:

```python
# Physchem descriptors
phys = result["physicochemical"]
# Drug-likeness rules
drug = result["drug_likeness"]
# Predicted ADMET endpoints (or {"status": "unavailable"} if admet-ai missing)
admet = result["admet_toxicity"]
# Tox21 endpoints (or {"status": "unavailable"} if deepchem missing)
tox21 = result["tox21_endpoints"]
# Baseline-narcosis ecotox + BCF
eco = result["ecotoxicology"]
# Empirical Tm
mp = result["melting_point"]
# SMARTS + oxygen-balance explosivity flags
expl = result["explosivity"]
# Draft GHS rollup (signal word, H-codes, pictograms, PBT)
ghs = result["ghs_classification"]
```

Rules:

- **Raises `ValueError` on an unparseable SMILES** — the orchestrator deliberately does not silently fall back.
- **The orchestrator runs `standardize_smiles` internally** and uses the canonical form for all downstream calls. The original input is preserved in `result["smiles_input"]`.
- **Optional-dep sections degrade gracefully.** If `admet-ai` or `deepchem` is missing, the corresponding section returns `{"status": "unavailable", "note": ...}` and the rest of the profile still computes — but the draft GHS rollup will be missing those hazard lines.
- **Output is JSON-serializable**, suitable for inclusion in a `woe_reasoning` evidence record.

---

