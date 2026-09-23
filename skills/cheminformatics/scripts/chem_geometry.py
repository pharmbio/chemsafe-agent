"""3D structure generation and geometry.

Turning a 2D graph into coordinates is a conformational search, not a
measurement: ETKDG samples plausible geometries and a force field relaxes them.
Report which method produced a geometry and how many conformers were kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors3D, rdMolAlign

from .chem_common import mol_copy, mute_rdkit_log, to_mol

__all__ = [
    "ConformerSet",
    "conformer_rmsd",
    "descriptors_3d",
    "generate_conformers",
    "lowest_energy_conformer",
]


@dataclass
class ConformerSet:
    """Embedded conformers of one molecule, with their force-field energies."""

    mol: Chem.Mol
    energies: List[Tuple[int, float]] = field(default_factory=list)
    method: str = "ETKDGv3"
    optimizer: Optional[str] = None
    num_requested: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def num_conformers(self) -> int:
        return self.mol.GetNumConformers()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "smiles": Chem.MolToSmiles(Chem.RemoveHs(self.mol)),
            "num_conformers": self.num_conformers,
            "num_requested": self.num_requested,
            "method": self.method,
            "optimizer": self.optimizer,
            "energies": list(self.energies),
            "notes": list(self.notes),
        }


def generate_conformers(
    mol_or_smiles: Any,
    *,
    num_confs: int = 10,
    seed: int = 0xF00D,
    optimize: Optional[str] = "mmff",
    prune_rms_thresh: float = 0.5,
    max_iters: int = 500,
    add_hs: bool = True,
) -> ConformerSet:
    """Embed conformers with ETKDGv3 and relax them.

    `optimize` is `"mmff"`, `"uff"` or `None`. `prune_rms_thresh` discards
    conformers within that RMSD of one already kept, so `num_conformers` is
    usually lower than `num_confs` — that is the deduplication working, not a
    failure. `seed` makes the embedding reproducible; change it and you get a
    different (equally valid) set of geometries.

    Hydrogens are added before embedding because geometry without them is
    meaningless; the SMILES in `to_dict()` is reported without them.
    """
    mol = mol_copy(to_mol(mol_or_smiles))
    if add_hs:
        mol = Chem.AddHs(mol)

    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.pruneRmsThresh = prune_rms_thresh
    notes: List[str] = []

    with mute_rdkit_log():
        conf_ids = list(AllChem.EmbedMultipleConfs(mol, numConfs=num_confs, params=params))
        if not conf_ids:
            params.useRandomCoords = True
            conf_ids = list(AllChem.EmbedMultipleConfs(mol, numConfs=num_confs, params=params))
            if conf_ids:
                notes.append("fell back to random-coordinate embedding")

    if not conf_ids:
        raise ValueError(
            "conformer embedding failed for this structure — check for unusual valences, "
            "disconnected fragments or an over-constrained ring system"
        )

    energies: List[Tuple[int, float]] = []
    if optimize:
        with mute_rdkit_log():
            if optimize.lower() == "mmff":
                results = AllChem.MMFFOptimizeMoleculeConfs(mol, maxIters=max_iters)
            elif optimize.lower() == "uff":
                results = AllChem.UFFOptimizeMoleculeConfs(mol, maxIters=max_iters)
            else:
                raise ValueError(f"optimize must be 'mmff', 'uff' or None, got {optimize!r}")
        energies = [(conf_id, float(energy)) for conf_id, (_flag, energy) in zip(conf_ids, results)]
        not_converged = sum(1 for _flag, _energy in results if _flag != 0)
        if not_converged:
            notes.append(f"{not_converged} conformer(s) did not converge in {max_iters} iterations")
        energies.sort(key=lambda item: item[1])

    return ConformerSet(
        mol=mol,
        energies=energies,
        optimizer=optimize,
        num_requested=num_confs,
        notes=notes,
    )


def lowest_energy_conformer(conformers: ConformerSet) -> Chem.Mol:
    """A single-conformer molecule holding the lowest-energy geometry.

    Requires an optimized `ConformerSet` — without energies there is no
    ordering, only an arbitrary first conformer.
    """
    if not conformers.energies:
        raise ValueError(
            "this ConformerSet has no energies; generate it with optimize='mmff' or 'uff' "
            "before asking for the lowest-energy conformer"
        )
    best_id = conformers.energies[0][0]
    single = Chem.Mol(conformers.mol)
    single.RemoveAllConformers()
    single.AddConformer(conformers.mol.GetConformer(best_id), assignId=True)
    return single


def descriptors_3d(mol_or_conformers: Any, *, conf_id: int = -1) -> Dict[str, float]:
    """Shape descriptors from a 3D conformer.

    Keys: `pmi1`/`pmi2`/`pmi3` (principal moments of inertia), `npr1`/`npr2`
    (normalized ratios — the rod/disc/sphere triangle), `asphericity`,
    `eccentricity`, `inertial_shape_factor`, `radius_of_gyration`,
    `spherocity_index`.

    These describe one conformer. A flexible molecule has a distribution of
    shapes, so quote the conformer they came from.
    """
    mol = mol_or_conformers.mol if isinstance(mol_or_conformers, ConformerSet) else to_mol(mol_or_conformers)
    if mol.GetNumConformers() == 0:
        raise ValueError(
            "this molecule has no 3D conformer — run generate_conformers(...) first"
        )
    return {
        "pmi1": Descriptors3D.PMI1(mol, confId=conf_id),
        "pmi2": Descriptors3D.PMI2(mol, confId=conf_id),
        "pmi3": Descriptors3D.PMI3(mol, confId=conf_id),
        "npr1": Descriptors3D.NPR1(mol, confId=conf_id),
        "npr2": Descriptors3D.NPR2(mol, confId=conf_id),
        "asphericity": Descriptors3D.Asphericity(mol, confId=conf_id),
        "eccentricity": Descriptors3D.Eccentricity(mol, confId=conf_id),
        "inertial_shape_factor": Descriptors3D.InertialShapeFactor(mol, confId=conf_id),
        "radius_of_gyration": Descriptors3D.RadiusOfGyration(mol, confId=conf_id),
        "spherocity_index": Descriptors3D.SpherocityIndex(mol, confId=conf_id),
    }


def conformer_rmsd(probe: Any, reference: Any, *, align: bool = True) -> float:
    """Symmetry-aware best RMSD between two 3D structures of the same molecule.

    `align=True` superimposes first (shape comparison); `align=False` compares
    the coordinates as they stand (pose comparison).
    """
    probe_mol = probe.mol if isinstance(probe, ConformerSet) else to_mol(probe)
    ref_mol = reference.mol if isinstance(reference, ConformerSet) else to_mol(reference)
    if probe_mol.GetNumConformers() == 0 or ref_mol.GetNumConformers() == 0:
        raise ValueError("both structures need a 3D conformer — run generate_conformers(...) first")
    with mute_rdkit_log():
        if align:
            return float(rdMolAlign.GetBestRMS(Chem.Mol(probe_mol), Chem.Mol(ref_mol)))
        return float(rdMolAlign.CalcRMS(Chem.Mol(probe_mol), Chem.Mol(ref_mol)))
