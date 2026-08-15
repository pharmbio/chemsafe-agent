---
name: database_traversal
description: Retrieve chemical safety data from external authoritative sources — PubChem compound records and LCSS, the NIOSH Pocket Guide for occupational exposure limits and PPE, OPCW for Chemical Weapons Convention schedules, and ECHA CHEM for REACH dossiers, CLP and harmonised classification, DNELs, PBT and vPvB assessments and ecotoxicity. Use to resolve a chemical name, CAS or EC number to real records, to obtain a classification, exposure limit or hazard statement that must be cited to a source, or to batch-query a list of compounds. Consult it before writing any retrieval code. For values computed from structure rather than looked up, use cheminformatics.
---

# Chemical Safety Database Traversal

This skill equips the agent to retrieve chemical safety data from four primary external sources. Each source has a distinct data model and access pattern. The agent should internalize these as capabilities and apply judgment about which to use based on what the user needs.

---

## Source Capabilities

### PubChem — REST API with Nested JSON Records

PubChem is the broadest source: GHS classification, physical properties, toxicity, LCSS safety summaries, and exposure limits. Access is a two-call sequence: resolve a name/synonym/CAS to a CID, then fetch the full compound record. The record is a deeply nested tree of `Section` objects keyed by `TOCHeading`. The agent must traverse this tree recursively to find relevant sections — there is no flat lookup.

The correct traversal pattern is recursive search through `record["Record"]["Section"]`, descending into `section["Section"]` children, collecting any section whose `TOCHeading` matches the target content. Text lives at leaf nodes inside `section["Information"][*]["Value"]["StringWithMarkup"][*]["String"]`.

```python
base = "https://pubchem.ncbi.nlm.nih.gov/rest"
# Always pass an explicit timeout: a request without one can hang until the whole
# execution is cut off, losing the work done before it.
cid = requests.get(f"{base}/pug/compound/name/{query}/cids/JSON", timeout=15).json()["IdentifierList"]["CID"][0]
record = requests.get(f"{base}/pug_view/data/compound/{cid}/JSON", timeout=30).json()
# then recurse record["Record"]["Section"], matching TOCHeading — see pubchem.md
```

Read `references/pubchem.md` for the full access pattern and extraction code.

### NIOSH NPG — HTML Index + Detail Page Scraping

NIOSH is the authoritative source for occupational safety: REL/IDLH exposure limits, required PPE, symptoms by exposure route, respirator recommendations. The data is split across two pages: an index table listing all compounds by CAS and name, and individual detail pages per compound.

The correct approach is to load the index table once into a DataFrame and reuse it across queries — not to re-fetch per compound. Detail pages are unstructured HTML; the extraction strategy is text-block slicing between known section title strings, not CSS/XPath selectors.

```python
df_niosh = get_niosh_table()                    # scrape index once, reuse across queries
entry = find_niosh_entry("acetone", df_niosh)   # local filter by name/CAS
data = scrape_niosh_page(entry["link"])         # slice detail HTML between section titles
```

Read `references/niosh.md` for scraping patterns and section extraction.

### OPCW — Embedded Power BI API

OPCW covers chemical weapons convention scheduled compounds. The data is served through a Power BI embed with no public REST API. The correct access pattern is a single POST to the Power BI query endpoint using a known public resource key and dataset ID, requesting up to 10,000 rows at once. The response uses the non-standard Power BI DSR format requiring a specific parser with fallback paths.

OPCW does not support per-compound queries — the entire compound list must be fetched and filtered locally. Treat the fetched DataFrame as an in-memory lookup table.

```python
df_opcw = fetch_opcw_compounds()                # one Power BI POST, up to 10,000 rows
name_col = df_opcw.columns[0]
matches = df_opcw[df_opcw[name_col].str.lower().str.contains("sarin", na=False)]  # filter locally
```

Read `references/opcw.md` for the full payload, headers, and DSR parser.

### ECHA CHEM — EU Regulatory Classification + REACH Dossiers

ECHA CHEM is the authoritative source for **EU regulatory hazard data**: the legally binding harmonised classification (Annex VI of CLP), industry CLP notifications, and the study-level data registrants file under REACH — DN(M)ELs, PBT/vPvB assessments, and the full tox/ecotox record. It complements PubChem (broad, global) and NIOSH (US occupational) with the European regulatory view.

Access is a self-contained **async Python toolset** under `scripts/echa_api/` — call the `tool_*` coroutines, not raw HTTP. Everything is keyed by an **ECHA substance index** (e.g. `100.000.002` for formaldehyde), so Step 0 is always resolving the chemical to its index. Two data planes sit behind the tools: fast JSON APIs (identifiers, CLP and harmonised classification) and slower *rendered dossier HTML* scraped page-by-page (REACH Section 2.1 GHS, 2.3 PBT, 5/6 ecotox, 7 toxicology) — prefer summary/section-filtered variants over the full toxicology dump.

The tools are `async` and share one connection pool bound to a single event loop, so keep **all ECHA awaits inside one `asyncio.run(...)`** — scattering multiple `asyncio.run()` calls raises loop errors.

```python
async def fetch(query):
    idx = json.loads(await resolve_substance_index(query))["substance_index"]  # Step 0
    harm = json.loads(await tool_get_harmonised_classification(idx))
    await get_client().close()          # release the shared pool once, at the end
    return harm
data = asyncio.run(fetch("formaldehyde"))   # one asyncio.run — never per-compound
```

A distinction the tools mirror, and which the question must disambiguate: **harmonised classification** (official, Commission-adopted, not every substance has one) vs **CLP notification** (industry self-classification, majority-vote style) vs **REACH Section 2.1 GHS** (the lead registrant's own classification). These can disagree.

Read `references/ECHA.md` for the tool catalog, substance-index resolution, and full pipeline templates.

---

## General Principles

**Choose the source based on what the user actually needs, not just what's easiest to query.** Occupational exposure limits → NIOSH. CWC schedule status → OPCW. Broad safety summary, GHS, physical properties → PubChem. EU harmonised/CLP classification, DNELs, PBT status, or REACH study data → ECHA CHEM. When concerns overlap, start with PubChem and supplement with NIOSH or ECHA.

**Resolve identifiers before fetching data.** Every source requires mapping a human query — a name, synonym, CAS number — to a source-specific identifier or match, and the resolution approach differs per source. Read `references/id_resolution.md` for normalization, CAS detection, cross-source resolution, and handling ambiguous multi-match results.

**Return structured dicts, never raw responses.** Every retrieval function should return a typed dict with at minimum `found` (bool), `query` (original input), and either data fields or a `reason` string on failure. Never surface raw HTML, unparsed JSON, or exceptions to the caller.

**Failures are expected and should be typed.** The agent should distinguish: `not_found` (compound absent from the source), `timeout` (network failure), `parse_error` (source structure changed), `auth_error` (API key rotated). Each has a different implication for the user.

**For batch queries, process sequentially and validate before returning.** None of these sources have rate limits that require parallelism — sequential processing avoids hammering APIs and keeps error handling simple. Return a `pd.DataFrame` from batch functions so results are immediately usable downstream. After a batch run, **validate each row**: confirm it has `found=true` or a typed `reason`. Then **fix and retry only the transient failures** (`timeout`, `parse_error`) — and `not_found` rows only if worth re-querying by CAS instead of name; leave genuine `not_found` and `auth_error` rows as-is rather than blindly re-running the whole batch. One retry pass is usually enough; rows still failing after it are reported, not looped on.

**Cache index data within a session.** NIOSH and OPCW return full datasets (the NIOSH index table, the OPCW compound list) that should be loaded once and reused, not re-fetched per compound.