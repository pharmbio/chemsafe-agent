# Model reference

Provenance, configuration and caveats for the 19 conformal models in
`scripts/`. The model list, the call signature and how to read a result are in
`SKILL.md`; this file is what you open when you need a citation, a knob, or the
reasons a number may not match the published one.

Each model is one module exposing one function of the same name:

```python
from scripts.<model> import <model>
```

## Contents

- [Using a prediction as evidence](#using-a-prediction-as-evidence)
- [Anti-patterns](#anti-patterns)
- [Model provenance](#model-provenance)
- [Environment variables](#environment-variables)
- [Known deviations from the published models](#known-deviations-from-the-published-models)
- [Validating the models](#validating-the-models)

---

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

Part of the [RiskMix project](https://www.aces.su.se/riskmix/). Each model is
a Mondrian (label-conditional) inductive conformal predictor built on 50 ICPs
over random-forest classifiers, using 119 RDKit descriptors. Predictions are
median-aggregated across the 50 ICPs.

The pickles in `persistence/models/ths_models/model_files/` were written in 2021 under Python 3.5,
scikit-learn 0.18.2, pandas 0.21 and cloudpickle 1.6 — a stack that no longer
installs. They are read here with an unpickler that never imports scikit-learn,
pandas, nonconformist or cloudpickle: each of their classes becomes an inert
stub that only collects the pickled state, and the parts actually needed (the
decision trees and the calibration scores) are plain numpy. Prediction is then
reimplemented in numpy. The runtime therefore depends on the version of nothing
except numpy, and a future scikit-learn or pandas upgrade cannot break it.

The full engineering account of that port is in the module docstring of
`scripts/utils.py`; read it before editing that file.

### Obtaining the model files

The pickles are ~5.7 GB and are gitignored, so a fresh clone has to fetch them:

- Archive: <https://drive.google.com/file/d/1yTfS9bLOEY3jUdDRTsyc_dSzKsXBb9aE/view?usp=sharing>
- Archive password: `conformal`

Unpack it so the 19 model files sit directly in
`persistence/models/ths_models/model_files/`. `THS_MODELS_DIR` overrides that
location; the variables below relocate the whole model home.

## Environment variables

| Variable          | Default            | Purpose                                                        |
|-------------------|--------------------|-----------------------------------------------------------------|
| `PERSISTENCE_ROOT` | `<repo>/persistence` | App persistence area; the same variable `app/config.py` reads |
| `MODELS_ROOT`     | `$PERSISTENCE_ROOT/models` | Shared model assets                                      |
| `THS_HOME`        | `$MODELS_ROOT/ths_models` | This model set's home (pickles + reference data)          |
| `THS_MODELS_DIR`  | `$THS_HOME/model_files` | Where the unpacked pickles live                             |
| `THS_REFERENCE_DIR` | `$THS_HOME/reference` | Validation fixtures for `validate_native.py`                |
| `THS_OUTPUT_DIR`  | *(conversation scope)* | Overrides the output directory outright; for offline use     |
| `THS_OUTPUT_SUBFOLDER` | `qsar_predictions` | Subfolder inside the conversation results folder            |
| `THS_MODEL_CACHE` | `2`                | Loaded models kept in memory (~0.4–0.7 GB each)                  |
| `THS_SMOOTHING`   | `0`                | `1` restores the upstream smoothed p-values                      |
| `THS_SEED`        | `0`                | Seed for the smoothing draws                                     |

The five model-location variables nest, so overriding any one of them
relocates everything below it. The pickles and validation fixtures are **not**
in the skill tree — they live in the app's persistence area at
`persistence/models/ths_models/`, because 5.7 GB of 2021 pickles is data rather
than source. The package reads these variables directly instead of importing
`app.config`, so it still needs nothing but numpy, pandas and RDKit.

By default batch CSVs are written to
`persistence/results/<user_id>/<thread>/qsar_predictions/`, resolved per call
from the active conversation scope. `THS_OUTPUT_DIR` replaces that directory;
an explicit `output_name=` argument replaces the whole path. When the package
is run standalone — `validate_native.py`, or any process without the repo root
importable — it falls back to a `predictions/` folder beside the package in the
skill tree, since there is no conversation scope to write into.

Leave `THS_SMOOTHING` off unless you are reproducing an upstream run. Smoothing
adds `n_eq * uniform(0,1) / (n_cal+1)` to every p-value, which makes an answer
depend on the RNG *and* on a compound's position in the batch — the same
molecule can get two different answers in two calls. The default unsmoothed
p-value is deterministic, position-independent and slightly conservative, so
regions come out a little wider.

## Known deviations from the published models

Record these whenever a result is compared against the paper.

- **RDKit version drift.** The models were trained on descriptors from a
  2018–2021 RDKit. Under RDKit 2026.03.2, 118 of the 119 descriptors still agree
  to ~1e-14, but `CalcNumHBA` changed definition and differs by up to 2 for 11
  of the 100 example compounds. Measured end-to-end effect: 2 of 1900 region
  calls change. Small, but results are not exactly comparable with the published
  ones.
- **Threshold ties are rounded.** `1 - 0.8` is `0.19999999999999996` in binary
  floating point, and these p-values are rationals with small denominators, so
  ~0.7% land exactly on the 0.2 threshold. The significance is rounded before
  comparison; without that, every tie kept a label it should have dropped.
- **float32 matters.** scikit-learn casts X to float32 before walking a tree, so
  thresholds are compared against float32-rounded descriptors. Comparing in
  float64 flips borderline splits and shifts p-values by up to 0.05. Do not
  "fix" this to float64.
- **Two upstream bugs are not reproduced.** The original `CP_predict.py` shifted
  every prediction after an unparsable SMILES onto the wrong compound, and
  without `-si` renamed result columns that never existed. Both are corrected
  here, so a row-by-row diff against an old upstream run will differ wherever a
  structure failed to parse.

## Validating the models

```bash
python skills/qsar_modelling/scripts/validate_native.py
```

`persistence/models/ths_models/reference/` holds p-values computed by the genuine
original stack (scikit-learn 0.21.3, pandas 1.1.5, nonconformist, cloudpickle
1.6, running the real pickled lambdas) for the 100 compounds in
`scripts/example_dataset.csv`, with smoothing off so they are deterministic. All 19
models reproduce them bit-for-bit (`np.array_equal`, not a tolerance). The
stochastic path was checked separately: seeding both implementations identically
also reproduces the smoothed p-values exactly.

Run this after any change to `scripts/utils.py`, and after a numpy or RDKit
upgrade.

---
