---
name: woe_reasoning
description: Use this skill whenever the task requires a Weight of Evidence (WoE) conclusion about a chemical's hazard, classification, read-across justification, or regulatory endpoint. Always use this skill before writing any hazard classification call, PBT/vPvB conclusion, endocrine disruptor determination, read-across justification, or any output that will enter a REACH/CLP/BPR/PPPR dossier regulatory argument. 
---

# Weight of Evidence (WoE) Reasoning Skill

Produce a regulator-defensible WoE conclusion by (1) pre-specifying the question, (2) assembling all relevant evidence, (3) scoring each line for reliability and relevance, (4) resolving conflicts transparently, (5) integrating across lines with explicit argumentation, and (6) reporting uncertainty and data gaps. The authoritative backbone is the **EFSA Scientific Committee Guidance on WoE (EFSA Journal 2017;15(8):4971)**: *assembly → weighing → integration*. ECHA **IR&CSA Chapter R.4** and **Practical Guide 2** govern REACH/CLP reporting. **OECD GD 194** and the **ECHA RAAF** govern grouping/read-across. **OECD (Q)SAR validation principles** govern any in-silico line.

Do not skip steps. Regulators reject WoE dossiers most often for undocumented weighting, cherry-picking, and missing conflicting evidence — the steps below exist to prevent those failure modes.

---

## Step 1 — Problem Formulation (pre-specification)

Before any evidence is inspected, pin down the assessment question in writing. Post-hoc framing is the single most common reason a WoE conclusion is overturned on review.

Record:
- **Substance** (name, CAS, EC, SMILES; purity/composition if relevant/quantity or volume if relevant).
- **Endpoint and scope** — e.g. "STOT-RE hazard classification under CLP Annex I," "PBT assessment under REACH Annex XIII," "read-across justification for chronic oral toxicity from analogue X."
- **Regulatory trigger** — REACH registration, CLP self-classification, BPR active substance approval, PPPR, endocrine disruptor identification, etc.
- **Population/exposure context** — species, route, duration, life stage.
- **Pre-specified weighting scheme** — which scoring tool will be applied to each evidence type (see Step 3). Choosing the tool *after* seeing results is cherry-picking.
- **Decision rule** — what pattern of evidence would lead to each possible conclusion, stated before inspecting data.

If any of these cannot be stated, stop and request clarification. A WoE conclusion cannot be stronger than its problem formulation.

---

## Step 2 — Evidence Assembly

Collect **all** potentially relevant evidence across the canonical lines. Missing lines must be flagged as gaps, not quietly omitted.

### Canonical lines of evidence

| # | Line                  | Typical sources                                                    | Evidence-gathering skill                   |
|---|-----------------------|---------------------------------------------------------------------|----------------------------------------------------------|
| 1 | Human data            | Epidemiology, case reports, biomonitoring, clinical                 | `database_traversal`,  `literature_search` |
| 2 | In vivo animal        | OECD TG studies, non-guideline studies                              | Uploaded studies, `database_traversal`,  `literature_search`     |
| 3 | In vitro              | Mechanistic assays, ToxCast/Tox21, OECD TG in vitro, EnviroTox or ADORE                 | `database_traversal`, uploads                            |
| 4 | In silico (QSAR)      | QSAR predictions, PBPK, profilers                                   | Uploaded QSAR outputs; model card required (see Step 3), pre-trained models (VEGA)  |
| 5 | Read-across / grouping| Analogue or category data, chemical scaffolds                                           | RAAF justification (Step 3)                              |
| 6 | Physicochemical       | logP, water solubility, vapor pressure, pKa, hydrolysis, flash point, lower explosive limit (LEL), upper explosive limit (UEL), autoignition temperature, boiling point
melting point, pH, color, odor, physical state             | `database_traversal` ,  `literature_search`             |
| 7 | Mechanistic / AOP     | Molecular initiating events, key events, AOP-Wiki                   | `database_traversal`,`literature_search`                        |
| 8 | Exposure / kinetics   | ADME, biomonitoring, PBPK                                           | `database_traversal`   ,  `literature_search`               |
| 9 | Regulation | Chemical hazards,   Dossiers, Clasification, Regulatory context                                         | `database_traversal`   ,  `literature_search`               |

### Assembly rules

- **Document the search** — for every source queried: database, query string, date, score threshold, number of hits screened, number retained. A non-reproducible search invalidates the WoE.
- **Use `sop_search`** to recover regulatory thresholds and procedural criteria the conclusion must respect.
- **Do not filter by direction of effect.** Collect supportive and conflicting studies alike. Filtering at assembly = cherry-picking.
- **If a canonical line has zero evidence, record it as a data gap in the evidence table.** Silent omission is not permitted.

---

## Step 3 — Per-Evidence Scoring: Reliability × Relevance

Every evidence entry must carry an explicit reliability score, relevance rating, and adequacy judgement. Apply the tool appropriate to the evidence type — do not apply Klimisch to an in-silico prediction or OECD principles to an in-vivo study.

### Reliability scoring tools

| Evidence type                           | Tool                                        | Reference                                 |
|-----------------------------------------|---------------------------------------------|-------------------------------------------|
| In vivo / in vitro mammalian toxicology | **Klimisch (1–4)** operationalized via **ToxRTool** (JRC) | Klimisch et al. 1997; Schneider et al. 2009 |
| Ecotoxicology (aquatic/terrestrial)     | **CRED** (Criteria for Reporting and Evaluating ecotoxicity Data) | Moermond et al. 2016, ET&C 35:1297–1309   |
| QSAR / in silico                        | **OECD (Q)SAR Validation Principles (1–5)** | OECD 2007                                 |
| Read-across / category                  | **ECHA RAAF** (scenarios 1–6)               | ECHA 2017 RAAF                            |
| Epidemiology                            | Study-design-specific tools (e.g. **OHAT**, **Navigation Guide**, IRIS confidence tiers) | NTP-OHAT 2019; US EPA IRIS Handbook 2022 |
| Systematic review integration           | **GRADE** or **CoSTER** where applicable    | GRADE working group                       |

### Klimisch reference (use exact wording in the evidence table)

- **1 — Reliable without restriction.** GLP + OECD/EU guideline; complete documentation.
- **2 — Reliable with restrictions.** Non-GLP or minor deviations; scientifically acceptable.
- **3 — Not reliable.** Significant methodological flaws that compromise the result.
- **4 — Not assignable.** Insufficient documentation to judge.

### OECD (Q)SAR Principles — every QSAR line must document all five

1. Defined endpoint
2. Unambiguous algorithm
3. Defined applicability domain (and whether the target substance is inside it)
4. Measures of goodness-of-fit, robustness, predictivity
5. Mechanistic interpretation if available

A QSAR prediction where the target is **outside the applicability domain** cannot carry decisive weight regardless of the model's overall accuracy.

### Relevance (do not conflate with reliability)

Score each entry for relevance to the pre-specified question on High / Medium / Low:
- **Species / test system** relevance to the target species/population.
- **Route, dose range, duration, quantity** relevance to the regulatory endpoint.
- **Endpoint match** — does the study measure the exact apical endpoint, a surrogate, or an upstream key event?
- **Chemical identity match** — target substance, analogue, mixture, impurity.

A Klimisch 1 study on an irrelevant endpoint has **low adequacy**, not high. Adequacy = reliability × relevance.

### Evidence table (required output of Step 3)

Produce a table with, at minimum, these columns. Persist as a CSV/Excel artifact in the conversation output scope so it is auditable.

| ID | Line | Reference | Test system | Endpoint | Key result | Reliability tool | Reliability score | Relevance | Adequacy | Direction (supports / conflicts / neutral) | Notes |
|----|------|-----------|-------------|----------|------------|------------------|-------------------|-----------|----------|--------------------------------------------|-------|

Every downstream claim must cite one or more IDs from this table.

---

## Step 4 — Conflict Detection and Resolution

Conflicting evidence is expected. What regulators require is that conflicts are **surfaced and adjudicated**, not hidden.

1. **Detect conflicts explicitly.** Group the evidence table by endpoint + direction. Any endpoint with rows in both "supports" and "conflicts" directions is a conflict to be resolved.
2. **Rank within the conflict** by adequacy (Step 3), not by count. Three low-adequacy studies do not outweigh one high-adequacy study.
3. **Apply mechanistic plausibility.** If an in-vivo and an in-vitro line disagree, consult AOP/mechanistic evidence (line 7) before weighting. Metabolic activation, species differences, and ADME (line 8) frequently explain apparent conflicts.
4. **If the conflict cannot be resolved**, do **not** default to the more convenient direction. Report the conflict, assign the endpoint a lower confidence in Step 6, and if the regulatory consequence is serious (e.g. CMR classification, PBT), escalate for human expert review.
5. **Never delete or silently down-weight a conflicting study.** Every conflict must appear in the final narrative (Step 7).

---

## Step 5 — Integration with Structured Argumentation

Integrate the weighted evidence into the WoE conclusion using an explicit **Claim → Evidence → Warrant → Conclusion** structure (Toulmin-style). Each claim is a single assertion; each piece of evidence is an evidence-table ID; each warrant is the weighting rationale.

### Required structure per endpoint

```
CLAIM       : <one-sentence assertion about the endpoint>
EVIDENCE    : [<IDs from evidence table>, ...]
WARRANT     : <why this evidence supports the claim — which scoring tool,
               which adequacy rating, which mechanistic plausibility>
REBUTTAL    : <conflicting evidence IDs and why they do not overturn the claim>
CONCLUSION  : <regulator-phrased conclusion, including confidence level from Step 6>
```

### Integration rules

- **One claim per assertion.** Compound claims ("likely hepatotoxic and genotoxic") must be split; each endpoint is weighed separately (EFSA 2017).
- **No evidence, no claim.** If the claim cannot cite evidence-table IDs, drop it.
- **Warrant must name the tool.** "Klimisch 1 + guideline OECD 408 + species match" is a warrant; "expert judgment" alone is not.
- **Rebuttal is mandatory** whenever any evidence of opposite direction exists. An empty rebuttal section when conflicts exist is a dossier defect.
- **Do not mix hazard identification, dose-response, and exposure** into one WoE block. Run Steps 1–7 separately for each (EFSA 2017).

---

## Step 6 — Uncertainty Quantification and Data Gaps

Every WoE conclusion must end with an explicit uncertainty statement. Regulators treat deterministic-sounding WoE conclusions as unreliable.

### Confidence expression (align with EFSA 2018 uncertainty guidance)

Use calibrated language mapped to approximate probability ranges:

| Qualitative term       | Approx. probability |
|------------------------|---------------------|
| Almost certain         | 99–100%             |
| Extremely likely       | 95–99%              |
| Very likely            | 90–95%              |
| Likely                 | 66–90%              |
| About as likely as not | 33–66%              |
| Unlikely               | 10–33%              |
| Very unlikely          | 5–10%               |
| Extremely unlikely     | 1–5%                |
| Almost impossible      | 0–1%                |

Attach one of these to every `CONCLUSION` produced in Step 5.

### Sources of uncertainty — inventory explicitly

- **Aleatory (variability)** — inter-individual, inter-species, inter-study biological variability.
- **Epistemic (knowledge)** — data gaps, model limitations, extrapolation beyond applicability domain, missing endpoints.
- **Methodological** — non-GLP studies, outdated guidelines, unvalidated assays, unclear dosing.

### Data gap inventory (required output)

List, per endpoint:
- Missing canonical lines from Step 2.
- Endpoints where only in-silico evidence exists outside its applicability domain.
- Read-across legs not supported by RAAF scenarios 1–6.
- Any regulatory criterion from `sop_search` that cannot be evaluated with the assembled evidence.

**Absence of evidence is not evidence of absence.** Never let a data gap silently resolve into a "no effect" conclusion.

---

## Step 7 — Draft the Regulatory WoE Narrative

Produce a narrative that is directly insertable into a REACH IUCLID WoE block (ECHA Practical Guide 2), a CLP Annex VI dossier, an EFSA opinion, or a Case Study 2 deliverable.

### Mandatory narrative sections

1. **Problem formulation** — verbatim from Step 1.
2. **Search strategy and assembly** — reproducible log from Step 2 (databases, queries, dates, thresholds, hit counts).
3. **Evidence table** — attached artifact reference; summarized in prose.
4. **Weighting methodology** — which tools (Klimisch/ToxRTool/CRED/OECD/RAAF), pre-specified in Step 1.
5. **Integrated argumentation** — one `Claim → Evidence → Warrant → Rebuttal → Conclusion` block per endpoint from Step 5.
6. **Conflict resolution** — explicit discussion of every conflict surfaced in Step 4.
7. **Uncertainty statement** — calibrated language + aleatory/epistemic/methodological inventory (Step 6).
8. **Data gap inventory** — Step 6 output.
9. **Conclusion against the regulatory criterion** — map the integrated finding to the exact CLP/REACH/BPR criterion from `sop_search`. Do not invent thresholds.
10. **References** — full citations; at minimum EFSA 2017, ECHA PG 2, and any tool papers (Klimisch 1997, Moermond 2016, OECD 2007, RAAF 2017) used.

### Style rules for the narrative

- Use **calibrated language only** — "likely," "about as likely as not," etc. as mapped in Step 6. Avoid "clearly," "obviously," "definitely."
- **Cite evidence-table IDs inline** (e.g. "consistent with E-03 and E-07").
- **Quote regulatory thresholds verbatim** from `sop_search` results with their SOP ID.
- Keep the tone neutral and auditable. The narrative is a regulatory document, not an advocacy piece.

---

## Step 8 — Self-Audit Against Common Regulatory Pitfalls

Before presenting the narrative, run this checklist. Any "fail" must be fixed, not waived.

- [ ] **Problem formulation pre-specified** in writing before Step 2. (Step 1)
- [ ] **Search is reproducible** — every query, threshold, date logged. (Step 2)
- [ ] **No canonical line silently omitted** — gaps in the inventory. (Step 2, 6)
- [ ] **Every evidence entry scored** with the tool appropriate to its type. (Step 3)
- [ ] **Reliability and relevance scored separately**; adequacy derived, not conflated. (Step 3)
- [ ] **QSAR entries document all five OECD principles**, including applicability-domain check. (Step 3)
- [ ] **Read-across entries map to a RAAF scenario (1–6)** with mechanistic/metabolic similarity justification. (Step 3)
- [ ] **Conflicts surfaced and adjudicated** — no silent dismissal. (Step 4)
- [ ] **One claim per endpoint**, each with Claim/Evidence/Warrant/Rebuttal/Conclusion. (Step 5)
- [ ] **Every claim traces to evidence-table IDs.** (Step 5)
- [ ] **Hazard, dose-response, and exposure** weighed separately, not merged. (Step 5)
- [ ] **Calibrated confidence language** attached to every conclusion. (Step 6)
- [ ] **Aleatory / epistemic / methodological uncertainty** inventoried. (Step 6)
- [ ] **Data gaps listed**; no gap silently treated as "no effect." (Step 6)
- [ ] **Regulatory thresholds quoted from `sop_search`**, not invented. (Step 7)
- [ ] **Framework references cited** (EFSA 2017 at minimum). (Step 7)
- [ ] **No use of "clearly," "obviously," "definitely"** in place of calibrated language. (Step 7)

If the WoE conclusion affects a CMR classification, PBT/vPvB determination, endocrine disruptor identification, chemical hazard, or any decision with direct human-health or environmental consequence, add a final line requesting expert human review before the output is treated as authoritative.

---