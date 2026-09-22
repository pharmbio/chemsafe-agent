# Cheminformatics reference tables

Consulted-as-needed lookups for the cheminformatics skill. Read the relevant
section when you need descriptor meanings or catalog provenance. Nothing here is
executable — call the helpers as shown in `SKILL.md`; this file only documents
what their outputs mean and where they come from.

## Contents

- [Physicochemical descriptor glossary](#physicochemical-descriptor-glossary)
- [Built-in FilterCatalog provenance](#built-in-filtercatalog-provenance)

---

## Physicochemical descriptor glossary

Keys returned by `compute_descriptors`, what each means, and the `woe_reasoning`
line of evidence it supports.

| Descriptor         | What it tells you                                                | Line of evidence it supports                     |
|--------------------|------------------------------------------------------------------|--------------------------------------------------|
| molecular_formula  | Atom inventory                                                   | Identity verification, mass-spec cross-check     |
| mw                 | Average molecular weight                                         | Physchem (6), read-across similarity             |
| exact_mass         | Monoisotopic mass                                                | MS-based identity verification                   |
| logp_crippen       | Octanol–water partition; proxy for bioaccumulation, permeability | Physchem (6), PBT screening, exposure/kinetics   |
| molar_refractivity | Polarizability proxy                                             | Reactivity / mechanistic context                 |
| tpsa               | Topological polar surface area; permeability proxy               | Physchem (6), ADME/kinetics                      |
| hbd / hba          | Hydrogen-bond donors / acceptors                                 | Physchem (6), permeability                       |
| rotatable_bonds    | Conformational flexibility                                       | Physchem (6), drug-likeness                      |
| aromatic_rings     | Aromatic content                                                 | Reactivity / mechanistic context                 |
| num_stereocenters  | Stereocenter count                                               | Identity completeness, QSAR stereo-dependence    |
| fraction_csp3      | Saturation fraction                                              | Structural complexity                            |
| formal_charge      | Species ionization as drawn                                      | Identity verification (should be 0 after standardization with `neutralize=True`) |
| qed_drug_likeness  | Quantitative Estimate of Drug-likeness (Bickerton et al. 2012)   | Drug-likeness context only — not hazard          |

---

## Built-in FilterCatalog provenance

Catalog keys accepted by `build_filter_catalog`, what each screens for, and the
authoritative citation to record alongside any hit.

| Catalog  | What it screens for                                                  | Primary reference                           |
|----------|----------------------------------------------------------------------|---------------------------------------------|
| `pains`  | Pan-assay interference compounds (frequent hitters in HTS)           | Baell & Holloway 2010, *J Med Chem* 53:2719 |
| `brenk`  | Unwanted functionality for drug design (reactive / toxic / unstable) | Brenk et al. 2008, *ChemMedChem* 3:435      |
| `nih`    | NIH annotated unwanted features                                      | NIH MLSMR / MLPCN                           |
| `zinc`   | ZINC15 drug-likeness filters                                         | Sterling & Irwin 2015                       |
| `chembl` | Structural filters from ChEMBL's curation workflow                   | ChEMBL                                      |

---
