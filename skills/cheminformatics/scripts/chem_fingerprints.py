"""Fingerprints, similarity, neighbour search, clustering and diversity.

A similarity number only means something together with the fingerprint and the
metric that produced it, so every helper here takes both explicitly and the
defaults are stated once: Morgan, radius 2 (ECFP4-like), 2048 bits, Tanimoto —
the same settings the prebuilt structure index uses, so numbers are comparable.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from rdkit import Chem, DataStructs
from rdkit.Chem import MACCSkeys, rdFingerprintGenerator

from .chem_common import StructureError, mute_rdkit_log, to_mol

__all__ = [
    "FINGERPRINT_KINDS",
    "SIMILARITY_METRICS",
    "cluster_molecules",
    "explain_morgan_bits",
    "fingerprint",
    "maccs_fingerprint",
    "morgan_fingerprint",
    "nearest_neighbors",
    "pick_diverse",
    "similarity",
    "similarity_between",
    "similarity_matrix",
    "tanimoto",
]

FINGERPRINT_KINDS = ("morgan", "fcfp", "rdkit", "atom_pair", "topological_torsion", "maccs", "pattern")

SIMILARITY_METRICS = {
    "tanimoto": DataStructs.TanimotoSimilarity,
    "dice": DataStructs.DiceSimilarity,
    "cosine": DataStructs.CosineSimilarity,
    "sokal": DataStructs.SokalSimilarity,
    "russel": DataStructs.RusselSimilarity,
    "kulczynski": DataStructs.KulczynskiSimilarity,
    "mcconnaughey": DataStructs.McConnaugheySimilarity,
    "braun_blanquet": DataStructs.BraunBlanquetSimilarity,
}

_BULK_METRICS = {
    "tanimoto": DataStructs.BulkTanimotoSimilarity,
    "dice": DataStructs.BulkDiceSimilarity,
    "cosine": DataStructs.BulkCosineSimilarity,
    "sokal": DataStructs.BulkSokalSimilarity,
    "russel": DataStructs.BulkRusselSimilarity,
    "kulczynski": DataStructs.BulkKulczynskiSimilarity,
    "mcconnaughey": DataStructs.BulkMcConnaugheySimilarity,
    "braun_blanquet": DataStructs.BulkBraunBlanquetSimilarity,
}


def _generator(kind: str, radius: int, n_bits: int, use_chirality: bool):
    if kind == "morgan":
        return rdFingerprintGenerator.GetMorganGenerator(
            radius=radius, fpSize=n_bits, includeChirality=use_chirality
        )
    if kind == "fcfp":
        return rdFingerprintGenerator.GetMorganGenerator(
            radius=radius,
            fpSize=n_bits,
            includeChirality=use_chirality,
            atomInvariantsGenerator=rdFingerprintGenerator.GetMorganFeatureAtomInvGen(),
        )
    if kind == "rdkit":
        return rdFingerprintGenerator.GetRDKitFPGenerator(fpSize=n_bits)
    if kind == "atom_pair":
        return rdFingerprintGenerator.GetAtomPairGenerator(
            fpSize=n_bits, includeChirality=use_chirality
        )
    if kind == "topological_torsion":
        return rdFingerprintGenerator.GetTopologicalTorsionGenerator(
            fpSize=n_bits, includeChirality=use_chirality
        )
    raise ValueError(f"unknown fingerprint kind {kind!r}. Known: {FINGERPRINT_KINDS}")


def fingerprint(
    mol_or_smiles: Any,
    kind: str = "morgan",
    *,
    radius: int = 2,
    n_bits: int = 2048,
    use_chirality: bool = False,
    counts: bool = False,
):
    """Build a fingerprint.

    `kind`:

    - `morgan` — circular, radius 2 ≈ ECFP4, radius 3 ≈ ECFP6. The default.
    - `fcfp` — Morgan on pharmacophoric atom types (donor/acceptor/aromatic…),
      so bioisosteres score closer than they do with plain ECFP.
    - `rdkit` — path-based (Daylight-like); sensitive to linker topology.
    - `atom_pair`, `topological_torsion` — distance-encoded; better for large
      flexible molecules than circular fingerprints.
    - `maccs` — 166 curated structural keys; coarse, interpretable, and *not*
      comparable to ECFP values.
    - `pattern` — substructure screening only, never for similarity.

    `counts=True` returns a count vector (occurrences per feature) instead of a
    bit vector; count and bit fingerprints are not interchangeable in a
    similarity calculation.
    """
    mol = to_mol(mol_or_smiles)
    if kind == "maccs":
        if counts:
            raise ValueError("MACCS keys are bit-only; use counts=False")
        return MACCSkeys.GenMACCSKeys(mol)
    if kind == "pattern":
        if counts:
            raise ValueError("the pattern fingerprint is bit-only; use counts=False")
        return Chem.PatternFingerprint(mol, fpSize=n_bits)
    gen = _generator(kind, radius, n_bits, use_chirality)
    return gen.GetCountFingerprint(mol) if counts else gen.GetFingerprint(mol)


def morgan_fingerprint(
    mol_or_smiles: Any,
    *,
    radius: int = 2,
    n_bits: int = 2048,
    use_features: bool = False,
    use_chirality: bool = False,
):
    """Morgan (ECFP-like) bit vector. radius=2 ≈ ECFP4, radius=3 ≈ ECFP6."""
    return fingerprint(
        mol_or_smiles,
        "fcfp" if use_features else "morgan",
        radius=radius,
        n_bits=n_bits,
        use_chirality=use_chirality,
    )


def maccs_fingerprint(mol_or_smiles: Any):
    """166-bit MACCS structural keys."""
    return fingerprint(mol_or_smiles, "maccs")


def similarity(fp_a, fp_b, metric: str = "tanimoto", *, alpha: float = 0.5, beta: float = 0.5) -> float:
    """Similarity between two fingerprints of the *same* kind and size.

    `metric="tversky"` is asymmetric: `alpha` weights features unique to the
    first fingerprint, `beta` those unique to the second — use it for
    substructure-style "is A contained in B" questions.
    """
    if fp_a is None or fp_b is None:
        raise ValueError("similarity() needs two fingerprints; got None (check the inputs parsed)")
    if metric == "tversky":
        return float(DataStructs.TverskySimilarity(fp_a, fp_b, alpha, beta))
    if metric not in SIMILARITY_METRICS:
        raise ValueError(f"unknown metric {metric!r}. Known: {sorted(SIMILARITY_METRICS)} + 'tversky'")
    return float(SIMILARITY_METRICS[metric](fp_a, fp_b))


def tanimoto(fp_a, fp_b) -> float:
    """Tanimoto similarity between two fingerprints."""
    return similarity(fp_a, fp_b, "tanimoto")


def similarity_between(
    a: Any,
    b: Any,
    *,
    kind: str = "morgan",
    metric: str = "tanimoto",
    radius: int = 2,
    n_bits: int = 2048,
    use_chirality: bool = False,
) -> float:
    """Similarity between two structures, fingerprinting both the same way.

    Report the number together with the fingerprint and metric used — 0.38 on
    ECFP4 and 0.38 on MACCS are different statements about the same pair.
    """
    fp_a = fingerprint(a, kind, radius=radius, n_bits=n_bits, use_chirality=use_chirality)
    fp_b = fingerprint(b, kind, radius=radius, n_bits=n_bits, use_chirality=use_chirality)
    return similarity(fp_a, fp_b, metric)


def _bulk(target_fp, fps: Sequence, metric: str) -> List[float]:
    if metric in _BULK_METRICS and fps:
        return [float(value) for value in _BULK_METRICS[metric](target_fp, list(fps))]
    return [similarity(target_fp, fp, metric) for fp in fps]


def nearest_neighbors(
    target_smiles: Any,
    candidates: Iterable[Any],
    *,
    k: int = 5,
    radius: int = 2,
    n_bits: int = 2048,
    kind: str = "morgan",
    metric: str = "tanimoto",
    min_similarity: Optional[float] = None,
    skip_invalid: bool = True,
) -> List[Tuple[str, float]]:
    """Rank `candidates` by similarity to the target, most similar first.

    Returns `[(candidate_smiles, similarity), ...]` truncated to `k`. An
    unparseable candidate is skipped (`skip_invalid=False` raises instead), so
    check the returned length against the pool size when completeness matters.
    The target is fingerprinted with the same settings as the candidates.
    """
    target_fp = fingerprint(target_smiles, kind, radius=radius, n_bits=n_bits)
    labels: List[str] = []
    fps = []
    for index, candidate in enumerate(candidates):
        try:
            fps.append(fingerprint(candidate, kind, radius=radius, n_bits=n_bits))
        except StructureError:
            if not skip_invalid:
                raise
            continue
        labels.append(candidate if isinstance(candidate, str) else Chem.MolToSmiles(candidate))

    scored = list(zip(labels, _bulk(target_fp, fps, metric)))
    if min_similarity is not None:
        scored = [item for item in scored if item[1] >= min_similarity]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[: max(1, k)]


def similarity_matrix(
    smiles_list: Sequence[Any],
    *,
    kind: str = "morgan",
    metric: str = "tanimoto",
    radius: int = 2,
    n_bits: int = 2048,
):
    """Square all-against-all similarity matrix as a numpy array.

    Rows and columns follow the input order; the diagonal is 1.0. Feed it to a
    heatmap or a clustering routine.
    """
    import numpy as np

    fps = [fingerprint(item, kind, radius=radius, n_bits=n_bits) for item in smiles_list]
    size = len(fps)
    matrix = np.eye(size, dtype=float)
    for i in range(1, size):
        sims = _bulk(fps[i], fps[:i], metric)
        for j, value in enumerate(sims):
            matrix[i, j] = matrix[j, i] = value
    return matrix


def cluster_molecules(
    smiles_list: Sequence[Any],
    *,
    cutoff: float = 0.35,
    kind: str = "morgan",
    metric: str = "tanimoto",
    radius: int = 2,
    n_bits: int = 2048,
) -> List[List[int]]:
    """Butina cluster the set; returns index lists, each starting with its centroid.

    `cutoff` is a *distance* (1 − similarity): 0.35 groups compounds more
    similar than Tanimoto 0.65. Clusters are returned largest first.
    """
    from rdkit.ML.Cluster import Butina

    fps = [fingerprint(item, kind, radius=radius, n_bits=n_bits) for item in smiles_list]
    size = len(fps)
    if size < 2:
        return [[0]] if size else []
    distances: List[float] = []
    for i in range(1, size):
        distances.extend(1.0 - value for value in _bulk(fps[i], fps[:i], metric))
    clusters = Butina.ClusterData(distances, size, cutoff, isDistData=True)
    return sorted((list(cluster) for cluster in clusters), key=len, reverse=True)


def pick_diverse(
    smiles_list: Sequence[Any],
    n: int,
    *,
    kind: str = "morgan",
    radius: int = 2,
    n_bits: int = 2048,
    seed: int = 42,
) -> List[int]:
    """MaxMin-pick `n` maximally dissimilar structures; returns their indices.

    Deterministic for a given `seed`. Use it to spread a shortlist across the
    structural space of a set instead of taking the top of a similarity rank.
    """
    from rdkit.SimDivFilters.rdSimDivPickers import MaxMinPicker

    fps = [fingerprint(item, kind, radius=radius, n_bits=n_bits) for item in smiles_list]
    if n >= len(fps):
        return list(range(len(fps)))
    picker = MaxMinPicker()
    return sorted(picker.LazyBitVectorPick(fps, len(fps), n, seed=seed))


def explain_morgan_bits(
    mol_or_smiles: Any,
    *,
    bits: Optional[Sequence[int]] = None,
    radius: int = 2,
    n_bits: int = 2048,
) -> Dict[int, List[Dict[str, Any]]]:
    """Map set Morgan bits back to the atom environments that set them.

    `{bit: [{"center_atom", "radius", "smiles"}, ...]}` — the substructure each
    bit stands for. Use it to say *why* two structures score similar instead of
    reporting the number alone.
    """
    mol = to_mol(mol_or_smiles)
    gen = _generator("morgan", radius, n_bits, False)
    output = rdFingerprintGenerator.AdditionalOutput()
    output.AllocateBitInfoMap()
    gen.GetFingerprint(mol, additionalOutput=output)
    info = output.GetBitInfoMap() or {}

    wanted = set(bits) if bits is not None else set(info)
    explained: Dict[int, List[Dict[str, Any]]] = {}
    for bit, environments in info.items():
        if bit not in wanted:
            continue
        entries = []
        for center, env_radius in environments:
            if env_radius == 0:
                fragment = mol.GetAtomWithIdx(center).GetSymbol()
            else:
                env = Chem.FindAtomEnvironmentOfRadiusN(mol, env_radius, center)
                atoms = {mol.GetBondWithIdx(b).GetBeginAtomIdx() for b in env}
                atoms |= {mol.GetBondWithIdx(b).GetEndAtomIdx() for b in env}
                atoms.add(center)
                with mute_rdkit_log():
                    fragment = Chem.MolFragmentToSmiles(mol, atomsToUse=sorted(atoms), bondsToUse=list(env))
            entries.append({"center_atom": center, "radius": env_radius, "smiles": fragment})
        explained[bit] = entries
    return explained
