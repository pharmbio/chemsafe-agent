"""Molecular descriptors computed from the 2D structure.

The core set is curated: the descriptors that carry chemical meaning and get
reported, rather than everything RDKit can produce. `compute_all_descriptors`
is there when a model or a screen needs the full block.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, Fragments, QED, rdMolDescriptors

from .chem_common import StructureError, mute_rdkit_log, to_mol

__all__ = [
    "compute_all_descriptors",
    "compute_descriptors",
    "describe_batch",
    "druglikeness_profile",
    "element_counts",
    "functional_groups",
    "lipinski_flags",
]


def _num_stereocenters(mol: Chem.Mol, unspecified: bool = False) -> int:
    """Atom stereocentres, counted the same way `stereo_summary` reports them."""
    centres = [
        element
        for element in Chem.FindPotentialStereo(mol)
        if "Atom" in str(element.type)
    ]
    if unspecified:
        return sum(
            1 for element in centres if str(element.specified).rsplit(".", 1)[-1] != "Specified"
        )
    return len(centres)


def _count_element(mol: Chem.Mol, symbols: Sequence[str]) -> int:
    wanted = set(symbols)
    return sum(1 for atom in mol.GetAtoms() if atom.GetSymbol() in wanted)


# name -> callable. Anything added here becomes a column everywhere, so the set
# stays curated: identity, lipophilicity, polarity, shape, composition.
_CORE_DESCRIPTORS = {
    "molecular_formula": rdMolDescriptors.CalcMolFormula,
    "mw": Descriptors.MolWt,
    "exact_mass": Descriptors.ExactMolWt,
    "heavy_atoms": lambda m: m.GetNumHeavyAtoms(),
    "num_fragments": lambda m: len(Chem.GetMolFrags(m)),
    "formal_charge": Chem.GetFormalCharge,
    "logp_crippen": Descriptors.MolLogP,
    "molar_refractivity": Crippen.MolMR,
    "tpsa": Descriptors.TPSA,
    "hbd": rdMolDescriptors.CalcNumHBD,
    "hba": rdMolDescriptors.CalcNumHBA,
    "rotatable_bonds": rdMolDescriptors.CalcNumRotatableBonds,
    "rings": rdMolDescriptors.CalcNumRings,
    "aromatic_rings": rdMolDescriptors.CalcNumAromaticRings,
    "aliphatic_rings": rdMolDescriptors.CalcNumAliphaticRings,
    "saturated_rings": rdMolDescriptors.CalcNumSaturatedRings,
    "num_heteroatoms": rdMolDescriptors.CalcNumHeteroatoms,
    "num_halogens": lambda m: _count_element(m, ("F", "Cl", "Br", "I")),
    "num_nitrogen": lambda m: _count_element(m, ("N",)),
    "num_oxygen": lambda m: _count_element(m, ("O",)),
    "num_sulfur": lambda m: _count_element(m, ("S",)),
    "num_phosphorus": lambda m: _count_element(m, ("P",)),
    "fraction_csp3": rdMolDescriptors.CalcFractionCSP3,
    "num_stereocenters": _num_stereocenters,
    "num_unspecified_stereocenters": lambda m: _num_stereocenters(m, unspecified=True),
    "spiro_atoms": rdMolDescriptors.CalcNumSpiroAtoms,
    "bridgehead_atoms": rdMolDescriptors.CalcNumBridgeheadAtoms,
    "amide_bonds": rdMolDescriptors.CalcNumAmideBonds,
    "radical_electrons": Descriptors.NumRadicalElectrons,
    "labute_asa": rdMolDescriptors.CalcLabuteASA,
    "bertz_complexity": Descriptors.BertzCT,
    "qed_drug_likeness": QED.qed,
}

CORE_DESCRIPTOR_NAMES = tuple(_CORE_DESCRIPTORS)


def compute_descriptors(mol_or_smiles: Any, *, include: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Compute the core descriptor block for one structure.

    Returns every key in `CORE_DESCRIPTOR_NAMES` (or just `include`). A
    descriptor RDKit cannot compute for a given structure comes back as `None`
    rather than being dropped, so the dict shape is stable across a batch.

    Values are calculated from the 2D structure as drawn — they describe the
    exact species you passed in, so standardize first when the numbers will be
    compared across compounds.
    """
    mol = to_mol(mol_or_smiles)
    names = list(include) if include else list(_CORE_DESCRIPTORS)
    unknown = [name for name in names if name not in _CORE_DESCRIPTORS]
    if unknown:
        raise ValueError(f"unknown descriptor(s): {unknown}. Known: {list(_CORE_DESCRIPTORS)}")

    out: Dict[str, Any] = {}
    with mute_rdkit_log():
        for name in names:
            try:
                out[name] = _CORE_DESCRIPTORS[name](mol)
            except Exception:
                out[name] = None
    return out


def compute_all_descriptors(mol_or_smiles: Any) -> Dict[str, Any]:
    """Every 2D descriptor RDKit ships (~200 values).

    Use when a downstream model needs a fixed descriptor block. Most of these
    are strongly collinear and several (`Ipc`, the `Chi`/`Kappa` families) are
    numerically extreme for large molecules — read them as model features, not
    as chemistry.
    """
    mol = to_mol(mol_or_smiles)
    with mute_rdkit_log():
        return dict(Descriptors.CalcMolDescriptors(mol))


def describe_batch(
    items: Any,
    *,
    include: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Descriptors for many structures, one row per input, failures kept.

    `items` is a list of SMILES or a `{name: smiles}` mapping. Each row has
    `name`, `input_smiles`, the descriptor keys, and `error` (`None` when the
    row computed). The row count always matches the input count — a table that
    silently lost a compound is a data-quality defect.
    """
    if isinstance(items, dict):
        pairs = list(items.items())
    else:
        pairs = [(None, item) for item in items]

    rows: List[Dict[str, Any]] = []
    for name, item in pairs:
        row: Dict[str, Any] = {
            "name": name,
            "input_smiles": item if isinstance(item, str) else None,
            "error": None,
        }
        try:
            row.update(compute_descriptors(item, include=include))
        except (StructureError, ValueError) as exc:
            row["error"] = str(exc)
            for key in (include or CORE_DESCRIPTOR_NAMES):
                row.setdefault(key, None)
        rows.append(row)
    return rows


def lipinski_flags(descriptors: Dict[str, Any]) -> Dict[str, bool]:
    """Rule-of-Five violation flags from a `compute_descriptors` dict.

    Lipinski's rule describes oral-drug absorption in a screening library; it
    is a property filter, not a chemical-safety criterion.
    """
    return {
        "mw_over_500": (descriptors.get("mw") or 0) > 500,
        "logp_over_5": (descriptors.get("logp_crippen") or 0) > 5,
        "hbd_over_5": (descriptors.get("hbd") or 0) > 5,
        "hba_over_10": (descriptors.get("hba") or 0) > 10,
    }


def druglikeness_profile(mol_or_smiles: Any) -> Dict[str, Any]:
    """Lipinski, Veber, Egan and Ghose filters plus QED for one structure.

    Keys: `lipinski_violations`, `lipinski_pass` (≤1 violation, as Lipinski
    defined it), `veber_pass`, `egan_pass`, `ghose_pass`, `qed`, and
    `descriptors` — the values the flags were derived from.

    These are library-design filters from medicinal chemistry. They describe
    how drug-like a molecule looks, and nothing else.
    """
    desc = compute_descriptors(mol_or_smiles)
    mw = desc["mw"] or 0.0
    logp = desc["logp_crippen"] or 0.0
    mr = desc["molar_refractivity"] or 0.0
    tpsa = desc["tpsa"] or 0.0
    heavy = desc["heavy_atoms"] or 0
    rot = desc["rotatable_bonds"] or 0
    flags = lipinski_flags(desc)
    violations = sum(1 for value in flags.values() if value)
    return {
        "lipinski_violations": violations,
        "lipinski_flags": flags,
        "lipinski_pass": violations <= 1,
        "veber_pass": rot <= 10 and tpsa <= 140,
        "egan_pass": tpsa <= 131.6 and logp <= 5.88,
        "ghose_pass": (160 <= mw <= 480) and (-0.4 <= logp <= 5.6) and (40 <= mr <= 130) and (20 <= heavy <= 70),
        "qed": desc["qed_drug_likeness"],
        "descriptors": desc,
    }


_FRAGMENT_FUNCTIONS = {
    name[3:]: getattr(Fragments, name) for name in dir(Fragments) if name.startswith("fr_")
}


def functional_groups(mol_or_smiles: Any, *, include_zero: bool = False) -> Dict[str, int]:
    """Count RDKit's 85 named functional groups in the structure.

    Returns `{group_name: count}` for the groups present (all of them when
    `include_zero=True`). The group definitions are RDKit's `Chem.Fragments`
    SMARTS — a substructure inventory of the molecule as drawn.
    """
    mol = to_mol(mol_or_smiles)
    counts: Dict[str, int] = {}
    with mute_rdkit_log():
        for name, fn in sorted(_FRAGMENT_FUNCTIONS.items()):
            try:
                count = int(fn(mol))
            except Exception:
                continue
            if count or include_zero:
                counts[name] = count
    return counts


def element_counts(mol_or_smiles: Any, *, include_hydrogens: bool = True) -> Dict[str, int]:
    """Atom inventory by element symbol, including implicit hydrogens."""
    mol = to_mol(mol_or_smiles)
    counts: Dict[str, int] = {}
    hydrogens = 0
    for atom in mol.GetAtoms():
        counts[atom.GetSymbol()] = counts.get(atom.GetSymbol(), 0) + 1
        hydrogens += atom.GetTotalNumHs()
    if include_hydrogens and hydrogens:
        counts["H"] = counts.get("H", 0) + hydrogens
    return dict(sorted(counts.items()))
