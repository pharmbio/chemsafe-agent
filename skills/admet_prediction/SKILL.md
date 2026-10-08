---
name: admet_prediction
description: Predicts ADMET and physicochemical properties from structure with ADMET-AI. One call returns all 41 endpoints and 11 computed properties at once - absorption (HIA, bioavailability, solubility, logD, Caco-2, PAMPA, P-gp), distribution (BBB, plasma protein binding, Vd), metabolism (CYP1A2/2C9/2C19/2D6/3A4 inhibition and substrate), excretion (clearance, half-life), toxicity (hERG, Ames, DILI, ClinTox, carcinogenicity, LD50, skin sensitisation, the 12 Tox21 assays) and RDKit properties (MW, logP, TPSA, QED, Lipinski, PAINS/BRENK/NIH alerts). Use when an ADMET, pharmacokinetic, toxicity or drug-likeness property must be estimated from structure because no measured value exists. Classification endpoints return an uncalibrated probability and regression endpoints a value in the endpoint's own units; no applicability domain is applied.
---

# ADMET Prediction Skill

ADMET-AI is **one model behind one web API**, not a library of models. A single
call predicts all 41 ADMET endpoints and computes all 11 physicochemical
properties for every structure sent. There is nothing to select: asking for
CYP3A4 inhibition and asking for hERG blocking are the same call, and both
answers are already in the same row.

```python
from scripts.admet_ai import admet_ai

admet_ai("CN1C=NC2=C1C(=O)N(C)C(=O)N2C")
```

**Import the function out of its own module.** The module and the function share
a name, so `from scripts import admet_ai` binds the *module* and calling it
raises `'module' object is not callable`.

Read [Read the result](#read-the-result) before reporting anything: what a
column supports depends on whether it is a classification endpoint, a regression
endpoint or a computed property.

## Available endpoints

Look up the column you were asked about and read it out of the row you already
have; never call again for a second endpoint.

`n` is the training-set size and the metrics are the authors' test-set values,
both from Therapeutics Data Commons. Report them with any value used as
evidence - they are what tells a reader how much the number is worth.
**AUROC/AUPRC marks a classification endpoint**, returned as an uncalibrated
probability from 0 to 1; **R²/MAE marks a regression endpoint**, returned in the
units given.

### Absorption

| Asked for | Result column | Units, test metrics (n) |
|---|---|---|
| Human intestinal absorption, HIA | `HIA_Hou` | AUROC 0.994, AUPRC 0.999 (n=578) |
| Oral bioavailability, F | `Bioavailability_Ma` | AUROC 0.716, AUPRC 0.870 (n=640) |
| Aqueous solubility, logS | `Solubility_AqSolDB` | log(mol/L) - R² 0.817, MAE 0.69 (n=9,980) |
| Lipophilicity, logD 7.4 | `Lipophilicity_AstraZeneca` | log-ratio - R² 0.771, MAE 0.41 (n=4,200) |
| Hydration free energy | `HydrationFreeEnergy_FreeSolv` | kcal/mol - R² 0.888, MAE 0.78 (n=642) |
| Caco-2 permeability, Papp | `Caco2_Wang` | log(10⁻⁶ cm/s) - R² 0.707, MAE 0.31 (n=906) |
| PAMPA permeability | `PAMPA_NCATS` | AUROC 0.786, AUPRC 0.955 (n=2,034) |
| P-glycoprotein inhibition, P-gp | `Pgp_Broccatelli` | AUROC 0.947, AUPRC 0.958 (n=1,212) |

### Distribution

| Asked for | Result column | Units, test metrics (n) |
|---|---|---|
| Blood-brain barrier penetration, BBB | `BBB_Martins` | AUROC 0.900, AUPRC 0.959 (n=1,975) |
| Plasma protein binding, PPB | `PPBR_AZ` | % bound - R² 0.589, MAE 6.85 (n=1,614) |
| Volume of distribution, Vd,ss | `VDss_Lombardo` | L/kg - **R² -1.211**, MAE 5.04 (n=1,111) |

### Metabolism

| Asked for | Result column | Units, test metrics (n) |
|---|---|---|
| CYP1A2 inhibition | `CYP1A2_Veith` | AUROC 0.941, AUPRC 0.935 (n=12,579) |
| CYP2C19 inhibition | `CYP2C19_Veith` | AUROC 0.906, AUPRC 0.875 (n=12,665) |
| CYP2C9 inhibition | `CYP2C9_Veith` | AUROC 0.908, AUPRC 0.822 (n=12,092) |
| CYP2D6 inhibition | `CYP2D6_Veith` | AUROC 0.886, AUPRC 0.726 (n=13,130) |
| CYP3A4 inhibition | `CYP3A4_Veith` | AUROC 0.912, AUPRC 0.873 (n=12,328) |
| CYP2C9 substrate | `CYP2C9_Substrate_CarbonMangels` | AUROC 0.628, AUPRC 0.370 (n=666) |
| CYP2D6 substrate | `CYP2D6_Substrate_CarbonMangels` | AUROC 0.820, AUPRC 0.660 (n=664) |
| CYP3A4 substrate | `CYP3A4_Substrate_CarbonMangels` | AUROC 0.705, AUPRC 0.727 (n=667) |

### Excretion

| Asked for | Result column | Units, test metrics (n) |
|---|---|---|
| Clearance, CL - hepatocyte | `Clearance_Hepatocyte_AZ` | µL/min/10⁶ cells - R² 0.264, MAE 32.7 (n=1,020) |
| Clearance, CL - microsome | `Clearance_Microsome_AZ` | µL/min/mg - R² 0.277, MAE 26.7 (n=1,102) |
| Half-life, t½ | `Half_Life_Obach` | hours - **R² -2.386**, MAE 31.8 (n=665) |

A request for "clearance" or "CL" with no matrix named covers both columns;
report both and say which matrix each is.

### Toxicity

| Asked for | Result column | Units, test metrics (n) |
|---|---|---|
| hERG blocking, cardiotoxicity | `hERG` | AUROC 0.839, AUPRC 0.906 (n=648) |
| Clinical toxicity, trial failure | `ClinTox` | AUROC 0.928, AUPRC 0.607 (n=1,459) |
| Mutagenicity, Ames | `AMES` | AUROC 0.882, AUPRC 0.896 (n=7,255) |
| Drug-induced liver injury, DILI | `DILI` | AUROC 0.881, AUPRC 0.878 (n=475) |
| Carcinogenicity | `Carcinogens_Lagunin` | AUROC 0.772, AUPRC 0.608 (n=278) |
| Acute toxicity, LD50 (rat, oral) | `LD50_Zhu` | log(1/(mol/kg)) - R² 0.596, MAE 0.45 (n=7,342) |
| Skin reaction, sensitisation | `Skin_Reaction` | AUROC 0.718, AUPRC 0.873 (n=404) |

### Toxicity - Tox21 assays

Twelve qHTS nuclear-receptor and stress-response assays. AUPRC is low on most of
them because actives are rare; see [Choosing a threshold](#choosing-a-threshold).

| Asked for | Result column | Test metrics (n) |
|---|---|---|
| Androgen receptor, full length | `NR-AR` | AUROC 0.779, AUPRC 0.515 (n=7,258) |
| Androgen receptor, ligand-binding domain | `NR-AR-LBD` | AUROC 0.883, AUPRC 0.672 (n=6,751) |
| Aryl hydrocarbon receptor, AhR | `NR-AhR` | AUROC 0.904, AUPRC 0.636 (n=6,542) |
| Aromatase, CYP19A1 | `NR-Aromatase` | AUROC 0.906, AUPRC 0.475 (n=5,815) |
| Estrogen receptor, full length | `NR-ER` | AUROC 0.749, AUPRC 0.458 (n=6,186) |
| Estrogen receptor, ligand-binding domain | `NR-ER-LBD` | AUROC 0.826, AUPRC 0.484 (n=6,948) |
| PPAR-gamma | `NR-PPAR-gamma` | AUROC 0.916, AUPRC 0.439 (n=6,443) |
| Antioxidant response element, Nrf2/ARE | `SR-ARE` | AUROC 0.838, AUPRC 0.520 (n=5,825) |
| ATAD5, genotoxicity | `SR-ATAD5` | AUROC 0.879, AUPRC 0.312 (n=7,065) |
| Heat shock response, HSE | `SR-HSE` | AUROC 0.843, AUPRC 0.427 (n=6,460) |
| Mitochondrial membrane potential | `SR-MMP` | AUROC 0.925, AUPRC 0.698 (n=5,804) |
| Tumour protein p53 | `SR-p53` | AUROC 0.880, AUPRC 0.441 (n=6,767) |

### Physicochemical properties

These eleven are **computed by RDKit, not predicted**. They are exact for the
structure as given, carry no model error and no metrics, and must never be
called predictions or given a confidence.

| Asked for | Result column | What it is |
|---|---|---|
| Molecular weight, MW | `molecular_weight` | Da |
| LogP | `logP` | Crippen calculated logP, not the measured value |
| H-bond acceptors | `hydrogen_bond_acceptors` | count |
| H-bond donors | `hydrogen_bond_donors` | count |
| Lipinski rule of five | `Lipinski` | how many of the 4 rules are **satisfied**, 0-4; 4 means no violation |
| Druglikeness, QED | `QED` | 0-1, higher is more drug-like |
| Stereocentres | `stereo_centers` | count |
| Polar surface area, TPSA | `tpsa` | Å² |
| PAINS alerts | `PAINS_alert` | **count** of matched alert substructures |
| BRENK alerts | `BRENK_alert` | **count** of matched alert substructures |
| NIH alerts | `NIH_alert` | **count** of matched alert substructures |

## Run the model

```python
admet_ai("CCO")                           # one SMILES      -> dict of all 54 fields
admet_ai("CCO, c1ccccc1O")                # comma-separated -> path to CSV
admet_ai(["CCO", "c1ccccc1O"])            # list            -> path to CSV
admet_ai("/full/path/compounds.csv")      # CSV/TSV with a column containing "smiles"
```

A file must be given by its full path; a bare file name is not found.

- **One compound returns a dict; more than one writes a CSV and returns its path.** A trailing `[warning]` line names any structure RDKit could not parse, so take `str(path).splitlines()[0]` before opening the file.
- An unparsable structure keeps its row, in input order, with every value empty.
- **Failures come back as `"Error: ..."` strings, not exceptions.** Check that prefix before using a result. An error naming the model's URL means the service could not be reached or rejected the input: the model has not run, so report the call as failed, not as a null result.
- **Standardize first** with `cheminformatics.standardize_smiles`. ADMET-AI predicts whatever structure it is given, salt and all. Never strip stereochemistry to make a SMILES parse.

The call takes about 30 s per 1000 compounds - for every endpoint at once, which
is why there is never a reason to loop over endpoints or to call twice for the
same compounds.

## Run a dataset

The result CSV holds `smiles`, `endpoint` and the 52 property columns, one row
per input SMILES in input order. To keep a dataset's IDs, standardize, pass the
list, and attach the prediction columns back by position:

```python
import pandas as pd
from scripts.chem_standardize import standardize_molecules
from scripts.admet_ai import admet_ai

df = pd.read_csv("/full/path/compounds.csv").dropna(subset=["SMILES"])
df["std_smiles"] = [m.canonical_smiles for m in standardize_molecules(df["SMILES"])]
df = df[df["std_smiles"].notna()].reset_index(drop=True)

res = admet_ai(df["std_smiles"].tolist())
pred = pd.read_csv(str(res).splitlines()[0])
df = pd.concat([df, pred.drop(columns=["smiles", "endpoint"])], axis=1)
```

Keep only the columns the task asked for before showing or writing a table; 52
columns per compound is not a readable deliverable.

If any structure failed to parse, pandas reads the count columns
(`hydrogen_bond_acceptors`, `Lipinski`, `PAINS_alert`, ...) as floats because of
the empty rows. Compare them as numbers, not as integers.

Batch CSVs land in the conversation's results folder, resolved per call -
nothing to pass in, and a re-run in the same conversation overwrites it:

```
persistence/results/<user_id>/<thread>/admet_predictions/ADMET-AI_results.csv
```

To name the file yourself, pass
`output_name=prepare_output_path("admet_predictions.csv")`.

## Read the result

The model attaches no confidence level to any value, so none may be reported
with one.

**A classification column is an uncalibrated probability** of the positive label
as that endpoint's training set defined it, 0 to 1. It is not a label; the model
applies no threshold. AUROC and AUPRC measure how well the model *ranks*
compounds, not whether its numbers match observed frequencies. `DILI = 0.93`
does not mean "93 % of compounds like this cause liver injury" - it means this
compound ranks high on the DILI model (caffeine scores 0.93, on a model trained
on 475 compounds). Use these values comparatively: to rank or triage a set, to
flag the top of a list for a closer look.

**A regression column is a value in that endpoint's own units**, taken from the
tables above and always stated. Values are **not clipped to a physical range**:
caffeine is predicted `Clearance_Microsome_AZ = -13.0` µL/min/mg and
`VDss_Lombardo = -2.06` L/kg. A negative clearance, volume of distribution,
half-life or solubility is the model failing on that compound - report it as a
failure, never as a low value.

**Two endpoints must not be used quantitatively.** `Half_Life_Obach`
(R² -2.386) and `VDss_Lombardo` (R² -1.211) have a negative test-set R²: on the
authors' own test set they are worse than always predicting the training mean.
If asked for half-life or volume of distribution, say the available model does
not support a quantitative answer and give the R² as the reason.

**These are weak - say so when using them:** `Clearance_Hepatocyte_AZ`
(R² 0.264), `Clearance_Microsome_AZ` (R² 0.277), `CYP2C9_Substrate_CarbonMangels`
(AUROC 0.628), `CYP3A4_Substrate_CarbonMangels` (0.705), `Bioavailability_Ma`
(0.716), `Skin_Reaction` (0.718), `NR-ER` (0.749), `Carcinogens_Lagunin`
(0.772, n=278), `NR-AR` (0.779).

### There is no applicability domain

ADMET-AI applies none. Every structure RDKit parses gets a number - salts,
mixtures, metals, polymers, inorganics, anything. **A value coming back is not
evidence that the compound is in domain.** The training sets are Therapeutics
Data Commons pharmaceutical data, so an industrial chemical, a surfactant, a
metal salt or a PFAS may be far outside anything the model has seen while still
receiving a confident-looking number.

The domain argument has to be made separately, every time, and stated beside the
value: run `cheminformatics.applicability_domain_check` against a reference set,
check the physicochemical columns in the same row against drug-like ranges, and
apply plain chemical judgement about whether the substance resembles a drug at
all.

### Choosing a threshold

A probability becomes a call only with a threshold, and ADMET-AI ships none.
**0.5 is a convention, not a validated cut-off.** If you dichotomize, say the
threshold you used and that you chose it. For the Tox21 assays this matters
sharply: actives are rare, so AUPRC runs 0.31-0.70 even where AUROC looks
strong. `SR-ATAD5` has AUROC 0.879 and AUPRC 0.312 - a 0.5 cut-off there returns
mostly false positives. Prefer ranking a set; where a threshold is unavoidable,
prefer a high one and treat the result as a shortlist for review.

## Using a prediction as evidence

An ADMET-AI value is one line of evidence, never a conclusion. Record the value,
its units or the fact that it is a probability, the endpoint's test-set metrics
and training-set size, the absence of an applicability domain, and the citation
together.

- **`database_traversal`** - check for a measured value first. A prediction where an experimental result exists is a weaker answer, not a faster one.
- **`cheminformatics`** - supplies the standardized structure on the way in, and `applicability_domain_check` on the way out. That check is the only domain evidence available here, so run it rather than treating it as optional.
- **`woe_reasoning`** - an uncalibrated probability carrying no applicability domain is a weak Q-silico line of evidence. Weight it explicitly as such. A low probability is not a negative result.
- **`qprf_generating`** - QPRF §4 and §7 ask for the applicability domain. Write that ADMET-AI defines none and give the external domain assessment used, rather than leaving the section to imply one existed.
- **`qsar_modelling`** - `NR-AhR` overlaps `AHR_agonists`, and `NR-PPAR-gamma` overlaps `PPAR_gamma_agonist`/`PPAR_gamma_antagonist`. Different training data, different assay definitions, different output types: **neither confirms the other, and the two must never be merged or averaged.** Report both side by side, each with its own statistics, and let a disagreement stand. Where `qsar_modelling` covers the endpoint, prefer it - it states a confidence level and an applicability domain, and this model states neither.

These models predict molecular and pharmacokinetic properties. The apical effect
and the GHS or CLP classification call are `woe_reasoning`'s, and a prediction
from here is never presented as one.

Do not tally endpoints into a score: the 41 endpoints have different training
sets, base rates and reliabilities, so a count of "hits" across them is not a
hazard ranking.

## Model provenance

Cite this for any prediction that enters an evidence table or a dossier:

> Swanson, K.; Walther, P.; Leitz, J.; Mukherjee, S.; Wu, J. C.;
> Shivnaraine, R. V.; Zou, J. ADMET-AI: a machine learning ADMET platform for
> evaluation of large-scale chemical libraries. *Bioinformatics* **2024**,
> 40 (7), btae416.
> DOI: [10.1093/bioinformatics/btae416](https://doi.org/10.1093/bioinformatics/btae416)

The served model is `admet_ai` 2.0.1: Chemprop-RDKit graph neural networks, two
ensembles of five, trained on Therapeutics Data Commons datasets. It runs at
`https://admet-ai.serve.scilifelab.se`; `GET /` returns the live property
catalogue the tables above were built from.
