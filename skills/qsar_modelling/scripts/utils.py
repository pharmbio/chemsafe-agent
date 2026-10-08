'''Client for the RiskMix conformal QSAR models, each served as its own web API.

Each model runs in its own container on SciLifeLab Serve, at
https://<subdomain>.serve.scilifelab.se, where <subdomain> is the model name in
lower case with "_" written as "-" (TPO_inhibition -> tpo-inhibition,
PPAR-delta_agonist -> ppar-delta-agonist). Each model is a self-contained
folder in `models/` beside this repository (model, server, Dockerfile,
README.md).

QSAR_API_URL overrides the address, with {subdomain} filled in per model.
QSAR_API_URL=http://localhost:8080, for example, sends every call to one local
container, whichever model it asks for.

The model functions return what they did when the models ran in-process: a
dict for one compound, the path to a CSV for several, and "Error: ..." strings
on failure.
'''

import os
import time
from typing import List, Union

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))

API_URL = os.environ.get("QSAR_API_URL", "https://{subdomain}.serve.scilifelab.se")
# The server takes at most 2000 compounds per request. 1000 keeps a request on
# the largest model to ~10-20 s, well inside the proxy's timeout.
BATCH_SIZE = int(os.environ.get("QSAR_API_BATCH_SIZE", "1000"))
TIMEOUT = (10, 300)               # (connect, read) seconds
# A container that is restarting refuses connections or answers 502-504
# through the proxy for a few seconds; anything else is not retried.
ATTEMPTS = 3
RETRY_STATUS = (502, 503, 504)

LEGACY_OUTPUT_DIR = os.path.join(os.path.dirname(HERE), "predictions")
DEFAULT_OUTPUT_SUBFOLDER = "qsar_predictions"
COLUMNS = ["smiles", "endpoint", "confidence", "p_inactive", "p_active", "prediction"]


class PredictionError(RuntimeError):
    '''A prediction could not be produced, with a reason worth showing the agent.'''


#Input handling

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

def model_url(endpoint: str) -> str:
    '''Base URL of one model's API.'''
    return API_URL.format(subdomain=endpoint.lower().replace("_", "-")).rstrip("/")


def _request_predictions(endpoint: str, smiles_list: List[str]) -> List[dict]:
    '''One batch through the model's API: a row per SMILES, in input order.'''

    url = model_url(endpoint) + "/predict"
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
                endpoint, url, TIMEOUT[1]))
        if response.status_code in RETRY_STATUS:
            failure = "answered HTTP {}".format(response.status_code)
            continue
        break
    else:
        raise PredictionError("The {} model at {} {}, after {} attempts.".format(
            endpoint, url, failure, ATTEMPTS))

    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200:
        detail = body.get("detail") if isinstance(body, dict) else response.text[:300]
        raise PredictionError("{} (HTTP {} from {})".format(detail, response.status_code, url))
    if not isinstance(body, dict) or body.get("model") != endpoint:
        raise PredictionError("{} did not answer as the {} model.".format(url, endpoint))
    if len(body["predictions"]) != len(smiles_list):
        raise PredictionError("{} returned {} rows for {} SMILES.".format(
            url, len(body["predictions"]), len(smiles_list)))
    return body["predictions"]


# Output

def _output_dir() -> str:
    '''Directory for batch result CSVs, resolved fresh on every call.

    Batch results are deliverables, so they belong in the conversation's folder
    under ``persistence/results/<user>/<thread>/`` -- the same scope
    ``prepare_output_path`` writes to and the only place the app will list or
    serve them from. That scope is carried in contextvars and differs between
    runs, which is why this is a function and not a constant.

    Precedence: ``THS_OUTPUT_DIR`` (explicit override, read per call) > the
    active conversation scope > ``predictions/`` beside this package. The last
    is the standalone fallback: ``backend.utils.output_paths`` needs the repo
    root importable, which it is not when this package is run on its own.

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


def _output_path(endpoint: str, output_name=None) -> str:
    if output_name:
        path = os.path.abspath(os.path.expanduser(str(output_name)))
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    else:
        path = os.path.join(_output_dir(), "{}_results.csv".format(endpoint))
    if os.path.exists(path):
        os.remove(path)
    return path


def predict_endpoint(endpoint: str, smiles_input: Union[str, List[str]], output_name=None):
    '''Run one endpoint end to end through its API. Shared by every model function in this package.

    A conformal classifier does not return a probability. At the model's
    confidence it returns the set of labels it cannot rule out, so the answer is
    one of `active`, `inactive`, `both` (undecided at this confidence) or
    `empty` (the compound looks unlike anything in the calibration set). The
    confidence is fixed per model by its server and comes back with every row.

    Parameters:
    ---------
    endpoint (str): the model's name, e.g. "TPO_inhibition" or "PPAR-delta_agonist".
    smiles_input (str or list): anything `parse_smiles_input` accepts.
    output_name (str, optional): absolute path for the results CSV. Defaults to
        `<conversation results folder>/qsar_predictions/<endpoint>_results.csv`.

    Returns:
    ----------
    results (dict or str): a dict for a single compound, otherwise the path to the results CSV -- with a warning naming any structure RDKit could not parse.
    '''

    try:
        smiles_list = parse_smiles_input(smiles_input)
        rows = []
        for start in range(0, len(smiles_list), BATCH_SIZE):
            rows.extend(_request_predictions(endpoint, smiles_list[start:start + BATCH_SIZE]))

        bad_smiles = [row["smiles"] for row in rows if row["prediction"] is None]
        if len(bad_smiles) == len(rows):
            raise PredictionError(
                "RDKit could not parse any of the {} supplied structure(s): {}".format(
                    len(smiles_list), ", ".join(bad_smiles[:5])))
    except PredictionError as exc:
        return "Error: {}".format(exc)
    except Exception as exc:       # reported to the caller, not swallowed
        return "Error: {}: {}".format(type(exc).__name__, exc)

    if len(rows) == 1:
        return rows[0]

    path = _output_path(endpoint, output_name)
    pd.DataFrame(rows, columns=COLUMNS).to_csv(path, index=False)
    if bad_smiles:
        listed = ", ".join(bad_smiles[:10]) + ("..." if len(bad_smiles) > 10 else "")
        return ("{}\n\n[warning] {} of {} structures could not be parsed by RDKit and have "
                "an empty prediction: {}".format(path, len(bad_smiles), len(smiles_list), listed))
    return path
