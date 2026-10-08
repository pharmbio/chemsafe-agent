---
name: qsar_modelling
description: Runs conformal QSAR models for thyroid-hormone-system and nuclear-receptor activity from structure. Ships 19 models, one callable function each: AHR_agonists, CAR_agonist, CAR_antagonist, DIO1_inhibition, DIO2_inhibition, DIO3_inhibition, NIS_inhibition, PPAR_delta_agonist, PPAR_delta_antagonist, PPAR_gamma_agonist, PPAR_gamma_antagonist, PXR_agonist, TPO_inhibition, TRHR_antagonists, TR_beta_agonist, TR_beta_antagonist, TSHR_agonist, TSHR_antagonist, TTR_binding. Use when an activity, endocrine-disruption or hazard endpoint must be estimated from structure because no measured value exists, or when a prediction must carry an applicability domain for a dossier. Each call returns a conformal region - active, inactive, both or empty - at the model's fixed confidence, never a probability.
---

# QSAR Modelling Skill

One module per model, one function per module, the same signature on all of
them. Pick the model from the table, import its function, call it. Each model
runs as its own web API; the function calls it for you.

```python
from scripts.TPO_inhibition import TPO_inhibition

TPO_inhibition("CCCn1c(=O)c2nc(-c3ccccc3)[nH]c2n(CCC)c1=O")
```

**Import the function out of its own module.** The module and the function
share a name, so `from scripts import TPO_inhibition` binds the *module* and
calling it raises `'module' object is not callable`.

Read [Read the result](#read-the-result) before reporting anything: the answer
is a conformal region, and two of the four possible regions mean the model has
declined to answer.

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

The confidence is fixed per model by its service and comes back with every row;
it is not a parameter. Nothing here covers ADME, physicochemical or general
toxicity endpoints - for those use `admet_prediction`, and for a measured value
use `database_traversal`. **Don't substitute a related model for the one asked
about**: TR-beta agonism is not TR-beta antagonism, and neither is TSHR.

## Run a model

Every model accepts the same inputs and returns the same shapes:

```python
NIS_inhibition("CCO")                       # one SMILES      -> dict
NIS_inhibition("CCO, c1ccccc1O")            # comma-separated -> path to CSV
NIS_inhibition(["CCO", "c1ccccc1O"])        # list            -> path to CSV
NIS_inhibition("/full/path/compounds.csv")  # CSV/TSV with a column containing "smiles"
```

A file must be given by its full path; a bare file name is not found.

- **One compound returns a dict; more than one writes a CSV and returns its path.** A trailing `[warning]` line names any structure RDKit could not parse, so take `str(path).splitlines()[0]` before opening the file.
- **Failures come back as `"Error: ..."` strings, not exceptions.** Check that prefix before using a result. An error naming the model's URL means its service could not be reached or rejected the input: the model has not run, so report the call as failed, not as a null result.
- **Standardize first** with `cheminformatics.standardize_smiles`; an unstandardized salt is a different descriptor vector. Never strip stereochemistry to make a SMILES parse.

## Run a dataset

The result carries six fields - `smiles`, `endpoint`, `confidence`,
`p_inactive`, `p_active`, `prediction` - one row per input SMILES in input
order. To keep a dataset's IDs, standardize, pass the list, and attach the
prediction columns back by position:

```python
import pandas as pd
from scripts.chem_standardize import standardize_molecules
from scripts.TPO_inhibition import TPO_inhibition

df = pd.read_csv("/full/path/compounds.csv").dropna(subset=["SMILES"])
df["std_smiles"] = [m.canonical_smiles for m in standardize_molecules(df["SMILES"])]
df = df[df["std_smiles"].notna()].reset_index(drop=True)

res = TPO_inhibition(df["std_smiles"].tolist())
pred = pd.read_csv(str(res).splitlines()[0])
df = pd.concat([df, pred.drop(columns="smiles")], axis=1)
```

Each model takes about 15 s per 1000 compounds, and a `python_executor` call is
stopped after 10 minutes, losing its variables. For several models on more than
~2000 compounds, run one model per call.

Batch CSVs land in the conversation's results folder, resolved per call -
nothing to pass in, and a re-run of the same model in the same conversation
overwrites its CSV:

```
persistence/results/<user_id>/<thread>/qsar_predictions/<endpoint>_results.csv
```

`<endpoint>` is the `endpoint` field of the result, which for the PPAR models is
written with a hyphen (`PPAR-delta_agonist_results.csv`). To name the file
yourself, pass `output_name=prepare_output_path("nis_predictions.csv")`.

## Read the result

`prediction` is a conformal region - the labels that cannot be ruled out at that
model's confidence - and **not** a probability:

| Region | Meaning |
|---|---|
| `active` | Predicted active |
| `inactive` | Predicted inactive |
| `both` | Undecided at this confidence - a data gap, not a borderline or intermediate result |
| `empty` | Outside the applicability domain - the model has said nothing about this compound |

Report the region with the confidence and both p-values beside it; a bare
`active` is not reportable under OECD Principle 3.

**`p_inactive` and `p_active` are conformal p-values for excluding a label, and
they do not sum to 1.** `p_active = 0.11` does not mean "11 % likely active".

**Raising the confidence will not resolve a `both`.** Higher confidence widens
regions and produces more `both` calls, not fewer. The confidence is fixed by
the service anyway.

## Using a prediction as evidence

A conformal region is one line of evidence, never a conclusion. Record the
region, the model's confidence, both p-values, the applicability-domain status
and the citation together.

- **`database_traversal`** - check for a measured value first. A prediction where an experimental result exists is a weaker answer, not a faster one.
- **`cheminformatics`** - supplies the standardized structure on the way in, and `applicability_domain_check` for an independent similarity-to-training-set view. That check complements the conformal AD; it does not replace it.
- **`woe_reasoning`** - the prediction is a Q-silico line of evidence. An `empty` region is a data gap and so is a `both` region; neither is a negative result, and neither may be reported as "no activity predicted".
- **`qprf_generating`** - use when one prediction must be documented for a dossier. The region and the `empty`/`both` signal are the AD material for QPRF §4 and §7.
- **`admet_prediction`** - `AHR_agonists` overlaps ADMET-AI's `NR-AhR`, and the PPAR-gamma models overlap its `NR-PPAR-gamma`. Different training data, different assay definitions, different output types: **neither confirms the other, and the two must never be merged or averaged.** Report them side by side and let a disagreement stand. Where both exist, prefer the model here - it states a confidence and an applicability domain, and ADMET-AI states neither.

These models predict activity at a molecular target, which sits at the top of an
adverse outcome pathway. A prediction supports a mechanistic hypothesis; the
apical effect and the GHS or CLP classification call are `woe_reasoning`'s, and
a region from here is never presented as one.

Do not run all 19 models and reason over the count of actives: the endpoints
have different training sets, applicability domains and base rates, so a tally
across them is not a hazard score. And do not present these results as
reproducing the published paper's numbers.

## Model provenance

Cite this for any prediction that enters an evidence table or a dossier:

> Dracheva, E.; Norinder, U.; Rydén, P.; Engelhardt, J.; Weiss, J. M.;
> Andersson, P. L. *In Silico* Identification of Potential Thyroid Hormone
> System Disruptors among Chemicals in Human Serum and Chemicals with a High
> Exposure Index. *Environ. Sci. Technol.* **2022**.
> DOI: [10.1021/acs.est.1c07762](https://doi.org/10.1021/acs.est.1c07762)

Each RiskMix model is its own web API on SciLifeLab Serve at
`https://<subdomain>.serve.scilifelab.se`, the model name in lower case with `-`
for `_` (`TPO_inhibition` → `tpo-inhibition`, `PPAR_delta_agonist` →
`ppar-delta-agonist`). Calls need network access; large inputs are sent in
batches of 1000 and come back as one CSV.
