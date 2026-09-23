---
name: qsar_modelling
description: Run QSAR activity predictions on a molecular structure. Ships 19 conformal models, one callable function each: AHR_agonists, CAR_agonist, CAR_antagonist, DIO1_inhibition, DIO2_inhibition, DIO3_inhibition, NIS_inhibition, PPAR_delta_agonist, PPAR_delta_antagonist, PPAR_gamma_agonist, PPAR_gamma_antagonist, PXR_agonist, TPO_inhibition, TRHR_antagonists, TR_beta_agonist, TR_beta_antagonist, TSHR_agonist, TSHR_antagonist, TTR_binding. Use when an activity or hazard endpoint must be estimated from structure because no measured value exists. Each call returns a conformal prediction region, not a probability. For measured or regulatory values use database_traversal; for deterministic structural facts use cheminformatics.
---

# QSAR Modelling Skill

Nineteen conformal models. One module per model, one function per module, same
signature on all of them. Pick the model you need from the table, import its
function, call it.

## Available models

| Function | Predicts | Confidence |
|---|---|---|
| `AHR_agonists` | Aryl hydrocarbon receptor (AhR) agonism | 0.80 |
| `CAR_agonist` | Constitutive androstane receptor (CAR) agonism | 0.80 |
| `CAR_antagonist` | Constitutive androstane receptor (CAR) antagonism | 0.80 |
| `DIO1_inhibition` | Type 1 iodothyronine deiodinase (DIO1) inhibition | 0.80 |
| `DIO2_inhibition` | Type 2 iodothyronine deiodinase (DIO2) inhibition | 0.80 |
| `DIO3_inhibition` | Type 3 iodothyronine deiodinase (DIO3) inhibition | 0.80 |
| `NIS_inhibition` | Sodium/iodide symporter (NIS) inhibition | 0.75 |
| `PPAR_delta_agonist` | PPAR-delta agonism | 0.75 |
| `PPAR_delta_antagonist` | PPAR-delta antagonism | 0.75 |
| `PPAR_gamma_agonist` | PPAR-gamma agonism | 0.75 |
| `PPAR_gamma_antagonist` | PPAR-gamma antagonism | 0.75 |
| `PXR_agonist` | Pregnane X receptor (PXR) agonism | 0.85 |
| `TPO_inhibition` | Thyroid peroxidase (TPO) inhibition | 0.85 |
| `TRHR_antagonists` | Thyrotropin-releasing hormone receptor (TRHR) antagonism | 0.85 |
| `TR_beta_agonist` | Thyroid hormone receptor beta (TR-beta) agonism | 0.75 |
| `TR_beta_antagonist` | Thyroid hormone receptor beta (TR-beta) antagonism | 0.80 |
| `TSHR_agonist` | Thyroid-stimulating hormone receptor (TSHR) agonism | 0.85 |
| `TSHR_antagonist` | Thyroid-stimulating hormone receptor (TSHR) antagonism | 0.85 |
| `TTR_binding` | Transthyretin (TTR) binding | 0.90 |

Confidence is set inside each function, differs between models, and comes back
in the result. It is not a parameter.

## Run a model

```python
from scripts.TPO_inhibition import TPO_inhibition

TPO_inhibition("CCCn1c(=O)c2nc(-c3ccccc3)[nH]c2n(CCC)c1=O")
# {'smiles': 'CCCn1...', 'endpoint': 'TPO_inhibition', 'confidence': 0.85,
#  'p_inactive': 0.5774, 'p_active': 0.1127, 'prediction': 'inactive'}
```

**Import the function out of its own module.** The module and the function share
a name, so `from scripts import TPO_inhibition` binds the *module* and calling
it raises `'module' object is not callable`.

Every model accepts the same inputs and returns the same shapes:

```python
NIS_inhibition("CCO")                    # one SMILES      -> dict
NIS_inhibition("CCO, c1ccccc1O")         # comma-separated -> path to CSV
NIS_inhibition(["CCO", "c1ccccc1O"])     # list            -> path to CSV
NIS_inhibition("compounds.csv")          # CSV/TSV with a column containing "smiles"
```

- **One compound returns a dict; more than one writes a CSV and returns its path.** A trailing `[warning]` line names any structure RDKit could not parse, so take `str(path).splitlines()[0]` before opening the file.
- **Failures come back as `"Error: ..."` strings, not exceptions.** Check that prefix before using a result.
- **Standardize first** with `cheminformatics.standardize_smiles`; an unstandardized salt is a different descriptor vector.

## Read the result

`prediction` is a conformal region — the labels that cannot be ruled out at that
model's confidence — **not** a probability:

| Region | Meaning |
|---|---|
| `active` | Predicted active |
| `inactive` | Predicted inactive |
| `both` | Undecided at this confidence — a data gap, not a borderline result |
| `empty` | Outside the applicability domain — the model has said nothing |

`p_inactive` and `p_active` are conformal p-values; they do not sum to 1. Report
the region, with the confidence and the p-values beside it.

## Run several models

Each model holds 0.4–0.7 GB and only two stay cached, so **hand one model the
whole compound list in a single call** rather than looping compounds across
models. Request the models the question needs; running all 19 is a real cost.

## Where results go

Batch CSVs land in the conversation's results folder, resolved per call —
nothing to pass in:

```
persistence/results/<user_id>/<thread>/qsar_predictions/<function>_results.csv
```

Re-running a model in the same conversation overwrites its CSV. To name the
file yourself:

```python
path = NIS_inhibition(smiles_list, output_name=prepare_output_path("nis_predictions.csv"))
```

## More detail

[`references/models.md`](references/models.md) — model provenance and citation,
environment variables, known deviations from the published models, how to
validate, how a region enters `woe_reasoning` or a QPRF, and the anti-patterns.
