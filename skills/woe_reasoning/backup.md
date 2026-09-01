---
name: woe_reasoning
description: Weigh already-gathered evidence into a defensible conclusion — score each source for quality and reliability, rank sources by authority for the question actually asked, surface contradictions between them, and emit a structured weight-of-evidence report carrying an explicit confidence level. Use once retrieval or prediction has produced evidence from more than one source, or whenever a chemical safety conclusion needs a stated confidence, even if the user never says weight of evidence.
---

# WoE: Weight of Evidence for Chemical Safety

Run all five blocks in order; **never skip one, even when evidence is absent or partial.**
Read `references/woe-detail.md` before resolving any numeric difference, and for the full
handling of Cases B–I and the canonical ALERT-01…05 text.

## BLOCK 1 — Inspect

**1.1** Record the sources consulted, the values/statements retrieved, and the substance (**Compound X**). No documents retrieved → `EVIDENCE_FOUND = FALSE`, `C = 0`, **ALERT-01**, skip Blocks 2–3, go to Block 4.

**1.2 `QUERY_TYPE`** — one or more of: Regulatory · Toxicological · Phytochemical · Drug-like · Environmental · Safety/Hazard.

**1.3 `EVIDENCE_TYPE`** — **Q** (numeric: LD50, NOAEL, OEL, flash point) or **N** (normative: SOP, guidance, a classification with no number). Each Q also gets `MODEL_TYPE`: Q-vivo (animal/human), Q-vitro (cell assay), Q-silico (QSAR/read-across), Q-unknown. Informational; never changes priority.

**1.4 `DATA_POINT`** per Q item — what the number means for the question asked:

`quantity · kind of measure · species/population · route` — plus, separately, the **instrument** (experimental study, read-across, QSAR, health-based recommendation, enforceable legal limit, consensus guideline) and whether the value is a **point estimate** or a **bound** (`> 5000 mg/kg` states only that the LD50 exceeds 5000). A NIOSH REL and an OSHA PEL are both `airborne limit · full-shift TWA · worker · inhalation`, on different instruments.

Granularity comes from the question: a convention that does not change which decision the number informs does **not** split the data point (8-h and 10-h full-shift TWAs are both the routine full-shift limit); a difference in *kind* does (TWA vs 15-min STEL vs ceiling).

## BLOCK 2 — Categorize

**2.1 Source priority** — build `SOURCE_LIST` with one entry per data point (same DB, 3 studies → 3 entries, each counted in T): `entry_id, source_name, priority_level, evidence_type, model_type, data_point, retrieved_content, agrees`.

| Priority | Level | Examples |
|---|---|---|
| 5 | International | WHO, ECHA, NIOSH, OPCW, JECFA, US EPA, EFSA |
| 4 | Continental | EU regulations, regional agencies (ANVISA, Health Canada) |
| 3 | Local/National | national agencies, country-specific SDS |
| 2 | User-provided | values supplied by the user |
| 1 | No data | cited but carries nothing usable for the query |

- Multi-level source → highest applicable level; a body not listed takes the level its remit implies (OSHA, HSE, BAuA are Local/National). Do not invent a ranking to break a tie. All sources priority 1 → `EVIDENCE_FOUND = FALSE`, C = 0, ALERT-01.
- Priority ranks **the publisher's authority for the question asked** — not how strict, recent or protective a value is — and applies only *between comparable items*. "NIOSH outranks OSHA, so the REL is the finding" misuses this table: the REL and the PEL answer different questions and both are reported.
- **A source's own caveat governs.** Record what a source says about its own reliability (Klimisch score, "key / supporting / disregarded study", "cannot be used for classification"). A record its publisher disclaims for the question asked is **priority 1 for that question** — it stays in the Source List and is reported, but it cannot set the value, and it does not count in T or A, so it cannot corroborate one either: "concordant with the disclaimed record" is not evidence. Publisher authority never overrides a publisher's own caveat.

**2.2 Coverage** — any expected database not consulted → **ALERT-02**; merge the lists for a multi-type query:

| Type | Expected databases |
|---|---|
| Regulatory · Safety/Hazard | PubChem (LCSS), ECHA CHEM, NIOSH, OPCW (+ OpenFoodTox) |
| Toxicological | ISSTOX, T3DB, ECOTOX, EnviroTox |
| Environmental | ERED (USACE), ECOTOX, EnviroTox |
| Phytochemical · Drug-like | PubChem (+ ECHA CHEM) |

## THE COMPARABILITY GATE — governs Blocks 3 and 4

**Score two items against each other only when their `DATA_POINT` matches.** Write the shared data point on one line before comparing; if you cannot, there is nothing to compare and nothing to resolve — a rat LD50 neither agrees nor disagrees with a cat LD50. Non-matching items are `agrees = N/A`, excluded from T and A, and reported beside the finding as context — more information, not dissent. Scoring across data points fails unsafe: it manufactures a disagreement, divides C by it, and reports a sound conclusion as low-confidence.

**Legitimate differences**, reported side by side with **no ALERT-03 and no reduction in C**: different instruments for one quantity (a NIOSH REL, an OSHA PEL and an ACGIH TLV are a recommendation, a legal minimum and a guideline — all hold at once), different species, different route or averaging basis, a bound against a point estimate, hazard identification against risk assessment.

**Same data point, different instrument: they confirm each other and cannot refute each other.**
- **Values coincide** → they agree. Count both in T and A; the finding is corroborated across instruments. Two bodies independently arriving at 200 ppm is stronger than one — never report only one, and never call the group single-source because the shift conventions differ.
- **Values differ** → not disagreement. Report each with its instrument and recommend the most protective, which satisfies the others.

**Counter-test — the instrument must actually explain the difference.** Yes for a health-based recommendation against an enforceable legal minimum, or a 1971 PEL against a current REL. **No for two assessors applying the same criteria**: a harmonised Annex VI entry and a CLP notification both classify against CLP Annex I, so a different category for one endpoint means somebody read the criteria differently — genuine disagreement, so resolve by priority, emit ALERT-03, and keep the minority category on the record. Likewise two studies of one species and route, and two labs running one assay. The gate removes a comparison that was never valid; it never excuses one that is.

## BLOCK 3 — Score

**3.1** Group `SOURCE_LIST` by `DATA_POINT`; score **within one group, never across groups**. `C = A / T`, where **T** = entries in the group with usable info (priority ≥ 2) and **A** = how many of those agree with the **primary position** (the value/range/conclusion supported by the most entries; highest priority breaks ties).

- **Q** — **both** conditions must hold to agree: the same order of magnitude *and* the same hazard-class band. Either one failing is disagreement, so two values sitting either side of a GHS or regulatory category cutoff **disagree even when they are the same order of magnitude** (72 and 330 mg/kg straddle the Acute Tox. 3/4 cutoff at 300 mg/kg, so A counts one of them, not both). A source reporting uncertainty bounds agrees if the primary position falls inside them. A **bound is not a point estimate**: `LD50 > 5000 mg/kg` agrees with a determined 7975 mg/kg, and can never itself set the primary position.
- **N** — directionally consistent ("full PPE + respirator" agrees with high acute toxicity; "no special precautions required" disagrees). Not mappable → `agrees = N/A`, excluded from T and A.

Same-database entries count independently; intra-database variability lowers C by design.

**3.2** `C = 1.0` with T ≥ 2 → ✅ High · `0.7 ≤ C < 1.0` → 🟡 Moderate-high · `0.5 ≤ C < 0.7` → 🟠 Moderate · `0.1 < C < 0.5` → 🔴 Low · `C ≤ 0.1` → 🔴 Very low · `C = 0` → ⚫ No data.

**3.3 T = 1**: this means one source reported the quantity — two bodies publishing the same value are two sources, not one. C = 1.0 arithmetically but carries no information — a single source cannot disagree with itself. Label **⚪ Single source — unscored**, never high confidence, and append "Confidence score is based on a single source. Independent corroboration is recommended." Do not justify the score by noting that nothing contradicts the source; nothing was in a position to.

## BLOCK 4 — Resolve

**4.1** Two sources are inconsistent when they report contradictory values/classifications **for the same data point** (rat oral LD50 500 vs 5000 mg/kg; Carc. 1B vs Carc. 2 for one endpoint; banned vs permitted under one regulation). Apply the gate first. Each genuine inconsistency → **ALERT-03**; none anywhere → "No contradictions detected among the consulted sources."

**4.2 Resolve by priority** — comparable, genuinely inconsistent items only. The higher `priority_level` becomes the primary finding. Equal priority → **unresolved, both reported**, said plainly; that is a complete answer, not a failure. **Resolution is not deletion**: the overridden entry stays in the answer, named, with its value and — where the source gives one — the share of assessors behind it ("the harmonised entry gives Carc. 1B; a notification from 40% of notifiers gives Carc. 2, which does not displace it"). A reader who cannot see what was overridden cannot check the resolution.

**4.3 Intra-database variability** (same DB, different model types): report every entry separately, check consistency by 3.1, note convergence if consistent, emit **ALERT-04** if not. **Q-vivo > Q-vitro > Q-silico** is decision context only; it never overrides 4.2.

**4.4 Silence is not a negative finding.** A source that classifies some endpoints and omits another has not found that hazard absent — an Annex VI entry with no carcinogenicity classification means carcinogenicity was not harmonised, possibly never assessed. Write "no harmonised classification for this endpoint", never "not a carcinogen". A source that *has* evaluated the endpoint (an IARC group, a notified classification) carries the hazard finding; a silent source does not outrank it on a question it never answered.

**4.5 Absent evidence** (`EVIDENCE_FOUND = FALSE` or C = 0): state "No usable evidence was found for Compound X for this query type." Never fabricate; label any model-knowledge fallback "⚠️ Model-generated estimate, not sourced from consulted databases."

**4.6 Absence of a value is not evidence of safety.** No OEL set, no study, not on a control schedule — each describes the *state of the record*, not the substance. Say both halves: what was not found, **and** that the gap does not license relaxing controls. Name what would have to be retrieved.

## BLOCK 5 — Report

```
## WoE REPORT | [Compound X] | Query: [QUERY_TYPE]

### 1. Evidence Summary
- Sources consulted: [N] · Usable (T): [T] · In agreement (A): [A]
- Data point scored: [the DATA_POINT C refers to] · Not comparable, reported as context: [N or "none"]
- Missing expected databases: [list or "None"]

### 2. Source List
| # | Source | Priority | Ev. Type | Model Type | Data Point | Content Retrieved | Agrees? |
one row per entry; Agrees = Yes/No/N/A, and N/A covers every entry on another data point

### 3. Confidence Score
- C = [value] → [3.2 label] · Method: [Unweighted/Weighted] · [single-source note if T = 1]

### 4. Inconsistencies and Alerts
[every ALERT-01…05 raised, or "No cross-source contradictions detected."]

### 5. Primary Finding
[Conclusion from the highest-priority, most consistent evidence — 1–3 sentences.]

### 6. Confidence Narrative
[1–2 sentences on what C means for this specific query.]
```

Append: "This WoE Report is intended for use by the summary agent. The confidence score (C), alerts, and primary finding should be incorporated into the final response to the user. Do not omit alerts from the final summary."

## CONDITIONAL CASES — apply the FIRST match
**A** evidence, C ≥ 0.7, no ALERT-03 · **B** ALERT-03 raised · **C** ALERT-02, expected DBs missing · **D** no evidence / all priority 1 · **E** unknown compound (no CAS, no registry entry) · **F** ≥ 2 `QUERY_TYPE` · **G** priority-2 user data vs a priority 3–5 source · **H** same DB, divergent model types · **I** all items Type N, no Q.

Case A is the default and needs nothing special — but most runs that *look* like B are really A, so apply the gate before concluding there is a conflict. **For any match other than A, read `references/woe-detail.md` and follow the handling there.**

## CONSTRAINTS
- **Compare only comparable items** — the single most consequential rule here. Two answers to two different questions are more information, not a contradiction, and must not lower C.
- **Never suppress an alert**; **never collapse database entries** (n studies = n rows); **preserve minority findings** — an overridden source still appears in the Source List and in C, and `agrees = N/A` entries appear too but do not inflate T.
- **Always compute C explicitly** — state A and T even at C = 0 or C = 1, and how many Q vs N contributed.
- **Never fabricate values**, and report as found: do not reinterpret a source's conclusion; carry its self-reported uncertainty into the Narrative.
