# NIOSH NPG Reference

**Source type:** HTML index table → per-compound detail page scrape  
**Base URL:** `https://www.cdc.gov/niosh/npg/`  
**Auth:** None required  
**Rate limit:** Polite scraping; sequential is safe  
**Required libraries:** `requests`, `beautifulsoup4`

---

## Access Pattern (2-stage)

```
1. Scrape index table → DataFrame of (CAS, chemical_name, detail_url)
2. Query DataFrame by name or CAS → get detail URL
3. Scrape detail page → extract safety sections by text block
```

> **Important:** Cache the index DataFrame across compound lookups — scraping the index for every query is wasteful. Load once, reuse.

---

## Stage 1: Fetch the Index Table

```python
from bs4 import BeautifulSoup

def get_niosh_table() -> pd.DataFrame:
    url = "https://www.cdc.gov/niosh/npg/npgdcas.html"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    rows = table.find_all("tr")[1:]   # skip header

    data = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) >= 2:
            cas = cols[0].get_text(strip=True)
            name_tag = cols[1].find("a")
            if name_tag:
                data.append({
                    "cas": cas,
                    "chemical_name": name_tag.get_text(strip=True),
                    "link": "https://www.cdc.gov/niosh/npg/" + name_tag.get("href", "")
                })

    return pd.DataFrame(data)
```

- Returns a DataFrame with columns: `cas`, `chemical_name`, `link`
- Store this as `df_niosh` and pass it to downstream functions

---

## Stage 2: Find a Compound in the Index

```python
def find_niosh_entry(query: str, df: pd.DataFrame) -> dict:
    q = query.lower()
    mask = (
        df["chemical_name"].str.lower().str.contains(q, na=False) |
        df["cas"].str.contains(q, na=False)
    )
    result = df[mask]
    if result.empty:
        return {"found": False}

    row = result.iloc[0]
    return {
        "found": True,
        "cas": row["cas"],
        "chemical_name": row["chemical_name"],
        "link": row["link"]
    }
```

- Matches on **partial name** (case-insensitive) or **exact CAS**
- Returns the first match

---

## Stage 3: Scrape the Detail Page

NIOSH detail pages are unstructured HTML. Use text-block extraction between known section titles:

```python
NIOSH_SECTIONS = [
    "Formula",
    "Physical Description",
    "Incompatibilities & Reactivities",
    "Symptoms",
    "Personal Protection",
    "First Aid",
    "Respirator Recommendations"
]

def clean_text(text: str) -> str:
    return " ".join(text.split())

def extract_block(soup, title: str) -> Optional[str]:
    all_text = soup.get_text("\n")
    if title not in all_text:
        return None

    start = all_text.find(title)
    end = len(all_text)

    for sec in NIOSH_SECTIONS:
        if sec != title:
            pos = all_text.find(sec, start + 1)
            if pos != -1:
                end = min(end, pos)

    content = all_text[start:end].replace(title, "", 1)
    return clean_text(content) or None

def scrape_niosh_page(url: str) -> dict:
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    return {
        "Formula": extract_block(soup, "Formula"),
        "Physical Description": extract_block(soup, "Physical Description"),
        "Incompatibilities": extract_block(soup, "Incompatibilities & Reactivities"),
        "Symptoms": extract_block(soup, "Symptoms"),
        "Personal Protection": extract_block(soup, "Personal Protection"),
        "First Aid": extract_block(soup, "First Aid"),
        "Respirator Recommendations": extract_block(soup, "Respirator Recommendations")
    }
```

---

## Full Pipeline Template

```python
def get_niosh_data(query: str, df: pd.DataFrame) -> dict:
    entry = find_niosh_entry(query, df)
    if not entry["found"]:
        return {"found": False, "query": query, "reason": "not_found"}

    try:
        data = scrape_niosh_page(entry["link"])
    except Exception as e:
        return {"found": False, "query": query, "reason": "scrape_error", "error": str(e)}

    return {
        "found": True,
        "query": query,
        "cas": entry["cas"],
        "chemical_name": entry["chemical_name"],
        "url": entry["link"],
        "data": data
    }

# Usage pattern:
# df_niosh = get_niosh_table()            # load once
# result = get_niosh_data("acetone", df_niosh)
```

---

## Key Data Fields

| Field | Description |
|---|---|
| `Symptoms` | Health effects from exposure (inhalation, skin, eye) |
| `Personal Protection` | Required PPE — gloves, goggles, respirator type |
| `Respirator Recommendations` | Specific NIOSH-approved respirator by concentration |
| `First Aid` | Emergency response procedures |
| `Incompatibilities` | Reactive chemicals to avoid |
| `Physical Description` | State, color, odor at room temperature |

---

## Parse Failure Notes

- The `extract_block` function relies on known section title strings — if CDC changes headings, blocks will return `None`
- Always check for `None` values in the returned dict before using them
- Some compounds have missing sections (e.g., no Respirator Recommendations for low-hazard compounds) — this is expected