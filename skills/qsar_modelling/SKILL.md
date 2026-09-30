---
name: qsar_modelling
description: Run QSAR models on a molecular structure. Ships 19 conformal models, one callable function each: AHR_agonists, CAR_agonist, CAR_antagonist, DIO1_inhibition, DIO2_inhibition, DIO3_inhibition, NIS_inhibition, PPAR_delta_agonist, PPAR_delta_antagonist, PPAR_gamma_agonist, PPAR_gamma_antagonist, PXR_agonist, TPO_inhibition, TRHR_antagonists, TR_beta_agonist, TR_beta_antagonist, TSHR_agonist, TSHR_antagonist, TTR_binding. Use when an activity or hazard endpoint must be estimated from structure because no measured value exists. Each call returns a conformal prediction region, not a probability.
---

# QSAR Modelling Skill

One module per model, one function per module, same
signature on all of them. Pick the model you need from the table, import its
function, then call it.

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

## Run a model

```python
from scripts.TPO_inhibition import TPO_inhibition

TPO_inhibition("CCCn1c(=O)c2nc(-c3ccccc3)[nH]c2n(CCC)c1=O")
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

`prediction` is a conformal region (the labels that cannot be ruled out at that
model's confidence), **not** a probability:

| Region | Meaning |
|---|---|
| `active` | Predicted active |
| `inactive` | Predicted inactive |
| `both` | Undecided at this confidence — a data gap, not a borderline result |
| `empty` | Outside the applicability domain — the model has said nothing |

`p_inactive` and `p_active` are conformal p-values; they do not sum to 1. Report
the region, with the confidence and the p-values beside it.

## Outputs location

Batch CSVs land in the conversation's results folder, resolved per call —
nothing to pass in:

```
persistence/results/<user_id>/<thread>/qsar_predictions/<function>_results.csv
```

Re-running a model in the same conversation overwrites its CSV. To name the
file yourself, use:

```python
path = NIS_inhibition(smiles_list, output_name=prepare_output_path("nis_predictions.csv"))
```

## Using a prediction as evidence

A conformal region is one line of evidence, never a conclusion. Record the
region, the confidence (0.8), both p-values, the model citation and the
applicability-domain status together — a bare `active` is not reportable under
OECD Principle 3.

- **`woe_reasoning`** — the prediction is a Q-silico line of evidence. An
  `empty` region is a data gap and so is a `both` region; neither is a negative
  result, and neither may be reported as "no activity predicted".
- **`qprf_generating`** — use when one prediction must be documented for a
  dossier. The region and the `empty`/`both` signal are the AD material for
  QPRF §4 and §7.
- **`cheminformatics`** — supplies the standardized structure on the way in,
  and `applicability_domain_check` for an independent similarity-to-training-set
  view. That check complements the conformal AD; it does not replace it.
- **`database_traversal`** — check for a measured value first. A prediction
  where an experimental result exists is a weaker answer, not a faster one.

These models predict activity at a molecular target, which sits at the top of
an adverse outcome pathway. A prediction supports a mechanistic hypothesis; the
apical effect and the classification call are `woe_reasoning`'s.

## Anti-patterns

- **Don't read a region as a probability.** `p_active = 0.11` does not mean
  "11% likely active". These are p-values for excluding a label, and they do
  not sum to 1.
- **Don't report `both` as borderline or intermediate.** The model is undecided
  at 0.8 confidence. Say that.
- **Don't report `empty` as "inactive".** The compound is outside the
  applicability domain and the model has said nothing about it.
- **Don't raise the confidence to resolve a `both`.** Higher confidence widens
  regions and produces more `both` calls. Nothing makes an undecided compound
  decided.
- **Don't predict on unstandardized structures**, and don't strip
  stereochemistry to make a SMILES parse.
- **Don't treat an `"Error: ..."` string as a value.** It is a failed call to
  report, not a null result.
- **Don't present a model prediction as a GHS or CLP classification.**
  These models predict molecular-level activity, not apical hazard.
- **Don't run all 19 models and reason over the count of actives.** The
  endpoints have different training sets, ADs and base rates; a tally across
  them is not a hazard score.
- **Don't compare results with the published paper's numbers as if identical** —
  see [known deviations](#known-deviations-from-the-published-models).
- **Do not create hallucinations** about predicted values. Report only what an
  endpoint function actually returned.

## Model provenance

Cite this for any prediction that enters an evidence table or a dossier:

> Dracheva, E.; Norinder, U.; Rydén, P.; Engelhardt, J.; Weiss, J. M.;
> Andersson, P. L. *In Silico* Identification of Potential Thyroid Hormone
> System Disruptors among Chemicals in Human Serum and Chemicals with a High
> Exposure Index. *Environ. Sci. Technol.* **2022**.
> DOI: [10.1021/acs.est.1c07762](https://doi.org/10.1021/acs.est.1c07762)
