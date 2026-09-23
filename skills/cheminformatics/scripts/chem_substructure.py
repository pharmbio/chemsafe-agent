"""Substructure matching, scaffolds, maximum common substructure, filter catalogs.

All of it is graph matching: a query pattern (SMARTS) or a shared subgraph is
located in a molecule, and what comes back is where it matched — never an
interpretation of what the match means.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from rdkit import Chem
from rdkit.Chem import rdFMCS
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams
from rdkit.Chem.Scaffolds import MurckoScaffold

from .chem_common import StructureError, mute_rdkit_log, to_mol

__all__ = [
    "MCSResult",
    "build_filter_catalog",
    "count_substructure_matches",
    "filter_by_substructure",
    "find_structural_alerts",
    "find_substructure_matches",
    "generic_scaffold_smiles",
    "group_by_scaffold",
    "has_substructure",
    "list_filter_catalogs",
    "match_custom_smarts",
    "maximum_common_substructure",
    "murcko_scaffold_smiles",
    "ring_systems",
    "screen_alerts",
]


def _query(pattern: Any) -> Chem.Mol:
    """Accept a SMARTS string or an already-compiled query molecule."""
    if isinstance(pattern, Chem.Mol):
        return pattern
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"substructure query must be a SMARTS string or Mol, got {pattern!r}")
    with mute_rdkit_log():
        query = Chem.MolFromSmarts(pattern.strip())
    if query is None:
        raise ValueError(f"SMARTS did not compile: {pattern!r}")
    return query


# Substructure matching


def has_substructure(mol_or_smiles: Any, pattern: Any, *, use_chirality: bool = False) -> bool:
    """Whether the SMARTS pattern matches anywhere in the molecule."""
    return to_mol(mol_or_smiles).HasSubstructMatch(_query(pattern), useChirality=use_chirality)


def match_custom_smarts(mol_or_smiles: Any, smarts: str) -> bool:
    """Whether a custom SMARTS pattern matches the molecule."""
    return has_substructure(mol_or_smiles, smarts)


def find_substructure_matches(
    mol_or_smiles: Any,
    pattern: Any,
    *,
    uniquify: bool = True,
    use_chirality: bool = False,
    max_matches: int = 1000,
) -> List[Tuple[int, ...]]:
    """Every match of the pattern, as tuples of atom indices.

    `uniquify=False` keeps symmetry-equivalent mappings (a benzene ring matches
    a ring query 12 ways) — leave it on unless you need each mapping.
    """
    return [
        tuple(match)
        for match in to_mol(mol_or_smiles).GetSubstructMatches(
            _query(pattern), uniquify=uniquify, useChirality=use_chirality, maxMatches=max_matches
        )
    ]


def count_substructure_matches(mol_or_smiles: Any, pattern: Any, **kwargs) -> int:
    """How many times the pattern occurs (distinct matches)."""
    return len(find_substructure_matches(mol_or_smiles, pattern, **kwargs))


def filter_by_substructure(
    items: Iterable[Any],
    pattern: Any,
    *,
    exclude: bool = False,
    skip_invalid: bool = True,
) -> List[str]:
    """Keep (or with `exclude=True`, drop) the structures matching the pattern."""
    query = _query(pattern)
    kept: List[str] = []
    for item in items:
        try:
            mol = to_mol(item)
        except StructureError:
            if not skip_invalid:
                raise
            continue
        if mol.HasSubstructMatch(query) != exclude:
            kept.append(item if isinstance(item, str) else Chem.MolToSmiles(mol))
    return kept


# Scaffolds


def murcko_scaffold_smiles(mol_or_smiles: Any, *, generic: bool = False) -> str:
    """Bemis–Murcko scaffold: ring systems plus the linkers joining them.

    Returns `""` for an acyclic molecule, which has no Murcko scaffold.
    `generic=True` additionally erases atom types and bond orders, leaving the
    carbon skeleton — the coarser grouping key.
    """
    mol = to_mol(mol_or_smiles)
    scaffold = MurckoScaffold.GetScaffoldForMol(mol)
    if scaffold is None:
        return ""
    if generic:
        with mute_rdkit_log():
            scaffold = MurckoScaffold.MakeScaffoldGeneric(scaffold)
    return Chem.MolToSmiles(scaffold)


def generic_scaffold_smiles(mol_or_smiles: Any) -> str:
    """Murcko scaffold with atom types and bond orders erased."""
    return murcko_scaffold_smiles(mol_or_smiles, generic=True)


def group_by_scaffold(items: Iterable[Any], *, generic: bool = False) -> Dict[str, List[str]]:
    """Group structures by Murcko scaffold: `{scaffold_smiles: [smiles, ...]}`.

    Acyclic structures collect under the `""` key.
    """
    groups: Dict[str, List[str]] = {}
    for item in items:
        mol = to_mol(item)
        key = murcko_scaffold_smiles(mol, generic=generic)
        groups.setdefault(key, []).append(item if isinstance(item, str) else Chem.MolToSmiles(mol))
    return groups


def ring_systems(mol_or_smiles: Any, *, include_spiro: bool = True) -> List[str]:
    """The molecule's ring systems as SMILES, fused rings merged into one.

    `include_spiro=False` splits systems joined at a single spiro atom.
    """
    mol = to_mol(mol_or_smiles)
    rings = [set(ring) for ring in mol.GetRingInfo().AtomRings()]
    merged: List[set] = []
    for ring in rings:
        overlap_min = 1 if include_spiro else 2
        joined = [existing for existing in merged if len(existing & ring) >= overlap_min]
        for existing in joined:
            merged.remove(existing)
            ring = ring | existing
        merged.append(ring)

    out = []
    for system in merged:
        bonds = [
            bond.GetIdx()
            for bond in mol.GetBonds()
            if bond.GetBeginAtomIdx() in system and bond.GetEndAtomIdx() in system
        ]
        with mute_rdkit_log():
            out.append(Chem.MolFragmentToSmiles(mol, atomsToUse=sorted(system), bondsToUse=bonds))
    return sorted(set(out))


# Maximum common substructure

_ATOM_COMPARE = {
    "elements": rdFMCS.AtomCompare.CompareElements,
    "any": rdFMCS.AtomCompare.CompareAny,
    "any_heavy": rdFMCS.AtomCompare.CompareAnyHeavyAtom,
    "isotopes": rdFMCS.AtomCompare.CompareIsotopes,
}

_BOND_COMPARE = {
    "order": rdFMCS.BondCompare.CompareOrder,
    "order_exact": rdFMCS.BondCompare.CompareOrderExact,
    "any": rdFMCS.BondCompare.CompareAny,
}


@dataclass
class MCSResult:
    """Maximum common substructure across a set of molecules."""

    smarts: Optional[str]
    num_atoms: int
    num_bonds: int
    timed_out: bool
    num_molecules: int
    threshold: float

    @property
    def found(self) -> bool:
        return bool(self.smarts) and self.num_atoms > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "smarts": self.smarts,
            "num_atoms": self.num_atoms,
            "num_bonds": self.num_bonds,
            "timed_out": self.timed_out,
            "num_molecules": self.num_molecules,
            "threshold": self.threshold,
        }


def maximum_common_substructure(
    smiles_list: Sequence[Any],
    *,
    complete_rings_only: bool = True,
    ring_matches_ring_only: bool = True,
    atom_compare: str = "elements",
    bond_compare: str = "order",
    match_valences: bool = False,
    threshold: float = 1.0,
    timeout: int = 30,
) -> MCSResult:
    """Largest substructure common to every molecule in the list.

    Defaults keep rings intact (`complete_rings_only`) and refuse to match a
    ring atom to a chain atom (`ring_matches_ring_only`), which is what makes
    the result readable as a chemical class rather than a fragment.

    `threshold=0.8` relaxes "every molecule" to "at least 80% of them", useful
    when one outlier would otherwise collapse the MCS.

    **Check `timed_out`.** MCS is NP-hard; on timeout RDKit returns the best
    substructure found so far, which is not the maximum common substructure.
    """
    mols = [to_mol(item, label=f"smiles_list[{i}]") for i, item in enumerate(smiles_list)]
    if len(mols) < 2:
        raise ValueError("maximum_common_substructure needs at least two molecules")
    if atom_compare not in _ATOM_COMPARE:
        raise ValueError(f"atom_compare must be one of {sorted(_ATOM_COMPARE)}")
    if bond_compare not in _BOND_COMPARE:
        raise ValueError(f"bond_compare must be one of {sorted(_BOND_COMPARE)}")

    with mute_rdkit_log():
        result = rdFMCS.FindMCS(
            mols,
            completeRingsOnly=complete_rings_only,
            ringMatchesRingOnly=ring_matches_ring_only,
            atomCompare=_ATOM_COMPARE[atom_compare],
            bondCompare=_BOND_COMPARE[bond_compare],
            matchValences=match_valences,
            threshold=threshold,
            timeout=timeout,
        )
    return MCSResult(
        smarts=result.smartsString or None,
        num_atoms=result.numAtoms,
        num_bonds=result.numBonds,
        timed_out=bool(result.canceled),
        num_molecules=len(mols),
        threshold=threshold,
    )


# Filter catalogs

_BUILTIN_CATALOGS = {
    "pains": FilterCatalogParams.FilterCatalogs.PAINS,
    "pains_a": FilterCatalogParams.FilterCatalogs.PAINS_A,
    "pains_b": FilterCatalogParams.FilterCatalogs.PAINS_B,
    "pains_c": FilterCatalogParams.FilterCatalogs.PAINS_C,
    "brenk": FilterCatalogParams.FilterCatalogs.BRENK,
    "nih": FilterCatalogParams.FilterCatalogs.NIH,
    "zinc": FilterCatalogParams.FilterCatalogs.ZINC,
    "chembl": FilterCatalogParams.FilterCatalogs.CHEMBL,
    "chembl_bms": FilterCatalogParams.FilterCatalogs.CHEMBL_BMS,
    "chembl_dundee": FilterCatalogParams.FilterCatalogs.CHEMBL_Dundee,
    "chembl_glaxo": FilterCatalogParams.FilterCatalogs.CHEMBL_Glaxo,
    "chembl_inpharmatica": FilterCatalogParams.FilterCatalogs.CHEMBL_Inpharmatica,
    "chembl_lint": FilterCatalogParams.FilterCatalogs.CHEMBL_LINT,
    "chembl_mlsmr": FilterCatalogParams.FilterCatalogs.CHEMBL_MLSMR,
    "chembl_surechembl": FilterCatalogParams.FilterCatalogs.CHEMBL_SureChEMBL,
    "all": FilterCatalogParams.FilterCatalogs.ALL,
}


def list_filter_catalogs() -> List[str]:
    """Names accepted by `build_filter_catalog`."""
    return sorted(_BUILTIN_CATALOGS)


def build_filter_catalog(catalogs: Sequence[str] = ("pains", "brenk", "nih")) -> FilterCatalog:
    """Build a catalog from one or more of RDKit's built-in filter sets.

    Build it once and pass it to every call — constructing a catalog parses
    hundreds of SMARTS patterns.
    """
    params = FilterCatalogParams()
    for name in catalogs:
        key = str(name).lower()
        if key not in _BUILTIN_CATALOGS:
            raise ValueError(f"unknown filter catalog {name!r}. Known: {list_filter_catalogs()}")
        params.AddCatalog(_BUILTIN_CATALOGS[key])
    return FilterCatalog(params)


def find_structural_alerts(
    mol_or_smiles: Any,
    catalog: Optional[FilterCatalog] = None,
    *,
    catalogs: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Match a structure against a filter catalog.

    One dict per hit: `catalog` (which filter set), `alert` (the entry's name),
    `scope` (what that set screens for), `reference` (the catalog's own
    literature citation — record it with the hit), `smarts` (the pattern that
    matched), `num_matches` (how many times it occurs) and `atom_indices`
    (every atom it covers, for highlighting).
    """
    mol = to_mol(mol_or_smiles)
    if catalog is None:
        catalog = build_filter_catalog(catalogs or ("pains", "brenk", "nih"))

    hits: List[Dict[str, Any]] = []
    with mute_rdkit_log():
        for entry in catalog.GetMatches(mol):
            props = set(entry.GetPropList())
            matches = entry.GetFilterMatches(mol)
            pattern = matches[0].filterMatch.GetPattern() if matches else None

            # The catalog reports one match per entry; re-run the pattern to get
            # every occurrence, so a count and a highlight are not truncated.
            occurrences: List[Tuple[int, ...]] = []
            if pattern is not None:
                try:
                    occurrences = [tuple(match) for match in mol.GetSubstructMatches(pattern)]
                except Exception:
                    occurrences = []
            if not occurrences:
                occurrences = [tuple(pair.target for pair in match.atomPairs) for match in matches]

            hits.append(
                {
                    "catalog": entry.GetProp("FilterSet") if "FilterSet" in props else None,
                    "alert": entry.GetDescription(),
                    "scope": entry.GetProp("Scope") if "Scope" in props else None,
                    "reference": entry.GetProp("Reference") if "Reference" in props else None,
                    "smarts": Chem.MolToSmarts(pattern) if pattern is not None else None,
                    "num_matches": len(occurrences),
                    "atom_indices": sorted({idx for match in occurrences for idx in match}),
                }
            )
    return hits


def screen_alerts(
    items: Any,
    *,
    catalog: Optional[FilterCatalog] = None,
    catalogs: Sequence[str] = ("pains", "brenk", "nih"),
) -> List[Dict[str, Any]]:
    """Run the alert screen over many structures, one row per input.

    `items` is a list of SMILES or a `{name: smiles}` mapping. Each row:
    `name`, `input_smiles`, `num_alerts`, `alerts` (the names), `hits` (the
    full dicts) and `error`. Failures stay in the table.
    """
    catalog = catalog or build_filter_catalog(catalogs)
    pairs = list(items.items()) if isinstance(items, dict) else [(None, item) for item in items]

    rows: List[Dict[str, Any]] = []
    for name, item in pairs:
        row: Dict[str, Any] = {
            "name": name,
            "input_smiles": item if isinstance(item, str) else None,
            "num_alerts": None,
            "alerts": [],
            "hits": [],
            "error": None,
        }
        try:
            hits = find_structural_alerts(item, catalog)
            row["hits"] = hits
            row["alerts"] = sorted({hit["alert"] for hit in hits})
            row["num_alerts"] = len(hits)
        except (StructureError, ValueError) as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows
