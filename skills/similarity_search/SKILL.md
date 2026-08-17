---
name: similarity_search
description: Find structural analogues of a query SMILES among the 16,907 ECHA registered substances by Tanimoto similarity over Morgan fingerprints, writing each hit's name, CAS, EC number and ECHA overview link to a CSV. Use when the task needs source analogues for read-across or grouping, a category or structural neighbourhood around a substance.
---

# Similarity Search Skill (ECHA registered substances)

Screens one structure against a prebuilt index of ECHA registered substances and writes the closest
ones out with their regulatory identifiers. It is the *candidate-finding* step: it tells you which
registered substances are structurally near the query, not whether any of them is a defensible
analogue. 

## Helper

One entry point, and this is its whole signature:

```python
from scripts.similarity_search import similarity_search

similarity_search(query_smiles, k=10, output_name=prepare_output_path("similarity_hits.csv"))  # -> str
```

- `query_smiles` — a **list of SMILES**.
- `k` — hits per query. There is no similarity cut-off and no self-hit filter, so take a generous `k`
  and narrow the CSV afterwards.
- `output_name` — **must** be `prepare_output_path("<name>.csv")`; a bare filename raises. That
  helper is what scopes the file to this conversation. Name it after the query when running more than
  one search, or the second call overwrites the first.

It returns **only a message naming the file** — `"The results is available at <path>."`, followed by
`"Skipping [...] due to invalid."` when a SMILES could not be parsed. The hits themselves are in the
CSV, long-format, one row per (query, hit): `query_smiles`, `rank`, `similarity`, `Name`,
`EC Number`, `CAS Number`, overview link, `SMILES_Source`, `Canonical_SMILES`, `Isomeric_SMILES`.

Read what you need back with pandas in the same `python_executor` session, filter there, and carry
only the rows you will actually cite into the report:

```python
import pandas as pd
csv_path = prepare_output_path("aspirin_analogues.csv")
print(similarity_search(["CC(=O)Oc1ccccc1C(=O)O"], k=50, output_name=csv_path))
hits = pd.read_csv(csv_path)
hits = hits[(hits.similarity >= 0.7) & (hits.similarity < 1.0)]   # cut-off + drop the query itself
```

Do not introspect the helper: `inspect` is **not** an authorized import and importing it aborts the
whole cell. The signature above is complete; if you need more,
`read_files("similarity_search/scripts/similarity_search.py")`.

## What the index is

16,907 ECHA registered substances · Morgan fingerprints (radius 2, 2048 bits) of each substance's
`Canonical_SMILES` · SMILES resolved from PubChem, OPSIN, NCI or Wikidata (see `SMILES_Source`).
The first search loads ~70 MB and caches it; later searches in the session are cheap. Queries are
fingerprinted with the index's own generator — do not rebuild the fingerprints with other settings.

## Rules

1. **Standardize the query first.** `cheminformatics.standardize_smiles` — an unstandardized salt or
   charged form fingerprints differently and returns the wrong neighbourhood.
2. **Similarity is not read-across.** Tanimoto ≥ 0.85 alone justifies nothing; RAAF also requires
   mechanistic and metabolic similarity. Report the value, then argue the case in `woe_reasoning`.
3. **Expect the query itself at rank 1** with similarity 1.0 when it is registered, and drop it when
   selecting analogues. Duplicate `Canonical_SMILES` exist in the index (different registrations of
   the same structure), so near-duplicate hits are normal — ask for a larger `k` than you need.
4. **A hit is a candidate, not evidence.** The index carries identity only. Every hazard, threshold
   or endpoint value for a hit must be retrieved through `database_traversal` and, for
   safety-relevant thresholds, grounded via `sop_search` — otherwise flag it `⚠️ UNVERIFIED`.
5. **Mixtures and multi-component SMILES** (`.`-separated) fingerprint as one molecule, so their
   similarity scores are weak evidence. Check `Canonical_SMILES` on any hit before relying on it.
6. Report `similarity` as returned (Tanimoto on this fingerprint), with the `k` used — a similarity
   number is not interpretable without the fingerprint and cut-off that produced it.
