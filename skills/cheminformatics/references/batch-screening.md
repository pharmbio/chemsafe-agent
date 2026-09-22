# Batch screening reference

Pattern for running a compound list through the deterministic structure
operations in one pass. Every column below is computed from the structure —
nothing here is a predicted endpoint. For predicted endpoints over a list, use
the `qsar_modelling` skill, whose endpoint functions take a list or a CSV path
directly.

## Batch Screening Pattern

Use to compare a small compound library on a fixed set of columns — useful for triage, similarity-based shortlist evaluation, and intermediate dossier tables.

```python
import pandas as pd
from scripts.cheminformatics import (
    standardize_smiles, compute_descriptors,
    build_filter_catalog, find_structural_alerts,
    murcko_scaffold_smiles,
)

compounds = {
    "Aspirin":     "CC(=O)Oc1ccccc1C(=O)O",
    "Ibuprofen":   "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
    "Caffeine":    "Cn1cnc2c1c(=O)n(c(=O)n2C)C",
    "Paracetamol": "CC(=O)Nc1ccc(O)cc1",
    "DDT":         "Clc1ccc(C(c2ccc(Cl)cc2)C(Cl)(Cl)Cl)cc1",
    "TNT":         "Cc1c([N+](=O)[O-])cc([N+](=O)[O-])cc1[N+](=O)[O-]",
}

catalog = build_filter_catalog(["pains", "brenk", "nih"])

rows = []
for name, smi in compounds.items():
    try:
        std = standardize_smiles(smi)
        if std.canonical_smiles is None:
            raise ValueError(f"unparseable SMILES: {smi!r}")
        desc  = compute_descriptors(std.canonical_smiles)
        hits  = find_structural_alerts(std.canonical_smiles, catalog=catalog)
        rows.append({
            "Name":         name,
            "Canonical":    std.canonical_smiles,
            "InChIKey":     std.inchikey,
            "Formula":      desc["molecular_formula"],
            "MW":           desc["mw"],
            "logP (est.)":  desc["logp_crippen"],
            "TPSA":         desc["tpsa"],
            "HBD/HBA":      f'{desc["hbd"]}/{desc["hba"]}',
            "Rot. bonds":   desc["rotatable_bonds"],
            "Stereocenters": desc["num_stereocenters"],
            "Scaffold":     murcko_scaffold_smiles(std.canonical_smiles),
            "Alert count":  len(hits),
            "Alerts":       "; ".join(sorted({h["alert"] for h in hits})) or "-",
        })
    except ValueError as e:
        rows.append({"Name": name, "Error": str(e)})

df_batch = pd.DataFrame(rows).set_index("Name")
```

Rules:

- **Catch `ValueError` per row** so a single unparseable SMILES does not abort the whole batch, and keep the failed rows in the table — a silently shorter table is a data-quality defect.
- **Standardize before the descriptors** if the table will be compared across compounds or joined to a database lookup; the canonical SMILES and InChIKey columns are the join keys.
- **`logP (est.)` is the Crippen estimate.** Label predicted or estimated columns as such — a reader must not mistake an RDKit estimate for a measured value.
- **An alert count is a triage signal, not a ranking.** Do not sort a hazard shortlist by it; alerts differ in mechanistic weight and the classification call lives in `woe_reasoning`.
- **To add predicted endpoints to a table like this**, run them through `qsar_modelling` separately and join on the canonical SMILES — those columns carry model error and an applicability-domain status that this table has no column for.

---
