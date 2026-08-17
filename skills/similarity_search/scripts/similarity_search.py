from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import rdFingerprintGenerator


DB_DIR = Path(__file__).resolve().parent
FP_MATRIX_PATH = DB_DIR / "db_matrix.npy"
FP_META_PATH = DB_DIR / "db_meta.parquet"

fp_generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)

# (db, db_pop, meta), loaded once per session
_DATABASE = None


def search(query_smiles, db, db_pop, meta, k=10, chunk=256, output_name=None):
    """Tanimoto search against the fingerprint matrix.

    db          : (N, 2048) uint16 matrix
    db_pop      : (N,) bit counts of db
    output_name : absolute path from `prepare_output_path(...)` — see similarity_search
    writes      : long-format CSV, top-k hits per query
    returns     : a message naming the file, plus any SMILES that could not be parsed
    """

    # The scope has to be handed in: contextvars set by the caller do not reach
    # the interpreter's worker thread, so resolving it here would silently write
    # to anonymous-user/default-thread instead of this conversation.
    output_path = Path(output_name) if output_name else None
    if output_path is None or not output_path.is_absolute():
        raise ValueError(
            f"output_name must be an absolute path, got {output_name!r}. Call "
            'similarity_search(..., output_name=prepare_output_path("similarity_hits.csv")).'
        )

    # validate SMILES
    valid_smiles = []
    invalid_smiles = []
    fps = []
    for s in query_smiles:
        mol = Chem.MolFromSmiles(s) if isinstance(s, str) else None
        if mol is None:
            invalid_smiles.append(s)
            continue
        valid_smiles.append(s)
        fps.append(fp_generator.GetFingerprintAsNumPy(mol))

    if not fps:
        raise ValueError("No valid SMILES found in query_smiles.")

    q = np.asarray(fps, dtype=np.uint16)
    q_pop = q.sum(axis=1, dtype=np.uint16)

    k = max(1, min(k, db.shape[0]))
    rows = []
    for s in range(0, q.shape[0], chunk):
        inter = (q[s:s + chunk] @ db.T).astype(np.float32)          # shared bits
        union = q_pop[s:s + chunk, None] + db_pop[None, :] - inter  # total bits
        sim = inter / np.maximum(union, 1)
        top = np.argpartition(-sim, k - 1, axis=1)[:, :k]           # unsorted top-k
        for r in range(sim.shape[0]):
            idx = top[r][np.argsort(-sim[r, top[r]])]               # sort those k
            hits = meta.iloc[idx].reset_index(drop=True)
            hits.insert(0, "similarity", sim[r, idx])
            hits.insert(0, "rank", np.arange(1, k + 1))
            hits.insert(0, "query_smiles", valid_smiles[s + r])     # aligned with q
            rows.append(hits)

    results_df = pd.concat(rows, ignore_index=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results_df.to_csv(output_path, index=False)

    message = f"The results is available at {output_path}."
    if invalid_smiles:
        message += f" Skipping {invalid_smiles} due to invalid."
    return message


def similarity_search(query_smiles: list, k=10, output_name=None):
    """Search a list of SMILES against the ECHA registered-substance database.

    Writes the top-k hits per query to `output_name`, which must be an absolute
    path from the scoped `prepare_output_path(...)` helper, and returns a message
    naming that file — the hit table is in the CSV, so read it back with pandas
    rather than expecting rows here. No similarity cut-off and no self-hit
    filter: take a generous `k` and narrow the CSV.

        similarity_search(["CCO"], k=20, output_name=prepare_output_path("hits.csv"))
    """
    global _DATABASE

    if isinstance(query_smiles, str):
        query_smiles = [query_smiles]

    if _DATABASE is None:
        fp_matrix = np.load(FP_MATRIX_PATH)
        meta = pd.read_parquet(FP_META_PATH)
        if fp_matrix.shape[0] != len(meta):
            raise ValueError(
                "Similarity database is inconsistent: "
                f"{fp_matrix.shape[0]} fingerprints vs {len(meta)} metadata rows."
            )
        db = fp_matrix.astype(np.uint16)   # exact integer matmul, no overflow (max 2048)
        db_pop = db.sum(axis=1, dtype=np.uint16)
        _DATABASE = (db, db_pop, meta)

    db, db_pop, meta = _DATABASE
    return search(query_smiles, db, db_pop, meta, k=k, output_name=output_name)


__all__ = [
    "search",
    "similarity_search",
]
