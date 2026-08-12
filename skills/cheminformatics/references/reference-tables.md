# Cheminformatics reference tables

Consulted-as-needed lookups for the cheminformatics skill. Read the relevant
section when you need descriptor meanings, catalog provenance, ADMET output
scales, or ecotox model citations. Nothing here is executable — call the helpers
as shown in `SKILL.md`; this file only documents what their outputs mean and
where they come from.

## Contents

- [Physicochemical descriptor glossary](#physicochemical-descriptor-glossary)
- [Built-in FilterCatalog provenance](#built-in-filtercatalog-provenance)
- [ADMET output scale conventions](#admet-output-scale-conventions)
- [Ecotoxicology model references](#ecotoxicology-model-references)

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

## ADMET output scale conventions

Units and scales returned by `calc_admet` (admet-ai / Chemprop MPNN, TDC-trained).

| Suffix / key                  | Scale                                                              |
|-------------------------------|-------------------------------------------------------------------|
| `*_prob`                      | probability in [0, 1] (binary classifier)                         |
| `LD50_oral_log_mg_per_kg`     | log10(mg/kg); helper also exposes `LD50_oral_mg_per_kg = 10**value` |
| `caco2_permeability_log_cm_s` | log10(cm/s)                                                       |
| `solubility_log_mol_L`        | log10(mol/L)                                                      |
| `VDss_log_L_per_kg`           | log10(L/kg)                                                       |

Reference: Swanson et al. 2023 — github.com/swansonk14/admet_ai.

---

## Ecotoxicology model references

Baseline-narcosis QSARs behind `calc_ecotoxicology`. Valid for non-ionic organics
with logP ~0–7 and MW ~50–500; reactive, electrophilic, or ionisable compounds
may deviate >1 log unit — flag explicitly.

| Endpoint                        | Model reference                          |
|---------------------------------|------------------------------------------|
| Fish (Fathead minnow 96-h LC50) | Veith et al. 1983 (baseline narcosis)    |
| Daphnia magna 48-h EC50         | Cronin & Dearden 1995 (baseline narcosis)|
| Green algae 72-h EC50           | Netzeva et al. 2005 (simplified)         |
| BCF                             | Meylan et al. 1999                       |
