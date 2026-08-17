---
name: qprf-generator
description: Generate an OECD QPRF v2.0 for one substance from the QMRF PDF of the QSAR model used and the software prediction report (e.g. VEGA). Substance identity and experimental data are enriched from ECHA CHEM. Use to document/justify a QSAR prediction for a regulatory dossier (REACH, K-REACH, CLP, BPR, etc.).
---

# QPRF Generator 
Model- and endpoint-agnostic and **self-contained**: the QMRF→QPRF mapping in §6 is embedded here
(built on the OECD QMRF v2.1 and QPRF v2.0 standards), so no external mapping table or spreadsheet is
needed at runtime — only the two PDFs plus ECHA. The OECD template fixes the structure; content comes
from four sources. Field IDs (e.g. QMRF 3.3, QPRF 4.2a) are the OECD standard numbering. Fields flagged
`[ENDPOINT-SPECIFIC]` take their concrete text from the QMRF, not from here.

## 1. Rules
1. One substance, one model, one prediction per QPRF. Repeat the skill for other predictions.
2. Never invent. Each field is copied, computed, boilerplate, or a rule-driven pick-list. Missing source → template default (`n/a` / the §9 pointer).
3. Authority: report → the prediction & indices; QMRF → the model; ECHA → substance identity. On a numeric conflict, trust the report's *AD Scores* page.
4. Output is English; regulatory boilerplate (§9) and field names verbatim.
5. All "Comments on …" fields → `n/a` unless relevant (not repeated in the mapping).

## 2. Source codes (used in §6)
`[QMRF]` QMRF field by ID · `[RPT]` software output (report) · `[ECHA]` ECHA CHEM · `[CALC]` RDKit (fallback ECHA) · `[CFG]` author/date/regulatory context · `[TPL]` boilerplate §9 · `[RULE]` pick-list/narrative from §8.

## 3. Pipeline
1. Parse QMRF (sections numbered 1.1–9.3; pull the IDs cited in §6).
2. Parse the report (§4).
3. Resolve the substance in ECHA (identity, MW) and search its experimental data for this endpoint → 7.5 (§5).
4. Derive structures with RDKit: canonical SMILES, InChI, formula, MW, and two 2-D images — *original* (stereo, from ECHA) and *processed* (flat, the report input) (§5).
5. Fill `[RULE]` fields via §7–§8; assemble via §6 with §9 boilerplate; render (§10).

## 4. Reading the prediction report `[RPT]`
- **Prediction Summary:** `Compound SMILES`, predicted class/value, structural alerts, remarks, reliability stars (1–3).
- **AD Scores page (authoritative):** Global AD Index, Similarity, Accuracy, Concordance, Max-error (if any), ACF, descriptor-range (if any), and the verdict line. Use these even if the summary rounds differently.
- **Similar compounds / nearest neighbours:** per compound → CAS, SMILES, dataset id (Training/Test set), Similarity, Experimental, Predicted; set match = experimental==predicted. → Annex 1 + field 7.4.

## 5. Substance enrichment
- **ECHA:** CAS, EC, other IDs, IUPAC/CAS name, formula, MW; the *original* (as-submitted, stereo) structure; and any registered experimental value for this endpoint (short summary + citation → 7.5; else `found=false`).
- **RDKit:** canonicalise report SMILES → processed SMILES/InChI; original SMILES/InChI from ECHA; formula + MW (cross-check ECHA); two 2-D depictions; flag stereoisomer if stereocentres/E-Z present.
- Do not fabricate ECHA values; on failure write `n/a` and note the gap.

## 6. Field mapping (QPRF v2.0)

**1 General** — 1.1 Date `[CFG]` · 1.2 Author + contact `[CFG]`.

**2 Substance**
| # | src | value |
|---|---|---|
|2.1/2.2/2.3|`[ECHA]`|CAS / EC / other IDs (e.g. METI, else n/a)|
|2.4|`[ECHA]`|IUPAC and/or CAS name|
|2.5|`[ECHA]`/`[CALC]`|molecular formula|
|2.6a|`[RPT]`+`[CALC]`|Original SMILES (stereo) **and** Processed SMILES (canonical)|
|2.6b|`[CALC]`/`[ECHA]`|Original **and** Processed InChI|
|2.6c|`[CALC]`|two 2-D images: original (stereo) / processed (flat)|
|2.6d|`[TPL]`|§9-A|
|2.6e|`[ECHA]`|composition/impurities note, else n/a|
|2.6f|`[CALC]`/`[ECHA]`|"Molecular weight: … g/mol"|

**3 Model & software**
| # | src | value |
|---|---|---|
|3.1a|`[QMRF 1.1]`|model name|
|3.1b|`[QMRF]`/`[CFG]`|version (state explicitly)|
|3.1c|`[QMRF 1.1+2.1+2.5]`|"«title», printed «date»; authors: «names»"|
|3.2a|`[QMRF 1.3]`/`[RPT]`|software name|
|3.2b|`[RPT]`|GUI version + calculation core version|
|3.2c|`[QMRF 1.3]`|URL/reference|
|3.2d|`[QMRF 1.3]`|availability (e.g. "Freely available")|

**4 Prediction**
| # | src | value |
|---|---|---|
|4.1a|`[QMRF 3.2+3.3]`|property incl. species/duration `[ENDPOINT-SPECIFIC]`|
|4.1b|`[QMRF 3.6]`|test guideline (e.g. OECD TG 203)|
|4.1c|`[QMRF 3.5]`|dependent variable|
|4.1 comments|`[QMRF 3.3+4.5+3.6]`|§8-E: class scheme + "most toxic verified class; default = least-hazardous class" + training-data origin `[ENDPOINT-SPECIFIC]`|
|4.2a|`[RPT]`|class + range (map class→range via QMRF 3.3), e.g. "NON-Toxic (LC50 > 100 mg/L)"; for alert hits, add alert(s)+reasoning|
|4.2b|`[RULE]`|§8-A (confidence per cascade §7)|
|4.2c|`[QMRF 3.4]`|unit(s)|

**5 Input**
| # | src | value |
|---|---|---|
|5.1a|`[RPT]`|exact input consumed (e.g. processed SMILES)|
|5.1b|`[TPL]`|§9-A|
|5.1c|`[TPL]`/`[CALC]`|"Not relevant." unless a known tautomer affects the prediction|
|5.2|`[TPL]`+`[RULE]`|§9-B + §8-B ACF outcome|
|5.3|`[TPL]`/`[RPT]`|§9-C if no customisation, else the settings used|

**6 AD & limitations**
| # | src | value |
|---|---|---|
|6.1|`[QMRF 5.1–5.4]`|reproduce the AD-index + ACF bands (§7) so the QPRF is self-standing|
|6.1a|`[RULE]`|pick-list §8-C|
|6.1b|`[RULE]`|§8-C justification|
|6.1c / comments|`[TPL]`|§9-D (unless a documented model limitation applies, then state it)|

**7 Reliability**
| # | src | value |
|---|---|---|
|7.1 + comments|`[RULE]`|pick-list §8-D (model / software public vs proprietary)|
|7.2|`[QMRF 6.7+7.7]`|§8-F (goodness-of-fit + balanced accuracy + external validation)|
|7.3a|`[TPL]`/`[QMRF]`|fragment model → "Not relevant, uses structural fragments"; else discuss descriptor range (`descriptor_range_ok`)|
|7.3b|`[RULE]`|ACF interpretation §8-B; xref 7.4|
|7.3c|`[RULE]`|do neighbours' experimental values bracket the predicted class? (see 7.4)|
|7.3d|`[QMRF 8.1–8.3]`|mechanistic basis if given, else n/a|
|7.3e|`[QMRF]`/`[CFG]`|n/a unless relevant|
|7.4 (a–g)|`[RPT]`|from nearest neighbours: CAS/SMILES; source (train/test); experimental; ref (dataset/QMRF); predicted; accuracy=match; similarity+note|
|7.4 considerations|`[RULE]`|§8-G|
|7.5|`[ECHA]`|experimental data for the target + fit with prediction, else n/a|
|7.6|`[RULE]`|§8-H|

**8 Purpose (regulatory)**
| # | src | value |
|---|---|---|
|8.1|`[CFG]`|regulation served (e.g. "Regulatory submission under K-REACH")|
|8.2|`[RULE]`|how the class/value is interpreted (unit conversion / assessment factor / WoE)|
|8.3|`[RULE]`|interpreted result in the regulatory frame|
|8.4|`[RULE]`|uncertainty per cascade §7|
|8.5|`[RULE]`|adequacy + Klimisch tag §8-J|

**9 References** — `[QMRF 2.7+9.2]` + §9-E (add Fourches 2010 & Sliwoski 2012 whenever §9-A is used).

## 7. Reliability bands (VEGA standard; verify vs QMRF §5) + cascade
- **Global AD Index:** >0.85 within/good · 0.7–0.85 borderline/moderate · ≤0.7 outside/low.
- **Similarity:** >0.8 strongly similar · 0.7–0.85 moderately similar · ≤0.7 none.
- **Accuracy (avg error):** <0.8 good · 0.8–1.5 not optimal · ≥1.5 not adequate.
- **Concordance:** <0.8 agree · 0.8–1.5 slightly disagree · ≥1.5 disagree.
- **Max error:** <0.8 low · 0.8–1.5 moderate · ≥1.5 high.
- **ACF:** =1 all fragments found · 0.7–1 some missing/rare · <0.7 many missing/rare.
- **Descriptor range:** TRUE inside · FALSE outside.

**Cascade (drives 4.2b, 8.4, 8.5):**
| AD index | AD verdict | confidence | uncertainty | Klimisch |
|---|---|---|---|---|
| >0.85 | within (good) | high | low | 1 |
| 0.7–0.85 | borderline (moderate) | moderate | medium | 2 |
| ≤0.7 | outside (low) | low | high | 3 / not adequate |

## 8. Templates & pick-lists (insert data-object values; keep the QPRF register)
- **§8-A 4.2b:** "The «software» prediction for «property» is '«class»' (LC50 «range»). «confidence» confidence, based on Global AD Index «x», ACF «x», Similarity «x», Accuracy «x», Concordance «x»."
- **§8-B ACF (5.2, 7.3b):** =1 → "all atom-centred fragments found; well represented." · 0.7–1 → "some fragments not found/rare; interpret with care." · <0.7 → "many fragments not found/rare; possible extrapolation."
- **§8-C 6.1a pick-list:** (1) Within domain (automatic) · (2) Within domain (expert) · (3) Outside domain (automatic) · (4) Outside domain (expert) · (5) Not applicable. VEGA computes AD automatically → AD>0.85 → 1; ≤0.7 → 3; borderline → follow the report verdict (1/3); override only with expert reasoning (→2/4). **6.1b:** "Global AD Index «x» → «within/borderline/outside» → «good/moderate/low» reliability."
- **§8-D 7.1:** QMRF non-proprietary + public software → "Model is publicly available and the prediction can be fully reproduced; algorithm and data are in the associated QMRF." else proprietary option. **Comments:** "Software is publicly available." / "Software is proprietary."
- **§8-E 4.1 comments `[ENDPOINT-SPECIFIC]`:** compose from QMRF 3.3 (class scheme) + 4.5 (rule logic: assign to most toxic verified class; if none, least-hazardous class) + 3.6/3.5 (training data + variable transform).
- **§8-F 7.2:** "Goodness-of-fit: «QMRF 6.7». Balanced accuracy: «QMRF 6.7». External validation: «QMRF 7.7». Considered acceptable for the intended purpose."
- **§8-G 7.4 considerations:** "«n» neighbours returned with experimental/predicted values and similarity. Similarity Index «x» → «strongly/moderately/no» similar; Accuracy «x» and Concordance «x» → reliable region, neighbours' experimental values agree with the prediction." (adapt if unfavourable).
- **§8-H 7.6:** "AD Index «x» → «good/moderate/low» reliability; with ACF «x», Similarity «x», Accuracy «x», Concordance «x», the prediction is «highly reliable / reliable with reservations / of limited reliability»."
- **§8-J 8.5:** "Adequate for regulatory purposes: '(Q)SAR result with «low/medium/high» uncertainty (Reliability «1/2/3»)'." If not adequate, state the additional information required.

## 9. Boilerplate (verbatim)
- **§9-A** (2.6d, 5.1b): "By default, stereochemistry is not considered by 2D-QSAR models, as it is an inherently three-dimensional phenomenon (Fourches et al., 2010; Sliwoski et al., 2012)."
- **§9-B** (5.2, fragment models): "The model was built using the fragments extracted from the SARpy software tool. Please refer to the QMRF section 4.4 for the comprehensive list of all fragments comprising the «endpoint» classes. The software evaluates unusual fragments via the ACF (Atom Centered Fragments) Index: index = 1 → all fragments found in the training set; 1 > index ≥ 0.7 → some not found or rare; index < 0.7 → a prominent number not found or rare." (+ §8-B outcome).
- **§9-C** (5.3): "Not relevant, since no further model's settings need to be defined."
- **§9-D** (6.1c, comments): "See above and QMRF Section 5."
- **§9-E** references: Fourches D, Muratov E, Tropsha A. 2010. J. Chem. Inf. Model. 50, 1189–1204. · Sliwoski G et al. 2012. Molecules 17(8): 9971–9989. doi:10.3390/molecules17089971.

## 10. Render & QA
Reproduce the QPRF v2.0 layout: numbered section tables; 2.6c shows the two 2-D structures; **Annex 1** = nearest-neighbours table (Structure rendered from SMILES, CAS, Similarity, Experimental, Predicted). Prefer exporting as PDF.

Before presenting, verify: one substance/model/prediction · mandatory fields filled or justified n/a (1.1, 1.2, 2.1, 2.4, 2.6a, 3.1a/c, 4.1a, 4.2a, 5.1a, 6.1/6.1a, 7.1, 7.6, 8.1, 8.5) · all AD-derived fields consistent with one `ad_index` · 4.2a range matches class · formula/MW agree (ECHA vs RDKit) · every neighbour in Annex 1 · pick-lists use only allowed strings · references include §9-E if §9-A used.

*Generalisation:* swap the QMRF to change model/endpoint — `[ENDPOINT-SPECIFIC]` fields follow it. §9-B and 7.3a assume fragment/alert (SARpy-type) models; for descriptor/statistical models, replace with the real descriptor list and use `descriptor_range_ok` in 7.3a. The same extraction can also feed the OECD QAF-M (model) and QAF-P (prediction) checklists, which reuse the QMRF/QPRF field IDs above, if the full OECD (Q)SAR Assessment Framework is required.