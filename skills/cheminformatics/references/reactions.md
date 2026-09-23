# Reactions and transformations

A reaction here is a mapped SMARTS template (SMIRKS) applied to molecules; the
output is exactly as sound as the rule you supply. Helpers import from
`scripts.chem_reactions`.

Contents: Writing and checking a template · Running a reaction ·
Single-reactant transformations · Library enumeration · Checking a written
reaction · Comparing reactions

## Writing and checking a template

```python
AMIDE = "[C:1](=[O:2])[OH].[N!H0:3]>>[C:1](=[O:2])[N:3]"

reaction_info(AMIDE)        # parse_reaction(AMIDE) → ChemicalReaction, or None
# {'smarts': '[C:1](=[O:2])[O&H1].[N&!H0:3]>>[C:1](=[O:2])[N:3]',
#  'num_reactant_templates': 2, 'num_product_templates': 1,
#  'num_agent_templates': 0, 'num_warnings': 0, 'num_errors': 0,
#  'is_mapped': True, 'unmapped_product_atoms': 0}
```

- `num_errors > 0` means RDKit rejected the template; `is_mapped=False` or
  `unmapped_product_atoms > 0` is almost always a mapping mistake.
- **Reaction SMARTS ≠ reaction SMILES.** `AMIDE` is a template;
  `CC(=O)O.CO>>CC(=O)OC.O` is a specific written reaction and needs
  `use_smiles=True`. Parsing one as the other is the usual reason a template
  matches nothing.
- **Map every atom that survives.** Unmapped reactant atoms are deleted;
  unmapped product atoms are created from nothing.
- **Constrain the environment deliberately** (`[N!H0:3]` matches any N-H) — the
  template fires everywhere it matches.

## Running a reaction

```python
result = run_reaction(AMIDE, ["CC(=O)Oc1ccccc1C(=O)O", "CNC"])
result.to_dict()
# {'reaction_smarts': '[C:1](=[O:2])[O&H1].[N&!H0:3]>>[C:1](=[O:2])[N:3]',
#  'product_sets': [['CC(=O)Oc1ccccc1C(=O)N(C)C']],
#  'products': ['CC(=O)Oc1ccccc1C(=O)N(C)C'],
#  'num_raw_outcomes': 1, 'num_unsanitizable': 0, 'truncated': False, 'notes': []}
result.reacted       # False when the template matched nothing
```

- **Reactants go in as a list in template order**; a count mismatch raises
  rather than mis-pairing.
- **`reacted=False` means no match, not "no reaction."** Test the template
  against one reactant with `find_substructure_matches` before concluding
  anything about the chemistry.
- **`product_sets` keeps each outcome together** (one entry per product
  template, in order); `products` is flattened and deduplicated. A template
  that can fire at several sites returns one outcome per site.
- **Unsanitizable outcomes** (e.g. impossible valence) are counted in
  `num_unsanitizable` and noted, never silently dropped.
- **Atom maps are cleared from products** by default; `clear_atom_maps=False`
  keeps them to trace atoms through the transform.
- **`truncated=True` means the outcome list hit `max_products`.**

## Single-reactant transformations

```python
ESTER_HYDROLYSIS = "[C:1](=[O:2])O[c:3]>>[C:1](=[O:2])[OH].[c:3][OH]"

apply_transform("CC(=O)Oc1ccccc1C(=O)O", ESTER_HYDROLYSIS)
# ['CC(=O)O', 'O=C(O)c1ccccc1O']
apply_transform("CC(=O)Oc1ccc(OC(C)=O)cc1", ESTER_HYDROLYSIS, exhaustive=True)
# ['CC(=O)O', 'CC(=O)Oc1ccc(O)cc1', 'Oc1ccc(O)cc1']
```

- **`exhaustive=True` re-applies the rule to its own products** until nothing
  changes (capped by `max_products`) — e.g. removing every acetyl group.
  Without it, one pass fires at each matching site separately, one product set
  per site.
- **A template is a structural operation**: whether, and which, transformation
  occurs under given conditions is not something it establishes.

## Library enumeration

```python
enumerate_library(AMIDE, [["CC(=O)O", "CCC(=O)O"], ["CNC", "NCC"]])
# [{'reactants': ['CC(=O)O', 'CNC'],  'product': 'CC(=O)N(C)C'},
#  {'reactants': ['CC(=O)O', 'NCC'],  'product': 'CCNC(C)=O'},
#  {'reactants': ['CCC(=O)O', 'CNC'], 'product': 'CCC(=O)N(C)C'},
#  {'reactants': ['CCC(=O)O', 'NCC'], 'product': 'CCNC(=O)CC'}]
```

One row per matching reactant combination; non-matching combinations give no
row, and output is capped at `max_products`. The combination count is the
product of the list lengths — check it before enumerating.

## Checking a written reaction

```python
check_atom_balance("CC(=O)O.CO>>CC(=O)OC.O")["balanced"]        # True
check_atom_balance("CC(=O)O.CO>>CC(=O)OC")
# {'balanced': False, 'missing_from_products': {'H': 2, 'O': 1}, ...}
```

Takes a reaction SMILES (`reactants>agents>products`, agents optional) and
compares element counts and charge on both sides. It catches a missing
co-product or stoichiometry error — necessary, not sufficient, for a correct
reaction.

## Comparing reactions

`reaction_similarity(rxn_a, rxn_b)` (0–1) defaults to `kind="structural"`,
which fingerprints reactants and products together and so scores high for
similar substrates. `kind="difference"` fingerprints only what changed,
grouping by transformation type regardless of substrate — use it for "is this
the same kind of reaction?"
