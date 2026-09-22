# Tier A reference — deterministic structure operations

Full usage for `scripts/cheminformatics.py`. Everything here is computed from the
structure itself and is reproducible fact, not prediction.

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

