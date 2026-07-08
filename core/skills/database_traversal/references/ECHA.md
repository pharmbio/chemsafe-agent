# ECHA CHEM Reference

**Source type:** Async Python toolset over ECHA CHEM JSON APIs + rendered dossier HTML
**Base URL:** `https://chem.echa.europa.eu`
**Toolset:** `core/skills/database_traversal/scripts/echa_api/` (call the resolver + `tool_*` coroutines — do not write raw HTTP)
**Auth:** None. (The client sets a browser User-Agent and uses `verify=False`; TLS verification is intentionally relaxed for this host.)
**Rate limit:** No published limit. Be gentle — call sequentially, and prefer summary/section-filtered tools over full dossier dumps.

ECHA CHEM is the European Chemicals Agency's public database. Use it for the **EU regulatory view** of a substance: legally binding harmonised classification (Annex VI, CLP), industry CLP self-classification notifications, and the study-level data registrants file under REACH — DN(M)ELs, PBT/vPvB assessments, and the full tox/ecotox record.

---

## Step 0 (always): resolve the substance index

Every tool is keyed by an **ECHA substance index**, a dotted number like `100.000.002` (formaldehyde). This is ECHA's InfoCard / list number — *not* a CAS or EC number, and it cannot be computed from either. So **the first step of any ECHA task is to resolve the chemical to its index** with `substance_id_resolve`, then pass that index into every other tool.

```python
from core.skills.database_traversal.scripts.echa_api.substance_id_resolve import (
    resolve_substance_index,   # name/CAS -> single best index (use this)
    search_substances,         # name/CAS -> all candidates (use to disambiguate)
)

r = json.loads(await resolve_substance_index("formaldehyde"))   # or a CAS like "50-00-0"
# -> {"resolved": true, "ambiguous": false, "substance_index": "100.000.002",
#     "best": {...}, "candidates": [...]}
index = r["substance_index"]
```

Rules of thumb:

1. **Prefer CAS over name.** A CAS number resolves cleanly to one substance. A name can match isomers, reaction masses, or `"…and releasers"` pseudo-entries — the resolver down-weights those, but a CAS is unambiguous.
2. **Check `resolved` and `ambiguous`.** If `resolved` is false there was no match — do not proceed with a guessed index. If `ambiguous` is true, inspect `candidates` (or call `search_substances`) and confirm the right substance before trusting downstream classification data; when unsure, ask the user or re-query by CAS.
3. **Don't guess or compute the index from the EC number** — the mapping is not a clean formula across inventories.
4. If a chemical genuinely cannot be resolved, say so and fall back to PubChem for classification rather than returning data for the wrong substance.

Worked example: `resolve_substance_index("50-00-0")` → `100.000.002` → CAS `50-00-0`, formaldehyde.

---

## Invocation pattern (read this before writing any ECHA code)

The tools are `async` and share a single module-level `httpx` connection pool. That pool is bound to the event loop it was created on, so **do all ECHA work inside one `asyncio.run(...)`** — scattering multiple `asyncio.run()` calls will reuse a pool bound to a dead loop and raise runtime errors.

```python
import asyncio
import json

from core.skills.database_traversal.scripts.echa_api.substance_id_resolve import resolve_substance_index
from core.skills.database_traversal.scripts.echa_api.tools import (
    tool_get_substance_info,
    tool_get_harmonised_classification,
    tool_get_toxicology_summary,
)
from core.skills.database_traversal.scripts.echa_api.echa_client import get_client


async def fetch_echa(query: str) -> dict:
    # All awaits happen on the SAME loop — batch every ECHA call in here.
    resolved = json.loads(await resolve_substance_index(query))   # Step 0
    if not resolved["resolved"]:
        await get_client().close()
        return {"error": f"could not resolve '{query}' to an ECHA substance index"}
    index = resolved["substance_index"]

    info = json.loads(await tool_get_substance_info(index))
    harmonised = json.loads(await tool_get_harmonised_classification(index))
    tox = json.loads(await tool_get_toxicology_summary(index))
    await get_client().close()  # release the shared pool at the end
    return {"index": index, "info": info, "harmonised": harmonised, "tox_summary": tox}


data = asyncio.run(fetch_echa("formaldehyde"))   # a name or CAS — not an index
```

Every coroutine returns a **JSON string** — parse it with `json.loads`. On failure the JSON carries an `"error"` key (tools) or `resolved: false` (resolver) instead of data; always check before using the result.

---

## Tool catalog

Resolver — import from `core.skills.database_traversal.scripts.echa_api.substance_id_resolve`:

| Tool | Returns | Use when |
|---|---|---|
| `resolve_substance_index(query, max_results=10)` | Best-match index + ranked candidates (`resolved`, `ambiguous`, `substance_index`) | **Step 0** — turn a name/CAS into an index before anything else |
| `search_substances(query, max_results=10)` | All raw candidates (each with its `substance_index`) | Disambiguate an ambiguous name; browse every match |

Data tools — import from `core.skills.database_traversal.scripts.echa_api.tools`:

| Tool | Returns | Use when |
|---|---|---|
| `tool_get_substance_info(index)` | CAS, EC, chemical/IUPAC names, formula, InChI/SMILES | Right after resolving — confirm the index is the substance you meant |
| `tool_list_dossiers(index, status="Active", max_results=10)` | REACH registration dossiers (asset IDs, subtype, role, dates) | Discover what dossiers exist before pulling section data |
| `tool_get_harmonised_classification(index)` | Annex VI classification: categories, H-codes, signal word, pictograms, SCL, M-factors, ATE, notes | You need the **official EU** classification |
| `tool_get_clp_classification(index, max_results=5)` | Industry CLP notifications, sorted by notifier agreement % | You need **industry self-classification** / notification consensus |
| `tool_get_reach_ghs(index)` | Registrant's own GHS classification (dossier Section 2.1) | You need the **lead registrant's** classification specifically |
| `tool_get_reach_pbt(index)` | PBT/vPvB status and P/vP, B/vB, T conclusions (Section 2.3) | Persistence / bioaccumulation / toxicity assessment |
| `tool_get_toxicology_summary(index)` | Section 7 summaries + DN(M)EL values only (fast) | Quick tox overview or DNEL lookup |
| `tool_get_toxicology_studies(index, section=None, max_studies=50)` | Study-level records (species, route, effect levels), optionally one subsection | Study detail for a specific endpoint (e.g. `section="7.2"`) |
| `tool_get_toxicology_full(index)` | Everything in Section 7 (summaries + up to 100 studies + DNELs) | Comprehensive tox pull — **slow**, use only when needed |
| `tool_get_ecotoxicology_data(index, section=None, max_studies=50)` | Section 5 environmental fate + Section 6 ecotox (aquatic/sediment/terrestrial, PNEC) | Environmental fate / ecotoxicity |
| `resource_hcode_mapping()` / `resource_hcode_mapping_json()` | GHS hazard-category → H-code table (Markdown / JSON) | Map short category codes (`Acute Tox. 4 (Oral)`) to H-codes (`H302`) |

---

## Two data planes

- **JSON APIs** (fast, structured): `tool_get_substance_info`, `tool_list_dossiers`, `tool_get_clp_classification`, `tool_get_harmonised_classification`.
- **Rendered dossier HTML** (slower, scraped page-by-page from a dossier's `index.html` + per-document pages): `tool_get_reach_ghs`, `tool_get_reach_pbt`, `tool_get_toxicology_*`, `tool_get_ecotoxicology_data`. These pick the best lead dossier automatically (Active > Not active, Article 10-full > Article 18) and parse the `das-*` markup. Because each study is a separate HTTP fetch, keep `max_studies` modest and filter by `section` whenever you can.

---

## Which classification tool? (they can disagree)

| Tool | What it is | Authority |
|---|---|---|
| `tool_get_harmonised_classification` | Annex VI, adopted by the European Commission. Not every substance has one. | Legally binding across the EU |
| `tool_get_clp_classification` | Aggregated industry self-classification notifications. | Industry consensus, not binding |
| `tool_get_reach_ghs` | The lead registrant's GHS classification in their REACH dossier (Section 2.1). | One registrant's position |

Default to **harmonised** for "the EU classification". Fall back to CLP notifications when no harmonised entry exists (`has_harmonised: false`). Use REACH GHS only when the question is specifically about the registrant's own classification.

---

## Section 7 (toxicology) subsections

`section=` filters accept these subsection numbers:

| Section | Endpoint |
|---|---|
| 7.1 | Toxicokinetics / dermal absorption |
| 7.2 | Acute toxicity (oral, dermal, inhalation) |
| 7.3 | Irritation / corrosion (skin, eye) |
| 7.4 | Sensitisation (skin, respiratory) |
| 7.5 | Repeated dose toxicity |
| 7.6 | Genetic toxicity (in vitro, in vivo) |
| 7.7 | Carcinogenicity |
| 7.8 | Toxicity to reproduction / developmental |
| 7.9 | Neurotoxicity / immunotoxicity |
| 7.10 | Human data / epidemiology |

Ecotox (`tool_get_ecotoxicology_data`) covers Section 5 (environmental fate) and Section 6 (aquatic/sediment/terrestrial toxicity, PNEC); pass e.g. `section="6.1.1"` to narrow.

---

## Full pipeline template

A name/CAS-in, hazard-profile-out workflow: resolve the index first (Step 0), then a classification-first lookup with a graceful fallback and DNELs — all on one event loop:

```python
import asyncio
import json

from core.skills.database_traversal.scripts.echa_api.substance_id_resolve import resolve_substance_index
from core.skills.database_traversal.scripts.echa_api.tools import (
    tool_get_substance_info,
    tool_get_harmonised_classification,
    tool_get_clp_classification,
    tool_get_toxicology_summary,
)
from core.skills.database_traversal.scripts.echa_api.echa_client import get_client


async def echa_hazard_profile(query: str) -> dict:
    """query is a chemical name or CAS number — NOT an index."""
    result = {"query": query, "found": False}
    try:
        # Step 0 — resolve to an index (prefer CAS; confirm if ambiguous).
        resolved = json.loads(await resolve_substance_index(query))
        if not resolved["resolved"]:
            return {**result, "reason": "unresolved"}
        index = resolved["substance_index"]
        result["substance_index"] = index
        result["ambiguous"] = resolved["ambiguous"]

        info = json.loads(await tool_get_substance_info(index))
        result["found"] = True
        result["identity"] = {"cas": info["cas_number"], "name": info["chemical_name"]}

        # Prefer the harmonised classification; fall back to CLP notifications.
        harm = json.loads(await tool_get_harmonised_classification(index))
        if harm.get("has_harmonised"):
            result["classification_source"] = "harmonised"
            result["classification"] = harm["classifications"]
        else:
            clp = json.loads(await tool_get_clp_classification(index, max_results=3))
            result["classification_source"] = "clp_notification"
            result["classification"] = clp.get("classifications", [])

        # DN(M)ELs come from the tox summary (fast path).
        tox = json.loads(await tool_get_toxicology_summary(index))
        result["dnmels"] = tox.get("dnmels", [])
        return result
    except Exception as e:
        return {**result, "reason": f"error: {e}"}


async def main(queries: list[str]) -> list[dict]:
    try:
        # One event loop, sequential awaits — never asyncio.run() per compound.
        return [await echa_hazard_profile(q) for q in queries]
    finally:
        await get_client().close()  # close once, after all work is done


profiles = asyncio.run(main(["50-00-0", "acetone"]))   # CAS or names
print(json.dumps(profiles, indent=2, ensure_ascii=False))
```

Note the shared pool is closed **once** in `main`, not inside `echa_hazard_profile` — that keeps the single-compound and batch cases on the same helper without closing the pool mid-run.

---

## Failure modes

- **`{"resolved": false, "reason": "no_match"}`** (resolver) — ECHA has no substance matching the query. Re-try by CAS instead of name; if still nothing, the substance isn't in ECHA CHEM — fall back to PubChem/NIOSH. Do not proceed with a guessed index.
- **`{"resolved": true, "ambiguous": true, ...}`** (resolver) — a name matched several substances (isomers, mixtures, "…and releasers"). Inspect `candidates` / call `search_substances`, or re-query by CAS, and confirm before trusting downstream data.
- **`{"error": "Substance not found for index: ..."}`** — bad or unknown index. Re-resolve the substance index; do not retry blindly.
- **`{"has_harmonised": false, ...}`** — no Annex VI entry. Expected for most substances; fall back to `tool_get_clp_classification`.
- **`{"error": "No lead dossiers found ..."}` / `"No suitable dossier found ..."`** — the substance has no usable REACH registration dossier, so Section 2.1/2.3/5/6/7 data is unavailable. Fall back to PubChem/NIOSH for tox data.
- **Empty `sections` / `dnmels`** — the dossier exists but that section wasn't populated by the registrant; not an error.
- **Slow / timeouts on `tool_get_toxicology_full`** — expected for data-rich substances (each study is a separate HTML fetch). Switch to `tool_get_toxicology_summary` or a section-filtered `tool_get_toxicology_studies`.
- **Event-loop errors on a second call** — you used more than one `asyncio.run()`. Consolidate all ECHA awaits into a single async function / single `asyncio.run`.
