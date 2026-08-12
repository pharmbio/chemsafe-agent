# Identifier Resolution Reference

Chemical queries arrive in many forms. Before hitting any database, resolve the input to the identifier that database expects.

---

## Identifier Types

| Type | Example | Used By |
|---|---|---|
| **Common name** | `"aspirin"`, `"acetone"` | All sources (via name search) |
| **IUPAC name** | `"2-acetyloxybenzoic acid"` | PubChem (direct) |
| **CAS number** | `"50-78-2"` | NIOSH (direct match), PubChem (via name endpoint) |
| **PubChem CID** | `2244` | PubChem (direct) |
| **Synonym / trade name** | `"Tylenol"`, `"paracetamol"` | PubChem (synonym lookup) |

---

## Input Normalization

Always normalize before querying:

```python
import re

def normalize_query(query: str) -> str:
    """Strip whitespace, normalize case for comparison."""
    return query.strip()

def looks_like_cas(query: str) -> bool:
    """CAS numbers follow the pattern: digits-digits-digit."""
    return bool(re.match(r'^\d{2,7}-\d{2}-\d$', query.strip()))

def looks_like_cid(query: str) -> bool:
    """PubChem CIDs are plain integers."""
    return query.strip().isdigit()
```

---

## Resolution Strategies by Source

### PubChem
Accepts names, synonyms, and CAS numbers through the same endpoint:
```python
# Works for all: common name, IUPAC, CAS, synonym
url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{query}/cids/JSON"
```
- If input is a CID already, skip this step and go directly to `get_full_record(cid)`

### NIOSH
Uses local DataFrame filtering after loading the index:
```python
# Match on name (partial, case-insensitive) OR CAS (exact)
mask = (
    df["chemical_name"].str.lower().str.contains(query.lower(), na=False) |
    df["cas"].str.contains(query, na=False)
)
```
- CAS matching is exact string match — format must be `XXX-XX-X`
- Name matching is partial — `"nitrochlorobenzene"` will match `"p-Nitrochlorobenzene"`

### OPCW
Uses local DataFrame filtering after bulk fetch:
```python
mask = df["Chemical Name"].str.lower().str.contains(query.lower(), na=False)
```
- Name-only matching — no CAS numbers in the OPCW dataset

---

## Cross-Source Resolution Pipeline

When a query must be resolved across multiple sources, use this order:

```python
def resolve_identifier(query: str) -> dict:
    """
    Attempt to resolve a chemical query to identifiers usable across sources.
    Returns a dict with available identifiers.
    """
    result = {"original_query": query, "cid": None, "cas": None, "name": None}

    # 1. If it looks like a CID, use directly
    if looks_like_cid(query):
        result["cid"] = int(query)
        return result

    # 2. Try PubChem for canonical name + CID
    cid = get_cid(query)   # from pubchem.md
    if cid:
        result["cid"] = cid
        # Optionally fetch canonical name and CAS from PubChem properties
        props = get_pubchem_properties(cid, ["IUPACName", "MolecularFormula"])
        result["name"] = props.get("IUPACName", query)

    # 3. Use CAS if present in query
    if looks_like_cas(query):
        result["cas"] = query

    return result


def get_pubchem_properties(cid: int, properties: list[str]) -> dict:
    """Fetch scalar properties from PubChem Properties API."""
    props_str = ",".join(properties)
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/{props_str}/JSON"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()["PropertyTable"]["Properties"][0]
    except Exception:
        return {}
```

---

## Handling Ambiguous Queries

When a query matches multiple compounds (e.g., `"nitrobenzene"` matching several isomers):

1. **PubChem** — returns the most common CID first; this is usually correct
2. **NIOSH** — returns the first DataFrame match; user may need to disambiguate
3. **OPCW** — return all matches as a list; let caller decide

```python
# For NIOSH/OPCW: return all matches for ambiguous queries
if len(matches) > 1:
    return {
        "found": True,
        "ambiguous": True,
        "matches": matches.to_dict("records"),
        "note": "Multiple compounds matched — using first result"
    }
```

---

## CAS Number Formatting

NIOSH uses the standard CAS format (`XX-XX-X` to `XXXXXXX-XX-X`). If the user provides a CAS without dashes, reformat:

```python
def format_cas(raw: str) -> str:
    """Add dashes to a raw CAS number if missing."""
    digits = raw.replace("-", "").strip()
    if len(digits) >= 4:
        return f"{digits[:-3]}-{digits[-3:-1]}-{digits[-1]}"
    return raw
```