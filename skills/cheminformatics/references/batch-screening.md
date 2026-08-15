# Batch screening reference

Pattern for running a compound list through Tier A and Tier B.

## Batch Screening Pattern

Use to compare a small compound library on a fixed set of columns — useful for triage, similarity-based shortlist evaluation, and intermediate dossier tables.

```python
import pandas as pd
from scripts.qsar_toolbox import calculate_chemical_safety

compounds = {
    "Aspirin":     "CC(=O)Oc1ccccc1C(=O)O",
    "Ibuprofen":   "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
    "Caffeine":    "Cn1cnc2c1c(=O)n(c(=O)n2C)C",
    "Paracetamol": "CC(=O)Nc1ccc(O)cc1",
    "DDT":         "Clc1ccc(C(c2ccc(Cl)cc2)C(Cl)(Cl)Cl)cc1",
    "TNT":         "Cc1c([N+](=O)[O-])cc([N+](=O)[O-])cc1[N+](=O)[O-]",
}

rows = []
for name, smi in compounds.items():
    try:
        r     = calculate_chemical_safety(smi)
        phys  = r["physicochemical"]
        ghs   = r["ghs_classification"]
        eco   = r["ecotoxicology"]
        admet = r.get("admet_toxicity", {})
        expl  = r["explosivity"]
        rows.append({
            "Name":           name,
            "Formula":        phys["molecular_formula"],
            "MW":             phys["mw"],
            "logP":           phys["logp_crippen"],
            "Signal word":    ghs["signal_word"],
            "H-codes":        " ".join(h["H_code"] for h in ghs["hazard_statements"]),
            "Fish LC50 mg/L": eco["fish_fathead_minnow"]["value_mg_L"],
            "BCF L/kg":       eco["bioconcentration"]["BCF_L_per_kg"],
            "Tm C":           r["melting_point"]["melting_point_C"],
            "Explosivity":    expl["explosivity_risk_level"],
            "AMES prob":      admet.get("AMES_mutagenicity_prob", "-"),
            "hERG prob":      admet.get("hERG_inhibition_prob", "-"),
            "LD50 mg/kg":     admet.get("LD50_oral_mg_per_kg", "-"),
        })
    except ValueError as e:
        rows.append({"Name": name, "Error": str(e)})

df_batch = pd.DataFrame(rows).set_index("Name")
```

Rules:

- **Catch `ValueError` per row** so a single unparseable SMILES does not abort the whole batch.
- **Mixing draft GHS columns with experimental columns is dangerous** — if the table will be reviewed by a non-cheminformatician, prefix the predicted columns with `(predicted)` so a reader cannot mistake a draft for an authoritative classification.
- **For large libraries (hundreds+),** vectorize the admet-ai call by passing a SMILES list directly to `ADMETModel.predict` rather than calling `calculate_chemical_safety` per row — the orchestrator's per-call loading is acceptable for triage but inefficient at scale.

---

