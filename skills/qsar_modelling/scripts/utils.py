import os
import pickle
from typing import List, Union

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors, GraphDescriptors, rdMolDescriptors

HERE = os.path.dirname(os.path.abspath(__file__))


REPO_ROOT = os.path.abspath(os.path.join(HERE, *([os.pardir] * 3)))
PERSISTENCE_ROOT = os.environ.get("PERSISTENCE_ROOT", os.path.join(REPO_ROOT, "persistence"))
MODELS_ROOT = os.environ.get("MODELS_ROOT", os.path.join(PERSISTENCE_ROOT, "models"))
THS_HOME = os.environ.get("THS_HOME", os.path.join(MODELS_ROOT, "ths_models"))

MODELS_DIR = os.environ.get("THS_MODELS_DIR", os.path.join(THS_HOME, "model_files"))
REFERENCE_DIR = os.environ.get("THS_REFERENCE_DIR", os.path.join(THS_HOME, "reference"))
LEGACY_OUTPUT_DIR = os.path.join(os.path.dirname(HERE), "predictions")
DEFAULT_OUTPUT_SUBFOLDER = "qsar_predictions"
SMOOTHING = os.environ.get("THS_SMOOTHING", "0") == "1"
SEED = int(os.environ.get("THS_SEED", "0"))
# Each loaded model holds ~0.4 GB of tree arrays, so only a few are kept around.
CACHE_SIZE = int(os.environ.get("THS_MODEL_CACHE", "2"))


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
        smiles_list = frame[matches[0]].dropna().astype(str).tolist()
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


# Descriptors
# The 119 RDKit descriptors the models were trained on, in the order the trees
# index them. Ported verbatim from the upstream descriptor.py -- the order and
# the exact set are part of the models, so neither may be changed.
_DESCRIPTOR_NAMES = [
    'MolLogP', 'MolMR', 'LabuteASA', 'TPSA', 'MolWt', 'ExactMolWt', 'CalcNumLipinskiHBA',
    'CalcNumLipinskiHBD', 'NumRotatableBonds',
    'CalcNumHBD', 'CalcNumHBA', 'CalcNumAmideBonds', 'NumHeteroatoms', 'HeavyAtomCount', 'NumAtoms',
    'CalcNumAtomStereoCenters',
    'CalcNumUnspecifiedAtomStereoCenters', 'RingCount', 'NumAromaticRings', 'NumSaturatedRings',
    'NumAliphaticRings',
    'NumAromaticHeterocycles', 'NumSaturatedHeterocycles', 'NumAliphaticHeterocycles',
    'NumAromaticCarbocycles',
    'NumSaturatedCarbocycles', 'NumAliphaticCarbocycles', 'FractionCSP3',
    'Chi0v', 'Chi1v', 'Chi2v', 'Chi3v', 'Chi4v', 'Chi1n', 'Chi2n', 'Chi3n', 'Chi4n',
    'HallKierAlpha', 'Kappa1', 'Kappa2', 'Kappa3',
    'SlogP_VSA1', 'SlogP_VSA2', 'SlogP_VSA3', 'SlogP_VSA4', 'SlogP_VSA5', 'SlogP_VSA6', 'SlogP_VSA7',
    'SlogP_VSA8', 'SlogP_VSA9', 'SlogP_VSA10', 'SlogP_VSA11', 'SlogP_VSA12',
    'SMR_VSA1', 'SMR_VSA2', 'SMR_VSA3', 'SMR_VSA4', 'SMR_VSA5', 'SMR_VSA6', 'SMR_VSA7', 'SMR_VSA8',
    'SMR_VSA9', 'SMR_VSA10',
    'PEOE_VSA1', 'PEOE_VSA2', 'PEOE_VSA3', 'PEOE_VSA4', 'PEOE_VSA5', 'PEOE_VSA6', 'PEOE_VSA7', 'PEOE_VSA8',
    'PEOE_VSA9', 'PEOE_VSA10', 'PEOE_VSA11', 'PEOE_VSA12', 'PEOE_VSA13', 'PEOE_VSA14',
    'MQNs_',
]


def compound_descriptors(smiles: str) -> np.ndarray:
    '''The 119 model descriptors for one SMILES, or raise if RDKit cannot parse it.

    Parameters:
    ---------
    smiles (str): one SMILES string.

    Returns:
    ----------
    descriptors (numpy array): the 119 descriptor values, in model order.
    '''

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise PredictionError("bad molecule {}!".format(smiles))
    Chem.Kekulize(mol)

    values = []
    for feature in _DESCRIPTOR_NAMES:
        if feature == 'NumAtoms':
            found = len(Chem.rdchem.Mol.GetAtoms(Chem.rdmolops.AddHs(mol)))
        else:
            found = next((getattr(source, feature)(mol)
                          for source in (Descriptors, rdMolDescriptors, GraphDescriptors)
                          if hasattr(source, feature)), None)
        if isinstance(found, list):
            values.extend(found)      # MQNs_ expands to 42 values
        else:
            values.append(found)
    return np.asarray(values)


def descriptor_matrix(smiles_list: List[str]):
    '''Descriptors for a list of SMILES, reporting which ones RDKit rejected.

    Upstream returned a bare `(None, exception)` pair when a *single* molecule
    failed, which broke every caller that unpacked three values; the shape of the
    result is the same here whatever goes wrong.

    Parameters:
    ---------
    smiles_list (list): SMILES strings to featurize.

    Returns:
    ----------
    result (tuple): the (n_ok, 119) descriptor matrix, the indices of the molecules that failed, and those molecules.
    '''

    rows, bad_index, bad_smiles = [], [], []
    for i, smiles in enumerate(smiles_list):
        try:
            rows.append(compound_descriptors(smiles))
        except Exception:
            bad_index.append(i)
            bad_smiles.append(smiles)
    matrix = np.asarray(rows) if rows else np.empty((0, 119))
    return matrix, bad_index, bad_smiles


# Reading the model pickles 

_REAL_ROOTS = {"numpy", "builtins", "collections", "functools", "copyreg", "_codecs"}


class _Stub:
    '''Placeholder for any class we refuse to import; keeps the pickled state.'''

    def __init__(self, *args, **kwargs):
        self._ctor_args = args

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update(state)
        else:
            self.__dict__["_state"] = state


class _AnyCallable:
    '''Absorbs cloudpickle's function and code-object machinery.

    The pickles embed `condition = lambda x: x[1]` and
    `agg_func = lambda x: np.median(x, axis=2)` from Consensus.py as raw CPython
    3.5 bytecode. Both are reimplemented below, so the stored copies -- which
    could not be built or run anyway -- are discarded.
    '''

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _AnyCallable()

    def __setstate__(self, state):
        pass


class _StubUnpickler(pickle.Unpickler):
    _classes = {}

    def find_class(self, module, name):
        root = module.split(".")[0]
        if root in _REAL_ROOTS:
            return super().find_class(module, name)
        if root == "cloudpickle":
            return _AnyCallable
        key = (module, name)
        if key not in self._classes:
            self._classes[key] = type(name, (_Stub,), {"__module__": module})
        return self._classes[key]


# The conformal predictor 

class _Forest:
    '''A random forest flattened into padded arrays, one row per tree.'''

    __slots__ = ("left", "right", "feature", "threshold", "value")

    def __init__(self, trees):
        n_trees = len(trees)
        width = max(tree.node_count for tree in trees)
        n_classes = trees[0].values.shape[2]

        self.left = np.zeros((n_trees, width), dtype=np.int32)
        self.right = np.zeros((n_trees, width), dtype=np.int32)
        self.feature = np.zeros((n_trees, width), dtype=np.int32)
        self.threshold = np.zeros((n_trees, width), dtype=np.float64)
        self.value = np.zeros((n_trees, width, n_classes), dtype=np.float64)

        for i, tree in enumerate(trees):
            n = tree.node_count
            nodes = tree.nodes[:n]
            self.left[i, :n] = nodes["left_child"]
            self.right[i, :n] = nodes["right_child"]
            # Leaves keep feature == -2; clamp so it stays a valid column index.
            self.feature[i, :n] = np.maximum(nodes["feature"], 0)
            self.threshold[i, :n] = nodes["threshold"]

            # DecisionTreeClassifier.predict_proba normalises each leaf.
            values = tree.values[:n, 0, :].astype(np.float64)
            totals = values.sum(axis=1)
            totals[totals == 0.0] = 1.0
            self.value[i, :n] = values / totals[:, None]

    def predict_proba(self, x):
        '''Mean of the per-tree normalised leaf distributions.'''
        n_trees, n_samples = self.left.shape[0], x.shape[0]
        tree_idx = np.arange(n_trees)[:, None]
        sample_idx = np.arange(n_samples)[None, :]
        node = np.zeros((n_trees, n_samples), dtype=np.int32)

        while True:
            left = self.left[tree_idx, node]
            internal = left != -1                 # -1 marks a leaf (TREE_LEAF)
            if not internal.any():
                break
            feature = self.feature[tree_idx, node]
            go_left = x[sample_idx, feature] <= self.threshold[tree_idx, node]
            nxt = np.where(go_left, left, self.right[tree_idx, node])
            node = np.where(internal, nxt, node).astype(np.int32)

        return self.value[tree_idx, node].mean(axis=0)


class _Icp:
    '''One inductive conformal predictor: a forest plus its calibration scores.'''

    __slots__ = ("forest", "cal_scores", "classes")

    def __init__(self, forest, cal_scores, classes):
        self.forest = forest
        # Upstream stores them descending and reverses on every lookup.
        self.cal_scores = {k: np.asarray(v)[::-1].copy() for k, v in cal_scores.items()}
        self.classes = classes

    def p_values(self, x, smoothing, rng):
        proba = self.forest.predict_proba(x)
        p = np.zeros((x.shape[0], self.classes.size))

        for i, c in enumerate(self.classes):
            # InverseProbabilityErrFunc, in float32 as upstream computes it.
            prob = np.zeros(x.shape[0], dtype=np.float32)
            if int(c) < proba.shape[1]:
                prob[:] = proba[:, int(c)]
            nc = 1 - prob

            cal = self.cal_scores[c]              # label-conditional: condition = x[1]
            n_cal = cal.size
            idx_left = np.searchsorted(cal, nc, "left")
            idx_right = np.searchsorted(cal, nc, "right")
            n_gt = n_cal - idx_right
            n_eq = idx_right - idx_left + 1

            p[:, i] = n_gt / (n_cal + 1)
            if smoothing:
                # Upstream draws one uniform per sample, in sample order; one
                # vectorised call consumes the same MT19937 stream in the same
                # order, so seeded runs stay bit-identical to the original.
                p[:, i] += (n_eq * rng.uniform(0, 1, x.shape[0])) / (n_cal + 1)
            else:
                p[:, i] += n_eq / (n_cal + 1)

        return p


class ConformalModel:
    '''The 50 aggregated ICPs behind one endpoint.'''

    __slots__ = ("name", "icps", "classes")

    def __init__(self, name, icps, classes):
        self.name = name
        self.icps = icps
        self.classes = classes

    def predict_pvalues(self, x, smoothing=False, rng=None):
        '''p-values per compound and class, median-aggregated over the ICPs.

        Parameters:
        ---------
        x (numpy array): the (n_compounds, 119) descriptor matrix.
        smoothing (bool): add the upstream smoothing term to each p-value.
        rng (numpy RandomState): source of the smoothing draws.

        Returns:
        ----------
        p_values (numpy array): shape (n_compounds, 2), columns [inactive, active].
        '''

        if smoothing and rng is None:
            rng = np.random
        # scikit-learn's tree code casts the input to float32 before walking the
        # nodes, so thresholds are effectively compared against float32-rounded
        # descriptors. Match that or borderline splits take the other branch.
        x = np.ascontiguousarray(x, dtype=np.float32)
        stacked = np.dstack([icp.p_values(x, smoothing, rng) for icp in self.icps])
        return np.median(stacked, axis=2)         # Consensus' agg_func


def load_model(endpoint: str) -> ConformalModel:
    '''Read one endpoint's pickle without importing scikit-learn or pandas.

    Parameters:
    ---------
    endpoint (str): the model's file name inside `model_files/`.

    Returns:
    ----------
    model (ConformalModel): ready to predict on a descriptor matrix.
    '''

    path = os.path.join(MODELS_DIR, endpoint)
    if not os.path.exists(path):
        raise PredictionError(
            "The trained model {} is missing. Unpack model_files.rar into {} "
            "(see skills/qsar_modelling/references/models.md for the download "
            "link and archive password).".format(endpoint, MODELS_DIR))

    with open(path, "rb") as fh:
        raw = _StubUnpickler(fh).load()

    icps = []
    for icp in raw.predictor.predictors:
        forest = icp.nc_function.model.model
        if type(forest).__name__ != "RandomForestClassifier":
            raise PredictionError("unexpected underlying model: {}".format(type(forest).__name__))
        if getattr(icp.nc_function, "normalizer", None) is not None:
            raise PredictionError("model uses a normalizer, which this runtime does not implement")
        if not icp.conditional:
            raise PredictionError("model is not label-conditional; check its condition lambda")
        icps.append(_Icp(_Forest([e.tree_ for e in forest.estimators_]),
                         icp.cal_scores, icp.classes))

    model = ConformalModel(raw.model_name, icps, raw.predictor.classes)
    del raw                                        # drop the embedded training frames
    return model


_CACHE = {}
_CACHE_ORDER = []


def _cached_model(endpoint: str) -> ConformalModel:
    '''`load_model` with a small LRU cache, so repeated calls skip the ~0.5 s read.'''
    if endpoint in _CACHE:
        _CACHE_ORDER.remove(endpoint)
        _CACHE_ORDER.append(endpoint)
        return _CACHE[endpoint]

    model = load_model(endpoint)
    if CACHE_SIZE > 0:
        _CACHE[endpoint] = model
        _CACHE_ORDER.append(endpoint)
        while len(_CACHE_ORDER) > CACHE_SIZE:
            del _CACHE[_CACHE_ORDER.pop(0)]
    return model


# --- Turning p-values into an answer -----------------------------------------


def _region(p_inactive, p_active, significance):
    '''One compound's conformal prediction region at the given significance.'''
    if np.isnan(p_inactive) or np.isnan(p_active):
        return ""
    inactive, active = p_inactive > significance, p_active > significance
    if inactive and active:
        return "both"
    if inactive:
        return "inactive"
    if active:
        return "active"
    return "empty"


def _single_row_to_dict(frame: pd.DataFrame) -> dict:
    '''A one-compound result reads better inline than as a path to a one-row CSV.'''
    if frame.empty:
        return {}
    row = {}
    for key, value in frame.iloc[0].to_dict().items():
        if pd.isna(value):
            row[key] = None
        elif hasattr(value, "item"):
            row[key] = value.item()
        else:
            row[key] = value
    return row


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
    root importable, which it is not when this package is run on its own, and
    losing that would break the one property the port was built for.

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


def predict_endpoint(endpoint: str, smiles_input: Union[str, List[str]], confidence: float,
                     output_name=None):
    '''Run one endpoint end to end. Shared by every model function in this package.

    A conformal classifier does not return a probability. At the given
    confidence it returns the set of labels it cannot rule out, so the answer is
    one of `active`, `inactive`, `both` (undecided at this confidence) or
    `empty` (the compound looks unlike anything in the calibration set). Raising
    the confidence widens the regions; it does not make the model surer.

    Parameters:
    ---------
    endpoint (str): the model file name inside `model_files/`.
    smiles_input (str or list): anything `parse_smiles_input` accepts.
    confidence (float): the conformal confidence level, set by the calling model function.
    output_name (str, optional): absolute path for the results CSV. Defaults to
        `<conversation results folder>/qsar_predictions/<endpoint>_results.csv`.

    Returns:
    ----------
    results (dict or str): a dict for a single compound, otherwise the path to the results CSV -- with a warning naming any structure RDKit could not parse.
    '''

    try:
        if not 0 < confidence < 1:
            raise PredictionError("confidence must be between 0 and 1, got {}".format(confidence))
        # 1 - 0.8 is 0.19999999999999996 in binary floating point, and these
        # p-values are rationals with small denominators (n_gt/(n_cal+1)), so an
        # exact tie at the threshold is common rather than a corner case. Without
        # the rounding, p == 0.2 counts as above 0.2 and the label is kept,
        # widening the region on ties.
        significance = round(1 - confidence, 12)

        smiles_list = parse_smiles_input(smiles_input)
        descriptors, bad_index, bad_smiles = descriptor_matrix(smiles_list)
        if descriptors.shape[0] == 0:
            raise PredictionError(
                "RDKit could not parse any of the {} supplied structure(s): {}".format(
                    len(smiles_list), ", ".join(bad_smiles[:5])))

        rng = np.random.RandomState(SEED) if SMOOTHING else None
        p = _cached_model(endpoint).predict_pvalues(descriptors, smoothing=SMOOTHING, rng=rng)

        # Put predictions back on the rows they came from, so an unparsable
        # structure leaves a gap instead of shifting the rest up by one.
        kept = [i for i in range(len(smiles_list)) if i not in set(bad_index)]
        full = np.full((len(smiles_list), 2), np.nan)
        full[kept] = p

        frame = pd.DataFrame({
            "smiles": smiles_list,
            "endpoint": endpoint,
            "confidence": confidence,
            "p_inactive": full[:, 0],
            "p_active": full[:, 1],
            "prediction": [_region(a, b, significance) for a, b in full],
        })
    except PredictionError as exc:
        return "Error: {}".format(exc)
    except Exception as exc:       # reported to the caller, not swallowed
        return "Error: {}: {}".format(type(exc).__name__, exc)

    if len(frame.index) == 1:
        return _single_row_to_dict(frame)

    path = _output_path(endpoint, output_name)
    frame.to_csv(path, index=False)
    if bad_smiles:
        listed = ", ".join(bad_smiles[:10]) + ("..." if len(bad_smiles) > 10 else "")
        return ("{}\n\n[warning] {} of {} structures could not be parsed by RDKit and have "
                "an empty prediction: {}".format(path, len(bad_smiles), len(smiles_list), listed))
    return path
