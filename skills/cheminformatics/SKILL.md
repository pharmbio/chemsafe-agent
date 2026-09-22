---
name: cheminformatics
description: Derive deterministic chemistry evidence from a molecular structure using RDKit — parse and standardize SMILES, InChI or InChIKey; compute physicochemical descriptors; screen structural alerts and toxicophores; run similarity, scaffold, MCS and read-across analogue selection; check the applicability domain; and draw molecules. Use when the input is a chemical structure and the answer must be computed from it. Everything it returns is reproducible from RDKit alone, never a measured value — for measured or regulatory data use database_traversal, and for predicted hazard endpoints use qsar_modelling.
---

# Cheminformatics Skill (RDKit)

Produce structure-grounded evidence with RDKit. This skill is the structural-chemistry backbone for the agent: it feeds canonical identifiers into `database_traversal`, physchem descriptors into `woe_reasoning` Line 6, analogue pairs into RAAF read-across justifications, applicability-domain reports that gate QSAR reliance under OECD Principle 3, and structural-alert hits into the mechanistic line of evidence.

Everything here is **deterministic**: computed from the structure itself and reproducible from RDKit alone. Nothing in this skill predicts a biological or hazard endpoint. Predicted endpoints live in `qsar_modelling`, and measured or regulatory values live in `database_traversal`.

The repo whitelists `rdkit` in `core/tools/python_executor.py::DEFAULT_AUTHORIZED_IMPORTS`. One curated helper module sits under `scripts/`:

- `cheminformatics.py` — parse, standardize, descriptors, alerts, similarity, scaffold/MCS, applicability domain, visualization.

Prefer this helper over rewriting raw RDKit code; drop to raw RDKit only when it does not expose what you need.

Bulky lookups — the full descriptor glossary and FilterCatalog citations — live in [`references/reference-tables.md`](references/reference-tables.md). Read that file when you need to interpret a helper's output or cite a catalog's provenance; you do not need it to call the helpers.

This document describes **capabilities**, not a procedure. Select the capabilities the task needs and call them in whatever order the task implies. The only ordering constraints are the *preconditions* called out on each capability (for example, similarity and identity comparisons require standardized inputs — see [Standardization](references/structure-ops.md#standardization-canonical-identity)).

## What this skill is not

| You need                                         | Use instead            |
|--------------------------------------------------|------------------------|
| A measured value, a legal limit, a harmonised classification | `database_traversal`   |
| A predicted hazard or activity endpoint          | `qsar_modelling`       |
| The classification call itself                   | `woe_reasoning`        |
| A documented QSAR prediction for a dossier       | `qprf_generating`      |

A descriptor is not an endpoint, and a structural alert is not a classification. This skill supplies the inputs those skills reason over; it never closes the argument itself.

---

## Capability Index

| Capability                                                 | Primary helper(s)                                                       |
|------------------------------------------------------------|--------------------------------------------------------------------------|
| [Parse SMILES / InChI](references/structure-ops.md#parsing-and-input-validation) | `parse_smiles`                                                           |
| [Standardization (canonical identity)](references/structure-ops.md#standardization-canonical-identity) | `standardize_smiles`                                                     |
| [Physchem descriptors](references/structure-ops.md#physchem-descriptors) | `compute_descriptors`, `lipinski_flags`                                  |
| [Structural alerts (toxicophores)](references/structure-ops.md#structural-alerts-toxicophore-screening) | `build_filter_catalog`, `find_structural_alerts`, `match_custom_smarts`  |
| [Similarity & analogue selection](references/structure-ops.md#similarity-and-analogue-selection) | `morgan_fingerprint`, `tanimoto`, `nearest_neighbors`                    |
| [Scaffold & MCS](references/structure-ops.md#scaffold-and-mcs) | `murcko_scaffold_smiles`, `maximum_common_substructure`                  |
| [Applicability domain (QSAR Principle 3)](references/structure-ops.md#applicability-domain-qsar-principle-3) | `applicability_domain_check`                                             |
| [Visualization](references/structure-ops.md#visualization) | `draw_molecules`                                                         |
| [Batch screening pattern](references/batch-screening.md)   | the helpers above, over a dict of SMILES                                 |

---

## Where the detail lives

Load only what the step needs; each reference carries the full call signatures,
return shapes and worked examples.

- `read_files("references/structure-ops.md")` — parsing, standardization,
  descriptors, structural alerts, similarity and analogue selection, scaffold/MCS,
  applicability domain, molecule drawing.
- `read_files("references/batch-screening.md")` — running a compound list end to end.
- `read_files("references/reference-tables.md")` — descriptor glossary and catalog provenance.

Read the anti-patterns below before reporting any descriptor or alert.

## Anti-Patterns (explicit don'ts)

- **Don't compare unstandardized SMILES.** Standardize before every identity check, every cross-database lookup, and every similarity calculation.
- **Don't neutralize or tautomer-canonicalize when it destroys the hazard-relevant species.** Quaternary ammoniums, permanent zwitterions, thione/thiol pairs with different reactivity.
- **Don't strip stereochemistry.** Enantiomers and E/Z isomers often differ in activity; strip and you have reported on a different substance.
- **Don't treat an alert hit as a classification.** Alerts are mechanistic indications; classifications require `woe_reasoning` integration.
- **Don't use Lipinski / Veber / Egan / Ghose / QED flags as hazard criteria.** They are drug-likeness filters.
- **Don't report a descriptor as a measured value.** `logp_crippen` is an estimate; if `database_traversal` has an experimental logP, that is the value for the evidence table.
- **Don't treat Tanimoto ≥ 0.85 as a RAAF justification on its own.** RAAF requires mechanistic + metabolic similarity too.
- **Don't hand-roll SMARTS for alerts that `FilterCatalog` already provides.** Use the built-ins; reserve custom SMARTS for regulator-specific alerts with cited sources.
- **Don't write custom SMARTS without citing the authoritative source.** Unsourced alerts are non-reproducible.
- **Don't silently fall back to another input** when a SMILES fails to parse. Record the failure and either fix the input or stop.
- **Don't let an `applicability_domain_check` result stand in for a model's own domain report.** It scores similarity to a reference set you supply; `qsar_modelling` endpoints carry their own conformal AD signal, and both belong in the record under OECD Principle 3.
- **Do not create hallucinations** about predicted or calculated values. Only data obtained through a predictive model, the use of codes, or bibliographic research are valid.

---
