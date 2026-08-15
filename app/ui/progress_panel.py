from __future__ import annotations

from html import escape
from typing import Optional

import gradio as gr

from app.config import logger
from backend.utils import plan_store

_STATUS_LABEL = {
    plan_store.COMPLETED: ("done", "✓"),
    plan_store.IN_PROGRESS: ("active", "▸"),
    plan_store.BLOCKED: ("blocked", "!"),
    plan_store.SKIPPED: ("skipped", "–"),
}


def _step_markup(step) -> str:
    css_state, mark = _STATUS_LABEL.get(step.status, ("pending", ""))
    parts = [
        f"<li class='plan-step plan-step--{css_state}'>",
        f"<span class='plan-step__mark' aria-hidden='true'>{escape(mark)}</span>",
        "<span class='plan-step__body'>",
        f"<span class='plan-step__title'>{step.number}. {escape(step.title)}</span>",
    ]
    if step.note:
        parts.append(f"<span class='plan-step__note'>{escape(step.note)}</span>")
    parts.append("</span></li>")
    return "".join(parts)


def progress_markup(user_id: Optional[str], conversation_id: Optional[str]) -> str:
    """Render the active run of this conversation's plan, or nothing."""
    if not user_id or not conversation_id:
        return ""
    try:
        document = plan_store.load_document(user_id=user_id, conversation_id=conversation_id)
    except OSError as exc:
        logger.warning("Could not read the plan file for the progress panel: %s", exc)
        return ""

    run = document.active
    if run is None or not run.steps:
        return ""

    done, total = run.progress()
    percent = int(round(100 * done / total)) if total else 0
    unresolved = [step for step in run.steps if not step.is_terminal]
    if unresolved:
        caption = f"Step {unresolved[0].number} of {total} · {escape(unresolved[0].title)}"
    else:
        caption = f"All {total} steps resolved"

    header = [
        "<div class='plan-panel__head'>",
        "<span class='plan-panel__title'>Execution plan</span>",
        f"<span class='plan-panel__count'>{done}/{total}</span>",
        "</div>",
        f"<div class='plan-panel__caption'>{caption}</div>",
        "<div class='plan-panel__bar' role='progressbar' "
        f"aria-valuenow='{percent}' aria-valuemin='0' aria-valuemax='100'>"
        f"<span style='width:{percent}%'></span></div>",
    ]
    if run.goal:
        header.insert(3, f"<div class='plan-panel__goal'>{escape(run.goal)}</div>")
    if run.constraints:
        conditions = "".join(f"<li>{escape(item)}</li>" for item in run.constraints)
        header.append(
            "<div class='plan-panel__conditions'>"
            "<span class='plan-panel__conditions-title'>Approval conditions</span>"
            f"<ul>{conditions}</ul></div>"
        )

    steps = "".join(_step_markup(step) for step in run.steps)
    return (
        "<details class='plan-panel' open>"
        "<summary class='plan-panel__summary'>"
        + "".join(header)
        + "</summary>"
        f"<ul class='plan-panel__steps'>{steps}</ul>"
        "</details>"
    )


def progress_update(state):
    """Send the plan panel only when it differs from what was last sent.

    Same reasoning as the file list: re-sending a `gr.HTML` value swaps its DOM,
    which springs the panel's `<details>` back open and loses scroll position.
    The markup is derived purely from `plan.md` and carries no timestamps, so it
    is stable between renders and compares cleanly.
    """
    markup = progress_markup(state.user_id, state.current_thread_id)
    if markup == state.last_progress_markup:
        return gr.skip()
    state.last_progress_markup = markup
    return gr.update(value=markup, visible=bool(markup))
