'''Check utils.py against p-values produced by the original 2021 stack.

persistence/models/ths_models/reference/ holds the descriptor matrix and the
per-model p-values computed by the Python 3.5-era pickles running under their
real dependencies (scikit-learn
0.21.3, pandas 1.1.5, nonconformist, cloudpickle 1.6, executing the real pickled
lambdas), with smoothing disabled so the numbers are deterministic. This script
recomputes them with the native runtime and requires an exact match.

    python validate_native.py
'''

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import MODELS_DIR, REFERENCE_DIR, load_model  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REFERENCE = REFERENCE_DIR          # moved out of the skill tree with the models


def main():
    for label, directory in (("models", MODELS_DIR), ("reference data", REFERENCE)):
        if not os.path.isdir(directory):
            sys.exit("No {} found at {}. See references/models.md for the download link.".format(
                label, directory))

    x = np.load(os.path.join(REFERENCE, "descriptors.npy"))
    failures = []

    for endpoint in sorted(os.listdir(MODELS_DIR)):
        reference = os.path.join(REFERENCE, endpoint + ".npy")
        if not os.path.exists(reference):
            print("{:24s} no reference, skipped".format(endpoint))
            continue

        expected = np.load(reference)
        model = load_model(endpoint)
        produced = model.predict_pvalues(x, smoothing=False)
        del model

        if np.array_equal(produced, expected):
            print("{:24s} ok".format(endpoint))
        else:
            failures.append(endpoint)
            print("{:24s} MISMATCH  max|diff| {:.3e}".format(
                endpoint, np.abs(produced - expected).max()))

    _descriptor_drift(x)

    if failures:
        sys.exit("\n{} model(s) did not match: {}".format(len(failures), ", ".join(failures)))
    print("\nall models match the original stack exactly")


def _descriptor_drift(reference_x):
    '''Warn if the installed RDKit no longer reproduces the reference descriptors.'''
    import pandas as pd
    from rdkit import rdBase

    from utils import descriptor_matrix

    example = os.path.join(HERE, "example_dataset.csv")
    if not os.path.exists(example):
        return
    current, _, _ = descriptor_matrix(list(pd.read_csv(example)["SMILES"]))
    diff = np.abs(current - reference_x)
    material = diff > 1e-9        # anything smaller is lost in the float32 cast
    print("\nRDKit {}: {} descriptor cells differ materially from the reference "
          "(max {:.4g})".format(rdBase.rdkitVersion, int(material.sum()), diff.max()))


if __name__ == "__main__":
    main()
