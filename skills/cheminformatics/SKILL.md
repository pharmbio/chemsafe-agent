---
name: cheminformatics
description: Structure-grounded chemistry evidence and predicted hazard endpoints via RDKit and a QSAR toolbox. Parses and standardizes molecules; computes physicochemical descriptors; screens structural alerts / toxicophores; runs similarity, scaffold/MCS and read-across analogue selection; performs applicability-domain checks; and predicts ADMET, Tox21, ecotoxicity, melting point, explosivity and a draft GHS classification. Use this skill whenever a task involves SMILES, InChI, InChIKey, CAS numbers or chemical structures — including chemical safety assessment, hazard or toxicity screening, QSAR prediction, read-across, PBT/vPvB or GHS work, and weight-of-evidence chemistry — even when the user does not name a specific tool.
---

# Cheminformatics Skill (RDKit + QSAR toolbox)

Produce structure-grounded evidence with RDKit, and predicted hazard endpoints with the QSAR toolbox. This skill is the structural-chemistry backbone for the agent: it feeds canonical identifiers into `database_traversal`, physchem descriptors into `woe_reasoning` Line 6, analogue pairs into RAAF read-across justifications, applicability-domain reports that gate QSAR reliance under OECD Principle 3, structural-alert hits into the mechanistic line of evidence, and predicted ADMET/ecotox/GHS endpoints as additional (predicted, AD-gated) evidence rows.

The repo whitelists `rdkit`, `admet_ai`, and `deepchem` in `core/tools/python_executor.py::DEFAULT_AUTHORIZED_IMPORTS`. Two curated helper modules sit under `scripts/`:

- `cheminformatics.py` — **Tier A**: deterministic, RDKit-only structural operations (parse, standardize, descriptors, alerts, similarity, scaffold/MCS, applicability domain, visualization).
- `qsar_toolbox.py` — **Tier B**: QSAR / ML hazard-endpoint prediction (ADMET via admet-ai, Tox21 via DeepChem, ecotox baseline-narcosis, empirical melting-point QSAR, SMARTS-based explosivity, and a *draft* GHS rollup that composes them).

Prefer these helpers over rewriting raw RDKit code; drop to raw RDKit only when neither helper exposes what you need.

Bulky lookups — the full descriptor glossary, FilterCatalog citations, ADMET output scales, and ecotox model references — live in [`references/reference-tables.md`](references/reference-tables.md). Read that file when you need to interpret a helper's output or cite a model's provenance; you do not need it to call the helpers.

This document describes **capabilities**, not a procedure. Select the capabilities the task needs and call them in whatever order the task implies. The only ordering constraints are the *preconditions* called out on each capability (for example, similarity and identity comparisons require standardized inputs — see [Standardization](#standardization-canonical-identity)).

## Two tiers, two epistemological statuses

| Tier | What it produces                          | Reliability                                   | Where it enters `woe_reasoning`                 |
|------|-------------------------------------------|-----------------------------------------------|--------------------------------------------------|
| A    | Deterministic structural facts and descriptors | Reproducible from RDKit alone                | Identity / physchem / structural-alert evidence |
| B    | QSAR / ML *predictions* of biological and hazard endpoints | Model-dependent; AD-gated under OECD Principle 3 | Predicted endpoints, weighted by AD + reliability |

Tier-B outputs never carry an authoritative classification on their own. The draft GHS rollup (`classify_ghs`) is **screening output only** — the authoritative classification call lives in `woe_reasoning`, which integrates Tier-B predictions with experimental evidence, read-across, and AD status.

---

## Capability Index

### Tier A — Deterministic structure ops (`cheminformatics.py`)

| Capability                                                 | Primary helper(s)                                                       |
|------------------------------------------------------------|--------------------------------------------------------------------------|
| [Parse SMILES / InChI](#parsing-and-input-validation)      | `parse_smiles`                                                           |
| [Standardization (canonical identity)](#standardization-canonical-identity) | `standardize_smiles`                                                     |
| [Physchem descriptors](#physchem-descriptors)              | `compute_descriptors`, `lipinski_flags`                                  |
| [Structural alerts (toxicophores)](#structural-alerts-toxicophore-screening) | `build_filter_catalog`, `find_structural_alerts`, `match_custom_smarts`  |
| [Similarity & analogue selection](#similarity-and-analogue-selection) | `morgan_fingerprint`, `tanimoto`, `nearest_neighbors`                    |
| [Scaffold & MCS](#scaffold-and-mcs)                        | `murcko_scaffold_smiles`, `maximum_common_substructure`                  |
| [Applicability domain (QSAR Principle 3)](#applicability-domain-qsar-principle-3) | `applicability_domain_check`                                             |
| [Visualization](#visualization)                            | `draw_molecules`                                                         |

### Tier B — QSAR endpoint prediction (`qsar_toolbox.py`)

| Capability                                                 | Primary helper(s)                                                       |
|------------------------------------------------------------|--------------------------------------------------------------------------|
| [Drug-likeness rules (composite)](#drug-likeness-rules-composite) | `calc_drug_likeness`                                                     |
| [ADMET prediction (admet-ai)](#admet-prediction-admet-ai)  | `calc_admet`                                                             |
| [Tox21 regulatory endpoints (DeepChem)](#tox21-regulatory-endpoints-deepchem) | `calc_tox21`                                                             |
| [Ecotoxicology (baseline narcosis QSARs)](#ecotoxicology-baseline-narcosis-qsars) | `calc_ecotoxicology`                                                     |
| [Melting point (empirical QSAR)](#melting-point-empirical-qsar) | `calc_melting_point`                                                     |
| [Explosivity (SMARTS + oxygen balance)](#explosivity-smarts--oxygen-balance) | `calc_explosivity`                                                       |
| [Draft GHS rollup (screening)](#draft-ghs-rollup-screening) | `classify_ghs`                                                           |
| [Full safety profile (orchestrator)](#full-safety-profile-orchestrator) | `calculate_chemical_safety`                                              |
| [Batch screening pattern](#batch-screening-pattern)        | `calculate_chemical_safety` over a dict of SMILES                        |

---

# Tier A — Deterministic structure ops

## Parsing and Input Validation

Use when an input arrives as a SMILES string and you need an RDKit `Mol`, or you need to verify that a user-supplied identifier is parseable at all.

```python
from scripts.cheminformatics import parse_smiles

mol = parse_smiles(user_smiles)
if mol is None:
    # Record the failure explicitly; do not silently substitute another input.
    print(f"Invalid SMILES: {user_smiles!r}")
```

Rules:

- **`parse_smiles` returns `None` for any unparseable input** — treat as a data-quality failure, not a soft fallback.
- **Accept SMILES, but also `Chem.MolFromInchi` if the input is an InChI.** Inputs coming from literature often arrive as InChI/InChIKey.
- **If a CAS or name is provided, resolve it to a structure before parsing.** Use `database_traversal` or a `pubchempy` call — RDKit does not resolve identifiers.
- **Preserve the original input in the evidence log.** Every structural claim traces back to the exact string the user supplied, not just the canonical form.

---

## Standardization (canonical identity)

Use whenever the canonical form of a molecule is needed: cross-database lookup keys (InChIKey), in-memory identity comparison, similarity, read-across, or any operation where "the same molecule drawn differently" must collapse to one representation.

**Precondition for:** similarity, nearest-neighbor search, applicability-domain checks, identity matching against PubChem / ChEMBL / NIOSH, and any read-across analogue selection. Comparing unstandardized SMILES produces false negatives in identity matching and false positives in read-across.

```python
from scripts.cheminformatics import standardize_smiles

std = standardize_smiles(user_smiles)
canonical = std.canonical_smiles   # for SMILES-keyed comparisons
inchikey  = std.inchikey           # for cross-database lookups (PubChem CID, ChEMBL ID)
```

### What the pipeline does

The helper runs, in order: normalize (fix common drawing errors) → strip salts (largest fragment) → neutralize (uncharge pH-dependent representations) → canonical tautomer → emit canonical SMILES + InChI + InChIKey. Each stage is independently opt-out via keyword arguments (`strip_salts=`, `neutralize=`, `canonical_tautomer=`) — use the decision table below.

### Decision table for optional stages

| Use case                                         | strip_salts | neutralize | canonical_tautomer |
|--------------------------------------------------|-------------|------------|--------------------|
| Cross-database lookup (PubChem, ChEMBL, NIOSH)   | yes         | yes        | yes                |
| Read-across analogue selection                   | yes         | yes        | **case by case**   |
| Ionic-liquid / quaternary-ammonium hazard call   | yes         | **no**     | yes                |
| Metal complex / organometallic                   | **no**      | **no**     | no                 |
| Reaction / metabolite scaffold work              | yes         | yes        | no                 |

Rules:

- **Record which flags were set, and why,** in the evidence log. Reviewers must be able to reconstruct the exact canonical form.
- **Do not neutralize when the species of interest is genuinely the ionized form** (permanent quaternary ammonium, ionic liquids, metal oxoanions).
- **Do not canonicalize tautomers for read-across when the parent and tautomer differ in reactive functional group** (e.g. a thione vs. thiol matters for metal-binding hazards).
- **Never strip stereochemistry to "simplify" a comparison.** Stereocenters and E/Z geometry are load-bearing for biological activity. If a downstream QSAR predicts without stereo, state that explicitly — do not mutate the input to match.

---

## Physchem Descriptors

Use to produce the physicochemical line of evidence (Line 6 in `woe_reasoning`), to supply features for PBT / vPvB screening, or to provide ADME/kinetics context. The Tier-B helpers (`calc_drug_likeness`, `calc_ecotoxicology`, `calc_melting_point`) consume the same dict, so you only compute it once.

```python
from scripts.cheminformatics import compute_descriptors, lipinski_flags

desc = compute_descriptors(std.canonical_smiles)
# -> {"molecular_formula": ..., "mw": ..., "exact_mass": ..., "heavy_atoms": ...,
#     "logp_crippen": ..., "molar_refractivity": ..., "tpsa": ...,
#     "hbd": ..., "hba": ..., "rotatable_bonds": ..., "aromatic_rings": ...,
#     "rings": ..., "num_stereocenters": ..., "formal_charge": ...,
#     "fraction_csp3": ..., "qed_drug_likeness": ...}
```

For what each key means and the `woe_reasoning` line of evidence it supports, see the descriptor glossary in [`references/reference-tables.md`](references/reference-tables.md#physicochemical-descriptor-glossary).

Rules:

- **`logp_crippen` is the Crippen estimate, not an experimental logP.** If an experimental value is available (via `database_traversal` PubChem), prefer that for the evidence table and record the RDKit estimate only as a sanity check.
- **Lipinski / Veber / Egan / Ghose / QED rules are drug-likeness filters, not hazard criteria.** `lipinski_flags(desc)` (Tier A) and `calc_drug_likeness(desc)` (Tier B) provide them for context but must not enter a hazard classification argument.
- **For PBT / vPvB screening**, logP and MW are necessary but not sufficient — the PBT call lives in `woe_reasoning` against REACH Annex XIII criteria surfaced by `sop_search`.
- Descriptors do not require standardized input to compute, but the values are only comparable across compounds if the inputs were standardized consistently.

---

## Structural Alerts (toxicophore screening)

Use to produce the mechanistic line of evidence (Line 7 in `woe_reasoning`). Prefer RDKit's built-in `FilterCatalog` for the well-established filters; write custom SMARTS only for alerts not covered by those catalogs.

### Built-in RDKit catalogs (preferred)

`build_filter_catalog` accepts these keys: `pains` (HTS frequent hitters), `brenk` (reactive/toxic/unstable functionality), `nih` (NIH annotated unwanted features), `zinc` (ZINC15 drug-likeness), and `chembl` (ChEMBL curation filters). For the authoritative citation to record alongside each hit, see [`references/reference-tables.md`](references/reference-tables.md#built-in-filtercatalog-provenance).

```python
from scripts.cheminformatics import (
    build_filter_catalog, find_structural_alerts,
)

catalog = build_filter_catalog(["pains", "brenk", "nih"])
hits = find_structural_alerts(std.canonical_smiles, catalog=catalog)
# -> [{"catalog": ..., "alert": ..., "description": ..., "smarts": ...}, ...]
```

### Custom SMARTS for regulated toxicophores

For regulator-defined alerts not in the built-in sets (e.g. Ashby–Tennant genotoxicity alerts, Benigni–Bossa carcinogenicity alerts, Cramer classes for TTC), encode them as SMARTS and match explicitly:

```python
from scripts.cheminformatics import match_custom_smarts

# Example: aromatic amine (Ashby–Tennant alert for genotoxic carcinogenicity)
ALERT_AROMATIC_AMINE = "[NX3;H2,H1;!$(NC=O);!$(N=*)]-c"
is_aromatic_amine = match_custom_smarts(std.canonical_smiles, ALERT_AROMATIC_AMINE)

# Example: Michael acceptor (electrophile class)
ALERT_MICHAEL = "[$([CX3]=[CX3])]-[$([CX3]=[OX1])]"
is_michael = match_custom_smarts(std.canonical_smiles, ALERT_MICHAEL)
```

Rules:

- **An alert hit is mechanistic *indication*, not a hazard classification.** A hit enters the evidence table as one mechanistic line; a classification call requires integration with apical endpoint evidence via `woe_reasoning`.
- **Record the exact SMARTS and its authoritative source** in the evidence table. Unsourced SMARTS are non-reproducible and cannot be defended in a dossier.
- **Distinguish "alert present" from "alert activated."** Metabolic activation, steric hindrance, and electronic effects often negate an alert's expected reactivity. Flag the hit; decide the weight in `woe_reasoning` with mechanistic + in-vitro evidence.
- **No alerts hit ≠ no hazard.** Absence of a structural alert is weak evidence; regulators treat it as a screen, never a clearance.

---

## Similarity and Analogue Selection

Use to feed `woe_reasoning`'s RAAF row, to pick analogues for read-across, or to rank a candidate pool against a target.

**Precondition:** target and candidate SMILES must be standardized consistently (see [Standardization](#standardization-canonical-identity)). Comparing unstandardized inputs produces silently wrong rankings.

```python
from scripts.cheminformatics import (
    morgan_fingerprint, tanimoto, nearest_neighbors,
)

# Find top-5 closest analogues from a candidate list.
neighbors = nearest_neighbors(
    target_smiles=std.canonical_smiles,
    candidates=candidate_pool_smiles,
    k=5,
    radius=2,   # ECFP4-like
    n_bits=2048,
)
# -> [(smiles, tanimoto), ...] sorted descending
```

### Similarity thresholds (calibrate, do not take as universal)

| Tanimoto (ECFP4, 2048 bits) | Interpretation for read-across                                          |
|-----------------------------|--------------------------------------------------------------------------|
| ≥ 0.85                      | Very close analogue; strong structural basis for read-across            |
| 0.75 – 0.85                 | Close analogue; requires additional mechanistic / metabolic similarity  |
| 0.60 – 0.75                 | Moderate analogue; requires additional physicochemical, metabolic and biological evidence  |
| 0.40 – 0.60                 | Distant analogue; category approach only, needs cross-compound evidence |
| < 0.40                      | Not a read-across candidate on structural grounds alone                 |

Rules:

- **Tanimoto alone is not a RAAF justification.** The ECHA RAAF requires mechanistic and metabolic similarity in addition to structural similarity. Use this capability for the structural axis, then combine with mechanistic evidence (Line 7) and ADME evidence (Line 8) in `woe_reasoning`.
- **Fingerprint type matters.** Morgan/ECFP is the default, but MACCS (166-bit) or AtomPair often rank differently. If the downstream QSAR used a different fingerprint, match it rather than forcing ECFP.
- **Default radius=2** (≈ ECFP4). Use radius=3 (≈ ECFP6) for finer discrimination when neighbors cluster too tightly.
- **When read-across is on the table, always report the numeric Tanimoto value(s), not just a pass/fail band.** The similarity number is what `woe_reasoning` weights, and a reviewer needs to see it to judge the analogue.

---

## Scaffold and MCS

Use for category-style grouping or for extracting a shared substructure across a target + analogue set (e.g. to justify a chemical category in a RAAF dossier).

```python
from scripts.cheminformatics import (
    maximum_common_substructure, murcko_scaffold_smiles,
)

# MCS across target + analogue set (for category justification).
mcs_smarts = maximum_common_substructure([std.canonical_smiles, *top_analogues])

# Murcko scaffold for grouping.
scaffold = murcko_scaffold_smiles(std.canonical_smiles)
```

Rules:

- **Scaffold + MCS are category tools, not identity tools.** Two compounds sharing a Murcko scaffold are not therefore interchangeable — scaffolds erase peripheral functional groups that drive hazard.
- Inputs should be standardized for the same reason as similarity.

---

## Applicability Domain (QSAR Principle 3)

Use whenever a QSAR prediction will enter `woe_reasoning`. Without an AD report, the prediction cannot be weighted under OECD Principle 3. This applies to **every** Tier-B endpoint as well: admet-ai, Tox21, the ecotox narcosis QSARs, and the melting-point empirical QSAR all have their own ADs.

**Precondition:** target SMILES and the training-set SMILES must be standardized with the same flags.

```python
from scripts.cheminformatics import applicability_domain_check

ad = applicability_domain_check(
    target_smiles=std.canonical_smiles,
    training_smiles=qsar_training_set_smiles,
    k=5,
    threshold=0.30,   # conservative default; replace with model's published threshold
)
# -> AppDomainReport(nearest_training_similarity, mean_similarity_top_k,
#                    inside_domain, threshold, k)
```

Rules:

- **Use the QSAR model's own AD definition when it has one.** Leverage (Williams plot), descriptor-space ellipsoids, and model-specific fingerprint similarities all exist in the literature for specific models; the helper's ECFP4-mean-top-k check is a general fallback, not a substitute.
- **Outside AD ⇒ QSAR prediction cannot carry decisive weight** in `woe_reasoning`, regardless of the model's overall accuracy. This is non-negotiable under OECD Principle 3.
- **Record both** the AD report and the model's stated reliability metric. Reviewers need both to decide the weight.
- **If the training set is not available**, state this as a data gap in `woe_reasoning`. Do not substitute a generic "drug-like" AD — it is not the QSAR model's AD.
- **Tier-B endpoints inherit AD constraints.** The ecotox baseline-narcosis models are valid for non-ionic organics with logP ~0–7 and MW ~50–500 (see [Ecotoxicology](#ecotoxicology-baseline-narcosis-qsars)); admet-ai and Tox21 ADs are model-specific and must be sourced from their publications.

---

## Visualization

Use for dossier figures and read-across tables — grid rendering of the target plus its analogues.

```python
from scripts.cheminformatics import draw_molecules

img = draw_molecules(
    [std.canonical_smiles, *top_analogues],
    legends=["target", *[f"analogue {i+1}" for i in range(len(top_analogues))]],
    mols_per_row=4,
    sub_img_size=(300, 300),
)

# Save via the scope-aware helper injected by python_executor:
out_path = prepare_output_path("analogue_grid.png")
img.save(out_path)
```

Rules:

- All images belong under the conversation output scope. Use `prepare_output_path(...)` — do not write raw paths (see `core/tools/python_executor.py`).
- For publication-quality property plots and analogue overlays, delegate to `data_visualization` rather than styling Matplotlib inline — that skill owns the theme.

---

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

## Batch Screening Pattern

Use to compare a small compound library on a fixed set of columns — useful for triage, similarity-based shortlist evaluation, and intermediate dossier tables.

```python
import pandas as pd
from scripts.qsar_toolbox import calculate_chemical_safety

compounds = {
    "Aspirin":     "CC(=O)Oc1ccccc1C(=O)O",
    "Ibuprofen":   "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
    "Caffeine":    "Cn1cnc2c1c(=O)n(c(=O)n2C)C",
    "Paracetamol": "CC(=O)Nc1ccc(O)cc1",
    "DDT":         "Clc1ccc(C(c2ccc(Cl)cc2)C(Cl)(Cl)Cl)cc1",
    "TNT":         "Cc1c([N+](=O)[O-])cc([N+](=O)[O-])cc1[N+](=O)[O-]",
}

rows = []
for name, smi in compounds.items():
    try:
        r     = calculate_chemical_safety(smi)
        phys  = r["physicochemical"]
        ghs   = r["ghs_classification"]
        eco   = r["ecotoxicology"]
        admet = r.get("admet_toxicity", {})
        expl  = r["explosivity"]
        rows.append({
            "Name":           name,
            "Formula":        phys["molecular_formula"],
            "MW":             phys["mw"],
            "logP":           phys["logp_crippen"],
            "Signal word":    ghs["signal_word"],
            "H-codes":        " ".join(h["H_code"] for h in ghs["hazard_statements"]),
            "Fish LC50 mg/L": eco["fish_fathead_minnow"]["value_mg_L"],
            "BCF L/kg":       eco["bioconcentration"]["BCF_L_per_kg"],
            "Tm C":           r["melting_point"]["melting_point_C"],
            "Explosivity":    expl["explosivity_risk_level"],
            "AMES prob":      admet.get("AMES_mutagenicity_prob", "-"),
            "hERG prob":      admet.get("hERG_inhibition_prob", "-"),
            "LD50 mg/kg":     admet.get("LD50_oral_mg_per_kg", "-"),
        })
    except ValueError as e:
        rows.append({"Name": name, "Error": str(e)})

df_batch = pd.DataFrame(rows).set_index("Name")
```

Rules:

- **Catch `ValueError` per row** so a single unparseable SMILES does not abort the whole batch.
- **Mixing draft GHS columns with experimental columns is dangerous** — if the table will be reviewed by a non-cheminformatician, prefix the predicted columns with `(predicted)` so a reader cannot mistake a draft for an authoritative classification.
- **For large libraries (hundreds+),** vectorize the admet-ai call by passing a SMILES list directly to `ADMETModel.predict` rather than calling `calculate_chemical_safety` per row — the orchestrator's per-call loading is acceptable for triage but inefficient at scale.

---

## Anti-Patterns (explicit don'ts)

- **Don't compare unstandardized SMILES.** Standardize before every identity check, every cross-database lookup, and every similarity calculation.
- **Don't neutralize or tautomer-canonicalize when it destroys the hazard-relevant species.** Quaternary ammoniums, permanent zwitterions, thione/thiol pairs with different reactivity.
- **Don't strip stereochemistry.** Enantiomers and E/Z isomers often differ in activity; strip and you have reported on a different substance.
- **Don't treat an alert hit as a classification.** Alerts are mechanistic indications; classifications require `woe_reasoning` integration.
- **Don't use Lipinski / Veber / Egan / Ghose / QED flags as hazard criteria.** They are drug-likeness filters.
- **Don't report a QSAR prediction without an AD report.** OECD Principle 3 is not optional — and it applies to every Tier-B endpoint, not just custom QSARs.
- **Don't treat Tanimoto ≥ 0.85 as a RAAF justification on its own.** RAAF requires mechanistic + metabolic similarity too.
- **Don't hand-roll SMARTS for alerts that `FilterCatalog` already provides.** Use the built-ins; reserve custom SMARTS for regulator-specific alerts with cited sources.
- **Don't write custom SMARTS without citing the authoritative source.** Unsourced alerts are non-reproducible.
- **Don't silently fall back to another input** when a SMILES fails to parse. Record the failure and either fix the input or stop.
- **Don't put `classify_ghs` output on an SDS, in a REACH dossier, or in any regulator-facing classification call.** It is a draft / screening output; the authoritative call lives in `woe_reasoning`.
- **Don't treat a Tier-B prediction as ground truth.** Every `*_prob`, every QSAR-estimated EC50, every predicted Tm comes with model error and an AD constraint; record both with the value.
- **Do not create hallucinations** about predicted or calculated values. Only data obtained through a predictive model, the use of codes, or bibliographic research are valid.

---
