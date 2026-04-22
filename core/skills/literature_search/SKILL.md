---
name: literature_search
description: Use this skill whenever scientific literature evidence is needed from PubMed / PMC — to ground a WoE conclusion, locate hazard or mechanistic studies for a chemical, find dose-response or epidemiological data, verify a claim, or supplement SOP and database retrieval with peer-reviewed evidence.
---

# Literature Search Skill (PubMed / PMC via LitSense2)

Retrieve peer-reviewed evidence to ground decisions that would otherwise rest on narrative summary. Every piece of literature evidence entering a WoE evidence table, a regulatory argument, or a safety conclusion must trace back to a PMID (or PMCID) recovered through this skill. Do not paraphrase "known from the literature" without a PMID in the evidence table.

The backing API is **NCBI LitSense2** (`litsense2-api`), which performs **semantic passage-level retrieval** over PubMed abstracts and PMC Open Access full text. This differs from classical PubMed E-utilities in two important ways:

1. **Semantic, not boolean.** Multi-word natural-language queries outperform `"acid"[MeSH] AND "exposure"[MeSH]`. Boolean operators and field tags are ignored.
2. **Passage-level, not record-level.** Results are paragraphs (or sentences in sentence mode). A single PMID can return multiple passages from different sections. Treat the unit of evidence as the passage, then map it back to the parent article.

---

## Step 1 — Formulate the Literature Question (PESO)

Before issuing any query, articulate the question along the **PESO** dimensions. This is the literature analogue of `sop_search`'s domain articulation and `woe_reasoning`'s problem formulation.

| Dimension       | What to specify                                                  | Example                                                    |
|-----------------|-------------------------------------------------------------------|------------------------------------------------------------|
| **P**opulation / system | Species, strain, cell line, human subpopulation, environment | "Sprague-Dawley rat", "HepG2", "occupational cohort", "fathead minnow" |
| **E**xposure / substance | Chemical identity + route + dose range + duration            | "hydrogen fluoride inhalation", "bisphenol A oral subchronic" |
| **S**tudy type / endpoint | The observation or measurement                               | "hepatotoxicity", "estrogen receptor binding", "LC50", "genotoxicity Ames" |
| **O**utcome / context    | Regulatory or mechanistic frame                              | "IARC classification", "REACH registration", "AOP key event" |

**Rule:** If any PESO slot is unknown, record it as an open variable and let the query vary it across the query set below. Do not collapse unknowns into a generic single query.

---

## Step 2 — Identifier Strategy (critical for chemical searches)

Chemical name handling determines recall. LitSense2 does not do identifier resolution — you do, by **expanding the substance into a synonym set before querying**.

For every chemical involved, build a synonym list covering:

- **IUPAC name** (e.g. `2,2-bis(4-hydroxyphenyl)propane`)
- **Common / trivial name** (e.g. `bisphenol A`)
- **Trade names / INN** where applicable
- **CAS number** (e.g. `80-05-7`) — literature often cites CAS directly
- **Acronym / abbreviation** (e.g. `BPA`)
- **Key metabolite** if the hazard is metabolite-driven (e.g. `BPA-glucuronide`)

If you only know a CAS or a SMILES, resolve to names first using the `database_traversal` skill (`id_resolution.md`) or a `pubchempy` call through `python_executor`. Issuing a LitSense2 query with only a CAS number will under-recall — the semantic model favors prose over identifiers.

Generate **one query per name form plus endpoint** rather than a single union query. The semantic model ranks better on clean natural-language phrases than on disjunctions.

---

## Step 3 — Construct the Query Set (3–6 angled queries)

A single query rarely captures all relevant evidence. Build a query set that covers PESO from multiple angles. Follow the same philosophy as `sop_search`: **precision beats recall, and multiple narrow queries beat one broad one.**

### Query dimensions to vary

| Angle            | What to vary                                            | Example                                                              |
|------------------|---------------------------------------------------------|----------------------------------------------------------------------|
| **Mechanism**    | Molecular initiating event, target, pathway            | "bisphenol A estrogen receptor binding affinity"                    |
| **Apical effect**| Organ-level or population-level outcome                | "bisphenol A mammary gland proliferation rat"                       |
| **Dose / limit** | Dose range, NOAEL/LOAEL, reference value               | "bisphenol A NOAEL subchronic oral rat"                             |
| **Species bridge**| Human → animal or animal → in vitro                   | "bisphenol A human biomonitoring urinary concentration"             |
| **Negative / null** | Active search for absence of effect                 | "bisphenol A no observed adverse effect reproductive"               |
| **Review / meta**| Systematic reviews and pooled analyses                 | "bisphenol A systematic review endocrine disruption"                |
| **Regulatory frame** | Named framework or authority                       | "bisphenol A EFSA tolerable daily intake"                           |
| **Mode of action**| AOP key event or KER                                  | "bisphenol A aromatase inhibition granulosa cell"                   |

### Query construction rules

- **Write queries as noun phrases**, not boolean expressions. Good: `bisphenol A hepatotoxicity subchronic oral rat`. Bad: `"bisphenol A" AND (hepatotoxicity OR liver)`.
- **Include the species / system** when the endpoint is species-specific. Without it, the semantic model will mix human epidemiology into an animal-tox query.
- **Include one negative/null query** for every hazard endpoint. Regulators require that WoE actively search for disconfirming evidence — omitting this is cherry-picking (see `woe_reasoning` Step 4).
- **Include one review/meta query** to surface systematic reviews; these are disproportionately useful for WoE integration.
- **At least one query must use the regulatory frame** (EFSA, ECHA, IARC, EPA, OECD TG number, Klimisch score) if the deliverable feeds a dossier.
- **Do not include boolean operators, parentheses, or MeSH tags.** LitSense2 ignores them; at best neutral, at worst they lower the embedding quality.
- **Keep each query ≤ 15 words.** Long queries dilute semantic focus.

### Worked example — `bisphenol A` hepatotoxicity WoE

```python
queries = [
    # Mechanism
    "bisphenol A hepatocyte oxidative stress mitochondrial dysfunction",
    # Apical effect + species
    "bisphenol A liver toxicity subchronic oral rat histopathology",
    # Dose
    "bisphenol A NOAEL LOAEL hepatic effects rodent",
    # Human bridge
    "bisphenol A human biomonitoring liver enzymes alanine aminotransferase",
    # Null / negative
    "bisphenol A no observed adverse effect liver rodent",
    # Review
    "bisphenol A hepatotoxicity systematic review",
    # Regulatory frame
    "bisphenol A EFSA opinion tolerable daily intake liver",
]
```

---

## Step 4 — Execute Retrieval

The skill ships a sandbox-safe helper. Import it from `python_executor` exactly like `sop_search`:

```python
from core.skills.literature_search.scripts.literature_search import (
    batch_literature_search,
    dedupe_across_queries,
    format_passages_for_prompt,
    literature_search,
)

per_query = batch_literature_search(
    queries,
    per_query_limit=10,   # passages per query
    mode="passages",      # "passages" (default) or "sentences"
    rerank=True,
    min_score=None,       # set e.g. 0.5 once calibrated for the topic
)

# Flatten and deduplicate across the whole set (by pmid + pmcid + text prefix).
unique_passages = dedupe_across_queries(per_query)
```

### Parameter guidance

- **`mode="passages"`** is the default — paragraph-level context is what WoE evidence tables need. Use `"sentences"` only when you need a specific factual sentence (a quoted NOAEL value, a reference dose statement) and want maximum precision.
- **`per_query_limit`** should be 5–15 per query. Higher values mostly return low-relevance passages that inflate token cost without adding evidence.
- **`rerank=True`** is on by default. Turn it off only to inspect the raw retrieval ordering during debugging.
- **`min_score`** is a **local filter** applied after fetch — it does not change the API call. Calibrate per topic: start at `None`, inspect scores, then set a floor (e.g. 0.5) on subsequent runs to trim low-confidence matches.

### Error handling

`literature_search` and `batch_literature_search` **never raise** on HTTP or JSON errors — they return `[]` (or an empty list per query) so downstream logic can branch cleanly. If an entire query set returns empty results, treat that as a retrieval gap (Step 6), not a proven absence of literature.

---

## Step 5 — Review, Attribute, and Extract

For each passage retained, record the attributes that downstream WoE needs:

- **PMID** (required) and **PMCID** (if present)
- **Section** (e.g. `RESULTS`, `METHODS`, `ABSTRACT`) — materially affects how to weigh the passage
- **Text** (verbatim — never paraphrase before it enters the evidence table)
- **Score** (LitSense2 relevance score)
- **Annotations** (entity tags returned by the API, where present)

Section guidance for weighing:

| Section                     | Weighting implication                                                     |
|-----------------------------|----------------------------------------------------------------------------|
| `ABSTRACT`                  | Sufficient for screening; not sufficient for numeric extraction            |
| `METHODS`                   | Needed to score reliability (Klimisch, OECD TG adherence, GLP)             |
| `RESULTS`                   | Primary source for numeric values (NOAEL, LC50, incidences)               |
| `DISCUSSION` / `CONCLUSION` | Authorial interpretation — cite as opinion, not as primary evidence        |
| `INTRODUCTION` / `REVIEW`   | Use only for context; never as the sole source of a quantitative claim    |

**Always capture the section.** Two passages with the same PMID but different sections carry different evidentiary weight.

---

## Step 6 — Handle Missing or Thin Results

If the initial query set returns nothing, or returns only peripheral passages, **do not conclude the literature is silent** until you have exhausted a fallback pass.

Fallback sequence, in order:

1. **Broaden the substance term** — swap trade name for IUPAC, drop stereochemistry qualifiers, try the parent compound if the query was about a metabolite.
2. **Relax the species / system** — drop the species term entirely to scan across species, then filter afterwards.
3. **Retry in `sentences` mode** — if passages mode missed it, the relevant fact may be a single sentence.
4. **Lower the endpoint specificity** — e.g. `hepatotoxicity` → `liver adverse effect`.
5. **Swap to a mechanistic or regulatory-frame query** if an apical-effect query failed (and vice versa).

If after the fallback pass the literature is still empty on a canonical PESO slot, record it as a **retrieval gap** in the WoE data-gap inventory (`woe_reasoning` Step 6). Absence of retrieval is not evidence of absence of effect.

---

## Step 7 — Compile the Literature Evidence Table

Consolidate retained passages into a table that is directly compatible with the `woe_reasoning` evidence table:

| ID | PMID | PMCID | Section | Query that surfaced it | Score | Verbatim text | Proposed line of evidence | Direction (supports / conflicts / neutral) | Notes |
|----|------|-------|---------|------------------------|-------|----------------|---------------------------|-------------------------------------------|-------|

Persist the table to the conversation output scope (`prepare_output_path("literature_evidence.csv")` inside `python_executor`) so reviewers can audit the retrieval before any conclusion is drawn.

**Rules:**

- **One row per passage, not per PMID.** If three passages from one article each carry distinct evidence, they are three rows.
- **Verbatim text only.** Paraphrasing at this stage destroys traceability.
- **Never invent a PMID.** If a passage has `pmid = None`, keep it flagged and do not cite it as peer-reviewed evidence.
- **Direction must be assigned by a human-readable reading of the passage**, not by the LitSense score. Score measures semantic relevance to the query, not the direction of the finding.

---

## Step 8 — Integrate with Downstream Skills

- **For WoE conclusions**, pass the evidence table into `woe_reasoning` Step 3 (per-evidence scoring). Each literature row must still receive a Klimisch/ToxRTool/CRED/OHAT score based on the underlying study — the LitSense score is a retrieval relevance score, not a reliability score and must never be substituted for one.
- **For regulatory thresholds**, cross-reference each numeric value recovered from literature against `sop_search` results before treating it as authoritative.
- **For chemical identification**, resolve ambiguous matches via `database_traversal` (`id_resolution.md`) before adding a passage to the evidence table — the same trivial name can refer to multiple CAS numbers.
- **For in-vivo vs. in-vitro classification** of a retrieved study, the passage's `METHODS` section is decisive. If `METHODS` was not retrieved, issue a follow-up query constraining to that section's typical phrasing (e.g. `"bisphenol A" hepatocyte primary culture incubation`).

---

## Anti-Patterns (explicit don'ts)

- **Don't use boolean operators / MeSH tags / field qualifiers.** LitSense2 is semantic. `"acid"[MeSH]` degrades results.
- **Don't treat LitSense score as reliability.** It is a retrieval relevance score. Reliability is scored separately by Klimisch / CRED / ToxRTool in `woe_reasoning`.
- **Don't cite a passage without its PMID.** Passages with `pmid = None` come from sources that may not be peer-reviewed; never promote them to the evidence table.
- **Don't skip the null/negative query.** Regulators explicitly require WoE to search for disconfirming evidence.
- **Don't paraphrase at retrieval time.** Keep verbatim text in the evidence table; paraphrase only inside the integrated argumentation in `woe_reasoning` Step 5.
- **Don't rely on a single broad query.** A 3–6 query set is the floor, not a ceiling.
- **Don't exceed ~15 words per query.** Longer queries dilute the embedding.
- **Don't ignore the section tag.** A `DISCUSSION` passage is authorial opinion, not primary evidence.
