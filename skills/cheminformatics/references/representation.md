# Representation

Getting a structure into RDKit, back out in another form, and knowing what you
got — a structure that parsed is not necessarily right. Helpers import from
`scripts.chem_identity`; 3D ones from `scripts.chem_geometry`.

Contents: Parsing · Converting · Checking what you got · Stereochemistry ·
Tautomers · Fragments and mixtures · Structure files (SDF) · 3D structures

## Parsing

```python
mol = parse_molecule(user_input)   # SMILES, InChI or molblock; format detected
if mol is None:
    print(f"Could not parse: {user_input!r}")   # record it; never substitute another input
```

- In a batch loop, `except ValueError` catches `StructureError`.
- **An InChIKey, name or CAS number is not a structure.** An InChIKey is a
  one-way hash (`parse_molecule` returns `None`, `to_mol` raises saying so), and
  RDKit does no identifier lookup: resolve it to a SMILES or InChI first.
- **SMARTS is a query.** `parse_smarts` compiles a pattern for matching; never
  report a SMARTS as a substance's structure.
- **Keep the original input string** beside the canonical form; every
  structural claim must trace back to what was supplied.

## Converting

```python
identity_record("CC(=O)Oc1ccccc1C(=O)O")    # also: canonical_smiles, to_inchi, to_inchikey, to_molblock
# {'input': 'CC(=O)Oc1ccccc1C(=O)O', 'input_format': 'smiles',
#  'canonical_smiles': 'CC(=O)Oc1ccccc1C(=O)O', 'flat_smiles': 'CC(=O)Oc1ccccc1C(=O)O',
#  'inchi': 'InChI=1S/C9H8O4/...', 'inchikey': 'BSYNRYMUTXBXSQ-UHFFFAOYSA-N',
#  'inchikey_skeleton': 'BSYNRYMUTXBXSQ', 'formula': 'C9H8O4',
#  'formal_charge': 0, 'num_fragments': 1}
```

- **InChIKey is the cross-database join key; canonical SMILES is the lossless
  working form** for fingerprints and every RDKit call. Canonical SMILES is
  RDKit-specific, not comparable across toolkits; standard InChIKeys are. InChI
  loses some organometallic and polymeric bonding; molblock/SDF keeps
  coordinates, atom blocks and per-record properties.
- **Compare `inchikey_skeleton` (first block) when you mean "same parent."** It
  drops the stereo, charge and isotope layers, so it matches stereoisomers and
  charge states of one parent — a salt only after desalting. The full key
  answers a stricter question than most lookups intend.
- **`canonical_smiles(..., isomeric=False)` deletes stereochemistry** — a
  deliberately stereo-blind answer, never the structure of record.

## Checking what you got

Run `validate_structure` before trusting any structure from a user, a file or a
search result. It never raises, and reports `parsed`, RDKit's `problems`, and
the flags `is_mixture`, `metal_atoms`, `formal_charge`, `has_isotopes`,
`has_atom_map_numbers`, `has_dummy_atoms`, `has_explicit_hs`,
`num_radical_electrons`, `unspecified_stereocenters`, `unspecified_double_bonds`.

```python
validate_structure("CO(C)C")["problems"]
# [{'type': 'AtomValenceException',
#   'message': 'Explicit valence for atom # 1 O, 3, is greater than permitted'}]
validate_structure("[Na+].CC(=O)[O-]")
# parsed=True, num_fragments=2, is_mixture=True, metal_atoms=['Na'], formal_charge=0
```

- **Read the flags before standardizing**: `metal_atoms` and `is_mixture` pick
  the row of the decision table in `standardization.md`.
- **`has_dummy_atoms` means a generic structure** (R-group or Markush sketch),
  not a substance: descriptors and fingerprints describe a fragment, and InChI
  generation fails.
- **A valence error is a data problem** — fix the input or record the failure.

Run `compare_structures(before, after)` after any rewrite (standardization, salt
stripping, neutralization, a reaction template):

```python
compare_structures("C[C@H](N)C(=O)O", "CC(N)C(=O)O")
# {'same_canonical_smiles': False, 'same_inchikey': False, 'same_skeleton': True,
#  'same_formula': True, 'charge_delta': 0, 'heavy_atom_delta': 0,
#  'fragment_delta': 0, 'stereocenters_lost': 1, 'a': {...}, 'b': {...}}
```

Non-zero `stereocenters_lost`, `charge_delta` or `fragment_delta` means a
different species: sometimes correct, always recorded.

## Stereochemistry

```python
stereo_summary("CC(N)C(=O)O")
# {'atom_stereocenters': [{'index': 1, 'symbol': 'C', 'type': 'Atom_Tetrahedral',
#                          'specified': False, 'descriptor': 'NoValue'}],
#  'bond_stereo': [], 'num_atom_stereocenters': 1, 'unspecified_atom_stereocenters': 1,
#  'num_bond_stereo': 0, 'unspecified_bond_stereo': 0}
enumerate_stereoisomers("CC(N)C(=O)O")   # ['C[C@@H](N)C(=O)O', 'C[C@H](N)C(=O)O']
```

- `unspecified_atom_stereocenters = n > 0`: the input covers up to 2**n
  stereoisomers — say which one the work is about, or that the result covers
  the set.
- **Enumerate rather than guess.** Output is capped at `max_isomers`;
  `try_embedding=True` drops combinations impossible in 3D, at one embedding per
  candidate.

## Tautomers

```python
enumerate_tautomers("Oc1ccccn1")
# {'input_smiles': 'Oc1ccccn1', 'canonical_tautomer': 'O=c1cccc[nH]1',
#  'tautomers': ['O=C1CC=CC=N1', 'O=c1cccc[nH]1', 'Oc1ccccn1'],
#  'count': 3, 'status': 'Completed', 'truncated': False}
```

- **Check `truncated`**: a `status` other than `Completed` means enumeration hit
  `max_tautomers`, so the list is a sample, not the set.
- **"Canonical" means reproducibly chosen, not predominant** — RDKit's
  rule-based score says nothing about the equilibrium in a given medium.
- **Don't collapse tautomers whose reactive chemistry differs** — a thione and
  its thiol behave differently toward metals and electrophiles.

## Fragments and mixtures

```python
split_fragments("C[N+](C)(C)CCO.[Cl-]")   # also: largest_fragment
# [{'smiles': 'C[N+](C)(C)CCO', 'formula': 'C5H14NO+', 'num_heavy_atoms': 7, 'formal_charge': 1},
#  {'smiles': '[Cl-]', 'formula': 'Cl-', 'num_heavy_atoms': 1, 'formal_charge': -1}]
```

- **Look at the components before removing any.** A `.`-separated SMILES may be
  a salt, hydrate, co-crystal or genuine multi-component substance; only a salt
  is safely reduced to one component.
- A multi-component fingerprint covers the whole set, so its similarity to one
  component is weak evidence.

## Structure files (SDF)

```python
mols = read_sdf("/path/to/library.sdf")
name, value = mols[0].GetProp("_Name"), mols[0].GetProp("some_property")

path = write_sdf(
    ["CC(=O)Oc1ccccc1C(=O)O", "CC(C)Cc1ccc(cc1)C(C)C(=O)O"],
    output_name=prepare_output_path("selection.sdf"),
    names=["aspirin", "ibuprofen"],
    properties=[{"source": "target"}, {"source": "analogue"}],
)
```

- **`read_sdf` skips records RDKit cannot parse** — compare `len(mols)` with the
  file's record count when completeness matters.
- `write_sdf` gives coordinate-less molecules 2D coordinates so the file opens
  anywhere; pass 3D molecules to keep their geometry.

## 3D structures

For shape or geometry questions, or a downstream tool that needs coordinates.

```python
confs = generate_conformers("CC(C)Cc1ccc(cc1)C(C)C(=O)O", num_confs=20, optimize="mmff")
confs.to_dict()
# {'smiles': 'CC(C)Cc1ccc(C(C)C(=O)O)cc1', 'num_conformers': 11, 'num_requested': 20,
#  'method': 'ETKDGv3', 'optimizer': 'mmff',
#  'energies': [(1, 23.79), (8, 23.79), (5, 23.79), ...], 'notes': []}
best = lowest_energy_conformer(confs)
shape = descriptors_3d(best)   # pmi1..3, npr1/npr2, asphericity, radius_of_gyration, ...
# conformer_rmsd(probe, reference) compares two geometries
```

- `num_conformers` below `num_confs` is normal: `prune_rms_thresh` discards
  near-duplicates.
- **Shape descriptors describe one geometry** of what, for a flexible molecule,
  is a distribution — quote the conformer they came from.
- **The same `seed` reproduces the same set**; another seed gives a different,
  equally valid one.
- **MMFF energies rank conformers of one molecule only** — not comparable
  across molecules, not thermodynamic quantities.
