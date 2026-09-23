"""Standardization and neutralization.

One structure can be drawn many ways — as a salt or its parent, charged or
neutral, in any tautomeric form. Standardization collapses those variants onto
one representation so that identity comparison, fingerprinting and any lookup
key mean the same thing for every compound in a set.

Each stage is independently switchable, because collapsing a variant is only
correct when the variant is not the thing you are reasoning about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from rdkit.Chem.MolStandardize import rdMolStandardize
from rdkit.Chem.SaltRemover import SaltRemover

from .chem_common import StructureError, cached, mol_copy, mute_rdkit_log, to_mol

__all__ = [
    "StandardizedMolecule",
    "tautomer_parameters",
    "canonical_tautomer",
    "charge_parent",
    "fragment_parent",
    "neutralize_charges",
    "remove_salts",
    "standardize_molecules",
    "standardize_smiles",
    "tautomer_parent",
]

# RDKit cookbook neutralization pattern: a charged atom that has no counter-charged
# neighbour, i.e. one that protonation/deprotonation can neutralize on its own.
_NEUTRALIZE_PATTERN = "[+1!h0!$([*]~[-1,-2,-3,-4]),-1!$([*]~[+1,+2,+3,+4])]"


@dataclass
class StandardizedMolecule:
    """Result of the standardization pipeline, with the audit trail.

    `steps` lists the stages that ran, in order, each flagged `*` when it
    changed the structure — that is what goes in the record so a reviewer can
    reconstruct the canonical form.
    """

    input_smiles: str
    canonical_smiles: Optional[str]
    inchi: Optional[str]
    inchikey: Optional[str]
    removed_salts: bool
    neutralized: bool
    tautomer_canonicalized: bool
    notes: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    formula: Optional[str] = None
    formal_charge: Optional[int] = None
    num_fragments: Optional[int] = None
    changed: bool = False

    @property
    def ok(self) -> bool:
        """True when the input parsed and a canonical form was produced."""
        return self.canonical_smiles is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_smiles": self.input_smiles,
            "canonical_smiles": self.canonical_smiles,
            "inchi": self.inchi,
            "inchikey": self.inchikey,
            "removed_salts": self.removed_salts,
            "neutralized": self.neutralized,
            "tautomer_canonicalized": self.tautomer_canonicalized,
            "notes": list(self.notes),
            "steps": list(self.steps),
            "formula": self.formula,
            "formal_charge": self.formal_charge,
            "num_fragments": self.num_fragments,
            "changed": self.changed,
        }


def _failed(input_smiles: str, note: str) -> StandardizedMolecule:
    return StandardizedMolecule(
        input_smiles=input_smiles,
        canonical_smiles=None,
        inchi=None,
        inchikey=None,
        removed_salts=False,
        neutralized=False,
        tautomer_canonicalized=False,
        notes=[note],
    )


def tautomer_parameters(max_tautomers: int = 1000):
    """Cleanup parameters for tautomer work, with stereochemistry preserved.

    RDKit's defaults drop sp3 stereo next to a tautomerizable centre and double
    bond stereo across one — which silently turns L-alanine into unspecified
    alanine. Stereochemistry is load-bearing for activity, so it is kept here
    and a stereocentre is only ever removed deliberately.
    """
    params = rdMolStandardize.CleanupParameters()
    params.maxTautomers = max_tautomers
    params.tautomerRemoveSp3Stereo = False
    params.tautomerRemoveBondStereo = False
    return params


def _tautomer_enumerator(max_tautomers: int):
    return cached(
        f"tautomer_enumerator:{max_tautomers}",
        lambda: rdMolStandardize.TautomerEnumerator(tautomer_parameters(max_tautomers)),
    )


def standardize_smiles(
    smiles: Any,
    *,
    strip_salts: bool = True,
    neutralize: bool = True,
    canonical_tautomer: bool = True,
    cleanup: bool = True,
    disconnect_metals: bool = True,
    keep_largest_fragment: Optional[bool] = None,
    max_tautomers: int = 1000,
) -> StandardizedMolecule:
    """Run the standardization pipeline and return the canonical identity.

    Stages, in order — each one opt-out:

    1. `cleanup` — remove explicit Hs, disconnect metal–ligand bonds
       (`disconnect_metals`), normalize functional-group drawing (nitro,
       N-oxide, …), reionize, re-perceive stereochemistry.
    2. `strip_salts` — drop counter-ions listed in RDKit's salt table, then
       keep the largest remaining component (`keep_largest_fragment` defaults
       to following `strip_salts`).
    3. `neutralize` — protonate/deprotonate atoms that carry a charge no
       counter-charge balances.
    4. `canonical_tautomer` — pick RDKit's canonical tautomer, keeping
       stereochemistry (see `tautomer_parameters`).

    Then emit canonical SMILES + InChI + InChIKey. Never raises for an
    unparseable input: the result carries `canonical_smiles=None` and a note.
    """
    input_smiles = smiles if isinstance(smiles, str) else Chem.MolToSmiles(smiles) if isinstance(smiles, Chem.Mol) else str(smiles)
    try:
        mol = mol_copy(to_mol(smiles, label="smiles"))
    except StructureError as exc:
        return _failed(input_smiles, str(exc))

    original = Chem.MolToSmiles(mol)
    notes: List[str] = []
    steps: List[str] = []

    def stage(name: str, fn) -> bool:
        """Run one stage, record whether it changed anything, keep going on error."""
        nonlocal mol
        before = Chem.MolToSmiles(mol)
        try:
            with mute_rdkit_log():
                result = fn(mol)
            if result is not None:
                mol = result
        except Exception as exc:  # RDKit raises a variety of C++-backed errors
            notes.append(f"{name} failed: {exc}")
            steps.append(f"{name}(failed)")
            return False
        after = Chem.MolToSmiles(mol)
        steps.append(f"{name}*" if after != before else name)
        return after != before

    if cleanup:
        stage("remove_explicit_hs", lambda m: Chem.RemoveHs(m))
        if disconnect_metals:
            stage("disconnect_metals", lambda m: cached("metal_disconnector", rdMolStandardize.MetalDisconnector).Disconnect(m))
        stage("normalize", lambda m: cached("normalizer", rdMolStandardize.Normalizer).normalize(m))
        stage("reionize", lambda m: cached("reionizer", rdMolStandardize.Reionizer).reionize(m))
        stage("assign_stereochemistry", lambda m: Chem.AssignStereochemistry(m, cleanIt=True, force=True))

    removed_salts = False
    if strip_salts:
        removed_salts = stage(
            "strip_salts",
            lambda m: cached("salt_remover", SaltRemover).StripMol(m, dontRemoveEverything=True),
        )

    if keep_largest_fragment is None:
        keep_largest_fragment = strip_salts
    if keep_largest_fragment:
        removed_salts = stage(
            "keep_largest_fragment",
            lambda m: cached("largest_fragment_chooser", rdMolStandardize.LargestFragmentChooser).choose(m),
        ) or removed_salts

    neutralized = False
    if neutralize:
        neutralized = stage(
            "neutralize", lambda m: cached("uncharger", rdMolStandardize.Uncharger).uncharge(m)
        )

    tautomer_canonicalized = False
    if canonical_tautomer:
        tautomer_canonicalized = stage(
            "canonical_tautomer", lambda m: _tautomer_enumerator(max_tautomers).Canonicalize(m)
        )

    try:
        with mute_rdkit_log():
            Chem.SanitizeMol(mol)
    except Exception as exc:
        return _failed(input_smiles, f"standardized structure failed sanitization: {exc}")

    canonical = Chem.MolToSmiles(mol)
    with mute_rdkit_log():
        inchi = Chem.MolToInchi(mol) or None
        inchikey = Chem.MolToInchiKey(mol) if inchi else None
    if inchi is None:
        notes.append("InChI could not be generated for this structure")

    return StandardizedMolecule(
        input_smiles=input_smiles,
        canonical_smiles=canonical,
        inchi=inchi,
        inchikey=inchikey,
        removed_salts=removed_salts,
        neutralized=neutralized,
        tautomer_canonicalized=tautomer_canonicalized,
        notes=notes,
        steps=steps,
        formula=rdMolDescriptors.CalcMolFormula(mol),
        formal_charge=Chem.GetFormalCharge(mol),
        num_fragments=len(Chem.GetMolFrags(mol)),
        changed=canonical != original,
    )


def standardize_molecules(items: Iterable[Any], **kwargs) -> List[StandardizedMolecule]:
    """Standardize a list with the same settings, one result per input.

    Failures stay in the list with `canonical_smiles=None`, so the output is
    always the same length as the input and a dropped compound is visible.
    """
    return [standardize_smiles(item, **kwargs) for item in items]


def neutralize_charges(mol_or_smiles: Any, *, method: str = "uncharger") -> str:
    """Neutralize charges that are not balanced by a counter-charge.

    `method="uncharger"` uses RDKit's `Uncharger`; `method="pattern"` uses the
    RDKit cookbook SMARTS recipe, which also neutralizes an ion whose
    counter-ion is present as a separate fragment.

    Neither touches a permanently charged centre such as a quaternary
    ammonium, because no protonation state change can neutralize it.
    """
    mol = mol_copy(to_mol(mol_or_smiles))
    if method == "uncharger":
        with mute_rdkit_log():
            return Chem.MolToSmiles(cached("uncharger", rdMolStandardize.Uncharger).uncharge(mol))
    if method != "pattern":
        raise ValueError(f"method must be 'uncharger' or 'pattern', got {method!r}")

    pattern = Chem.MolFromSmarts(_NEUTRALIZE_PATTERN)
    matches = mol.GetSubstructMatches(pattern)
    if matches:
        rw = Chem.RWMol(mol)
        for (idx,) in matches:
            atom = rw.GetAtomWithIdx(idx)
            charge = atom.GetFormalCharge()
            hcount = atom.GetTotalNumHs()
            atom.SetFormalCharge(0)
            atom.SetNumExplicitHs(max(0, hcount - charge))
            atom.UpdatePropertyCache(strict=False)
        mol = rw.GetMol()
        with mute_rdkit_log():
            Chem.SanitizeMol(mol)
    return Chem.MolToSmiles(mol)


def remove_salts(
    mol_or_smiles: Any,
    *,
    salts: Optional[str] = None,
    dont_remove_everything: bool = True,
) -> Dict[str, Any]:
    """Strip counter-ions and report what changed.

    `salts` takes a newline-separated SMARTS list to override RDKit's default
    salt table (15 common counter-ions). Keys: `smiles`, `removed` (bool),
    `num_fragments_before`, `num_fragments_after`.
    """
    mol = mol_copy(to_mol(mol_or_smiles))
    before = Chem.MolToSmiles(mol)
    remover = SaltRemover(defnData=salts) if salts else cached("salt_remover", SaltRemover)
    with mute_rdkit_log():
        stripped = remover.StripMol(mol, dontRemoveEverything=dont_remove_everything)
    after = Chem.MolToSmiles(stripped)
    return {
        "smiles": after,
        "removed": after != before,
        "num_fragments_before": len(Chem.GetMolFrags(mol)),
        "num_fragments_after": len(Chem.GetMolFrags(stripped)),
    }


def canonical_tautomer(mol_or_smiles: Any, *, max_tautomers: int = 1000) -> str:
    """RDKit's canonical tautomer, as SMILES.

    Canonical means "reproducibly chosen", not "the one that predominates in
    solution" — the scoring function is a heuristic over tautomer rules.
    """
    mol = to_mol(mol_or_smiles)
    with mute_rdkit_log():
        return Chem.MolToSmiles(_tautomer_enumerator(max_tautomers).Canonicalize(mol))


def fragment_parent(mol_or_smiles: Any) -> str:
    """Largest organic component, keeping its charge (RDKit `FragmentParent`)."""
    mol = to_mol(mol_or_smiles)
    with mute_rdkit_log():
        return Chem.MolToSmiles(rdMolStandardize.FragmentParent(mol))


def charge_parent(mol_or_smiles: Any) -> str:
    """Fragment parent, then neutralized (RDKit `ChargeParent`).

    This is what turns sodium acetate into acetic acid — the usual key for
    matching a salt to its parent substance.
    """
    mol = to_mol(mol_or_smiles)
    with mute_rdkit_log():
        return Chem.MolToSmiles(rdMolStandardize.ChargeParent(mol))


def tautomer_parent(mol_or_smiles: Any) -> str:
    """Canonical tautomer of the structure (RDKit `TautomerParent`)."""
    mol = to_mol(mol_or_smiles)
    with mute_rdkit_log():
        return Chem.MolToSmiles(rdMolStandardize.TautomerParent(mol))
