# Structure depiction

Every helper imports from `scripts.chem_drawing`, writes a file and returns its
path.

## Output contract

```python
path = draw_molecules(
    [target, *analogues],
    output_name=prepare_output_path("analogue_grid.png"),
    legends=["target", "analogue 1", "analogue 2"],
)
```

- **A bare filename raises** — it would write outside the conversation's output
  folder, where nothing downstream can reach it.
- **The extension picks the format**: `.png` raster, `.svg` vector (editable in
  Illustrator/Inkscape); anything else raises. Prefer `.svg` for a figure headed
  into a document.
- **An unparseable structure raises** rather than being skipped — a dropped
  molecule would shift every later legend onto the wrong structure.
- Property plots, distributions and heatmaps around these structures belong to
  the `data_visualization` skill, which owns the figure theme.

## One structure

```python
matches = find_substructure_matches(smiles, "[N+](=O)[O-]")   # scripts.chem_substructure
draw_molecule(
    smiles,
    output_name=prepare_output_path("nitro_sites.svg"),
    legend="nitro groups highlighted",
    highlight_atoms=[idx for match in matches for idx in match],
)
```

- `highlight_smarts=` highlights every match of a pattern; `highlight_atoms=`
  takes indices directly, so a filter-catalog hit's `atom_indices` or
  `find_substructure_matches` output shows exactly as matched.
- `add_atom_indices=True` prints atom numbers — for text that refers to
  specific atoms, not a presentation figure.

## Grids

```python
mcs = maximum_common_substructure([target, *analogues])       # scripts.chem_substructure
draw_molecules(
    [target, *analogues],
    output_name=prepare_output_path("series.svg"),
    legends=["target", *[f"analogue {i + 1}" for i in range(len(analogues))]],
    highlight_smarts=mcs.smarts,
    align_smarts=mcs.smarts,
    mols_per_row=4,
    sub_img_size=(300, 300),
)
```

- **`align_smarts=` orients every structure's copy of that substructure the
  same way**; without it a series sharing a core looks unrelated. The MCS SMARTS
  is the usual choice; a structure lacking the pattern keeps its own layout.
- **`highlight_smarts=` colors the shared core** so the eye lands on the
  differences.
- Legends must line up with structures — the call checks the lengths.

## Reactions

```python
draw_reaction(
    "[C:1](=[O:2])[OH].[N!H0:3]>>[C:1](=[O:2])[N:3]",
    output_name=prepare_output_path("amide_template.svg"),
    size=(900, 300),
)
```

Draws a template or a written reaction; query atoms and atom maps render as
RDKit shows them, which makes a template's scope visible.

## Similarity maps

```python
draw_similarity_map(
    reference_smiles, probe_smiles,
    output_name=prepare_output_path("similarity_map.png"),
)
```

Colors the probe by each atom's contribution to its Morgan/Tanimoto similarity
to the reference: green raises the score, pink lowers it. It explains a
similarity value only — nothing about either molecule alone — and depends on
the fingerprint, so name it in the caption.
