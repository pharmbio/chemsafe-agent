# PubChem Reference

**Source type:** REST API + deeply nested JSON  
**Base URL:** `https://pubchem.ncbi.nlm.nih.gov`  
**Auth:** None required  
**Rate limit:** ~5 requests/sec (sequential is safe)

---

## Access Pattern (2-stage)

```
1. Name/synonym → CID (compound identifier)
2. CID → Full PUG View record (nested JSON)
3. Record → extract target sections by TOCHeading
```

---

## Stage 1: Name → CID

```python
def get_cid(query: str) -> Optional[int]:
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{query}/cids/JSON"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()["IdentifierList"]["CID"][0]
    except Exception as e:
        print(f"[WARNING] CID not found for '{query}': {e}")
        return None
```

- Returns the **first (most common)** CID for the query
- Accepts: common names, IUPAC names, synonyms, CAS numbers
- Returns `None` on any failure — caller must check

---

## Stage 2: CID → Full Record

```python
def get_full_record(cid: int) -> Optional[dict]:
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[ERROR] Could not retrieve record for CID {cid}: {e}")
        return None
```

- Returns the **full PUG View record** — a large nested JSON
- Structure: `record["Record"]["Section"]` → list of top-level sections

---

## Stage 3: Extracting Sections by TOCHeading

The record is a tree of sections. Each section has:
- `TOCHeading` — section title (string)
- `Section` — list of child sections (recursive)
- `Information` — list of data items (leaf nodes)

### Recursive search pattern

```python
def find_sections(record_json: dict, heading_keywords: list[str]) -> list[dict]:
    """Find all sections whose TOCHeading contains any keyword."""
    results = []

    def search(section):
        heading = section.get("TOCHeading", "")
        if any(kw in heading for kw in heading_keywords):
            results.append(section)
        for sub in section.get("Section", []):
            search(sub)

    for sec in record_json["Record"]["Section"]:
        search(sec)

    return results
```

### Extracting text from a section

```python
def extract_text(section: dict) -> list[str]:
    """Recursively extract all StringWithMarkup text from a section."""
    texts = []
    for info in section.get("Information", []):
        for item in info.get("Value", {}).get("StringWithMarkup", []):
            texts.append(item["String"])
    for sub in section.get("Section", []):
        texts.extend(extract_text(sub))
    return texts
```

### Structured extraction (by subsection title)

```python
def extract_structured(sections: list[dict]) -> dict:
    """Return a dict of {subsection_title: text_content}."""
    structured = {}

    def process(section):
        title = section.get("TOCHeading", "Unknown")
        texts = []
        for info in section.get("Information", []):
            for item in info.get("Value", {}).get("StringWithMarkup", []):
                texts.append(item["String"])
        if texts:
            structured[title] = "\n".join(texts)
        for sub in section.get("Section", []):
            process(sub)

    for sec in sections:
        process(sec)
    return structured
```

---

## Key TOCHeading Keywords by Use Case

| Goal | Keywords to search |
|---|---|
| LCSS / safety summary | `"Laboratory Chemical Safety Summary"`, `"Safety"` |
| GHS classification | `"GHS Classification"`, `"Hazard"` |
| Physical properties | `"Physical Description"`, `"Boiling Point"` |
| Toxicity | `"Toxicity"`, `"LD50"` |
| Exposure limits | `"NIOSH"`, `"OSHA"`, `"Occupational"` |

---

## Full Pipeline Template

```python
def get_pubchem_lcss(query: str) -> dict:
    result = {
        "query": query, "cid": None,
        "found": False, "lcss_text": None, "structured": None
    }
    cid = get_cid(query)
    if cid is None:
        return {**result, "reason": "not_found"}
    result["cid"] = cid

    record = get_full_record(cid)
    if record is None:
        return {**result, "reason": "fetch_error"}

    sections = find_sections(record, ["Laboratory Chemical Safety Summary", "Safety"])
    if not sections:
        return {**result, "reason": "no_lcss"}

    result["found"] = True
    result["lcss_text"] = "\n".join(extract_text(s) for s in sections)
    result["structured"] = extract_structured(sections)
    return result
```