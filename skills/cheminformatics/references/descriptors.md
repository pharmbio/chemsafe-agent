# Molecular descriptors

Numbers computed from the graph for the exact species passed in. Standardization
is not needed to compute but is needed to compare — a salt and its parent
differ on every size-dependent key. Helpers import from
`scripts.chem_descriptors`.

## The core descriptor block

```python
compute_descriptors("CC(=O)Oc1ccccc1C(=O)O")
# {'molecular_formula': 'C9H8O4', 'mw': 180.159, 'exact_mass': 180.0423,
#  'heavy_atoms': 13, 'logp_crippen': 1.3101, 'tpsa': 63.6, 'hbd': 1, 'hba': 3, ...,
#  'qed_drug_likeness': 0.5501}
compute_descriptors(smiles, include=["mw", "logp_crippen", "tpsa"])   # a subset
```

The key set is fixed (`CORE_DESCRIPTOR_NAMES`), so a batch builds a rectangular
table; a value RDKit cannot compute is `None`, not dropped:

- identity and size: `molecular_formula` (Hill notation, with net charge),
  `mw`, `exact_mass`, `heavy_atoms`, `num_fragments`, `formal_charge` (as drawn)
- calculated physchem: `logp_crippen` and `molar_refractivity`
  (Wildman–Crippen), `tpsa` (Ertl), `labute_asa`
- H-bonding and flexibility: `hbd`, `hba`, `rotatable_bonds`
- rings: `rings`, `aromatic_rings`, `aliphatic_rings`, `saturated_rings`,
  `spiro_atoms`, `bridgehead_atoms`
- composition: `num_heteroatoms`, `num_halogens`, `num_nitrogen`,
  `num_oxygen`, `num_sulfur`, `num_phosphorus`, `fraction_csp3`, `amide_bonds`
- stereo: `num_stereocenters`, `num_unspecified_stereocenters`
- other: `radical_electrons` (a non-zero value is usually a drawing error),
  `bertz_complexity` (Bertz CT), `qed_drug_likeness` (0–1, Bickerton 2012;
  medicinal-chemistry context only)

Rules:

- **`exact_mass` is monoisotopic; `mw` is abundance-averaged.** The gap (0.15 Da
  for aspirin) is the isotope pattern, not an error — use exact mass for
  anything mass-spectrometric.
- **`num_stereocenters` counts stereogenic atoms whether or not specified**;
  `num_unspecified_stereocenters` is the one saying the input does not name a
  single substance. Both match `stereo_summary`.

## The full RDKit set

`compute_all_descriptors(smiles)` returns ~200 values, for a downstream model
needing a fixed vector. Many are strongly collinear, and some (`Ipc`, parts of
`Chi`/`Kappa`) go numerically extreme for large molecules — read them as model
features, not chemistry.

## Functional groups and composition

```python
functional_groups("Clc1ccc(C(c2ccc(Cl)cc2)C(Cl)(Cl)Cl)cc1")   # {'alkyl_halide': 3, 'benzene': 2, 'halogen': 5}
element_counts("Clc1ccc(C(c2ccc(Cl)cc2)C(Cl)(Cl)Cl)cc1")      # {'C': 14, 'Cl': 5, 'H': 9}
```

`functional_groups` counts RDKit's 85 named SMARTS group definitions on the
molecule as drawn and returns only those present (`include_zero=True` returns
all, for a fixed-width table). It is an inventory, nothing more.

## Property filters

```python
druglikeness_profile("COc1ccc2[nH]c(S(=O)Cc3ncc(C)c(OC)c3C)nc2c1")   # omeprazole
# {'lipinski_violations': 0, 'lipinski_flags': {...}, 'lipinski_pass': True,
#  'veber_pass': True, 'egan_pass': True, 'ghose_pass': True, 'qed': 0.769,
#  'descriptors': {...}}
```

| Filter | Criteria | Source |
|---|---|---|
| Lipinski Rule of Five | MW ≤ 500, clogP ≤ 5, HBD ≤ 5, HBA ≤ 10; passes with ≤ 1 violation | Lipinski et al. 1997, *Adv Drug Deliv Rev* 23:3 |
| Veber | rotatable bonds ≤ 10 and TPSA ≤ 140 Å² | Veber et al. 2002, *J Med Chem* 45:2615 |
| Egan | TPSA ≤ 131.6 Å² and clogP ≤ 5.88 | Egan et al. 2000, *J Med Chem* 43:3867 |
| Ghose | 160 ≤ MW ≤ 480, −0.4 ≤ clogP ≤ 5.6, 40 ≤ MR ≤ 130, 20 ≤ heavy atoms ≤ 70 | Ghose et al. 1999, *J Comb Chem* 1:55 |
| QED | weighted desirability over eight properties, 0–1 | Bickerton et al. 2012, *Nat Chem* 4:90 |

- **A failed filter is not a defect.** Many important substances fail all five,
  as do most agrochemicals, solvents and industrial intermediates.
- **Report the criteria with the verdict** — "Lipinski pass" depends on the
  ≤ 1 violation convention `lipinski_pass` applies and `lipinski_violations`
  exposes.

## Descriptors for a list

```python
rows = describe_batch({"aspirin": "CC(=O)Oc1ccccc1C(=O)O", "broken": "xx!!"})
# [{'name': 'aspirin', 'input_smiles': '...', 'error': None, 'mw': 180.159, ...},
#  {'name': 'broken', 'input_smiles': 'xx!!', 'error': "input could not be parsed
#    as smiles: 'xx!!'", 'mw': None, ...}]
df = pd.DataFrame(rows).set_index("name")
```

Accepts a list of SMILES or a `{name: smiles}` mapping. The full screening
pattern is in `batch-screening.md`.
