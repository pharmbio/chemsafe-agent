---
name: woe_reasoning
description: Weigh already-gathered evidence into a defensible conclusion — score each source for quality and reliability, rank sources by authority for the question actually asked, surface contradictions between them, and emit a structured weight-of-evidence report carrying an explicit confidence level. Use once retrieval or prediction has produced evidence from more than one source, or whenever a chemical safety conclusion needs a stated confidence, even if the user never says weight of evidence.
---

# WoE SKILL: Weight of Evidence for Chemical Safety Agents

## ROLE
Triggered automatically after any documentary search over chemical safety databases. Execute all five blocks sequentially; **never skip a block, even when evidence is absent or partial.**

Two bundled references are loaded on demand:
- `references/alerts.md`: canonical text for ALERT-01…05. Consult when emitting an alert.
- `references/conditional-cases.md`: full handling for Cases A–I. Consult when a run matches anything beyond Case A.

## BLOCK 1: Source Inspection & Extraction

**1.1 Retrieve** from the search: documents/databases consulted; the relevant fragments/values/statements; the substance under evaluation (**Compound X**).

**1.2 Classify query type** → store as `QUERY_TYPE` (a query may have multiple types):

| Type | Indicators |
|---|---|
| Regulatory | legal limit, classification, GHS, REACH, banned, permitted, exposure limit |
| Toxicological | LD50, LC50, NOAEL, LOAEL, carcinogen, mutagen, toxic dose, mechanism of toxicity |
| Phytochemical | plant origin, natural compound, secondary metabolite, alkaloid, flavonoid |
| Drug-like | bioavailability, drug-likeness, pharmacokinetics |
| Environmental | ecotoxicity, aquatic toxicity, bioaccumulation, soil residue, PNEC |
| Safety/Hazard | SDS, flash point, flammability, explosive, corrosive, PPE, handling |

**1.3 Classify each retrieved item** → `EVIDENCE_TYPE`:
- **Q (Quantitative):** numeric/measured/modelled values (LD50, NOAEL, EC50, flash point, OEL).
- **N (Normative):** qualitative guidance/procedures/classifications without a numeric value (SOPs, handling guidelines, GHS class without a numeric threshold, policy documents).

For each **Q** item also record `MODEL_TYPE`: **Q-vivo** (animal studies, human epidemiology), **Q-vitro** (cell assays, Ames, enzyme inhibition), **Q-silico** (QSAR/read-across predictions), or **Q-unknown** if undeterminable. This does not affect priority (2.1) but interprets agreement/variability in Blocks 3–4.

**1.4 No documents retrieved** → `EVIDENCE_FOUND = FALSE`, `C = 0`, emit **ALERT-01**; **skip Blocks 2–3, go to Block 4**. Otherwise `EVIDENCE_FOUND = TRUE`, continue.

## BLOCK 2: Categorization

**2.1 Source priority** (assign per source):

| Priority | Level | Description |
|---|---|---|
| 5 | International | WHO, ECHA, NIOSH, OPCW, FAO/WHO JECFA, EPA (US), EFSA |
| 4 | Continental | EU regulations, regional agencies (e.g. ANVISA, Health Canada) |
| 3 | Local/National | national health/environmental agencies, country-specific SDS |
| 2 | User-provided | documents/datasets/values supplied directly by the user |
| 1 | No data | source cited but contains no usable info for the query |

- **2.1.1** Source spanning multiple levels → assign the **highest applicable** level.
- **2.1.2** Store as `SOURCE_LIST`, one entry per data point. Same database with 3 studies → 3 separate entries, each counted independently in T. Entry schema: `entry_id, source_name, priority_level (1–5), evidence_type (Q/N), model_type (Q-vivo/Q-vitro/Q-silico/Q-unknown; null for N), retrieved_content, agrees (filled in Block 3)`.
- **2.1.3** If all sources are priority 1 → treat as `EVIDENCE_FOUND = FALSE`: `C = 0`, emit ALERT-01.

**2.2 Domain routing**: verify the expected databases were consulted per `QUERY_TYPE`:

| Type | Expected Databases |
|---|---|
| Regulatory | PubChem (LCSS), ECHA Chem, NIOSH, OPCW |
| Toxicological | ISSTOX, T3DB, ECOTOX, EnviroTox |
| Phytochemical | PubChem |
| Drug-like | PubChem, ECHA Chem |
| Environmental | ERED (USACE), ECOTOX, EnviroTox |
| Safety/Hazard | PubChem (LCSS), ECHA Chem, NIOSH, OPCW, OpenFoodTox |

- **2.2.1** Compare consulted vs expected DBs for each query type.
- **2.2.2** Any expected DB not consulted → emit **ALERT-02**.
- **2.2.3** Multi-type query → merge the expected lists and evaluate coverage across all of them.

## BLOCK 3: Scoring

**3.1 Confidence score `C = A / T`**
- **T** = sources in `SOURCE_LIST` with usable info (priority ≥ 2, or priority 3–5 for the relevant data point).
- **A** = of those, how many agree with the **primary position**. Determine the primary position first = the value/range/conclusion supported by the most sources (highest-priority source breaks ties); agreement is measured against it.

Agreement criteria:
- **Type Q:** value/range consistent with the primary position. Same order of magnitude AND same hazard-class boundary → agrees; different order of magnitude OR crossing a regulatory threshold → disagrees. A source reporting uncertainty bounds agrees if the primary position falls within them.
- **Type N:** directionally consistent, assessed qualitatively, e.g. "full PPE + respirator" agrees with high acute toxicity; "no special precautions required" disagrees. If not mappable to the primary finding → `agrees = N/A`, **exclude from both T and A**.

Each same-database entry is evaluated independently, contributing 0–n to A; intra-database variability lowers C intentionally (an accurate uncertainty signal).

**3.2 Interpretation:**

| C | Meaning | Label |
|---|---|---|
| = 1.0 | all sources agree | ✅ High confidence |
| 0.7 ≤ C < 1.0 | strong majority agrees | 🟡 Moderate-high |
| 0.5 ≤ C < 0.7 | majority agrees, notable disagreement | 🟠 Moderate |
| 0.1 < C < 0.5 | weak agreement, significant disagreement | 🔴 Low |
| C ≤ 0.1 | near-total disagreement / single dissenting source | 🔴 Very low |
| = 0 | no sources / no usable data | ⚫ No data |

**3.3** If **T = 1**: `C = 1.0` by definition (a single source cannot disagree with itself); append "Confidence score is based on a single source. Independent corroboration is recommended."

**3.4 Weighted scoring (optional):** `C_weighted = Σ(priorityᵢ × agreementᵢ) / Σ(priorityᵢ)`, where agreementᵢ = 1 if the source agrees, 0 if not. Use when sources differ significantly in priority. Always report whether weighted or unweighted C was used.

## BLOCK 4: Inconsistency Resolution

**4.1 Detect:** two sources are inconsistent if they report contradictory values/classifications for the same data point (e.g. LD50 500 vs 5000 mg/kg; carcinogen Cat 1B vs not classifiable; banned vs permitted under Regulation X).

**4.2 Resolve by priority:** the higher `priority_level` source takes precedence as the primary finding. Equal priority → flag **unresolved**, report both values.

**4.3** Each inconsistency → emit **ALERT-03**.

**4.4 Intra-database variability** (same DB, multiple entries using different model types):
1. Report all entries separately in the Source List — never collapse.
2. Check mutual consistency (3.1 criteria).
3. Consistent → note in the Narrative that convergence across models reinforces reliability.
4. Inconsistent → emit **ALERT-04**.

**Model hierarchy** (informational — does NOT override priority in 4.2): **Q-vivo > Q-vitro > Q-silico.** When same-DB entries conflict and one is Q-vivo, flag the hierarchy as decision context only.

**4.5 No inconsistency:** state "No contradictions detected among the consulted sources."

**4.6 Absent evidence** (`EVIDENCE_FOUND = FALSE` or `C = 0`): state "No usable evidence was found for Compound X for this query type." Never fabricate values or infer from model knowledge without disclosing it. Any model-knowledge fallback must be labelled "⚠️ Model-generated estimate, not sourced from consulted databases." Proceed to Block 5.

## BLOCK 5: Explainable Output

**5.1** Emit this report (consumed by the summary agent):

```
## WoE REPORT | [Compound X] | Query: [QUERY_TYPE]

### 1. Evidence Summary
- Sources consulted: [N] · Usable (T): [T] · In agreement (A): [A]
- Missing expected databases: [list or "None"]

### 2. Source List
| # | Source | Priority | Ev. Type | Model Type | Content Retrieved | Agrees? |
|---|--------|----------|----------|------------|-------------------|---------|
(one row per entry; Model Type = Q-vivo/Q-vitro/Q-silico/Q-unknown/—; Agrees = Yes/No/N/A)

### 3. Confidence Score
- C = [value] → [Rule 3.2 label] · Method: [Unweighted/Weighted] · [single-source note if T=1]

### 4. Inconsistencies and Alerts
[ALERT-01 if applicable] [ALERT-02 if applicable]
[each ALERT-03, or "No cross-source contradictions detected."]
[each ALERT-04, or omit if none]

### 5. Primary Finding
[Data point/conclusion from the highest-priority, most consistent evidence — 1–3 sentences.]

### 6. Confidence Narrative
[1–2 sentences interpreting what C means for this specific query.]
```

**5.2 Handoff note**: append to every report:
> "This WoE Report is intended for use by the summary agent. The confidence score (C), alerts, and primary finding should be incorporated into the final response to the user. Do not omit alerts from the final summary."

## CONDITIONAL CASES: apply the FIRST match
Full handling for each case is in `references/conditional-cases.md`; read it whenever a run matches anything beyond Case A.

| Case | Match condition | Core action |
|---|---|---|
| A | Known, evidence, C ≥ 0.7, no ALERT-03 | Run all 5 blocks; report ✅/🟡; no special alerts. |
| B | ALERT-03 raised | Priority resolution (4.2); C < 1.0; keep all conflicts + minority findings. |
| C | ALERT-02, missing DBs | C from available sources only; note underestimated uncertainty; surface ALERT-02. |
| D | `EVIDENCE_FOUND=FALSE` / all priority 1 | C = 0; ALERT-01; skip Blocks 2–3; apply 4.6; "Insufficient evidence" finding. |
| E | Unknown/novel compound (no CAS/registry) | C = 0; ALERT-01 unknown-compound variant; request CAS/SMILES/InChIKey; no inference. |
| F | ≥ 2 `QUERY_TYPE` | Run Blocks 2–5 per type; separate C each; end with cross-type consistency note. |
| G | Priority-2 user data contradicts priority 3–5 | Database wins (4.2); ALERT-03 + override notice; keep user data in list + C. |
| H | Same DB, divergent model types | List all entries; majority (3.1); ALERT-04; Primary Finding from highest model type. |
| I | All items Type N, no Q | C via normative agreement; ALERT-05; Primary Finding = consensus guidance, not a number. |

## GENERAL CONSTRAINTS
- **Never fabricate values**: if not in the consulted docs, don't invent; use 4.6 and label model-based estimates.
- **Never suppress alerts**: ALERT-01 through ALERT-05 must all appear in the final report.
- **Always compute C explicitly**: state A and T even at C = 0 or C = 1; specify how many Q vs N contributed to T.
- **Preserve minority findings**: a source overridden by priority still appears in the Source List and is reflected in C.
- **Language neutrality**: report data as found; do not reinterpret or reframe a source's conclusion beyond what is stated.
- **Uncertainty propagation**: if a source self-reports uncertainty ("estimated", "provisional"), reflect it in the Narrative even when C is high.
- **Never collapse multiple database entries**: n studies = n rows; aggregation is never performed silently.
- **N/A entries do not inflate T**: `agrees = N/A` sources are excluded from T and A but still appear in the Source List.
- **Model type is informational, not authoritative**: the Q-vivo > Q-vitro > Q-silico hierarchy provides context but never overrides priority-based resolution; both signals are always reported explicitly.
