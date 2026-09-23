# Standardization and neutralization

Standardization collapses salt/parent, charged/neutral and tautomeric variants
onto one representation — correct only when the variant is not what you are
reasoning about. So every stage is switchable and every run records what it
did. Helpers import from `scripts.chem_standardize`.

Contents: The pipeline · Decision table · Worked examples · Single-purpose
helpers · Standardizing a list

## The pipeline

```python
std = standardize_smiles("C[N+](C)(C)CCO.[Cl-]")   # choline chloride
std.to_dict()
# {'input_smiles': 'C[N+](C)(C)CCO.[Cl-]', 'canonical_smiles': 'C[N+](C)(C)CCO',
#  'inchi': 'InChI=1S/C5H14NO/c1-6(2,3)4-5-7/h7H,4-5H2,1-3H3/q+1',
#  'inchikey': 'OEYIOHPDSNJKLS-UHFFFAOYSA-N',
#  'removed_salts': True, 'neutralized': False, 'tautomer_canonicalized': False,
#  'notes': [], 'steps': ['remove_explicit_hs', 'disconnect_metals', 'normalize',
#   'reionize', 'assign_stereochemistry', 'strip_salts*', 'keep_largest_fragment',
#   'neutralize', 'canonical_tautomer'],
#  'formula': 'C5H14NO+', 'formal_charge': 1, 'num_fragments': 1, 'changed': True}
```

Stages run in this order, each a keyword argument; `*` in `steps` marks a stage
that changed the structure.

| Stage | Keyword | Does |
|---|---|---|
| cleanup | `cleanup=True` | removes explicit Hs, disconnects metal–ligand bonds (`disconnect_metals=True`), normalizes group drawing (nitro, N-oxide, azide…), reionizes, re-perceives stereo |
| desalt | `strip_salts=True` | drops counter-ions in RDKit's salt table, then keeps the largest remaining component (`keep_largest_fragment`, which follows `strip_salts`; set `False` to keep every component while still removing listed salts) |
| neutralize | `neutralize=True` | protonates/deprotonates atoms whose charge no counter-charge balances |
| tautomer | `canonical_tautomer=True` | picks RDKit's canonical tautomer, keeping stereo |

`standardize_smiles` never raises: unparseable input returns
`canonical_smiles=None` with the reason in `notes`.

## Decision table

| Situation | cleanup | disconnect_metals | strip_salts | neutralize | canonical_tautomer |
|---|---|---|---|---|---|
| Cross-database lookup key (InChIKey) | yes | yes | yes | yes | yes |
| Analogue selection / grouping | yes | yes | yes | yes | **case by case** |
| Permanently charged organic (quaternary ammonium, ionic liquid) | yes | yes | **no** if the counter-ion is part of the substance | yes (no-op on the permanent charge) | yes |
| Metal complex / organometallic | yes | **no** | **no** | **no** | **no** |
| Multi-component substance (UVCB, co-crystal, genuine mixture) | yes | yes | **no** (`keep_largest_fragment=False`) | case by case | no |
| Reaction or transformation work | yes | yes | yes | yes | **no** — keep the form the template was written against |

- **Record the flags used and why** — the canonical form is reproducible only
  if the settings are in the record (`steps` lists them in execution order).
- **Check what changed** with `compare_structures(input, std.canonical_smiles)`:
  non-zero `charge_delta`, `fragment_delta` or `stereocenters_lost` means a
  different species, which must be deliberate.
- Half a comparison standardized is worse than neither.

## Worked examples

- **Salt whose cation is the substance.** Choline chloride → `C[N+](C)(C)CCO`
  (+1): chloride is a listed salt, and neutralization is correctly a no-op on
  the permanently charged quaternary N.
- **Salt whose parent is the substance.** Sodium benzoate
  (`[Na]OC(=O)c1ccccc1`) → benzoic acid (`O=C(O)c1ccccc1`): cleanup ionizes
  Na–O, desalting removes Na, neutralization protonates the carboxylate.
- **Metal complex with defaults — the failure case.** Cisplatin
  (`[Pt](Cl)(Cl)(N)N`) → `[Pt+4]`: the disconnector breaks Pt–N and Pt–Cl, and
  the largest-fragment step keeps the bare ion. Potassium ferrocyanide as
  separate ions (`[K+].[K+].[K+].[K+].[Fe+2].[C-]#N...`) ends up as `C#N`.
  Neither is the substance. When `validate_structure` lists `metal_atoms`, use
  the organometallic row:

  ```python
  std = standardize_smiles(
      "[Pt](Cl)(Cl)(N)N",
      disconnect_metals=False, strip_salts=False,
      neutralize=False, canonical_tautomer=False,
  )
  std.canonical_smiles      # '[NH2][Pt]([NH2])([Cl])[Cl]'
  ```

- **Stereochemistry survives every stage**, tautomer canonicalization included.
  `standardize_smiles("C[C@H](N)C(=O)O")` returns `C[C@H](N)C(=O)O`, InChIKey
  `QNAYBMKLOCPYGJ-REOHCLBHSA-N`. RDKit's default tautomer settings would drop
  that stereocenter (it sits next to a tautomerizable carboxyl); this pipeline
  turns that off (`tautomer_parameters`).

## Single-purpose helpers

For one operation instead of the whole pipeline.

```python
neutralize_charges("[O-]C(=O)C[NH3+]")                      # 'NCC(=O)O'
neutralize_charges("CC(=O)[O-].[Na+]", method="pattern")    # 'CC(=O)O.[Na+]'
remove_salts("C[N+](C)(C)CCO.[Cl-]")
# {'smiles': 'C[N+](C)(C)CCO', 'removed': True,
#  'num_fragments_before': 2, 'num_fragments_after': 1}
fragment_parent("CC(=O)[O-].[Na+]")     # 'CC(=O)[O-]'  largest component, charge kept
charge_parent("CC(=O)[O-].[Na+]")       # 'CC(=O)O'     largest component, neutralized
tautomer_parent("Oc1ccccn1")            # 'O=c1cccc[nH]1'   (also: canonical_tautomer)
```

- **`charge_parent` is the salt→parent key** — it matches a sodium,
  hydrochloride or mesylate record to the parent substance.
- **Neutralization methods differ.** `uncharger` (default) leaves a charge a
  counter-ion balances, so sodium acetate stays a salt; `pattern` (RDKit
  cookbook SMARTS) neutralizes the organic ion and leaves the counter-ion as its
  own fragment. Neither touches a permanent charge such as quaternary ammonium.
- **Custom salt list:** `remove_salts(..., salts="[Cl,Br,I]\n[Na,K]")` replaces
  RDKit's 15-entry default table when a specific counter-ion must go or stay.

## Standardizing a list

```python
results = standardize_molecules(["CCO", "C[N+](C)(C)CCO.[Cl-]", "bogus!!"])
len(results)                                  # 3 — one record per input, always
[r.canonical_smiles for r in results]         # ['CCO', 'C[N+](C)(C)CCO', None]
failed = [r for r in results if not r.ok]     # keep these in the table
```

- Report failures with their `notes` — a silently shorter result set is a
  defect.
- **Standardize once, reuse the result** — every later step (descriptors,
  fingerprints, matching) consumes `canonical_smiles`, not the raw input.
