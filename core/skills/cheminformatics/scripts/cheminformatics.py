from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from rdkit import Chem, DataStructs
from rdkit.Chem import AllChem, Crippen, Descriptors, Draw, QED, rdMolDescriptors
from rdkit.Chem.MolStandardize import rdMolStandardize
from rdkit.Chem.SaltRemover import SaltRemover
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams
from rdkit.Chem.Scaffolds import MurckoScaffold


# ---------------------------------------------------------------------------
# Standardization
# ---------------------------------------------------------------------------

_SALT_REMOVER = SaltRemover()
_UNCHARGER = rdMolStandardize.Uncharger()
_TAUTOMER_ENUMERATOR = rdMolStandardize.TautomerEnumerator()
_NORMALIZER = rdMolStandardize.Normalizer()
_FRAGMENT_CHOOSER = rdMolStandardize.LargestFragmentChooser()


@dataclass
class StandardizedMolecule:
    """Result of the full standardization pipeline."""

    input_smiles: str
    canonical_smiles: Optional[str]
    inchi: Optional[str]
    inchikey: Optional[str]
    removed_salts: bool
    neutralized: bool
    tautomer_canonicalized: bool
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "input_smiles": self.input_smiles,
            "canonical_smiles": self.canonical_smiles,
            "inchi": self.inchi,
            "inchikey": self.inchikey,
            "removed_salts": self.removed_salts,
            "neutralized": self.neutralized,
            "tautomer_canonicalized": self.tautomer_canonicalized,
            "notes": list(self.notes),
        }


def parse_smiles(smiles: str) -> Optional[Chem.Mol]:
    """Parse a SMILES string into an RDKit Mol, or None if invalid."""
    if not smiles or not isinstance(smiles, str):
        return None
    mol = Chem.MolFromSmiles(smiles.strip())
    return mol


def standardize_smiles(
    smiles: str,
    *,
    strip_salts: bool = True,
    neutralize: bool = True,
    canonical_tautomer: bool = True,
) -> StandardizedMolecule:
    """Run the canonical pipeline: normalize → largest fragment → (salt strip) →
    (neutralize) → (canonical tautomer) → canonical SMILES + InChIKey.

    Every step is individually opt-out so the caller can inspect intermediate
    effects during read-across / grouping work where tautomer collapse might
    be undesirable.
    """

    notes: List[str] = []
    mol = parse_smiles(smiles)
    if mol is None:
        return StandardizedMolecule(
            input_smiles=smiles,
            canonical_smiles=None,
            inchi=None,
            inchikey=None,
            removed_salts=False,
            neutralized=False,
            tautomer_canonicalized=False,
            notes=["SMILES failed to parse"],
        )

    try:
        mol = _NORMALIZER.normalize(mol)
    except Exception as exc:  # pragma: no cover - defensive
        notes.append(f"normalize failed: {exc}")

    removed_salts = False
    if strip_salts:
        try:
            before = Chem.MolToSmiles(mol)
            mol = _SALT_REMOVER.StripMol(mol, dontRemoveEverything=True)
            mol = _FRAGMENT_CHOOSER.choose(mol)
            after = Chem.MolToSmiles(mol)
            removed_salts = before != after
        except Exception as exc:  # pragma: no cover
            notes.append(f"salt stripping failed: {exc}")

    neutralized = False
    if neutralize:
        try:
            before = Chem.MolToSmiles(mol)
            mol = _UNCHARGER.uncharge(mol)
            after = Chem.MolToSmiles(mol)
            neutralized = before != after
        except Exception as exc:  # pragma: no cover
            notes.append(f"neutralization failed: {exc}")

    tautomer_canonicalized = False
    if canonical_tautomer:
        try:
            before = Chem.MolToSmiles(mol)
            mol = _TAUTOMER_ENUMERATOR.Canonicalize(mol)
            after = Chem.MolToSmiles(mol)
            tautomer_canonicalized = before != after
        except Exception as exc:  # pragma: no cover
            notes.append(f"tautomer canonicalization failed: {exc}")

    canonical_smiles = Chem.MolToSmiles(mol)
    try:
        inchi = Chem.MolToInchi(mol)
        inchikey = Chem.MolToInchiKey(mol) if inchi else None
    except Exception as exc:  # pragma: no cover
        inchi = None
        inchikey = None
        notes.append(f"InChI generation failed: {exc}")

    return StandardizedMolecule(
        input_smiles=smiles,
        canonical_smiles=canonical_smiles,
        inchi=inchi,
        inchikey=inchikey,
        removed_salts=removed_salts,
        neutralized=neutralized,
        tautomer_canonicalized=tautomer_canonicalized,
        notes=notes,
    )


def murcko_scaffold_smiles(mol_or_smiles) -> Optional[str]:
    """Return the Murcko (Bemis–Murcko) scaffold SMILES."""
    mol = mol_or_smiles if isinstance(mol_or_smiles, Chem.Mol) else parse_smiles(mol_or_smiles)
    if mol is None:
        return None
    scaffold = MurckoScaffold.GetScaffoldForMol(mol)
    return Chem.MolToSmiles(scaffold) if scaffold is not None else None


# ---------------------------------------------------------------------------
# Descriptors
# ---------------------------------------------------------------------------

_CORE_DESCRIPTORS = {
    "molecular_formula": lambda m: rdMolDescriptors.CalcMolFormula(m),
    "mw": lambda m: Descriptors.MolWt(m),
    "exact_mass": lambda m: Descriptors.ExactMolWt(m),
    "heavy_atoms": lambda m: m.GetNumHeavyAtoms(),
    "logp_crippen": lambda m: Descriptors.MolLogP(m),
    "molar_refractivity": lambda m: Crippen.MolMR(m),
    "tpsa": lambda m: Descriptors.TPSA(m),
    "hbd": lambda m: rdMolDescriptors.CalcNumHBD(m),
    "hba": lambda m: rdMolDescriptors.CalcNumHBA(m),
    "rotatable_bonds": lambda m: rdMolDescriptors.CalcNumRotatableBonds(m),
    "aromatic_rings": lambda m: rdMolDescriptors.CalcNumAromaticRings(m),
    "rings": lambda m: rdMolDescriptors.CalcNumRings(m),
    "num_stereocenters": lambda m: rdMolDescriptors.CalcNumAtomStereoCenters(m),
    "formal_charge": lambda m: Chem.GetFormalCharge(m),
    "fraction_csp3": lambda m: rdMolDescriptors.CalcFractionCSP3(m),
    "qed_drug_likeness": lambda m: QED.qed(m),
}


def compute_descriptors(mol_or_smiles) -> dict:
    """Return a dict of common physchem/structural descriptors.

    Keys: molecular_formula, mw, exact_mass, heavy_atoms, logp_crippen,
    molar_refractivity, tpsa, hbd, hba, rotatable_bonds, aromatic_rings,
    rings, num_stereocenters, formal_charge, fraction_csp3, qed_drug_likeness.
    """
    mol = mol_or_smiles if isinstance(mol_or_smiles, Chem.Mol) else parse_smiles(mol_or_smiles)
    if mol is None:
        return {}
    return {name: fn(mol) for name, fn in _CORE_DESCRIPTORS.items()}


def lipinski_flags(descriptors: dict) -> dict:
    """Rule-of-Five violation flags (Lipinski 1997). For *drug-likeness* only —
    do not use as a hazard criterion."""
    return {
        "mw_over_500": descriptors.get("mw", 0) > 500,
        "logp_over_5": descriptors.get("logp_crippen", 0) > 5,
        "hbd_over_5": descriptors.get("hbd", 0) > 5,
        "hba_over_10": descriptors.get("hba", 0) > 10,
    }


# ---------------------------------------------------------------------------
# Structural alerts via RDKit FilterCatalog
# ---------------------------------------------------------------------------

_BUILTIN_CATALOGS = {
    "pains": FilterCatalogParams.FilterCatalogs.PAINS,
    "pains_a": FilterCatalogParams.FilterCatalogs.PAINS_A,
    "pains_b": FilterCatalogParams.FilterCatalogs.PAINS_B,
    "pains_c": FilterCatalogParams.FilterCatalogs.PAINS_C,
    "brenk": FilterCatalogParams.FilterCatalogs.BRENK,
    "nih": FilterCatalogParams.FilterCatalogs.NIH,
    "zinc": FilterCatalogParams.FilterCatalogs.ZINC,
    "chembl": FilterCatalogParams.FilterCatalogs.CHEMBL,
}


def build_filter_catalog(catalogs: Sequence[str] = ("pains", "brenk", "nih")) -> FilterCatalog:
    """Construct a FilterCatalog from one or more RDKit built-in filter sets.

    Known names: pains, pains_a, pains_b, pains_c, brenk, nih, zinc, chembl.
    """
    params = FilterCatalogParams()
    for name in catalogs:
        key = name.lower()
        if key not in _BUILTIN_CATALOGS:
            raise ValueError(f"Unknown filter catalog: {name}")
        params.AddCatalog(_BUILTIN_CATALOGS[key])
    return FilterCatalog(params)


def find_structural_alerts(
    mol_or_smiles,
    catalog: Optional[FilterCatalog] = None,
) -> List[dict]:
    """Return all matches from the given (or default) filter catalog.

    Each match is a dict with keys: `catalog`, `alert`, `description`, `smarts`.
    """
    mol = mol_or_smiles if isinstance(mol_or_smiles, Chem.Mol) else parse_smiles(mol_or_smiles)
    if mol is None:
        return []
    cat = catalog or build_filter_catalog()
    matches = cat.GetMatches(mol)
    out: List[dict] = []
    for match in matches:
        entry = match.GetFilterMatches(mol)
        smarts = entry[0].filterMatch.GetPattern() if entry else None
        out.append(
            {
                "catalog": match.GetProp("FilterSet") if match.HasProp("FilterSet") else None,
                "alert": match.GetDescription(),
                "description": match.GetDescription(),
                "smarts": Chem.MolToSmarts(smarts) if smarts is not None else None,
            }
        )
    return out


def match_custom_smarts(mol_or_smiles, smarts: str) -> bool:
    """Check whether a custom SMARTS pattern matches the molecule."""
    mol = mol_or_smiles if isinstance(mol_or_smiles, Chem.Mol) else parse_smiles(mol_or_smiles)
    pattern = Chem.MolFromSmarts(smarts) if smarts else None
    if mol is None or pattern is None:
        return False
    return mol.HasSubstructMatch(pattern)


# ---------------------------------------------------------------------------
# Fingerprints, similarity, read-across
# ---------------------------------------------------------------------------

def morgan_fingerprint(mol_or_smiles, *, radius: int = 2, n_bits: int = 2048):
    """Return a Morgan (ECFP-like) fingerprint as an ExplicitBitVect.

    radius=2 ≈ ECFP4, radius=3 ≈ ECFP6.
    """
    mol = mol_or_smiles if isinstance(mol_or_smiles, Chem.Mol) else parse_smiles(mol_or_smiles)
    if mol is None:
        return None
    return AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=n_bits)


def tanimoto(fp_a, fp_b) -> float:
    """Tanimoto similarity between two RDKit bit vectors."""
    if fp_a is None or fp_b is None:
        return 0.0
    return float(DataStructs.TanimotoSimilarity(fp_a, fp_b))


def nearest_neighbors(
    target_smiles: str,
    candidates: Iterable[str],
    *,
    k: int = 5,
    radius: int = 2,
    n_bits: int = 2048,
) -> List[Tuple[str, float]]:
    """Return the top-k most Tanimoto-similar candidates to the target.

    Useful for analogue selection in read-across (RAAF) and for training-set
    lookups in applicability-domain checks.
    """
    target_fp = morgan_fingerprint(target_smiles, radius=radius, n_bits=n_bits)
    if target_fp is None:
        return []
    scored: List[Tuple[str, float]] = []
    for cand in candidates:
        cand_fp = morgan_fingerprint(cand, radius=radius, n_bits=n_bits)
        if cand_fp is None:
            continue
        scored.append((cand, tanimoto(target_fp, cand_fp)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:k]


def maximum_common_substructure(smiles_list: Sequence[str]) -> Optional[str]:
    """Return the MCS SMARTS across a list of SMILES, or None if none found."""
    from rdkit.Chem import rdFMCS

    mols = [parse_smiles(s) for s in smiles_list]
    mols = [m for m in mols if m is not None]
    if len(mols) < 2:
        return None
    result = rdFMCS.FindMCS(mols, completeRingsOnly=True, ringMatchesRingOnly=True)
    return result.smartsString if result and result.numAtoms > 0 else None


# ---------------------------------------------------------------------------
# Applicability domain (for QSAR reliance, OECD Principle 3)
# ---------------------------------------------------------------------------

@dataclass
class AppDomainReport:
    target_smiles: str
    nearest_training_similarity: float
    mean_similarity_top_k: float
    inside_domain: bool
    threshold: float
    k: int

    def to_dict(self) -> dict:
        return {
            "target_smiles": self.target_smiles,
            "nearest_training_similarity": self.nearest_training_similarity,
            "mean_similarity_top_k": self.mean_similarity_top_k,
            "inside_domain": self.inside_domain,
            "threshold": self.threshold,
            "k": self.k,
        }


def applicability_domain_check(
    target_smiles: str,
    training_smiles: Sequence[str],
    *,
    k: int = 5,
    threshold: float = 0.3,
    radius: int = 2,
    n_bits: int = 2048,
) -> Optional[AppDomainReport]:
    """Structural-similarity applicability domain check.

    Returns `inside_domain=True` iff the mean Tanimoto to the top-k nearest
    training compounds is ≥ threshold. Threshold default 0.3 is conservative;
    calibrate per QSAR model using its published AD definition if available.

    This is a sufficient structural AD check but not a substitute for the
    model's own leverage / descriptor-space AD. Record both when available.
    """
    target_fp = morgan_fingerprint(target_smiles, radius=radius, n_bits=n_bits)
    if target_fp is None:
        return None

    sims: List[float] = []
    for train in training_smiles:
        fp = morgan_fingerprint(train, radius=radius, n_bits=n_bits)
        if fp is None:
            continue
        sims.append(tanimoto(target_fp, fp))

    if not sims:
        return AppDomainReport(
            target_smiles=target_smiles,
            nearest_training_similarity=0.0,
            mean_similarity_top_k=0.0,
            inside_domain=False,
            threshold=threshold,
            k=k,
        )

    sims.sort(reverse=True)
    top_k = sims[: max(1, k)]
    mean_top_k = sum(top_k) / len(top_k)
    return AppDomainReport(
        target_smiles=target_smiles,
        nearest_training_similarity=sims[0],
        mean_similarity_top_k=mean_top_k,
        inside_domain=mean_top_k >= threshold,
        threshold=threshold,
        k=k,
    )


# ---------------------------------------------------------------------------
# Visualization helper
# ---------------------------------------------------------------------------

def draw_molecules(
    smiles_list: Sequence[str],
    *,
    legends: Optional[Sequence[str]] = None,
    mols_per_row: int = 4,
    sub_img_size: Tuple[int, int] = (300, 300),
):
    """Return a PIL image grid for a list of SMILES. Save via the scoped
    `prepare_output_path(...)` helper exposed in `python_executor`."""
    mols = [parse_smiles(s) for s in smiles_list]
    mols = [m for m in mols if m is not None]
    if not mols:
        return None
    return Draw.MolsToGridImage(
        mols,
        molsPerRow=mols_per_row,
        subImgSize=sub_img_size,
        legends=list(legends) if legends else None,
    )


__all__ = [
    "AppDomainReport",
    "StandardizedMolecule",
    "applicability_domain_check",
    "build_filter_catalog",
    "compute_descriptors",
    "draw_molecules",
    "find_structural_alerts",
    "lipinski_flags",
    "match_custom_smarts",
    "maximum_common_substructure",
    "morgan_fingerprint",
    "murcko_scaffold_smiles",
    "nearest_neighbors",
    "parse_smiles",
    "standardize_smiles",
    "tanimoto",
]
