'''Client for ADMET-AI, served as one web API that returns every endpoint at once.

ADMET-AI is a single service: one call predicts all 41 ADMET endpoints and
computes all 11 physicochemical properties for every structure sent. There is
nothing to select -- asking for CYP3A4 inhibition and asking for hERG blocking
are the same call, and the answer to both is already in the same row.

The model is `admet_ai` 2.0.1 (Swanson et al., 2024): Chemprop-RDKit graph neural
networks, two ensembles of five, trained on Therapeutics Data Commons datasets.
It runs at https://admet-ai.serve.scilifelab.se; ADMET_API_URL overrides the
address, e.g. ADMET_API_URL=http://localhost:8080 for a local container.

A classification endpoint comes back as an uncalibrated probability of the
positive label, a regression endpoint as a value in that endpoint's own units,
and no applicability domain is applied -- see the "Read the result" section of
SKILL.md before reporting anything from here.

The return shapes match the `qsar_modelling` functions: a dict for one compound,
the path to a CSV for several, and "Error: ..." strings on failure.
'''

import os
import time
from typing import List, Union

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))

API_URL = os.environ.get("ADMET_API_URL", "https://admet-ai.serve.scilifelab.se")
# The server takes at most 2000 compounds per request. 1000 keeps a request to
# roughly 15-30 s, well inside the proxy's timeout.
BATCH_SIZE = int(os.environ.get("ADMET_API_BATCH_SIZE", "1000"))
TIMEOUT = (10, 300)               # (connect, read) seconds
# A container that is restarting refuses connections or answers 502-504 through
# the proxy for a few seconds; anything else is not retried.
ATTEMPTS = 3
RETRY_STATUS = (502, 503, 504)

ENDPOINT = "ADMET-AI"
LEGACY_OUTPUT_DIR = os.path.join(os.path.dirname(HERE), "predictions")
DEFAULT_OUTPUT_SUBFOLDER = "admet_predictions"


class PredictionError(RuntimeError):
    '''A prediction could not be produced, with a reason worth showing the agent.'''


# Input handling

def parse_smiles_input(smiles_input: Union[str, List[str]]) -> List[str]:
    '''Normalise any accepted SMILES input into a list of SMILES strings.

    Accepts a single SMILES, a comma-separated string, a list, or a path to a
    CSV/TSV file with a column whose name contains "smiles".

    Parameters:
    ---------
    smiles_input (str or list): a single SMILES, a comma-separated string, a list of SMILES, or a path to a CSV/TSV file with a 'smiles' column.

    Returns:
    ----------
    smiles_list (list): the SMILES strings, in input order.
    '''

    if isinstance(smiles_input, str) and os.path.isfile(smiles_input):
        ext = os.path.splitext(smiles_input)[-1].lower()
        if ext not in (".csv", ".tsv"):
            raise PredictionError("Only CSV or TSV files are supported for SMILES input.")
        frame = pd.read_csv(smiles_input, sep="\t" if ext == ".tsv" else ",")
        matches = [col for col in frame.columns if "smiles" in str(col).lower()]
        if not matches:
            raise PredictionError(
                "No column containing 'smiles' found in {}. Columns present: {}.".format(
                    smiles_input, ", ".join(str(c) for c in frame.columns)))
        smiles_list = [s.strip() for s in frame[matches[0]].dropna().astype(str) if s.strip()]
    elif isinstance(smiles_input, str) and smiles_input.strip().lower().endswith((".csv", ".tsv")):
        raise PredictionError("File not found: {}. Pass the file's full path.".format(smiles_input))
    elif isinstance(smiles_input, str):
        smiles_list = [item.strip() for item in smiles_input.split(",") if item.strip()]
    elif isinstance(smiles_input, (list, tuple)):
        smiles_list = [str(item).strip() for item in smiles_input if str(item).strip()]
    else:
        raise PredictionError(
            "Input must be a SMILES string, a comma-separated string, a list of "
            "SMILES, or a path to a CSV/TSV file with a 'smiles' column.")

    if not smiles_list:
        raise PredictionError("No valid SMILES strings were provided.")
    return smiles_list


# Calling the model's API

def _request_predictions(smiles_list: List[str]):
    '''One batch through the API: a row per SMILES, in input order, and the SMILES RDKit rejected.'''

    url = API_URL.rstrip("/") + "/predict"
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(5 * attempt)
        try:
            response = requests.post(url, json={"smiles": smiles_list}, timeout=TIMEOUT)
        except requests.ConnectionError as exc:
            failure = "could not be reached ({})".format(type(exc).__name__)
            continue
        except requests.Timeout:
            raise PredictionError("The {} model at {} did not answer within {} s.".format(
                ENDPOINT, url, TIMEOUT[1]))
        if response.status_code in RETRY_STATUS:
            failure = "answered HTTP {}".format(response.status_code)
            continue
        break
    else:
        raise PredictionError("The {} model at {} {}, after {} attempts.".format(
            ENDPOINT, url, failure, ATTEMPTS))

    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200:
        detail = body.get("detail") if isinstance(body, dict) else response.text[:300]
        raise PredictionError("{} (HTTP {} from {})".format(detail, response.status_code, url))
    if not isinstance(body, dict) or body.get("model") != ENDPOINT:
        raise PredictionError("{} did not answer as the {} model.".format(url, ENDPOINT))
    if len(body["predictions"]) != len(smiles_list):
        raise PredictionError("{} returned {} rows for {} SMILES.".format(
            url, len(body["predictions"]), len(smiles_list)))
    return body["predictions"], body.get("unparsed") or []


# Output

def _output_dir() -> str:
    '''Directory for batch result CSVs, resolved fresh on every call.

    Batch results are deliverables, so they belong in the conversation's folder
    under ``persistence/results/<user>/<thread>/`` -- the same scope
    ``prepare_output_path`` writes to and the only place the app will list or
    serve them from. That scope is carried in contextvars and differs between
    runs, which is why this is a function and not a constant.

    Precedence: ``THS_OUTPUT_DIR`` (explicit override, read per call) > the
    active conversation scope > ``predictions/`` beside this package.

    Returns:
    ----------
    directory (str): an existing directory to write result CSVs into.
    '''

    override = os.environ.get("THS_OUTPUT_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override

    try:
        from backend.utils.output_paths import resolve_output_folder
        subfolder = os.environ.get("THS_OUTPUT_SUBFOLDER", DEFAULT_OUTPUT_SUBFOLDER)
        return str(resolve_output_folder(subfolder or None))
    except Exception:
        # No app context (standalone run, or the scope helpers are unavailable).
        os.makedirs(LEGACY_OUTPUT_DIR, exist_ok=True)
        return LEGACY_OUTPUT_DIR


def _output_path(output_name=None) -> str:
    if output_name:
        path = os.path.abspath(os.path.expanduser(str(output_name)))
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    else:
        path = os.path.join(_output_dir(), "{}_results.csv".format(ENDPOINT))
    if os.path.exists(path):
        os.remove(path)
    return path


def admet_ai(smiles_input, output_name=None):
    '''Predict all 41 ADMET endpoints and compute all 11 physicochemical properties, through the ADMET-AI web API.

    One call covers every endpoint: absorption, distribution, metabolism,
    excretion, toxicity (hERG, AMES, DILI, ClinTox, LD50, carcinogenicity, skin
    reaction and the 12 Tox21 assays), the five CYP inhibition and three CYP
    substrate endpoints, and the RDKit physicochemical properties. There is no
    endpoint argument -- every result row carries all 52 columns.

    How to read what comes back:

    - A classification column is the predicted probability of the positive
      label, from 0 to 1, and it is uncalibrated: it ranks compounds, it does
      not give an observed frequency. It is not a label, and the model applies
      no threshold.
    - A regression column is a value in that endpoint's own units (for example
      `Solubility_AqSolDB` in log(mol/L), `LD50_Zhu` in log(1/(mol/kg)),
      `Half_Life_Obach` in hours). Values are not clipped to a physical range.
    - No applicability domain is applied. Every structure RDKit parses gets a
      value, salts, mixtures and metals included, so a number here is never on
      its own evidence that the compound is in domain.

    See SKILL.md for each endpoint's units, training-set size and the authors'
    test-set metrics, which must be reported alongside any value used as
    evidence. Several endpoints have weak test-set performance, and
    `Half_Life_Obach` and `VDss_Lombardo` have a negative R^2.

    Parameters:
    ---------
    smiles_input (str or list): A SMILES string, a comma-separated string of SMILES, a list of SMILES, or a path to a CSV/TSV file with a 'smiles' column.
    output_name (str, optional): absolute path for the results CSV, from `prepare_output_path(...)`. Defaults to `<conversation results folder>/admet_predictions/ADMET-AI_results.csv`.

    Returns:
    ----------
    results (dict or str): A dict of all 52 columns for a single compound, otherwise the path to the results CSV -- with a warning naming any structure RDKit could not parse.
    '''

    try:
        smiles_list = parse_smiles_input(smiles_input)
        rows, unparsed = [], []
        for start in range(0, len(smiles_list), BATCH_SIZE):
            batch_rows, batch_unparsed = _request_predictions(smiles_list[start:start + BATCH_SIZE])
            rows.extend(batch_rows)
            unparsed.extend(batch_unparsed)

        if len(unparsed) == len(rows):
            raise PredictionError(
                "RDKit could not parse any of the {} supplied structure(s): {}".format(
                    len(smiles_list), ", ".join(unparsed[:5])))
    except PredictionError as exc:
        return "Error: {}".format(exc)
    except Exception as exc:       # reported to the caller, not swallowed
        return "Error: {}: {}".format(type(exc).__name__, exc)

    if len(rows) == 1:
        return rows[0]

    # The server returns the same columns, in the same order, for every batch.
    path = _output_path(output_name)
    pd.DataFrame(rows, columns=list(rows[0])).to_csv(path, index=False)
    if unparsed:
        listed = ", ".join(unparsed[:10]) + ("..." if len(unparsed) > 10 else "")
        return ("{}\n\n[warning] {} of {} structures could not be parsed by RDKit and have "
                "empty predictions: {}".format(path, len(unparsed), len(smiles_list), listed))
    return path
