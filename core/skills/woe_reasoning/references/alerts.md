# WoE Alert Catalog

Canonical text for every alert the skill can raise. When a block instructs "emit ALERT-0N", output the exact string below, substituting the bracketed fields. **Every alert raised during a run must appear in the final report (§5) — never suppress one.**

## ALERT-01 — No usable documentary evidence
Emit when no documents were retrieved (Block 1.4), all sources are priority 1 (Block 2.1.3), or evidence is otherwise absent (Case D).

> "No documentary sources were located for Compound X. Confidence score C = 0. Results are based solely on model knowledge, which may be incomplete or outdated."

**Unknown-compound variant (Case E):** append —
> "Compound X could not be identified in any consulted registry. Verify the compound name, CAS number, or SMILES structure before re-querying."

## ALERT-02 — Missing expected database
Emit when an expected database for the `QUERY_TYPE` (see Block 2.2 routing table) was not consulted.

> "The following databases relevant to [QUERY_TYPE] were not included in the search: [list]. Results may be incomplete."

## ALERT-03 — Cross-source contradiction
Emit once per inconsistency detected in Block 4.1. State three things:
1. The data point in dispute.
2. Each conflicting source, with its `priority_level` and reported value/classification.
3. The resolution: **Source A**, **Source B**, or **Unresolved (both reported)** — per the priority rule in 4.2.

Close with: "User review recommended."

## ALERT-04 — Intra-database model-type conflict
Emit per affected database when same-DB entries with different `model_type` values disagree (Block 4.4).

List each entry with its `model_type` and value; state that the values are inconsistent across evaluation models; note that the Q-vivo result is more representative for regulatory/toxicological decisions (per the model hierarchy). Close with: "User review recommended."

## ALERT-05 — Normative-only evidence
Emit when every retrieved item is Type N and no quantitative value exists (Case I).

> "All available evidence for Compound X is normative (SOPs, guidelines, classifications). No quantitative values (LD50, NOAEL, EC50, etc.) were found in the consulted sources. The confidence score reflects consistency among qualitative sources only."
