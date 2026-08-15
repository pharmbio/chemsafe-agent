from __future__ import annotations
import gradio as gr
from typing import Any, Dict, Optional
from html import escape

DEFAULT_APPROVAL_MESSAGE = ("Review the plan above. Approve it to start execution, or describe the changes you want and the planner will revise it.")

APPROVE_TEXT = "Approved. Please proceed with the plan as written."
REQUEST_CHANGES_HINT = ("Describe what should change, then send. The plan will be revised and brought back for another review.")

def approval_banner_markup(payload: Optional[Dict[str, Any]]) -> str:
    if not payload:
        return ""
    message = str(payload.get("message") or DEFAULT_APPROVAL_MESSAGE)
    return (
        "<div class='approval-panel' role='status' aria-live='polite'>"
        "<div class='approval-panel__title'>"
        "<span class='approval-panel__icon' aria-hidden='true'>⏸</span>"
        "Waiting for your approval"
        "</div>"
        f"<div class='approval-panel__message'>{escape(message)}</div>"
        "<div class='approval-panel__hint'>"
        "You can also approve with conditions — e.g. "
        "<em>“go ahead, but use the STEL rather than the TWA”</em> — by typing "
        "them below."
        "</div>"
        "</div>"
    )


def approval_updates(payload: Optional[Dict[str, Any]]):
    """Component updates for (banner, approve button, request-changes button)."""
    waiting = payload is not None
    return (
        gr.update(value=approval_banner_markup(payload), visible=waiting),
        gr.update(visible=waiting),
        gr.update(visible=waiting),
    )
