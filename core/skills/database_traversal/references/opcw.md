# OPCW Reference

**Source type:** Embedded Power BI API (undocumented POST endpoint)  
**Data:** OPCW Chemical Weapons Convention scheduled compounds  
**Auth:** Public resource key embedded in the Power BI report (no login required)  
**Required libraries:** `requests`, `pandas`, `uuid`

---

## Access Pattern (1-stage, bulk)

Unlike PubChem and NIOSH, the OPCW source does **not** support per-compound lookup. The correct pattern is:

```
1. POST to Power BI API → get full compound list (up to 10,000 rows)
2. Load into DataFrame
3. Filter locally by name or schedule
```

> **Important:** Pull the full dataset once, then filter — do not make repeated API calls per compound.

---

## API Configuration

```python
import uuid

URL = "https://wabi-west-europe-d-primary-api.analysis.windows.net/public/reports/querydata?synchronous=true"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://app.powerbi.com",
    "Referer": "https://app.powerbi.com/",
    "User-Agent": "Mozilla/5.0",
    "X-PowerBI-ResourceKey": "f3274b52-ae68-45cb-8254-053137b6b028",
    "ActivityId": str(uuid.uuid4()),   # random UUID per request
    "RequestId": str(uuid.uuid4())     # random UUID per request
}
```

> **Note on the resource key:** `f3274b52-ae68-45cb-8254-053137b6b028` is the public key embedded in the OPCW Power BI report. If requests start returning 401/403, the key may have rotated — flag this to the user.

---

## Request Payload

```python
PAYLOAD = {
    "version": "1.0.0",
    "queries": [
        {
            "Query": {
                "Commands": [
                    {
                        "SemanticQueryDataShapeCommand": {
                            "Query": {
                                "Version": 2,
                                "From": [
                                    {"Name": "c", "Entity": "CompoundsData", "Type": 0}
                                ],
                                "Select": [
                                    {
                                        "Column": {
                                            "Expression": {"SourceRef": {"Source": "c"}},
                                            "Property": "Name (all)"
                                        },
                                        "Name": "Name"
                                    }
                                ],
                                "Top": 10000
                            },
                            "Binding": {
                                "Primary": {"Groupings": [{"Projections": [0]}]}
                            },
                            "DataReduction": {
                                "DataVolume": 3,
                                "Primary": {"Top": {"Count": 10000}}
                            },
                            "Version": 1
                        }
                    }
                ]
            },
            # No VisualId — critical for unrestricted results
            "ApplicationContext": {
                "DatasetId": "b44be3f9-2e77-425f-bc6c-ab73f5ff7124"
            }
        }
    ],
    "modelId": 2789122
}
```

---

## Fetching and Parsing the Response

The Power BI `dsr` (Data Shape Result) format is non-standard. Use a robust parser with fallbacks:

```python
def fetch_opcw_compounds() -> pd.DataFrame:
    response = requests.post(URL, headers=HEADERS, json=PAYLOAD, timeout=30)
    response.raise_for_status()
    data = response.json()

    rows = _extract_dsr_rows(data)
    df = pd.DataFrame(rows)

    # Keep only real data columns (start with "G" in Power BI DSR)
    cols = [c for c in df.columns if c.startswith("G")]
    if not cols:
        cols = df.columns.tolist()

    df = df[cols].copy()
    df.columns = [f"col_{i}" for i in range(len(df.columns))]

    # Clean quoted strings
    for col in df.columns:
        df[col] = df[col].astype(str).str.replace('"', '', regex=False)

    if df.shape[1] == 1:
        df.columns = ["Chemical Name"]

    return df


def _extract_dsr_rows(data: dict) -> list:
    """Robust DSR row extractor with multiple fallback paths."""
    r = data["results"][0]
    try:
        return r["result"]["data"]["dsr"]["DS"][0]["PH"][0]["DM0"]
    except (KeyError, IndexError):
        pass
    try:
        return r["result"]["data"]["dsr"]["PH"][0]["DM0"]
    except (KeyError, IndexError):
        pass
    raise ValueError("Could not parse Power BI DSR response — structure may have changed")
```

---

## Filtering the Dataset

```python
def find_opcw_compound(query: str, df: pd.DataFrame) -> dict:
    q = query.lower()
    name_col = df.columns[0]   # usually "Chemical Name"

    matches = df[df[name_col].str.lower().str.contains(q, na=False)]

    if matches.empty:
        return {"found": False, "query": query, "reason": "not_found"}

    return {
        "found": True,
        "query": query,
        "matches": matches[name_col].tolist()
    }
```

---

## Full Pipeline Template

```python
def get_opcw_data(query: str) -> dict:
    try:
        df = fetch_opcw_compounds()
    except Exception as e:
        return {"found": False, "query": query, "reason": "fetch_error", "error": str(e)}

    return find_opcw_compound(query, df)

# For batch use, load once:
# df_opcw = fetch_opcw_compounds()
# results = [find_opcw_compound(q, df_opcw) for q in queries]
```

---

## Failure Modes

| Symptom | Likely Cause |
|---|---|
| HTTP 401 / 403 | Resource key rotated — alert user |
| `ValueError` from `_extract_dsr_rows` | Power BI DSR structure changed |
| Empty DataFrame | `Top: 10000` limit hit or dataset moved |
| All rows `None`/`nan` | Column selection mismatch — check `G*` column logic |