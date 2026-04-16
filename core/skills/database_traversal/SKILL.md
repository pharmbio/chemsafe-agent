---
name: database_traversal
description: Retrieve and extract chemical safety data from external databases and APIs. Use this skill whenever the agent needs to query, scrape, or traverse chemical safety sources — including PubChem (LCSS, compound records), NIOSH Pocket Guide (occupational exposure limits, PPE, symptoms), OPCW (chemical weapons convention compounds), or any similar regulatory/scientific chemical database. Trigger this skill when the task involves: resolving a chemical name or CAS number to safety data, traversing nested JSON records from PubChem, scraping HTML index tables followed by detail pages, calling embedded Power BI or undocumented APIs, batch processing a list of compounds, or selecting the right database for a given query type. Always consult this skill before writing any database retrieval code.
---

# Chemical Safety Database Traversal

This skill equips the agent to retrieve chemical safety data from three primary external sources. Each source has a distinct data model and access pattern. The agent should internalize these as capabilities and apply judgment about which to use based on what the user needs.

---

## Source Capabilities

### PubChem — REST API with Nested JSON Records

PubChem is the broadest source: GHS classification, physical properties, toxicity, LCSS safety summaries, and exposure limits. Access is a two-call sequence: resolve a name/synonym/CAS to a CID, then fetch the full compound record. The record is a deeply nested tree of `Section` objects keyed by `TOCHeading`. The agent must traverse this tree recursively to find relevant sections — there is no flat lookup.

The correct traversal pattern is recursive search through `record["Record"]["Section"]`, descending into `section["Section"]` children, collecting any section whose `TOCHeading` matches the target content. Text lives at leaf nodes inside `section["Information"][*]["Value"]["StringWithMarkup"][*]["String"]`.

Read `references/pubchem.md` for the full access pattern and extraction code.

### NIOSH NPG — HTML Index + Detail Page Scraping

NIOSH is the authoritative source for occupational safety: REL/IDLH exposure limits, required PPE, symptoms by exposure route, respirator recommendations. The data is split across two pages: an index table listing all compounds by CAS and name, and individual detail pages per compound.

The correct approach is to load the index table once into a DataFrame and reuse it across queries — not to re-fetch per compound. Detail pages are unstructured HTML; the extraction strategy is text-block slicing between known section title strings, not CSS/XPath selectors.

Read `references/niosh.md` for scraping patterns and section extraction.

### OPCW — Embedded Power BI API

OPCW covers chemical weapons convention scheduled compounds. The data is served through a Power BI embed with no public REST API. The correct access pattern is a single POST to the Power BI query endpoint using a known public resource key and dataset ID, requesting up to 10,000 rows at once. The response uses the non-standard Power BI DSR format requiring a specific parser with fallback paths.

OPCW does not support per-compound queries — the entire compound list must be fetched and filtered locally. Treat the fetched DataFrame as an in-memory lookup table.

Read `references/opcw.md` for the full payload, headers, and DSR parser.

---

## General Principles

**Choose the source based on what the user actually needs, not just what's easiest to query.** Occupational exposure limits → NIOSH. CWC schedule status → OPCW. Broad safety summary, GHS, physical properties → PubChem. When multiple concerns overlap, PubChem first, NIOSH to supplement.

**Resolve identifiers before fetching data.** All three sources require mapping a human query — a name, synonym, CAS number — to a source-specific identifier or match. The resolution approach differs per source. Read `references/id_resolution.md` for normalization, CAS detection, cross-source resolution, and handling ambiguous multi-match results.

**Return structured dicts, never raw responses.** Every retrieval function should return a typed dict with at minimum `found` (bool), `query` (original input), and either data fields or a `reason` string on failure. Never surface raw HTML, unparsed JSON, or exceptions to the caller.

**Failures are expected and should be typed.** The agent should distinguish: `not_found` (compound absent from the source), `timeout` (network failure), `parse_error` (source structure changed), `auth_error` (API key rotated). Each has a different implication for the user.

**For batch queries, process sequentially.** None of these sources have rate limits that require parallelism — sequential processing avoids hammering APIs and keeps error handling simple. Return a `pd.DataFrame` from batch functions so results are immediately usable downstream.

**Cache index data within a session.** NIOSH and OPCW return full datasets (the NIOSH index table, the OPCW compound list) that should be loaded once and reused, not re-fetched per compound.

---

## Reference Files

| File | Read when... |
|---|---|
| `references/pubchem.md` | Querying PubChem REST API or extracting LCSS/safety sections |
| `references/niosh.md` | Scraping NIOSH NPG index or detail pages |
| `references/opcw.md` | Querying OPCW via the Power BI endpoint |
| `references/id_resolution.md` | Resolving names, CAS numbers, or synonyms across any source |