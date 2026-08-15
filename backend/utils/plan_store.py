from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.utils.output_paths import conversation_output_root

PLAN_FILENAME = "plan.md"
DOCUMENT_TITLE = "# Execution plan"

PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
BLOCKED = "blocked"
SKIPPED = "skipped"

STATUSES: Tuple[str, ...] = (PENDING, IN_PROGRESS, COMPLETED, BLOCKED, SKIPPED)
TERMINAL_STATUSES: Tuple[str, ...] = (COMPLETED, BLOCKED, SKIPPED)

_MARKERS: Dict[str, str] = {
    PENDING: " ",
    IN_PROGRESS: "~",
    COMPLETED: "x",
    BLOCKED: "!",
    SKIPPED: "-",
}
_MARKER_TO_STATUS: Dict[str, str] = {v: k for k, v in _MARKERS.items()}

# One lock per plan file so read-modify-write stays atomic within the process.
_locks: Dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()

_RUN_HEADING = re.compile(r"^##\s+Run\s+(\d+)\s*(?:·\s*(\S+))?\s*(?:·\s*(.*))?$")
_STEP_LINE = re.compile(r"^-\s+\[(.)\]\s+\*\*(.+?)\.\*\*\s+(.*?)(?:\s+·\s+`(\w+)`)?(?:\s+·\s+(.*))?$")
_CONTINUATION = re.compile(r"^\s{4,}(Details|Depends on|Note):\s*(.*)$")
_GOAL_LINE = re.compile(r"^\*\*Goal:\*\*\s*(.*)$")

# The planning agent's canonical breakdown: `  [1] **Title**`
_PLAN_STEP = re.compile(r"^\s*\[(\d+)\]\s*\*\*(.+?)\*\*\s*$")
_PLAN_DETAIL = re.compile(r"^\s*\*\*Details:\*\*\s*(.*)$", re.I)
_PLAN_DEPENDS = re.compile(r"^\s*\*\*Depends on:\*\*\s*(.*)$", re.I)
# Fallback shapes: `1. Title` / `1) Title`
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.{3,})$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@dataclass
class PlanStep:
    number: int
    title: str
    details: str = ""
    depends_on: str = ""
    status: str = PENDING
    note: str = ""
    updated_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass
class PlanRun:
    run_id: int
    kind: str = "complex"
    started_at: str = ""
    goal: str = ""
    constraints: List[str] = field(default_factory=list)
    steps: List[PlanStep] = field(default_factory=list)
    outcome: str = ""

    def step(self, number: int) -> Optional[PlanStep]:
        return next((s for s in self.steps if s.number == number), None)

    def progress(self) -> Tuple[int, int]:
        return sum(1 for s in self.steps if s.is_terminal), len(self.steps)


@dataclass
class PlanDocument:
    runs: List[PlanRun] = field(default_factory=list)
    preamble: str = ""

    def run(self, run_id: int) -> Optional[PlanRun]:
        return next((r for r in self.runs if r.run_id == run_id), None)

    @property
    def active(self) -> Optional[PlanRun]:
        return self.runs[-1] if self.runs else None


# --------------------------------------------------------------------------
# Parsing the planning agent's output into steps
# --------------------------------------------------------------------------


def parse_plan_steps(plan_text: str) -> List[PlanStep]:
    """Turn an approved plan into structured steps.

    Tolerant by design: the canonical `[1] **Title**` breakdown first, then a
    plain numbered list, and finally a single catch-all step. Execution must
    never be blocked because the planner phrased its output differently.
    """
    text = plan_text or ""
    lines = text.splitlines()

    steps: List[PlanStep] = []
    current: Optional[PlanStep] = None
    for line in lines:
        match = _PLAN_STEP.match(line)
        if match:
            current = PlanStep(number=int(match.group(1)), title=match.group(2).strip())
            steps.append(current)
            continue
        if current is None:
            continue
        detail = _PLAN_DETAIL.match(line)
        if detail:
            current.details = detail.group(1).strip()
            continue
        depends = _PLAN_DEPENDS.match(line)
        if depends:
            current.depends_on = depends.group(1).strip()

    if not steps:
        for line in lines:
            match = _NUMBERED.match(line)
            if match:
                title = match.group(2).strip().strip("*").strip()
                if title:
                    steps.append(PlanStep(number=int(match.group(1)), title=title))

    if not steps:
        summary = next((ln.strip() for ln in lines if ln.strip()), "Execute the approved plan")
        steps = [PlanStep(number=1, title=_clip(summary, 120), details=_clip(text, 600))]

    # Renumber densely so step ids are always 1..N and addressable.
    for index, step in enumerate(steps, start=1):
        step.number = index
    return steps


def parse_plan_goal(plan_text: str) -> str:
    """The one-line goal from `📋 **PLAN:** ...`, else the first useful line."""
    for line in (plan_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        marker = re.match(r"^.*?\*\*PLAN:\*\*\s*(.+)$", stripped)
        if marker:
            return _clip(marker.group(1).strip(), 200)
    for line in (plan_text or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "-", "*", "[")):
            return _clip(stripped, 200)
    return ""


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# Document rendering and parsing
# --------------------------------------------------------------------------


def render_step(step: PlanStep) -> List[str]:
    marker = _MARKERS.get(step.status, " ")
    head = f"- [{marker}] **{step.number}.** {step.title} · `{step.status}`"
    if step.updated_at:
        head += f" · {step.updated_at}"
    lines = [head]
    if step.details:
        lines.append(f"      Details: {step.details}")
    if step.depends_on:
        lines.append(f"      Depends on: {step.depends_on}")
    if step.note:
        lines.append(f"      Note: {step.note}")
    return lines


def render_run(run: PlanRun) -> str:
    done, total = run.progress()
    lines = [f"## Run {run.run_id} · {run.kind} · {run.started_at}"]
    if run.goal:
        lines.append(f"**Goal:** {run.goal}")
    if run.constraints:
        lines.append("")
        lines.append("**Approval conditions:**")
        lines.extend(f"- {item}" for item in run.constraints)
    lines.append("")
    for step in run.steps:
        lines.extend(render_step(step))
    lines.append("")
    lines.append(f"_Progress: {done}/{total} steps resolved._")
    if run.outcome:
        lines.append(f"_Outcome: {run.outcome}_")
    return "\n".join(lines)


def render_document(document: PlanDocument) -> str:
    parts = [DOCUMENT_TITLE, ""]
    if document.preamble.strip():
        parts.extend([document.preamble.strip(), ""])
    for run in document.runs:
        parts.append(render_run(run))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def parse_document(text: str) -> PlanDocument:
    document = PlanDocument()
    current: Optional[PlanRun] = None
    current_step: Optional[PlanStep] = None

    for line in (text or "").splitlines():
        heading = _RUN_HEADING.match(line.strip())
        if heading:
            current = PlanRun(
                run_id=int(heading.group(1)),
                kind=(heading.group(2) or "complex").strip(),
                started_at=(heading.group(3) or "").strip(),
            )
            document.runs.append(current)
            current_step = None
            continue
        if current is None:
            continue

        goal = _GOAL_LINE.match(line.strip())
        if goal:
            current.goal = goal.group(1).strip()
            continue

        step_match = _STEP_LINE.match(line.rstrip())
        if step_match:
            marker, number, title, status, stamp = step_match.groups()
            resolved = status if status in STATUSES else _MARKER_TO_STATUS.get(marker, PENDING)
            current_step = PlanStep(
                number=int(number),
                title=title.strip(),
                status=resolved,
                updated_at=(stamp or "").strip(),
            )
            current.steps.append(current_step)
            continue

        continuation = _CONTINUATION.match(line)
        if continuation and current_step is not None:
            key, value = continuation.group(1), continuation.group(2).strip()
            if key == "Details":
                current_step.details = value
            elif key == "Depends on":
                current_step.depends_on = value
            else:
                current_step.note = value
            continue

        stripped = line.strip()
        if stripped.startswith("- ") and current_step is None and current.steps == []:
            current.constraints.append(stripped[2:].strip())
        elif stripped.startswith("_Outcome:"):
            current.outcome = stripped.strip("_").replace("Outcome:", "").strip()

    return document


# --------------------------------------------------------------------------
# File access
# --------------------------------------------------------------------------


def plan_file_path(
    *, user_id: Optional[str] = None, conversation_id: Optional[str] = None
) -> Path:
    root = conversation_output_root(conversation_id, user_id=user_id)
    return root / PLAN_FILENAME


def _lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def load_document(
    *, user_id: Optional[str] = None, conversation_id: Optional[str] = None
) -> PlanDocument:
    path = plan_file_path(user_id=user_id, conversation_id=conversation_id)
    if not path.exists():
        return PlanDocument()
    try:
        return parse_document(path.read_text(encoding="utf-8"))
    except OSError:
        return PlanDocument()


def start_run(
    *,
    goal: str,
    steps: List[PlanStep],
    kind: str = "complex",
    constraints: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> PlanRun:
    """Append a new run section and return it. Deterministic; no LLM involved."""
    path = plan_file_path(user_id=user_id, conversation_id=conversation_id)
    with _lock_for(path):
        document = load_document(user_id=user_id, conversation_id=conversation_id)
        run = PlanRun(
            run_id=(document.runs[-1].run_id + 1) if document.runs else 1,
            kind=kind,
            started_at=_now(),
            goal=_clip(goal, 200),
            constraints=[c for c in (constraints or []) if str(c).strip()],
            steps=steps,
        )
        document.runs.append(run)
        _write_atomic(path, render_document(document))
        return run


def update_step(
    *,
    step_number: int,
    status: str,
    note: str = "",
    run_id: Optional[int] = None,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Tuple[bool, str, Optional[PlanRun]]:
    """Set one step's status. Returns (ok, message, updated run).

    Validates the status and the step number against the file rather than
    trusting the caller, and rewrites the document atomically.
    """
    normalized = (status or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in STATUSES:
        return False, f"Unknown status {status!r}. Use one of: {', '.join(STATUSES)}.", None

    path = plan_file_path(user_id=user_id, conversation_id=conversation_id)
    with _lock_for(path):
        document = load_document(user_id=user_id, conversation_id=conversation_id)
        run = document.run(run_id) if run_id is not None else document.active
        if run is None:
            return False, "No execution plan exists for this conversation yet.", None
        step = run.step(step_number)
        if step is None:
            available = ", ".join(str(s.number) for s in run.steps) or "none"
            return (
                False,
                f"Step {step_number} is not in run {run.run_id}. Available steps: {available}.",
                run,
            )

        step.status = normalized
        step.updated_at = _now()
        if note:
            step.note = _clip(note, 300)
        _write_atomic(path, render_document(document))
        return True, f"Step {step_number} set to {normalized}.", run


def set_outcome(
    *,
    outcome: str,
    run_id: Optional[int] = None,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Optional[PlanRun]:
    path = plan_file_path(user_id=user_id, conversation_id=conversation_id)
    with _lock_for(path):
        document = load_document(user_id=user_id, conversation_id=conversation_id)
        run = document.run(run_id) if run_id is not None else document.active
        if run is None:
            return None
        run.outcome = _clip(outcome, 300)
        _write_atomic(path, render_document(document))
        return run


def progress_line(run: Optional[PlanRun]) -> str:
    if run is None or not run.steps:
        return ""
    done, total = run.progress()
    unresolved = [s for s in run.steps if not s.is_terminal]
    nxt = f" · next: step {unresolved[0].number} ({unresolved[0].title})" if unresolved else ""
    return f"Run {run.run_id}: {done}/{total} steps resolved{nxt}"
