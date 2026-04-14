---
name: sop_search
description: Use this skill whenever SOPs (Standard Operating Procedures) need to be retrieved to ground any decision, action, or output. Triggers when any agent is about to take a step, apply a numeric limit, or make a decision that should be governed by established procedures. Always use this skill before acting on any domain-specific task — never invent thresholds, rules, or procedures from context alone. Applies across chemical handling, storage, disposal, exposure response, PPE requirements, spill containment, and any other chemical safety domain where SOPs exist.
---

# SOP Retrieval Skill

Retrieve all SOPs relevant to the current task. All numeric values and decision criteria must trace back to this register. Never assume or invent a procedure or threshold — in chemical safety, unvalidated limits can cause direct harm.

---

## Step 1 — Identify the Task Domain

Before querying, articulate the domain clearly from the task at hand.
Be specific — the domain drives query construction and determines retrieval quality.

Examples:
- "Hydrochloric acid storage in laboratory setting"
- "Chlorine gas exposure response and evacuation"
- "Flammable solvent handling near ignition sources"
- "Chemical waste disposal for halogenated compounds"
- "PPE requirements for corrosive liquid transfer"

---

## Step 2 — Construct Queries for Comprehensive Retrieval

A single broad query rarely captures all relevant SOPs. Build a query set that
approaches the domain from multiple angles.

### Query dimensions to cover

| Dimension       | What to vary                                          | Example                                                       |
|-----------------|-------------------------------------------------------|---------------------------------------------------------------|
| **Action**      | The core operation being performed                    | "storage", "transfer", "disposal", "neutralization"           |
| **Entity**      | The chemical or substance involved                    | "hydrochloric acid", "flammable solvent", "oxidizer"          |
| **Constraint**  | Exposure limits, storage thresholds, quantity limits  | "TLV", "PEL", "maximum storage quantity", "flash point"       |
| **Condition**   | Edge cases, triggers, emergency scenarios             | "spill", "overexposure", "fire", "incompatible contact"       |
| **Role**        | Who is responsible or must be notified                | "safety officer", "lab supervisor", "emergency responder"     |
| **Outcome**     | What the procedure produces or prevents               | "incident report", "evacuation", "decontamination", "disposal record" |

### Query construction rules

- Use **chemical-specific terminology** from the task context (e.g. chemical name,
  CAS number, hazard class, GHS category) rather than generic terms.
- Generate **at least 3 queries** per domain, covering different dimensions.
- Prefer **narrow, specific queries** over a single broad one — retrievers rank on
  relevance, so precision beats recall.
- Include **at least one threshold-focused query** to surface exposure limits and
  quantity restrictions explicitly (e.g. `"permissible exposure limit chlorine gas"`).
- Include **at least one emergency/exception query** to capture incident response SOPs
  (e.g. `"hydrochloric acid spill containment procedure"`).

### Example query set for "Hydrochloric acid transfer in laboratory"

```python
queries = [
    "hydrochloric acid laboratory handling procedure",
    "corrosive liquid transfer PPE requirements",
    "permissible exposure limit hydrochloric acid HCl",
    "acid spill containment and neutralization procedure",
    "incompatible chemical storage acid oxidizer separation",
    "corrosive chemical incident reporting safety officer",
]
```

---

## Step 3 — Execute Retrieval (will modify later)

Run each query and deduplicate results by SOP ID:

```python
from core.skills.sop_search.sop_search import sop_search

score_threshold = 0.35  # tighten or relax this per task
all_sops = {}
for query in queries:
    results = sop_search(
        query=query,
        score_threshold=score_threshold,
        max_results=12,
    )
    # Deduplicate downstream by SOP/source identifier.
```

Actively modify threshold and max_results depends on the situation.

---

## Step 4 — Review and Extract

For each unique SOP returned, extract:

- **SOP ID** and **title**
- **Mandatory steps** or decision rules it defines
- **All thresholds, limits, and numeric criteria** (capture exact values and units —
  e.g. ppm, mg/m³, °C, litres, kg)
- **Conditions** under which the SOP applies (e.g. chemical class, quantity,
  indoor vs. outdoor, ventilation requirement)
- **Roles** responsible for each step (e.g. who must be notified, who must be present)

Flag any SOP that seems only partially relevant — include it but mark it for human review.

---

## Step 5 — Compile the Threshold Register

Consolidate all numeric values and decision criteria into a Threshold Register
before taking any action or producing any output.
**Rules:**
- Every row must have a SOP ID. No threshold without a source.
- If two SOPs define conflicting values for the same parameter, list both rows
  and flag the conflict — do not silently pick one. In chemical safety, conflicting
  limits must be escalated to the safety officer before proceeding.
- Any action requiring a numeric value not present in this register must be flagged:
  `Threshold: Unvalidated — safety officer review required before proceeding`

---

## Step 6 — Handle Missing SOPs

If retrieval returns no relevant SOPs after all queries are exhausted:

- ⚠️ No matching SOP found for domain: `<domain>`.
- Do not proceed without validated procedures. Escalate to the chemical safety officer immediately — acting without SOP backing in a chemical safety context is not permitted.

If some queries return results but others do not, note specifically which dimensions
are uncovered (e.g. "No SOP found for spill response or incompatible chemical contact")
and flag those gaps before any action is taken.
