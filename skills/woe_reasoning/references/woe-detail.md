# WoE detail — comparability, Cases B–I, alert text

## Comparability: the domain facts behind the gate

The gate itself is in SKILL.md. Occupational limits are the common trap because the three
most-cited ones are different kinds of object:

| Limit | Set by | What it is |
|---|---|---|
| **REL** | NIOSH | Health-based *recommendation*, not enforceable. Often set at the lowest feasible concentration for a carcinogen, so it can sit far below the others. |
| **PEL** | OSHA | *Enforceable legal minimum* in US workplaces. Many date from 1971 and were never revised. |
| **TLV** | ACGIH | *Consensus guideline* from a professional body. Not law. |
| **DNEL** | REACH registrant | *Derived no-effect level* for a specific exposure scenario under EU chemical safety assessment. |

A 47-fold REL/PEL gap (formaldehyde: 0.016 vs 0.75 ppm) is not a dispute about a measurement.
The finding is **both values, each named with its instrument**, plus the note that working to
the most protective satisfies the others. Do not call the gap a contradiction, emit ALERT-03
for it, reduce C for it, rank the bodies against each other, or report one number without
naming its instrument. Where they *coincide* (2-butanone: NIOSH 200 ppm, OSHA 200 ppm) they
agree and the finding is corroborated across instruments — never report only one, and never
call the group single-source because the shift conventions differ.

Other differences that are **not** contradictions:

- **Species.** For a GHS acute category the reference species governs; others are supporting
  data. Phenol rat oral LD50 530 mg/kg vs cat 100 mg/kg is a real interspecies difference
  (cats lack glucuronidation). Classify on the reference species, report the range, name the
  sensitive species.
- **Route or duration.** An 8-h TWA and a 15-min ceiling are both in force at once; an oral
  LD50 and an inhalation LC50 are not convertible.
- **A bound against a point estimate.** `LD50 > 5000 mg/kg` agrees with any estimate that
  satisfies it (7975 does), conflicts only with one that violates it, and can never set the
  primary position alone.
- **Hazard identification vs risk assessment.** IARC classifies *hazard*; "unlikely to pose a
  carcinogenic hazard under expected use" assesses *risk at realistic exposure*. Glyphosate
  carries both (IARC 2A vs EFSA). Report both, say which question each answers, and if the
  evidence cannot reconcile them say so — an unresolved conflict between two bodies of equal
  standing is a finding, not a failure.
- **Silence against a classification** — omitting an endpoint is not finding the hazard absent
  (SKILL.md 4.4).

Differences that **are** contradictions, and must raise ALERT-03 — apply the counter-test
(does the difference in instrument explain the difference in value?):

- same species and route, values in different hazard categories (formaldehyde rat oral LD50 at
  100, 800 and 2020 mg/kg spans Acute Tox. 3, 4 and 5 — genuinely unsettled);
- one endpoint classified differently by two entries of the same instrument (harmonised
  Carc. 1B vs notified Carc. 2 — both applying CLP Annex I);
- a user-supplied value against a published one for the same data point;
- the same regulation read as permitting and as prohibiting.

**A contradiction is two answers to one question. Two answers to two questions is just more
information.** Resolution is not deletion: keep the overridden value on the record with the
share of assessors behind it.

## Cases — apply the FIRST match

**B — genuine conflict.** ALERT-03 raised, **and only after the gate confirms the conflicting
items share a data point**; most runs that look like B are really A. Priority resolution
(4.2); C < 1.0 by definition. Include every ALERT-03, never suppress a minority finding, and
label plainly which source was prioritised.

**C — partial coverage.** ALERT-02 raised. Compute C from available sources only. Append to
the Narrative: "Score may underestimate uncertainty due to missing coverage of [databases]."
Surface ALERT-02 prominently.

**D — no evidence.** `EVIDENCE_FOUND = FALSE`, or all sources priority 1. C = 0; ALERT-01;
skip Blocks 2–3; apply 4.5 and 4.6. Primary Finding: "Insufficient evidence. No reliable
conclusion can be drawn from consulted sources." Label any fallback estimate ⚠️.

**E — unknown / novel compound.** No CAS, no registry entry. C = 0; ALERT-01 with the
unknown-compound variant; ask for CAS / SMILES / InChIKey / synonyms. Do **not** proceed to
inference — the output is the identification-gap notice.

**F — multi-type query.** ≥ 2 `QUERY_TYPE`. Blocks 2–5 independently per type, a separate C
each, one report section each. End with a **cross-type consistency note** (toxicologically
hazardous but unrestricted = a policy gap, not a data inconsistency).

**G — user data vs database.** A priority-2 source contradicts a priority 3–5 source. Apply
4.2 (database wins); emit ALERT-03 plus: "User-provided data (Priority 2) was overridden by
[Source] (Priority [N]). If the user believes their data is more current or context-specific,
manual review is recommended." The user data stays in the Source List and in C.

**H — high intra-database variability.** Same DB, divergent model types. List every entry with
its `model_type`; take the majority across all entries; compute C normally (divergence lowers
it by design); emit ALERT-04 per affected DB. Narrative: "C reflects genuine variability
across evaluation models within [DB]. This is expected when in vivo, in vitro and in silico
data coexist." Primary Finding from the highest available model type (Q-vivo preferred),
acknowledging the range from the others.

**I — normative only.** All items Type N, no Q. Compute C from directional consistency; emit
ALERT-05. Primary Finding = the consensus guidance statement, not a number. Note the absence
of quantitative data and recommend targeted searches for empirical values.

## Alerts

Output the text below, substituting the bracketed fields. **Every alert raised must appear in
the final report — never suppress one.**

**ALERT-01 — no usable documentary evidence.** No documents retrieved, all sources priority 1,
or Case D.
> "No documentary sources were located for Compound X. Confidence score C = 0. Results are
> based solely on model knowledge, which may be incomplete or outdated."

*Unknown-compound variant (Case E)*, appended:
> "Compound X could not be identified in any consulted registry. Verify the compound name,
> CAS number, or SMILES structure before re-querying."

**ALERT-02 — missing expected database.** An expected DB for the `QUERY_TYPE` (2.2) was not
consulted.
> "The following databases relevant to [QUERY_TYPE] were not included in the search: [list].
> Results may be incomplete."

**ALERT-03 — cross-source contradiction.** Once per inconsistency. **Confirm first that the
two sources share a data point** — a difference between two instruments, two species, two
routes, or a bound and a point estimate is not an inconsistency and must not raise this
alert. State three things: (1) the disputed data point written out in full (quantity,
species/population, route, averaging basis), so a reader can check that both sources really
do measure it; (2) each conflicting source, with its `priority_level` and reported
value/classification; (3) the resolution — **Source A**, **Source B**, or **Unresolved (both
reported)**. Close with: "User review recommended."

**ALERT-04 — intra-database model-type conflict.** Per affected DB when same-DB entries with
different `model_type` disagree. List each entry with its `model_type` and value; state that
the values are inconsistent across evaluation models; note that the Q-vivo result is more
representative for regulatory/toxicological decisions. Close with: "User review recommended."

**ALERT-05 — normative-only evidence.** Every retrieved item is Type N and no quantitative
value exists (Case I).
> "All available evidence for Compound X is normative (SOPs, guidelines, classifications). No
> quantitative values (LD50, NOAEL, EC50, etc.) were found in the consulted sources. The
> confidence score reflects consistency among qualitative sources only."
