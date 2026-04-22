---
name: cheminformatics
description: Use this skill whenever the task requires structural manipulation, property calculation, or structure-based reasoning about a chemical — parsing or standardizing SMILES/InChI, computing physchem descriptors (MW, logP, TPSA, HBD/HBA), detecting structural alerts (PAINS / Brenk / NIH / custom toxicophore SMARTS), computing similarity or selecting analogues for read-across, extracting scaffolds or MCS, or performing an applicability-domain check for a QSAR prediction. Triggers before any cross-source chemical lookup (identifiers must be standardized first), before any read-across analogue selection, before any QSAR result is weighted in `woe_reasoning`, and whenever a physicochemical line of evidence must be produced from structure. Always run Step 1–2 (parse + standardize) before comparing two structures — unstandardized SMILES lead to silently wrong read-across and failed database matches.
---

# Cheminformatics Skill (RDKit)

Produce structure-grounded evidence with RDKit. This skill is the structural-chemistry backbone for the agent: it feeds canonical identifiers into `database_traversal`, physchem descriptors into `woe_reasoning` Line 6, analogue pairs into RAAF read-across justifications, applicability-domain reports that gate QSAR reliance under OECD Principle 3, and structural-alert hits into the mechanistic line of evidence.

The repo already whitelists `rdkit` (see `core/tools/python_executor.py::DEFAULT_AUTHORIZED_IMPORTS`). A curated helper module at `core/skills/cheminformatics/scripts/cheminformatics.py` wraps the operations that are error-prone to rewrite each call (full standardization pipeline, fingerprint similarity, AD distance, FilterCatalog alerts). Prefer the helper for those operations; drop to raw RDKit only when you need something it does not expose.

---

## Step 1 — Parse and Validate

Never trust user-supplied SMILES. Parse first, branch on failure.

```python
from core.skills.cheminformatics.scripts.cheminformatics import parse_smiles

mol = parse_smiles(user_smiles)
if mol is None:
    # Record the failure explicitly; do not silently substitute another input.
    print(f"Invalid SMILES: {user_smiles!r}")
```

Parsing rules:

- **`parse_smiles` returns `None` for any unparseable input** — treat as a data-quality failure, not a soft fallback.
- **Accept SMILES, but also `Chem.MolFromInchi` if the input is an InChI.** Inputs coming from literature often arrive as InChI/InChIKey.
- **If a CAS or name is provided, resolve it to a structure before parsing.** Use `database_traversal` (`references/id_resolution.md`) or a `pubchempy` call — RDKit does not resolve identifiers.
- **Preserve the original input in the evidence log.** Every structural claim traces back to the exact string the user supplied, not just the canonical form.

---

## Step 2 — Standardize (canonical identity pipeline)

Two structures are "the same" in a regulatory sense only after the full standardization pipeline has run. Skipping any stage produces false negatives in identity matching and false positives in read-across.

```python
from core.skills.cheminformatics.scripts.cheminformatics import standardize_smiles

std = standardize_smiles(user_smiles)
canonical = std.canonical_smiles   # for SMILES-keyed comparisons
inchikey  = std.inchikey           # for cross-database lookups (PubChem CID, ChEMBL ID)
```

### What the pipeline does (in order)

1. **Normalize** — fix common drawing errors (nitro groups, diazo, etc.) via `rdMolStandardize.Normalizer`.
2. **Strip salts and pick the largest fragment** — removes counterions (`Cl-`, `Na+`), hydrates, and solvent adducts. The parent organic fragment is what gets scored.
3. **Neutralize (uncharge)** — removes representational charges that are pH-dependent artifacts (protonated amines, deprotonated carboxylates). Do **not** neutralize when the species of interest is genuinely the ionized form (e.g. permanent quaternary ammonium, ionic liquids, metal oxoanions) — pass `neutralize=False`.
4. **Canonical tautomer** — collapses tautomeric variants to a single representative. Do **not** canonicalize tautomers for read-across when the parent and tautomer differ in reactive functional group (e.g. a thione vs. thiol matters for metal-binding hazards) — pass `canonical_tautomer=False`.
5. **Emit canonical SMILES + InChI + InChIKey** — the InChIKey is the right cross-database key; canonical SMILES is the right in-memory key.

### Decision table for optional stages

| Use case                                         | strip_salts | neutralize | canonical_tautomer |
|--------------------------------------------------|-------------|------------|--------------------|
| Cross-database lookup (PubChem, ChEMBL, NIOSH)   | yes         | yes        | yes                |
| Read-across analogue selection                   | yes         | yes        | **case by case**   |
| Ionic-liquid / quaternary-ammonium hazard call   | yes         | **no**     | yes                |
| Metal complex / organometallic                   | **no**      | **no**     | no                 |
| Reaction / metabolite scaffold work              | yes         | yes        | no                 |

Record in the evidence log which flags were set, and why. Reviewers must be able to reconstruct the exact canonical form.

### When to preserve stereochemistry

Stereocenters and E/Z geometry are load-bearing for biological activity. Never strip stereochemistry to "simplify" a comparison. If a QSAR model predicts without stereo, state that explicitly — do not mutate the input to match.

---

## Step 3 — Descriptor Calculation (physchem line of evidence)

```python
from core.skills.cheminformatics.scripts.cheminformatics import compute_descriptors, lipinski_flags

desc = compute_descriptors(std.canonical_smiles)
# -> {"mw": ..., "logp_crippen": ..., "tpsa": ..., "hbd": ..., "hba": ...,
#     "rotatable_bonds": ..., "aromatic_rings": ..., "rings": ...,
#     "formal_charge": ..., "fraction_csp3": ...}
```

### Descriptor meaning and regulatory use

| Descriptor         | What it tells you                                                  | Line of evidence it supports                      |
|--------------------|---------------------------------------------------------------------|---------------------------------------------------|
| MW                 | Mass; influences absorption, filters by regulation                 | Physchem (6), read-across similarity              |
| logP (Crippen)     | Octanol–water partition; proxy for bioaccumulation, permeability   | Physchem (6), PBT screening, exposure/kinetics    |
| TPSA               | Topological polar surface area; permeability proxy                 | Physchem (6), ADME/kinetics                       |
| HBD / HBA          | Hydrogen bond donors / acceptors                                   | Physchem (6), permeability                        |
| Rotatable bonds    | Conformational flexibility                                          | Physchem (6), drug-likeness                       |
| Aromatic rings     | Aromatic content                                                    | Reactivity / mechanistic context                  |
| Fraction Csp3      | Saturation fraction                                                 | Structural complexity                             |
| Formal charge      | Species ionization as drawn                                         | Identity verification (should be 0 after Step 2)  |

### Rules

- **`logp_crippen` is the Crippen estimate, not an experimental logP.** If an experimental value is available (via `database_traversal` PubChem), prefer that for the evidence table and record the RDKit estimate only as a sanity check.
- **Lipinski / Veber / Egan rules are drug-likeness filters, not hazard criteria.** `lipinski_flags(desc)` is available for context but must not enter a hazard classification argument.
- **For PBT / vPvB screening**, logP and MW are necessary but not sufficient — the PBT call lives in `woe_reasoning` Step 5 against REACH Annex XIII criteria surfaced by `sop_search`.

---

## Step 4 — Structural Alerts (toxicophore screening)

Structural alerts provide the mechanistic line of evidence (Line 7 in `woe_reasoning`). Use RDKit's built-in `FilterCatalog` for the well-established filters; write custom SMARTS only for alerts not covered by those catalogs.

### Built-in RDKit catalogs (preferred)

| Catalog  | What it screens for                                                    | Primary reference                              |
|----------|------------------------------------------------------------------------|------------------------------------------------|
| `pains`  | Pan-assay interference compounds (frequent hitters in HTS)             | Baell & Holloway 2010, *J Med Chem* 53:2719    |
| `brenk`  | Unwanted functionality for drug design (reactive / toxic / unstable)   | Brenk et al. 2008, *ChemMedChem* 3:435         |
| `nih`    | NIH annotated unwanted features                                        | NIH MLSMR / MLPCN                              |
| `zinc`   | ZINC15 drug-likeness filters                                           | Sterling & Irwin 2015                          |
| `chembl` | Structural filters from ChEMBL's curation workflow                     | ChEMBL                                         |

```python
from core.skills.cheminformatics.scripts.cheminformatics import (
    build_filter_catalog, find_structural_alerts,
)

catalog = build_filter_catalog(["pains", "brenk", "nih"])
hits = find_structural_alerts(std.canonical_smiles, catalog=catalog)
# -> [{"catalog": ..., "alert": ..., "description": ..., "smarts": ...}, ...]
```

### Custom SMARTS for regulated toxicophores

For regulator-defined alerts not in the built-in sets (e.g. Ashby–Tennant genotoxicity alerts, Benigni–Bossa carcinogenicity alerts, Cramer classes for TTC), encode them as SMARTS and match explicitly:

```python
from core.skills.cheminformatics.scripts.cheminformatics import match_custom_smarts

# Example: aromatic amine (Ashby–Tennant alert for genotoxic carcinogenicity)
ALERT_AROMATIC_AMINE = "[NX3;H2,H1;!$(NC=O);!$(N=*)]-c"
is_aromatic_amine = match_custom_smarts(std.canonical_smiles, ALERT_AROMATIC_AMINE)

# Example: Michael acceptor (electrophile class)
ALERT_MICHAEL = "[$([CX3]=[CX3])]-[$([CX3]=[OX1])]"
is_michael = match_custom_smarts(std.canonical_smiles, ALERT_MICHAEL)
```

### Rules

- **An alert hit is mechanistic *indication*, not a hazard classification.** A hit enters the evidence table as one mechanistic line; a classification call requires integration with apical endpoint evidence via `woe_reasoning`.
- **Record the exact SMARTS and its authoritative source** in the evidence table. Unsourced SMARTS are non-reproducible and cannot be defended in a dossier.
- **Distinguish "alert present" from "alert activated."** Metabolic activation, steric hindrance, and electronic effects often negate an alert's expected reactivity. Flag the hit; decide the weight in `woe_reasoning` with mechanistic + in-vitro evidence.
- **No alerts hit ≠ no hazard.** Absence of a structural alert is weak evidence; regulators treat it as a screen, never a clearance.

---

## Step 5 — Similarity and Read-Across Support

Analogue selection for read-across (feeds `woe_reasoning` Step 3's RAAF row) uses circular-topological fingerprints and Tanimoto similarity.

```python
from core.skills.cheminformatics.scripts.cheminformatics import (
    morgan_fingerprint, tanimoto, nearest_neighbors, maximum_common_substructure,
    murcko_scaffold_smiles,
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

# MCS across target + analogue set (for category justification).
mcs_smarts = maximum_common_substructure([std.canonical_smiles, *top_analogues])

# Scaffold for grouping.
scaffold = murcko_scaffold_smiles(std.canonical_smiles)
```

### Similarity thresholds (calibrate, do not take as universal)

| Tanimoto (ECFP4, 2048 bits) | Interpretation for read-across                                          |
|-----------------------------|--------------------------------------------------------------------------|
| ≥ 0.85                      | Very close analogue; strong structural basis for read-across            |
| 0.70 – 0.85                 | Close analogue; requires additional mechanistic / metabolic similarity  |
| 0.50 – 0.70                 | Distant analogue; category approach only, needs cross-compound evidence |
| < 0.50                      | Not a read-across candidate on structural grounds alone                 |

### Rules

- **Tanimoto alone is not a RAAF justification.** The ECHA RAAF requires mechanistic and metabolic similarity in addition to structural similarity. Use this skill to supply the structural axis, then combine with mechanistic evidence (Line 7) and ADME evidence (Line 8) in `woe_reasoning`.
- **Fingerprint type matters.** Morgan/ECFP is the default, but MACCS (166-bit) or AtomPair often rank differently. If the downstream QSAR used a different fingerprint, match it rather than forcing ECFP.
- **Default radius=2** (≈ ECFP4). Use radius=3 (≈ ECFP6) for finer discrimination when neighbors cluster too tightly.
- **Scaffold + MCS are category tools, not identity tools.** Two compounds sharing a Murcko scaffold are not therefore interchangeable — scaffolds erase peripheral functional groups that drive hazard.

---

## Step 6 — Applicability Domain (QSAR Principle 3)

Any QSAR prediction that enters `woe_reasoning` must carry an applicability-domain (AD) report. Without it, the prediction cannot be weighted under OECD Principle 3.

```python
from core.skills.cheminformatics.scripts.cheminformatics import applicability_domain_check

ad = applicability_domain_check(
    target_smiles=std.canonical_smiles,
    training_smiles=qsar_training_set_smiles,
    k=5,
    threshold=0.30,   # conservative default; replace with model's published threshold
)
# -> AppDomainReport(nearest_training_similarity, mean_similarity_top_k,
#                    inside_domain, threshold, k)
```

### Rules

- **Use the QSAR model's own AD definition when it has one.** Leverage (Williams plot), descriptor-space ellipsoids, and model-specific fingerprint similarities all exist in the literature for specific models; the helper's ECFP4-mean-top-k check is a general fallback, not a substitute.
- **Outside AD ⇒ QSAR prediction cannot carry decisive weight** in `woe_reasoning` Step 3, regardless of the model's overall accuracy. This is non-negotiable under OECD Principle 3.
- **Record both** the AD report and the model's stated reliability metric. Reviewers need both to decide the weight.
- **If the training set is not available**, state this as a data gap in `woe_reasoning` Step 6. Do not substitute a generic "drug-like" AD — it is not the QSAR model's AD.

---

## Step 7 — Visualization

For dossier figures and read-across tables, grid rendering of the target plus its analogues is often needed.

```python
from core.skills.cheminformatics.scripts.cheminformatics import draw_molecules

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

All images belong under the conversation output scope. Use `prepare_output_path(...)` — do not write raw paths (see `core/tools/python_executor.py`).

For publication-quality property plots and analogue overlays, delegate to `data_visualization` rather than styling Matplotlib inline — that skill owns the theme.

---

## Step 8 — Integration with Other Skills

| Upstream / parallel skill  | Handoff                                                                          |
|----------------------------|----------------------------------------------------------------------------------|
| `database_traversal`       | Consumes InChIKey and canonical SMILES from Step 2 for PubChem/ChEMBL lookups. |
| `literature_search`        | Consumes the synonym set and canonical names; this skill standardizes the query input, that skill searches prose. |
| `woe_reasoning`            | Receives structural alert hits (Line 7), descriptors (Line 6), analogue + MCS for RAAF (Line 5), AD report for QSAR (Line 4). |
| `sop_search`               | Supplies the regulatory thresholds (e.g. PBT criteria) that descriptors from this skill are checked against. |
| `data_visualization`       | Styles any analogue / descriptor figure beyond a quick grid image.              |

---

## Anti-Patterns (explicit don'ts)

- **Don't compare unstandardized SMILES.** Run Step 2 before every identity check, every cross-database lookup, and every similarity calculation.
- **Don't neutralize or tautomer-canonicalize when it destroys the hazard-relevant species.** Quaternary ammoniums, permanent zwitterions, thione/thiol pairs with different reactivity.
- **Don't strip stereochemistry.** Enantiomers and E/Z isomers often differ in activity; strip and you have reported on a different substance.
- **Don't treat an alert hit as a classification.** Alerts are mechanistic indications; classifications require `woe_reasoning` integration.
- **Don't use Lipinski / Veber flags as hazard criteria.** They are drug-likeness filters.
- **Don't report a QSAR prediction without an AD report.** OECD Principle 3 is not optional.
- **Don't treat Tanimoto ≥ 0.85 as a RAAF justification on its own.** RAAF requires mechanistic + metabolic similarity too.
- **Don't hand-roll SMARTS for alerts that `FilterCatalog` already provides.** Use the built-ins; reserve custom SMARTS for regulator-specific alerts with cited sources.
- **Don't write custom SMARTS without citing the authoritative source.** Unsourced alerts are non-reproducible.
- **Don't silently fall back to another input** when a SMILES fails to parse. Record the failure and either fix the input or stop.

---