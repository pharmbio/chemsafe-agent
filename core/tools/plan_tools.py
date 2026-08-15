"""Tools for reading and advancing the on-disk execution plan.

These replace the prose TRACKING block. The agent supplies only a step number,
a status and an optional note; the file mutation, validation, timestamping and
the ledger rendered back are all done here, so progress reporting cannot drift
from what actually happened.
"""

from __future__ import annotations

from typing import Optional

from langchain.tools import tool

from backend.utils import plan_store
from backend.utils.output_paths import get_current_conversation_id, get_current_user_id


def _scope() -> dict:
    return {
        "user_id": get_current_user_id() or "anonymous-user",
        "conversation_id": get_current_conversation_id() or "default-thread",
    }


def render_ledger(run: Optional[plan_store.PlanRun], *, path: str = "") -> str:
    """Compact, code-generated progress view — the deterministic TRACKING block."""
    if run is None or not run.steps:
        return "No execution plan has been created for this conversation yet."
    done, total = run.progress()
    lines = [f"PLAN · run {run.run_id} · {done}/{total} steps resolved"]
    if run.goal:
        lines.append(f"Goal: {run.goal}")
    if run.constraints:
        lines.append("Approval conditions:")
        lines.extend(f"  - {item}" for item in run.constraints)
    lines.append("")
    for step in run.steps:
        marker = {
            plan_store.COMPLETED: "[x]",
            plan_store.BLOCKED: "[!]",
            plan_store.IN_PROGRESS: "[~]",
            plan_store.SKIPPED: "[-]",
        }.get(step.status, "[ ]")
        lines.append(f"{marker} {step.number}. {step.title}")
        if step.details:
            lines.append(f"       {step.details}")
        if step.depends_on and step.depends_on.lower() not in {"none", "-", ""}:
            lines.append(f"       depends on: {step.depends_on}")
        if step.note:
            lines.append(f"       note: {step.note}")
    unresolved = [s for s in run.steps if not s.is_terminal]
    lines.append("")
    lines.append(
        f"Next unresolved: step {unresolved[0].number} — {unresolved[0].title}"
        if unresolved
        else "All steps resolved."
    )
    if path:
        lines.append(f"File: {path}")
    return "\n".join(lines)


@tool
def plan_status() -> str:
    """Show the execution plan for this conversation and how far it has got.

    Returns every step of the active run with its status, plus which step is
    next. Read this when you need the plan — it is the authoritative copy, and
    it is kept on disk rather than in the conversation.
    """
    scope = _scope()
    document = plan_store.load_document(**scope)
    return render_ledger(
        document.active, path=str(plan_store.plan_file_path(**scope))
    )


@tool
def plan_update(step: int, status: str, note: str = "") -> str:
    """Record the outcome of one plan step, then show the updated plan.

    Call this once per step, when the step's outcome is actually established by
    tool evidence — not before starting it and not to restate progress.

    Args:
        step: Step number in the active run, as shown by `plan_status`.
        status: One of `in_progress`, `completed`, `blocked`, `skipped`, `pending`.
        note: Optional one-line result worth carrying forward, such as a key
            value, an output path, or why the step was blocked.

    Returns:
        The updated plan, or an error naming the valid steps or statuses.
    """
    scope = _scope()
    ok, message, run = plan_store.update_step(
        step_number=step, status=status, note=note, **scope
    )
    if not ok:
        return f"Plan not updated: {message}"
    return f"{message}\n\n" + render_ledger(
        run, path=str(plan_store.plan_file_path(**scope))
    )
