---
name: cheminformatics
description: Derive chemistry evidence from a molecular structure using RDKit and QSAR models — parse and standardize SMILES, InChI or InChIKey; compute physicochemical descriptors; screen structural alerts and toxicophores; run similarity, scaffold, MCS and read-across analogue selection; check the applicability domain; and predict ADMET, Tox21, ecotoxicity, melting point, explosivity and a draft GHS classification. Use when the input is a chemical structure and the answer must be computed from it. Everything it returns is a prediction, so for measured or regulatory values use database_traversal instead, and never present a predicted endpoint as an authoritative classification.
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

---

## Where the detail lives

Load only the tier the step needs; each reference carries the full call signatures,
return shapes and worked examples.

- `read_files("references/tier-a-structure-ops.md")` — parsing, standardization,
  descriptors, structural alerts, similarity and analogue selection, scaffold/MCS,
  applicability domain, molecule drawing.
- `read_files("references/tier-b-qsar-endpoints.md")` — drug-likeness, ADMET, Tox21,
  ecotoxicology, melting point, explosivity, draft GHS rollup, full safety profile.
- `read_files("references/batch-screening.md")` — running a compound list end to end.
- `read_files("references/reference-tables.md")` — lookup tables and cutoffs.

Read the anti-patterns below before reporting any Tier B number.

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
