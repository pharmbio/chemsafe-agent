# WoE Conditional Cases — Full Handling

Apply the **FIRST** matching case. The compact selector lives in SKILL.md; this file holds the full handling for each case. Read it when the run matches a case beyond the trivial "known, clean evidence" path.

## A — Known compound, evidence, no conflict
`EVIDENCE_FOUND = TRUE`, C ≥ 0.7, no ALERT-03.
Run all five blocks. Report C with ✅/🟡. State the primary finding with confidence. No special alerts.

## B — Known compound, evidence, conflicts
ALERT-03 raised.
Run all blocks. Apply priority resolution (4.2); C < 1.0 by definition. Include **all** ALERT-03 instances and do not suppress minority findings. In §5, clearly label which source was prioritised.

## C — Known compound, partial coverage
ALERT-02 raised (expected DBs missing).
Compute C only from available sources. Append to the Narrative: "Score may underestimate uncertainty due to missing coverage of [database list]." Surface ALERT-02 prominently.

## D — Known compound, no evidence
`EVIDENCE_FOUND = FALSE` or all sources priority 1.
C = 0; emit ALERT-01; skip Blocks 2–3; apply 4.6. §5 Primary Finding must state: "Insufficient evidence. No reliable conclusion can be drawn from consulted sources." Label any fallback estimate ⚠️ per 4.6.

## E — Unknown / novel compound
No CAS, no registry entry.
`EVIDENCE_FOUND = FALSE`, C = 0; emit ALERT-01 with the unknown-compound variant (see references/alerts.md). Ask the user for CAS / SMILES / InChIKey / synonyms. Do **not** proceed to inference — output is limited to the identification-gap notice.

## F — Multi-type query
≥ 2 `QUERY_TYPE` values.
Run Blocks 2–5 independently per type; compute a separate C per type; present one report section per type. End with a **Cross-type consistency note** flagging cross-domain contradictions (e.g. toxicologically hazardous but no regulatory restriction = a policy gap, not a data inconsistency).

## G — User data vs database
A priority-2 source contradicts a priority 3–5 source.
Apply 4.2 (database wins); emit ALERT-03 plus: "User-provided data (Priority 2) was overridden by [Source Name] (Priority [N]). If the user believes their data is more current or context-specific, manual review is recommended." Still include the user data in the Source List and the C calculation.

## H — High intra-database variability
Same DB, divergent model types.
List all entries individually with `model_type`. Determine the majority across all entries (3.1); compute C normally (each counts in T; divergence lowers C by design). Emit ALERT-04 per affected DB. Narrative: "C score reflects genuine variability across evaluation models within [Database Name]. This is expected when in vivo, in vitro, and in silico data coexist for the same compound." Primary Finding = value from the highest available model type (Q-vivo preferred), while acknowledging the range reported by other models.

## I — Normative-only
All items Type N, no Q.
Compute C via normative agreement (3.1 directional consistency); emit ALERT-05. Primary Finding = the consensus guidance statement, not a number. Narrative notes the absence of quantitative data and recommends targeted database searches for empirical values.
