---
name: cheminformatics
description: Operates on chemical structures with RDKit — parsing and interconverting SMILES, InChI, InChIKey, molblock and SDF; validating a structure and inventorying its stereochemistry, tautomers and fragments; standardizing, desalting and neutralizing it to a canonical identity; physicochemical descriptors, functional groups and property filters; SMARTS and built-in filter-catalog matching; fingerprint similarity, scaffolds, maximum common substructure and clustering; reaction templates; 3D conformers; structure figures. Use whenever a task involves a molecular structure and the answer is computed from the structure itself.
---

# Cheminformatics

Everything here is computed from the molecular graph with RDKit and is
reproducible: the canonical SMILES and InChIKey other work joins on, descriptor
tables, the substructure matches behind a structural argument, structure
figures. Use the capabilities the task needs, in the order it implies — except
that identity comparison, fingerprints, similarity and matching always run on
inputs standardized with the same settings on every side (a salt against its
parent, or two drawings of one tautomer, gives silently wrong numbers).

## Helpers

Import each from the module that owns it (there is no aggregate module), e.g.
`from scripts.chem_standardize import standardize_smiles`. `chem_common` holds
`to_mol` (input coercion) and `StructureError` (a `ValueError`). Prefer the
helpers; drop to raw RDKit only for what they do not expose.

**Failure contract:**

- `parse_*` returns `None` on unparseable input and `validate_structure`
  explains why; always check the result.
- Every other helper raises `StructureError` on input it cannot turn into a
  molecule, so a mistake stops where it happens instead of becoming an empty
  result that reads like an answer.
- Batch helpers (`standardize_molecules`, `describe_batch`, `screen_alerts`)
  never raise for a bad row: one record per input, with an `error` field.

**Files:** every writer (`write_sdf`, all `draw_*`) takes
`output_name=prepare_output_path("name.ext")` and returns the path; a relative
path raises.

## Capability index

Each reference has signatures, return shapes, examples and rules. Load the one
the step needs with `read_files("references/<name>.md")`.

| Capability | Module: helpers | Reference |
|---|---|---|
| Parse and interconvert | `chem_identity`: `parse_molecule`, `parse_smiles`, `parse_smarts`, `canonical_smiles`, `to_inchi`, `to_inchikey`, `to_molblock`, `identity_record` | representation |
| Validate, inventory, compare | `chem_identity`: `validate_structure`, `stereo_summary`, `split_fragments`, `compare_structures` | representation |
| Stereoisomers, tautomers | `chem_identity`: `enumerate_stereoisomers`, `enumerate_tautomers` | representation |
| Structure files | `chem_identity`: `read_sdf`, `write_sdf` | representation |
| 3D conformers, shape | `chem_geometry`: `generate_conformers`, `lowest_energy_conformer`, `descriptors_3d`, `conformer_rmsd` | representation |
| Standardize, desalt, neutralize; parents | `chem_standardize`: `standardize_smiles`, `standardize_molecules`, `neutralize_charges`, `remove_salts`, `charge_parent`, `fragment_parent`, `canonical_tautomer` | standardization |
| Descriptors | `chem_descriptors`: `compute_descriptors`, `compute_all_descriptors`, `describe_batch`, `element_counts` | descriptors |
| Functional groups, property filters | `chem_descriptors`: `functional_groups`, `druglikeness_profile`, `lipinski_flags` | descriptors |
| Fingerprints, similarity | `chem_fingerprints`: `fingerprint`, `morgan_fingerprint`, `similarity_between`, `similarity`, `nearest_neighbors`, `explain_morgan_bits` | molecular-algorithms |
| Clustering, diversity | `chem_fingerprints`: `similarity_matrix`, `cluster_molecules`, `pick_diverse` | molecular-algorithms |
| SMARTS matching | `chem_substructure`: `has_substructure`, `find_substructure_matches`, `count_substructure_matches`, `filter_by_substructure` | molecular-algorithms |
| Filter catalogs | `chem_substructure`: `list_filter_catalogs`, `build_filter_catalog`, `find_structural_alerts`, `screen_alerts` | molecular-algorithms |
| Scaffolds, ring systems, MCS | `chem_substructure`: `murcko_scaffold_smiles`, `generic_scaffold_smiles`, `group_by_scaffold`, `ring_systems`, `maximum_common_substructure` | molecular-algorithms |
| Reactions, transformations | `chem_reactions`: `parse_reaction`, `reaction_info`, `run_reaction`, `apply_transform`, `enumerate_library`, `check_atom_balance`, `reaction_similarity` | reactions |
| Structure figures | `chem_drawing`: `draw_molecule`, `draw_molecules`, `draw_reaction`, `draw_similarity_map` | visualization |
| A compound list end to end | the batch helpers above | batch-screening |

## Before reporting any value

- **Don't standardize past the species you are reasoning about.** The default
  pipeline turns cisplatin into `[Pt+4]` and a ferrocyanide salt into `C#N`:
  check `validate_structure` for `metal_atoms` and `is_mixture` first and use
  the standardization decision table.
- **Don't strip stereochemistry to make something match** — enantiomers and E/Z
  isomers are different substances. After any rewrite, check
  `compare_structures(before, after)["stereocenters_lost"]`.
- **Don't treat an unspecified stereocenter as absent.** The input names a set
  of isomers; say which one the work is about.
- **Quote a similarity with its fingerprint and metric** ("Tanimoto 0.42 on
  ECFP4/2048", never "similarity 0.42"), and never compare values across
  fingerprint kinds, radii or sizes.
- **Check `timed_out` before reporting an MCS** — a timed-out search returns the
  best core so far, not the maximum.
- **A filter-catalog hit is only a substructure match**: a named pattern
  occurs, how often and where. Carry the entry's `reference` with it; no hit
  means only that no pattern in the chosen sets matched.
- **Don't hand-roll SMARTS a built-in catalog already provides**, or use a
  pattern encoding a published rule without citing its source.
- **A shared scaffold is not interchangeability** — the scaffold discards the
  peripheral groups that drive reactivity.
- **Drug-likeness filters describe library design only** — Lipinski, Veber,
  Egan, Ghose and QED measure oral-drug-like appearance.
- **Label calculated descriptors as calculated.** `logp_crippen` is an
  atom-contribution estimate; prefer a measured value where one exists.
- **Don't trust a reaction template until it has matched.** Check
  `reaction_info` for mapping errors and `result.reacted` before saying
  anything about products.
- **A generated conformer is one sampled geometry** from a stochastic
  embedding, not the structure; quote the method, force field, seed and
  conformer.
- **Record every failed input** — `parse_*` returning `None`, a raised
  `StructureError` and a batch row's `error` are findings, not rows to drop.
- **Report only values this skill computed**, from a call actually made on the
  exact structure named.
