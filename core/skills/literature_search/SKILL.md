---
name: literature_search
description: Use this skill whenever scientific literature evidence is needed from PubMed / PMC — to ground a WoE conclusion, locate hazard or mechanistic studies for a chemical, find dose-response or epidemiological data, verify a claim, or supplement SOP and database retrieval with peer-reviewed evidence.
---

# Literature Search (PubMed / PMC via LitSense2)

Every literature claim entering a WoE evidence table, regulatory argument, or safety conclusion must trace back to a PMID (or PMCID) recovered through this skill. Never write "known from the literature" without one.

The backing API is NCBI LitSense2, which differs from PubMed E-utilities in two ways that drive everything below:

1. **Semantic, not boolean.** Natural-language phrases outperform field-tagged queries. Boolean operators, parentheses, and MeSH tags are ignored at best and degrade the embedding at worst.
2. **Passage-level, not record-level.** Results are paragraphs (or sentences). One PMID can return several passages from different sections. The passage is the unit of evidence; map it back to the parent article afterwards.

---

## Step 1 — Frame the question (PESO)

- **P**opulation / system — species, strain, cell line, human subpopulation, environment
- **E**xposure / substance — chemical identity + route + dose range + duration
- **S**tudy type / endpoint — the observation or measurement
- **O**utcome / context — regulatory or mechanistic frame (IARC classification, REACH, AOP key event)

Unknown slots become open variables that the query set varies across in Step 3 — don't collapse them into one generic query.

## Step 2 — Expand identifiers before querying

LitSense2 does no identifier resolution, so recall depends on the synonym set you supply. For each substance, cover: IUPAC name, common/trivial name, trade name or INN, CAS number, acronym, and any hazard-driving metabolite.

Given only a CAS or SMILES, resolve to names first via `database_traversal` (`id_resolution.md`) or `pubchempy` through `python_executor`. The semantic model favours prose over identifiers, so a CAS-only query under-recalls badly.

Issue one query per name form × endpoint. Clean phrases rank better than disjunctions.

## Step 3 — Build a query set (3–6 queries, floor not ceiling)

Write noun phrases of ≤15 words — longer queries dilute semantic focus. Include the species when the endpoint is species-specific, or human epidemiology will bleed into an animal-tox query.

Vary the angle across: mechanism / molecular initiating event, apical organ- or population-level effect, dose and limit values (NOAEL, LOAEL, reference dose), species bridge (human ↔ animal ↔ in vitro), and mode of action (AOP key event or KER).

Three angles are mandatory:

- **Null / negative**, one per hazard endpoint — regulators require an active search for disconfirming evidence; omitting it is cherry-picking (`woe_reasoning` Step 4).
- **Review / meta** — systematic reviews are disproportionately useful for WoE integration.
- **Regulatory frame** (EFSA, ECHA, IARC, EPA, OECD TG number, Klimisch) whenever the deliverable feeds a dossier.

```python
queries = [
    "bisphenol A hepatocyte oxidative stress mitochondrial dysfunction",      # mechanism
    "bisphenol A liver toxicity subchronic oral rat histopathology",          # apical + species
    "bisphenol A NOAEL LOAEL hepatic effects rodent",                        # dose
    "bisphenol A human biomonitoring liver enzymes alanine aminotransferase", # human bridge
    "bisphenol A no observed adverse effect liver rodent",                    # null
    "bisphenol A hepatotoxicity systematic review",                          # review
    "bisphenol A EFSA opinion tolerable daily intake liver",                  # regulatory
]
```

## Step 4 — Retrieve

```python
from core.skills.literature_search.scripts.literature_search import (
    batch_literature_search,
    dedupe_across_queries,
    format_passages_for_prompt,
    literature_search,
)

per_query = batch_literature_search(
    queries,
    per_query_limit=10,   # 5–15; higher mostly adds low-relevance passages and token cost
    mode="passages",      # paragraph context is what WoE tables need
    rerank=True,          # disable only to inspect raw retrieval ordering
    min_score=None,       # local post-fetch filter, not an API param
)

unique_passages = dedupe_across_queries(per_query)  # by pmid + pmcid + text prefix
```

Use `mode="sentences"` only when you need one precise fact (a quoted NOAEL, a reference dose statement). Calibrate `min_score` per topic: start at `None`, inspect the scores, then set a floor (~0.5) on later runs.

Both functions swallow HTTP and JSON errors and return `[]` so downstream logic can branch cleanly — an empty result set is a retrieval gap (Step 6), never proof that the literature is silent.

## Step 5 — Attribute and extract

Record per retained passage: **PMID** (required), PMCID, **section**, verbatim text, LitSense score, and any entity annotations. Keep text verbatim — paraphrasing here destroys traceability; paraphrase only in `woe_reasoning` Step 5 argumentation.

Always capture the section, because two passages sharing a PMID can carry very different weight:

| Section | Weighting implication |
|---|---|
| `ABSTRACT` | Screening only; never sufficient for numeric extraction |
| `METHODS` | Required to score reliability (Klimisch, OECD TG adherence, GLP) and decisive for in-vivo vs. in-vitro classification |
| `RESULTS` | Primary source for numeric values (NOAEL, LC50, incidences) |
| `DISCUSSION` / `CONCLUSION` | Authorial interpretation — cite as opinion, not primary evidence |
| `INTRODUCTION` / `REVIEW` | Context only; never the sole source of a quantitative claim |

If `METHODS` wasn't retrieved but study design matters, re-query with its typical phrasing (e.g. `bisphenol A hepatocyte primary culture incubation`).

## Step 6 — Fallback for thin or empty results

Work down this list before concluding anything:

1. Broaden the substance term — trade name → IUPAC, drop stereochemistry, try the parent compound instead of the metabolite.
2. Drop the species term to scan across species, then filter afterwards.
3. Retry in `sentences` mode.
4. Lower endpoint specificity (`hepatotoxicity` → `liver adverse effect`).
5. Swap a failed apical-effect query for a mechanistic or regulatory-frame one, or vice versa.

Still empty on a canonical PESO slot? Log it in the `woe_reasoning` Step 6 data-gap inventory. Absence of retrieval is not absence of effect.

## Step 7 — Compile the evidence table

One row **per passage**, not per PMID — three distinct findings from one article are three rows.

| ID | PMID | PMCID | Section | Query that surfaced it | Score | Verbatim text | Proposed line of evidence | Direction (supports / conflicts / neutral) | Notes |
|---|---|---|---|---|---|---|---|---|---|

Persist it with `prepare_output_path("literature_evidence.csv")` inside `python_executor` so reviewers can audit retrieval before any conclusion is drawn.

Assign **direction** by reading the passage; the score measures semantic relevance to your query, not the direction or strength of the finding. If `pmid` is `None`, flag the row and keep it out of the evidence table — the source may not be peer-reviewed. Never invent a PMID.

## Step 8 — Hand off

- **`woe_reasoning` Step 3** — each row still needs its own Klimisch / ToxRTool / CRED / OHAT reliability score derived from the underlying study. The LitSense score is a retrieval score and must never be substituted for one.
- **`sop_search`** — cross-check every numeric threshold recovered from literature before treating it as authoritative.
- **`database_traversal`** (`id_resolution.md`) — resolve ambiguous chemical matches before a passage enters the table; one trivial name can map to several CAS numbers.