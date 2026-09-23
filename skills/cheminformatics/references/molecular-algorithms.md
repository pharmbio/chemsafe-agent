# Molecular algorithms

Fingerprints and similarity, substructure matching, scaffolds, MCS, clustering
and diversity. Each answers a precisely defined question about the graph;
interpretation is always a separate step. Fingerprint helpers import from
`scripts.chem_fingerprints`, the rest from `scripts.chem_substructure`.

Contents: Fingerprints · Similarity · Nearest neighbors · Clustering and
diversity · Explaining a similarity score · SMARTS matching · Filter catalogs ·
Scaffolds and ring systems · Maximum common substructure

## Fingerprints

```python
fp = morgan_fingerprint(smiles)                       # radius 2, 2048 bits
fp = fingerprint(smiles, "maccs")                     # 166 keys in a 167-bit vector
fp = fingerprint(smiles, "morgan", radius=3, n_bits=4096)
fp = fingerprint(smiles, "morgan", counts=True)       # count vector
```

| Kind | Use when |
|---|---|
| `morgan` (default; radius 2 ≈ ECFP4, 3 ≈ ECFP6) | general similarity, neighbor search, clustering |
| `fcfp` (Morgan over pharmacophoric atom types) | bioisosteres should score close |
| `rdkit` (path-based, Daylight-like) | linker and topology differences matter |
| `atom_pair` | large, flexible molecules |
| `topological_torsion` | backbone/torsion similarity |
| `maccs` (166 curated keys) | coarse, interpretable functional-content comparison |
| `pattern` | pre-filtering substructure searches only |

- **Defaults are Morgan, radius 2, 2048 bits, Tanimoto** — the same as the
  prebuilt structure index, so values compare across this codebase. Change them
  deliberately and say so when reporting.
- **`use_chirality=True` distinguishes enantiomers.** Off by default, as in most
  published work; turn it on when stereochemistry is the point.
- **Keep count fingerprints out of similarity calls calibrated on bit vectors.**

## Similarity

```python
similarity_between(aspirin, ibuprofen)                      # 0.195  (ECFP4/Tanimoto)
similarity_between(aspirin, ibuprofen, kind="maccs")        # 0.385  (MACCS/Tanimoto)
similarity_between(aspirin, ibuprofen, metric="dice")       # 0.327
similarity(fp_a, fp_b, "tversky", alpha=0.9, beta=0.1)      # asymmetric; also tanimoto(fp_a, fp_b)
```

Metrics: `tanimoto` (default), `dice`, `cosine`, `sokal`, `russel`,
`kulczynski`, `mcconnaughey`, `braun_blanquet`, and `tversky` with
`alpha`/`beta` for asymmetric "is A contained in B" questions.

Conventional reading of Tanimoto on ECFP4/2048 — calibration points, not
thresholds: ≥ 0.85 very close analogues, usually the same series · 0.75–0.85
close analogues · 0.60–0.75 moderately similar, shared scaffold or large shared
fragment · 0.40–0.60 distant, a common substructure at most · < 0.40
structurally unrelated by this fingerprint.

- **The bands shift with the fingerprint** (MACCS runs higher, atom-pair lower
  for the same pair); recalibrate rather than transferring a cut-off.
- **Similarity is structural only** — it says nothing about shared mechanism,
  metabolism or activity; those are separate lines of evidence.

## Nearest neighbors

```python
nearest_neighbors(naproxen, [aspirin, ibuprofen, caffeine], k=3)
# [('CC(C)Cc1ccc(cc1)C(C)C(=O)O', 0.421), ('CC(=O)Oc1ccccc1C(=O)O', 0.205),
#  ('Cn1cnc2c1c(=O)n(c(=O)n2C)C', 0.08)]
```

- **Unparseable candidates are skipped** — compare result length with pool size
  when completeness matters; `skip_invalid=False` raises instead.
- **`min_similarity=` filters before truncation**, so "the top 5" need not
  include unrelated structures.
- **Report the values, not just the rank** — rank 1 at 0.31 and at 0.91 are
  different findings.

## Clustering and diversity

```python
matrix = similarity_matrix([aspirin, ibuprofen, naproxen])     # numpy array, diagonal 1.0
cluster_molecules([aspirin, ibuprofen, naproxen, caffeine], cutoff=0.6)
# [[2, 1], [3], [0]]   index lists, centroid first, largest cluster first
pick_diverse([aspirin, ibuprofen, naproxen, caffeine], 2)      # indices, MaxMin
```

- **`cutoff` is a distance** (1 − similarity): 0.6 groups everything above
  Tanimoto 0.4. Butina is deterministic and non-hierarchical; the cutoff sets
  the cluster count.
- **The first index is the centroid** — the representative when one member
  must stand for the group.
- **`pick_diverse` spreads a selection across structural space** (the opposite
  of a similarity ranking); deterministic for a given `seed`.
- **Plot `similarity_matrix` as a heatmap via the `data_visualization` skill**,
  not with inline styling here.

## Explaining a similarity score

```python
explain_morgan_bits("CC(=O)Oc1ccccc1C(=O)O")
# {389: [{'center_atom': 12, 'radius': 1, 'smiles': 'CO'}],
#  456: [{'center_atom': 10, 'radius': 1, 'smiles': 'cC(=O)O'}], ...}
```

Maps each set bit to the atom environment that set it, so a similarity can be
reported as *which* substructures are shared rather than a bare number. As a
picture: `draw_similarity_map` (`visualization.md`).

## SMARTS matching

```python
has_substructure("Nc1ccccc1", "[NX3;H2;!$(NC=O)]-c")        # True
find_substructure_matches(tnt, "[N+](=O)[O-]")              # [(3, 4, 5), (8, 9, 10), (13, 14, 15)]
count_substructure_matches(tnt, "[N+](=O)[O-]")             # 3
filter_by_substructure(library, "c1ccccc1", exclude=True)   # drop aromatics
```

- **A SMARTS that does not compile raises** — no silent "no match". Test a
  pattern on a molecule known to contain the group before screening a set.
- **Matches are atom-index tuples**, ready for highlighting in `draw_molecule`.
- **`uniquify=True` (default) collapses symmetry-equivalent mappings**; turn it
  off only when each mapping matters (benzene matches a ring query 12 times
  ununiquified).
- **Write the query against the standardized form** — a neutral-amine pattern
  won't match the protonated drawing, and vice versa.

## Filter catalogs

Each RDKit catalog entry carries its own literature citation, returned with
every hit.

```python
catalog = build_filter_catalog(["pains", "brenk", "nih"])    # build once, reuse
find_structural_alerts(ddt, catalog)
# [{'catalog': 'Brenk', 'alert': 'alkyl_halide',
#   'scope': 'unwanted functionality due to potential tox reasons or unfavourable
#             pharmacokinetic properties',
#   'reference': 'Brenk R et al. ... ChemMedChem 3 (2008) 435-444. doi:10.1002/cmdc.200700139.',
#   'smarts': '[C&X4][Cl,Br,I]', 'num_matches': 3, 'atom_indices': [13, 14, 15, 16]}]
```

| Catalog key (`list_filter_catalogs()`) | Collects | Primary reference |
|---|---|---|
| `pains`, `pains_a`, `pains_b`, `pains_c` | pan-assay interference (HTS frequent hitters) | Baell & Holloway 2010, *J Med Chem* 53:2719 |
| `brenk` | functionality unwanted in screening libraries (reactive, unstable, tox-associated) | Brenk et al. 2008, *ChemMedChem* 3:435 |
| `nih` | NIH-annotated unwanted features | NIH MLSMR / MLPCN |
| `zinc` | ZINC15 property and functionality filters | Sterling & Irwin 2015, *J Chem Inf Model* 55:2324 |
| `chembl`, `chembl_*` (`dundee`, `bms`, `surechembl`, `mlsmr`, `inpharmatica`, `lint`, `glaxo`) | ChEMBL curation sets | ChEMBL / the named originator |
| `all` | every built-in set (1,585 entries) | — |

- **Name which set fired** — catalogs differ in purpose: PAINS is assay
  interference, Brenk library design, ZINC screening properties.
- **Build the catalog once** and pass it to every call; construction parses
  hundreds of SMARTS. `screen_alerts` does this for a whole list and keeps
  failing rows.

## Scaffolds and ring systems

```python
murcko_scaffold_smiles(naproxen)          # 'c1ccc2ccccc2c1'
generic_scaffold_smiles(naproxen)         # 'C1CCC2CCCCC2C1'   atom types erased
group_by_scaffold([aspirin, ibuprofen, naproxen])
# {'c1ccccc1': ['CC(=O)Oc1ccccc1C(=O)O', 'CC(C)Cc1ccc(cc1)C(C)C(=O)O'],
#  'c1ccc2ccccc2c1': ['COc1ccc2cc(ccc2c1)C(C)C(=O)O']}
ring_systems("c1ccc2ccccc2c1C1CC1")       # ['C1CC1', 'c1ccc2ccccc2c1']
```

- **An acyclic molecule's Murcko scaffold is `""`**; `group_by_scaffold`
  collects those under the `""` key.
- **`generic_scaffold_smiles` erases element identity and bond order** —
  useful for topology-level grouping, misleading as a chemical statement.

## Maximum common substructure

```python
mcs = maximum_common_substructure([aspirin, ibuprofen, naproxen])
mcs.to_dict()
# {'smarts': '[#6]1:&@[#6]:&@[#6]:&@[#6]:&@[#6]:&@[#6]:&@1-&!@[#6&!R]',
#  'num_atoms': 7, 'num_bonds': 7, 'timed_out': False,
#  'num_molecules': 3, 'threshold': 1.0}
```

- **Defaults keep rings whole** (`complete_rings_only=True`) and never map a
  ring atom onto a chain atom (`ring_matches_ring_only=True`), so the result
  reads as a chemical class. Relaxing them finds larger, less meaningful cores.
- **`threshold=0.8` relaxes "common to all" to "common to 80%"** — keeps one
  outlier from collapsing the MCS to nothing.
- **`atom_compare="any_heavy"` ignores element identity** to find topological
  cores across heteroatom patterns. Say which comparison produced the core; the
  SMARTS alone does not show it.
- **The MCS SMARTS is a query**, ready for `find_substructure_matches`,
  highlighting, or aligning a grid with `draw_molecules(align_smarts=...)`.
