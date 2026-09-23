"""Chemical representation: parse, convert, validate, and inventory a structure.

Covers the round trip between the string forms a structure arrives in (SMILES,
InChI, molblock, SDF) and an RDKit molecule, plus the checks that say whether
the molecule you got is the substance you meant: sanitization problems,
fragment composition, stereochemistry, tautomer and stereoisomer enumeration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from rdkit.Chem.EnumerateStereoisomers import EnumerateStereoisomers, StereoEnumerationOptions
from rdkit.Chem.MolStandardize import rdMolStandardize

from .chem_common import (
    StructureError,
    cached,
    detect_format,
    mol_copy,
    mute_rdkit_log,
    resolve_output_name,
    to_mol,
)

__all__ = [
    "canonical_smiles",
    "compare_structures",
    "enumerate_stereoisomers",
    "enumerate_tautomers",
    "identity_record",
    "largest_fragment",
    "molecular_formula",
    "parse_inchi",
    "parse_molblock",
    "parse_molecule",
    "parse_smarts",
    "parse_smiles",
    "read_sdf",
    "split_fragments",
    "stereo_summary",
    "to_inchi",
    "to_inchikey",
    "to_molblock",
    "validate_structure",
    "write_sdf",
]

# Everything outside this set is reported as a metal / metalloid, which is the
# trigger for the organometallic branch of the standardization decision table.
_NONMETALS = {
    "H", "He", "B", "C", "N", "O", "F", "Ne", "Si", "P", "S", "Cl", "Ar",
    "Ge", "As", "Se", "Br", "Kr", "Sb", "Te", "I", "Xe", "At", "Rn",
}


# Parsing


def parse_smiles(smiles: str) -> Optional[Chem.Mol]:
    """Parse a SMILES string into an RDKit `Mol`, or `None` if it does not parse."""
    if not isinstance(smiles, str) or not smiles.strip():
        return None
    with mute_rdkit_log():
        return Chem.MolFromSmiles(smiles.strip())


def parse_inchi(inchi: str) -> Optional[Chem.Mol]:
    """Parse an InChI string, or `None` if it does not parse."""
    if not isinstance(inchi, str) or not inchi.strip().startswith("InChI="):
        return None
    with mute_rdkit_log():
        return Chem.MolFromInchi(inchi.strip())


def parse_molblock(molblock: str) -> Optional[Chem.Mol]:
    """Parse a MDL molblock (V2000/V3000), or `None` if it does not parse."""
    if not isinstance(molblock, str) or not molblock.strip():
        return None
    with mute_rdkit_log():
        return Chem.MolFromMolBlock(molblock)


def parse_smarts(smarts: str) -> Optional[Chem.Mol]:
    """Compile a SMARTS query pattern, or `None` if it does not compile.

    A SMARTS pattern is a query, not a molecule: use it for matching, never as
    the structure of a substance.
    """
    if not isinstance(smarts, str) or not smarts.strip():
        return None
    with mute_rdkit_log():
        return Chem.MolFromSmarts(smarts.strip())


def parse_molecule(text: str, fmt: str = "auto") -> Optional[Chem.Mol]:
    """Parse SMILES, InChI or a molblock, detecting the format when `fmt="auto"`.

    Returns `None` for anything that does not parse — including an InChIKey,
    which is a hash and carries no structure.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    kind = detect_format(text) if fmt == "auto" else fmt
    if kind == "inchi":
        return parse_inchi(text)
    if kind == "molblock":
        return parse_molblock(text)
    if kind == "smiles":
        return parse_smiles(text)
    return None


# Conversion


def canonical_smiles(mol_or_smiles: Any, *, isomeric: bool = True, kekule: bool = False) -> str:
    """Canonical SMILES. Keeps stereochemistry unless `isomeric=False`.

    `isomeric=False` deletes stereochemistry from the output — only for an
    explicitly stereo-insensitive comparison, never as the structure of record.
    """
    mol = to_mol(mol_or_smiles)
    if kekule:
        mol = mol_copy(mol)
        Chem.Kekulize(mol, clearAromaticFlags=True)
        return Chem.MolToSmiles(mol, isomericSmiles=isomeric, kekuleSmiles=True)
    return Chem.MolToSmiles(mol, isomericSmiles=isomeric)


def to_inchi(mol_or_smiles: Any) -> Optional[str]:
    """Standard InChI, or `None` when the InChI layer cannot represent the input."""
    mol = to_mol(mol_or_smiles)
    with mute_rdkit_log():
        inchi = Chem.MolToInchi(mol)
    return inchi or None


def to_inchikey(mol_or_smiles: Any) -> Optional[str]:
    """Standard InChIKey — the hashed identifier used as a cross-database join key."""
    mol = to_mol(mol_or_smiles)
    with mute_rdkit_log():
        key = Chem.MolToInchiKey(mol)
    return key or None


def to_molblock(mol_or_smiles: Any, *, conf_id: int = -1, kekulize: bool = True) -> str:
    """MDL molblock. Emits 2D coordinates if the molecule has none."""
    mol = to_mol(mol_or_smiles)
    if mol.GetNumConformers() == 0:
        from rdkit.Chem import rdDepictor

        mol = mol_copy(mol)
        rdDepictor.Compute2DCoords(mol)
    return Chem.MolToMolBlock(mol, confId=conf_id, kekulize=kekulize)


def molecular_formula(mol_or_smiles: Any) -> str:
    """Hill-notation molecular formula, including the net charge."""
    return rdMolDescriptors.CalcMolFormula(to_mol(mol_or_smiles))


def identity_record(mol_or_smiles: Any) -> Dict[str, Any]:
    """Every identifier for one structure in a single dict.

    Keys: `input`, `input_format`, `canonical_smiles`, `flat_smiles`
    (stereo removed), `inchi`, `inchikey`, `inchikey_skeleton` (first block —
    equal for stereoisomers and for salts of the same parent), `formula`,
    `formal_charge`, `num_fragments`.
    """
    raw = mol_or_smiles if isinstance(mol_or_smiles, str) else None
    mol = to_mol(mol_or_smiles)
    inchikey = to_inchikey(mol)
    return {
        "input": raw,
        "input_format": detect_format(raw) if raw else "mol",
        "canonical_smiles": Chem.MolToSmiles(mol),
        "flat_smiles": Chem.MolToSmiles(mol, isomericSmiles=False),
        "inchi": to_inchi(mol),
        "inchikey": inchikey,
        "inchikey_skeleton": inchikey.split("-")[0] if inchikey else None,
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "formal_charge": Chem.GetFormalCharge(mol),
        "num_fragments": len(Chem.GetMolFrags(mol)),
    }


# Validation and inventory


def validate_structure(text: Any) -> Dict[str, Any]:
    """Report what is wrong with — or unusual about — an input structure.

    Never raises: an unparseable input comes back as a record with
    `parsed=False` and the RDKit problem messages, which is what belongs in the
    data-quality log.

    Keys: `input`, `input_format`, `parsed`, `problems`, `canonical_smiles`,
    `formula`, `num_fragments`, `is_mixture`, `formal_charge`, `metal_atoms`,
    `has_isotopes`, `has_atom_map_numbers`, `has_dummy_atoms`,
    `has_explicit_hs`, `num_radical_electrons`, `unspecified_stereocenters`,
    `unspecified_double_bonds`.
    """
    record: Dict[str, Any] = {
        "input": text if isinstance(text, str) else None,
        "input_format": detect_format(text) if isinstance(text, str) else "mol",
        "parsed": False,
        "problems": [],
        "canonical_smiles": None,
    }

    if record["input_format"] == "inchikey":
        record["problems"].append(
            {"type": "NotAStructure", "message": "input is an InChIKey (a hash), not a structure"}
        )
        return record

    # Parse without sanitization first so the specific chemistry problem is
    # reported rather than a bare "could not parse".
    try:
        raw = to_mol(text, sanitize=False, label="structure")
    except StructureError as exc:
        record["problems"].append({"type": "ParseError", "message": str(exc)})
        return record

    with mute_rdkit_log():
        for problem in Chem.DetectChemistryProblems(raw):
            record["problems"].append({"type": problem.GetType(), "message": problem.Message()})

    mol = None
    if not record["problems"]:
        try:
            mol = to_mol(text, label="structure")
        except StructureError as exc:  # pragma: no cover - defensive
            record["problems"].append({"type": "SanitizationError", "message": str(exc)})

    target = mol if mol is not None else raw
    frags = Chem.GetMolFrags(target)
    stereo = stereo_summary(target) if mol is not None else {
        "unspecified_atom_stereocenters": None,
        "unspecified_bond_stereo": None,
    }
    record.update(
        {
            "parsed": mol is not None,
            "canonical_smiles": Chem.MolToSmiles(mol) if mol is not None else None,
            "formula": rdMolDescriptors.CalcMolFormula(mol) if mol is not None else None,
            "num_fragments": len(frags),
            "is_mixture": len(frags) > 1,
            "formal_charge": Chem.GetFormalCharge(target),
            "metal_atoms": sorted(
                {a.GetSymbol() for a in target.GetAtoms() if a.GetSymbol() not in _NONMETALS and a.GetAtomicNum() > 0}
            ),
            "has_isotopes": any(a.GetIsotope() for a in target.GetAtoms()),
            "has_atom_map_numbers": any(a.GetAtomMapNum() for a in target.GetAtoms()),
            "has_dummy_atoms": any(a.GetAtomicNum() == 0 for a in target.GetAtoms()),
            "has_explicit_hs": any(a.GetAtomicNum() == 1 for a in target.GetAtoms()),
            "num_radical_electrons": sum(a.GetNumRadicalElectrons() for a in target.GetAtoms()),
            "unspecified_stereocenters": stereo["unspecified_atom_stereocenters"],
            "unspecified_double_bonds": stereo["unspecified_bond_stereo"],
        }
    )
    return record


def stereo_summary(mol_or_smiles: Any) -> Dict[str, Any]:
    """Inventory every stereogenic element and whether it is specified.

    Keys: `atom_stereocenters` and `bond_stereo` (lists of dicts with
    `index`/`atoms`, `specified`, `descriptor`), plus the counts
    `num_atom_stereocenters`, `unspecified_atom_stereocenters`,
    `num_bond_stereo`, `unspecified_bond_stereo`.

    An unspecified centre means the input names a *set* of stereoisomers, not
    one substance — resolve it before any comparison that depends on identity.
    """
    mol = to_mol(mol_or_smiles)
    atoms: List[Dict[str, Any]] = []
    bonds: List[Dict[str, Any]] = []
    with mute_rdkit_log():
        elements = Chem.FindPotentialStereo(mol)
    for element in elements:
        kind = str(element.type)
        specified = str(element.specified).rsplit(".", 1)[-1]
        descriptor = str(element.descriptor).rsplit(".", 1)[-1]
        if "Atom" in kind:
            atom = mol.GetAtomWithIdx(element.centeredOn)
            atoms.append(
                {
                    "index": element.centeredOn,
                    "symbol": atom.GetSymbol(),
                    "type": kind.rsplit(".", 1)[-1],
                    "specified": specified == "Specified",
                    "descriptor": descriptor,
                }
            )
        else:
            bond = mol.GetBondWithIdx(element.centeredOn)
            bonds.append(
                {
                    "index": element.centeredOn,
                    "atoms": (bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()),
                    "type": kind.rsplit(".", 1)[-1],
                    "specified": specified == "Specified",
                    "descriptor": descriptor,
                }
            )
    return {
        "atom_stereocenters": atoms,
        "bond_stereo": bonds,
        "num_atom_stereocenters": len(atoms),
        "unspecified_atom_stereocenters": sum(1 for a in atoms if not a["specified"]),
        "num_bond_stereo": len(bonds),
        "unspecified_bond_stereo": sum(1 for b in bonds if not b["specified"]),
    }


def split_fragments(mol_or_smiles: Any) -> List[Dict[str, Any]]:
    """Break a multi-component input into its components, largest first.

    Each component: `smiles`, `formula`, `num_heavy_atoms`, `formal_charge`.
    Use it to see what a salt, hydrate or mixture is actually made of before
    deciding what to strip.
    """
    mol = to_mol(mol_or_smiles)
    pieces = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
    out = []
    for piece in pieces:
        out.append(
            {
                "smiles": Chem.MolToSmiles(piece),
                "formula": rdMolDescriptors.CalcMolFormula(piece),
                "num_heavy_atoms": piece.GetNumHeavyAtoms(),
                "formal_charge": Chem.GetFormalCharge(piece),
            }
        )
    out.sort(key=lambda item: item["num_heavy_atoms"], reverse=True)
    return out


def largest_fragment(mol_or_smiles: Any) -> str:
    """SMILES of the largest covalent component (RDKit's `LargestFragmentChooser`)."""
    mol = to_mol(mol_or_smiles)
    chooser = cached("largest_fragment_chooser", rdMolStandardize.LargestFragmentChooser)
    with mute_rdkit_log():
        return Chem.MolToSmiles(chooser.choose(mol))


def compare_structures(a: Any, b: Any) -> Dict[str, Any]:
    """Say precisely how two structures differ — the check for "is this still
    the same substance?" after standardization, salt stripping or a transform.

    Keys: `same_canonical_smiles`, `same_inchikey`, `same_skeleton`
    (connectivity ignoring stereo/isotopes/charge layer), `same_formula`,
    `charge_delta`, `heavy_atom_delta`, `fragment_delta`,
    `stereocenters_lost`, plus the two records under `a` and `b`.
    """
    rec_a = identity_record(a)
    rec_b = identity_record(b)
    mol_a, mol_b = to_mol(a), to_mol(b)
    stereo_a = stereo_summary(mol_a)
    stereo_b = stereo_summary(mol_b)
    specified_a = stereo_a["num_atom_stereocenters"] - stereo_a["unspecified_atom_stereocenters"]
    specified_b = stereo_b["num_atom_stereocenters"] - stereo_b["unspecified_atom_stereocenters"]
    return {
        "a": rec_a,
        "b": rec_b,
        "same_canonical_smiles": rec_a["canonical_smiles"] == rec_b["canonical_smiles"],
        "same_inchikey": rec_a["inchikey"] is not None and rec_a["inchikey"] == rec_b["inchikey"],
        "same_skeleton": rec_a["inchikey_skeleton"] is not None
        and rec_a["inchikey_skeleton"] == rec_b["inchikey_skeleton"],
        "same_formula": rec_a["formula"] == rec_b["formula"],
        "charge_delta": rec_b["formal_charge"] - rec_a["formal_charge"],
        "heavy_atom_delta": mol_b.GetNumHeavyAtoms() - mol_a.GetNumHeavyAtoms(),
        "fragment_delta": rec_b["num_fragments"] - rec_a["num_fragments"],
        "stereocenters_lost": max(0, specified_a - specified_b),
    }


# Enumeration


def enumerate_stereoisomers(
    mol_or_smiles: Any,
    *,
    max_isomers: int = 16,
    only_unassigned: bool = True,
    try_embedding: bool = False,
) -> List[str]:
    """Expand unspecified stereocentres into explicit stereoisomer SMILES.

    `try_embedding=True` drops combinations that cannot be embedded in 3D
    (physically impossible ring stereochemistry) at the cost of a 3D embedding
    per candidate. The result is capped at `max_isomers`; a molecule with n
    unspecified centres has 2**n of them.
    """
    mol = to_mol(mol_or_smiles)
    options = StereoEnumerationOptions(
        maxIsomers=max_isomers, onlyUnassigned=only_unassigned, tryEmbedding=try_embedding
    )
    with mute_rdkit_log():
        isomers = [Chem.MolToSmiles(iso) for iso in EnumerateStereoisomers(mol, options=options)]
    return sorted(set(isomers))


def enumerate_tautomers(mol_or_smiles: Any, *, max_tautomers: int = 100) -> Dict[str, Any]:
    """Enumerate tautomers and report RDKit's canonical choice among them.

    Keys: `input_smiles`, `canonical_tautomer`, `tautomers`, `count`,
    `status` (`Completed` / `MaxTautomersReached` / `MaxTransformsReached` /
    `Canceled`), `truncated`.

    A truncated enumeration is not the tautomer set — say so rather than
    reporting the list as complete.
    """
    from .chem_standardize import tautomer_parameters

    mol = to_mol(mol_or_smiles)
    enumerator = rdMolStandardize.TautomerEnumerator(tautomer_parameters(max_tautomers))
    with mute_rdkit_log():
        result = enumerator.Enumerate(mol)
        tautomers = sorted({Chem.MolToSmiles(t) for t in result})
        canonical = Chem.MolToSmiles(enumerator.Canonicalize(mol))
    status = str(result.status).rsplit(".", 1)[-1]
    return {
        "input_smiles": Chem.MolToSmiles(mol),
        "canonical_tautomer": canonical,
        "tautomers": tautomers,
        "count": len(tautomers),
        "status": status,
        "truncated": status != "Completed",
    }


# Structure files


def read_sdf(path: str, *, sanitize: bool = True, max_records: Optional[int] = None) -> List[Chem.Mol]:
    """Read an SDF into a list of molecules, keeping their SD properties.

    Records RDKit cannot parse are skipped, so compare `len(mols)` against the
    file's record count when completeness matters. Read an SD property with
    `mol.GetProp("<name>")` and the record title with `mol.GetProp("_Name")`.
    """
    mols: List[Chem.Mol] = []
    with mute_rdkit_log():
        supplier = Chem.SDMolSupplier(str(path), sanitize=sanitize)
        for mol in supplier:
            if mol is None:
                continue
            mols.append(mol)
            if max_records is not None and len(mols) >= max_records:
                break
    return mols


def write_sdf(
    mols: Sequence[Any],
    *,
    output_name: Any,
    properties: Optional[Sequence[Dict[str, Any]]] = None,
    names: Optional[Sequence[str]] = None,
    conf_id: int = -1,
) -> str:
    """Write molecules (or SMILES) to an SDF and return the path.

    `output_name` must be an absolute path from `prepare_output_path(...)`.
    Molecules without coordinates get 2D coordinates so the file is usable in
    any viewer; pass 3D molecules from `generate_conformers` to keep geometry.
    """
    path = resolve_output_name(output_name, caller="write_sdf", suffix=".sdf")
    from rdkit.Chem import rdDepictor

    writer = Chem.SDWriter(str(path))
    try:
        for index, item in enumerate(mols):
            mol = mol_copy(to_mol(item, label=f"mols[{index}]"))
            if mol.GetNumConformers() == 0:
                rdDepictor.Compute2DCoords(mol)
            if names is not None and index < len(names):
                mol.SetProp("_Name", str(names[index]))
            if properties is not None and index < len(properties):
                for key, value in properties[index].items():
                    mol.SetProp(str(key), str(value))
            writer.write(mol, confId=conf_id)
    finally:
        writer.close()
    return str(path)
