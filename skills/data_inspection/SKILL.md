---
name: data_inspection
description: Inspect a data file before anything downstream depends on it — CSV, Excel, JSON, SQLite or SQL dump, log or free text. Establishes file type, schema, dtypes, row and column counts, missing values, duplicates and value ranges, and produces a Data Summary. Use before planning over an uploaded file, before writing transformation or analysis code, and before making any data-driven claim, including when the user does not ask for inspection.
---

# Data Inspection Skill

Systematically inspect uploaded data to produce a Data Summary that downstream
tasks (planning, analysis, transformation) can rely on. Always complete all steps
in order. Do not skip steps based on assumptions about the data.

---

## Step 1 — Load the Data

Use the appropriate library based on file type:

| File type                    | Library / approach                        |
|------------------------------|-------------------------------------------|
| `.csv`, `.tsv`               | `pandas.read_csv()`                       |
| `.xlsx`, `.xls`              | `pandas.read_excel()`                     |
| `.json` (flat)               | `pandas.read_json()`                      |
| `.json` (nested)             | `json.load()` then `pandas.json_normalize()` |
| `.db`, `.sqlite`             | `sqlite3` or `sqlalchemy`                 |
| `.sql` dump                  | Parse with `sqlalchemy` or run against temp DB |
| `.log`, `.txt`, unstructured | Read line-by-line with plain Python       |

If the file type is ambiguous, inspect the first few raw bytes before loading:
```python
with open(filepath, "rb") as f:
    print(f.read(500))
```

---

## Step 2 — Inspect Schema

Understand the structure before looking at values.

**Tabular (pandas DataFrame):**
```python
df.dtypes
df.columns.tolist()
df.shape  # (rows, columns)
df.head(5)
```

**JSON:**
```python
print(list(data.keys()))          # top-level keys
print(type(data[key]))            # value types
print(len(data[key]))             # array lengths if list
```

**Database:**
```python
# SQLite example
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
cursor.execute("PRAGMA table_info(table_name)")
# Also check foreign keys:
cursor.execute("PRAGMA foreign_key_list(table_name)")
```

**Text/logs:**
```python
with open(filepath) as f:
    for line in itertools.islice(f, 10):  # sample first 10 lines
        print(line)
# Identify: delimiter, timestamp format, log level pattern, field count per line
```

---

## Step 3 — Inspect Data Quality

Surface problems that could affect downstream planning or analysis.

**Tabular:**
```python
df.isnull().sum()           # nulls per column
df.duplicated().sum()       # duplicate rows
df.dtypes                   # flag columns with unexpected types (e.g. numeric stored as object)
```

**JSON:**
- Iterate records and count missing or null fields per key
- Flag records where expected keys are absent

**Database:**
```sql
SELECT COUNT(*) FROM table WHERE col IS NULL;  -- per critical column
SELECT COUNT(*) - COUNT(DISTINCT id) FROM table;  -- duplicate check
```

**Text/logs:**
- Count malformed lines (wrong field count, encoding errors)
- Flag irregular patterns (e.g. mixed delimiters, truncated lines)

---

## Step 4 — Summarize Distributions

Get a statistical feel for the data's content and range.

**Tabular:**
```python
df.describe(include="all")          # numeric + categorical summary
df[col].value_counts(dropna=False)  # for categorical columns
df[date_col].min(), df[date_col].max()  # for date ranges
```

**JSON:**
- Count distinct values per key field
- Summarize numeric ranges (min, max, mean) if present
- Note most frequent values for categorical keys

**Database:**
```sql
SELECT MIN(col), MAX(col), COUNT(DISTINCT col) FROM table;
SELECT col, COUNT(*) FROM table GROUP BY col ORDER BY COUNT(*) DESC LIMIT 10;
```

**Text/logs:**
```python
from collections import Counter
counter = Counter(line.split()[0] for line in lines)  # e.g. frequency of log levels
```

---

## Step 5 — Produce the Data Summary

Compile findings into a structured Data Summary before any downstream task proceeds.
