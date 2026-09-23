"""Structure depiction.

Every helper here writes an image file and returns its path. The file format
follows the extension of `output_name`: `.png` for a raster image, `.svg` for
vector output that stays editable in Illustrator or Inkscape.

`output_name` must be an absolute path from the scoped `prepare_output_path(...)`
helper, which is what keeps figures inside the conversation's output folder.
"""

from __future__ import annotations

from math import ceil
from typing import Any, List, Optional, Sequence, Tuple

from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

from .chem_common import mol_copy, mute_rdkit_log, resolve_output_name, to_mol
from .chem_substructure import _query

__all__ = [
    "draw_molecule",
    "draw_molecules",
    "draw_reaction",
    "draw_similarity_map",
]


def _drawer(width: int, height: int, svg: bool, panel: Optional[Tuple[int, int]] = None):
    if svg:
        if panel:
            return rdMolDraw2D.MolDraw2DSVG(width, height, panel[0], panel[1])
        return rdMolDraw2D.MolDraw2DSVG(width, height)
    if panel:
        return rdMolDraw2D.MolDraw2DCairo(width, height, panel[0], panel[1])
    return rdMolDraw2D.MolDraw2DCairo(width, height)


def _write(drawer, path, svg: bool) -> str:
    drawer.FinishDrawing()
    text = drawer.GetDrawingText()
    if svg:
        path.write_text(text, encoding="utf-8")
    else:
        path.write_bytes(text)
    return str(path)


def _is_svg(path) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".svg":
        return True
    if suffix == ".png":
        return False
    raise ValueError(f"output_name must end in .png or .svg, got {path.name!r}")


def _prepare(mol: Chem.Mol, *, template: Optional[Chem.Mol] = None) -> Chem.Mol:
    """2D coordinates, aromatic perception and (optionally) a fixed core layout."""
    prepared = rdMolDraw2D.PrepareMolForDrawing(mol_copy(mol))
    if template is not None:
        try:
            rdDepictor.GenerateDepictionMatching2DStructure(prepared, template, acceptFailure=True)
        except Exception:
            rdDepictor.Compute2DCoords(prepared)
    elif prepared.GetNumConformers() == 0:
        rdDepictor.Compute2DCoords(prepared)
    return prepared


def _alignment_template(mols: Sequence[Chem.Mol], align_smarts: Optional[str]) -> Optional[Chem.Mol]:
    """A 2D reference core so the same scaffold is drawn the same way everywhere."""
    if not align_smarts:
        return None
    query = _query(align_smarts)
    for mol in mols:
        match = mol.GetSubstructMatch(query)
        if not match:
            continue
        core = Chem.RWMol(mol)
        keep = set(match)
        for idx in sorted((a.GetIdx() for a in mol.GetAtoms() if a.GetIdx() not in keep), reverse=True):
            core.RemoveAtom(idx)
        template = core.GetMol()
        try:
            Chem.SanitizeMol(template)
        except Exception:
            template.UpdatePropertyCache(strict=False)
        rdDepictor.Compute2DCoords(template)
        return template
    return None


def draw_molecules(
    smiles_list: Sequence[Any],
    *,
    output_name: Any,
    legends: Optional[Sequence[str]] = None,
    mols_per_row: int = 4,
    sub_img_size: Tuple[int, int] = (300, 300),
    highlight_smarts: Optional[str] = None,
    align_smarts: Optional[str] = None,
    add_atom_indices: bool = False,
) -> str:
    """Draw a grid of structures to `output_name` and return the path.

    - `legends` must line up with `smiles_list`; a structure that fails to
      parse raises rather than being skipped, because a silently dropped
      molecule shifts every legend after it onto the wrong structure.
    - `highlight_smarts` colours the matching atoms in each structure — the way
      to show *where* a query, an alert or a shared core sits.
    - `align_smarts` lays every structure out with that substructure in the
      same orientation, which is what makes an analogue grid comparable at a
      glance. Pass the MCS SMARTS to align a read-across set on its common core.
    """
    path = resolve_output_name(output_name, caller="draw_molecules", suffix=".png")
    svg = _is_svg(path)
    mols = [to_mol(item, label=f"smiles_list[{i}]") for i, item in enumerate(smiles_list)]
    if not mols:
        raise ValueError("draw_molecules got an empty list")
    if legends is not None and len(legends) != len(mols):
        raise ValueError(f"legends has {len(legends)} entries for {len(mols)} structures")

    template = _alignment_template(mols, align_smarts)
    prepared = [_prepare(mol, template=template) for mol in mols]

    highlights: Optional[List[List[int]]] = None
    if highlight_smarts:
        query = _query(highlight_smarts)
        highlights = [
            sorted({idx for match in mol.GetSubstructMatches(query) for idx in match})
            for mol in prepared
        ]

    columns = max(1, min(mols_per_row, len(prepared)))
    rows = ceil(len(prepared) / columns)
    width, height = sub_img_size[0] * columns, sub_img_size[1] * rows
    drawer = _drawer(width, height, svg, panel=sub_img_size)
    options = drawer.drawOptions()
    options.addAtomIndices = add_atom_indices
    with mute_rdkit_log():
        drawer.DrawMolecules(
            prepared,
            legends=[str(legend) for legend in legends] if legends else None,
            highlightAtoms=highlights,
        )
    return _write(drawer, path, svg)


def draw_molecule(
    mol_or_smiles: Any,
    *,
    output_name: Any,
    legend: str = "",
    size: Tuple[int, int] = (400, 400),
    highlight_smarts: Optional[str] = None,
    highlight_atoms: Optional[Sequence[int]] = None,
    add_atom_indices: bool = False,
) -> str:
    """Draw one structure to `output_name` and return the path.

    `highlight_atoms` takes atom indices directly — the output of
    `find_substructure_matches` or the `atom_indices` of a structural-alert
    hit, so the figure shows exactly what matched.
    """
    path = resolve_output_name(output_name, caller="draw_molecule", suffix=".png")
    svg = _is_svg(path)
    mol = _prepare(to_mol(mol_or_smiles))

    atoms: List[int] = list(highlight_atoms or [])
    if highlight_smarts:
        query = _query(highlight_smarts)
        atoms.extend(idx for match in mol.GetSubstructMatches(query) for idx in match)
    atoms = sorted(set(atoms))
    bonds = [
        bond.GetIdx()
        for bond in mol.GetBonds()
        if bond.GetBeginAtomIdx() in atoms and bond.GetEndAtomIdx() in atoms
    ]

    drawer = _drawer(size[0], size[1], svg)
    drawer.drawOptions().addAtomIndices = add_atom_indices
    with mute_rdkit_log():
        rdMolDraw2D.PrepareAndDrawMolecule(
            drawer,
            mol,
            legend=str(legend),
            highlightAtoms=atoms or None,
            highlightBonds=bonds or None,
        )
    return _write(drawer, path, svg)


def draw_reaction(
    reaction: Any,
    *,
    output_name: Any,
    size: Tuple[int, int] = (900, 300),
) -> str:
    """Draw a reaction template or a reaction SMILES and return the path."""
    from .chem_reactions import _require_reaction

    path = resolve_output_name(output_name, caller="draw_reaction", suffix=".png")
    svg = _is_svg(path)
    rxn = _require_reaction(reaction)
    drawer = _drawer(size[0], size[1], svg)
    with mute_rdkit_log():
        drawer.DrawReaction(rxn)
    return _write(drawer, path, svg)


def draw_similarity_map(
    reference: Any,
    probe: Any,
    *,
    output_name: Any,
    radius: int = 2,
    n_bits: int = 2048,
    size: Tuple[int, int] = (450, 450),
) -> str:
    """Colour the probe by each atom's contribution to its similarity to the reference.

    Green regions raise the Morgan/Tanimoto similarity, pink regions lower it.
    It explains a similarity score structurally — it does not measure anything
    about either molecule on its own.
    """
    from rdkit.Chem.Draw import SimilarityMaps

    path = resolve_output_name(output_name, caller="draw_similarity_map", suffix=".png")
    svg = _is_svg(path)
    ref_mol = _prepare(to_mol(reference, label="reference"))
    probe_mol = _prepare(to_mol(probe, label="probe"))

    drawer = _drawer(size[0], size[1], svg)
    with mute_rdkit_log():
        SimilarityMaps.GetSimilarityMapForFingerprint(
            ref_mol,
            probe_mol,
            lambda mol, idx: SimilarityMaps.GetMorganFingerprint(
                mol, idx, radius=radius, fpType="bv", nBits=n_bits
            ),
            draw2d=drawer,
        )
    return _write(drawer, path, svg)
