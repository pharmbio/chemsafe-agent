# Batch screening

A compound list through the structure operations in one pass, keeping the table
honest about what failed: standardize first, compute everything from the
canonical form, assemble one table.

## The pattern

```python
import pandas as pd
from scripts.chem_standardize import standardize_molecules
from scripts.chem_descriptors import describe_batch
from scripts.chem_substructure import (
    screen_alerts, build_filter_catalog, murcko_scaffold_smiles,
)

compounds = {
    "Aspirin":     "CC(=O)Oc1ccccc1C(=O)O",
    "Ibuprofen":   "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
    "Caffeine":    "Cn1cnc2c1c(=O)n(c(=O)n2C)C",
    "Paracetamol": "CC(=O)Nc1ccc(O)cc1",
    "DDT":         "Clc1ccc(C(c2ccc(Cl)cc2)C(Cl)(Cl)Cl)cc1",
    "TNT":         "Cc1c([N+](=O)[O-])cc([N+](=O)[O-])cc1[N+](=O)[O-]",
}

# 1. Standardize — one record per input, failures kept.
std = dict(zip(compounds, standardize_molecules(list(compounds.values()))))
failed = {name: record.notes for name, record in std.items() if not record.ok}

# 2. Descriptors and catalog hits, computed from the canonical form.
canonical = {name: record.canonical_smiles for name, record in std.items() if record.ok}
catalog = build_filter_catalog(["pains", "brenk", "nih"])       # build once, outside any loop
desc_rows = describe_batch(canonical)
alert_rows = {row["name"]: row for row in screen_alerts(canonical, catalog=catalog)}

# 3. One table.
table = []
for row in desc_rows:
    name = row["name"]
    alerts = alert_rows[name]
    table.append({
        "Name":             name,
        "Canonical SMILES": std[name].canonical_smiles,
        "InChIKey":         std[name].inchikey,
        "Formula":          row["molecular_formula"],
        "MW":               row["mw"],
        "logP (calc.)":     row["logp_crippen"],
        "TPSA":             row["tpsa"],
        "HBD/HBA":          f'{row["hbd"]}/{row["hba"]}',
        "Rot. bonds":       row["rotatable_bonds"],
        "Stereocenters":    row["num_stereocenters"],
        "Scaffold":         murcko_scaffold_smiles(std[name].canonical_smiles),
        "Alert count":      alerts["num_alerts"],
        "Alerts":           "; ".join(alerts["alerts"]) or "-",
        "Error":            row["error"] or alerts["error"],
    })

df = pd.DataFrame(table).set_index("Name")
print(f"{len(df)} of {len(compounds)} standardized; failures: {failed}")
```

- **Report failures alongside the table** (`error` fields and `notes`), and
  check the row count against the input count before writing it out.
- **The canonical SMILES and InChIKey are the join keys** for every later table.
- **An alert count is a triage signal, not a ranking.** Don't sort by it: hits
  mean different things, and one catalog's hit is not comparable to another's.
- **Reuse the standardized structures** rather than re-standardizing per
  column.

## Grouping a list

For a set that needs organizing rather than tabulating:

```python
from scripts.chem_fingerprints import cluster_molecules, pick_diverse
from scripts.chem_substructure import group_by_scaffold

structures = list(canonical.values())
groups   = group_by_scaffold(structures)              # exact, by Murcko scaffold
clusters = cluster_molecules(structures, cutoff=0.6)  # fingerprint distance, centroid first
subset   = pick_diverse(structures, 3)                # indices spread across the set
```

Scaffold grouping is exact and interpretable; clustering depends on the
fingerprint and cutoff. Say which produced a grouping, with what settings
(details in `molecular-algorithms.md`).
