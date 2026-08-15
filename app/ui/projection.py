from __future__ import annotations

from typing import Any, Optional, Tuple

import gradio as gr

from app.state import UIState
from app.ui.approval import approval_updates
from app.ui.conversation_panel import conversation_panel_update
from app.ui.progress_panel import progress_update


def control_updates(state: UIState):
    """Send/Stop interactivity for the current state.

    Send is disabled while this thread is running: a second submit does not
    queue visibly, it blocks on the per-thread lock and then fires minutes later
    against a conversation that has moved on. Stop is only offered when there is
    something to stop.
    """
    running = state.is_running
    return gr.update(interactive=not running), gr.update(interactive=running)


def render(
    state: UIState,
    *,
    clear_input: bool = False,
    live: bool = False,
) -> Tuple[Any, ...]:
    """The workspace output tuple every handler returns.

    `clear_input` is true only on the yield that accepts a submission. Later
    yields leave the textbox alone, so text typed while the agent is working is
    not wiped out from under the user — which is what happened when every yield
    carried `gr.update(value="")`.

    `live` marks a frame emitted purely to advance streamed text. Those arrive
    several times a second and cannot have changed the file list or the plan, so
    the side panels are left alone: re-sending them swapped their DOM, which
    made the file list impossible to scroll while the agent was working.
    """
    banner, approve_btn, changes_btn = approval_updates(state.pending_approval)
    send_btn, stop_btn = control_updates(state)
    return (
        state,
        list(state.messages),
        gr.update(value="") if clear_input else gr.skip(),
        gr.skip() if live else conversation_panel_update(state),
        banner,
        approve_btn,
        changes_btn,
        send_btn,
        stop_btn,
        # Read straight from plan.md, so the panel cannot disagree with the file
        # that is the actual record of progress.
        gr.skip() if live else progress_update(state),
    )


def auth_status_text(state: UIState) -> str:
    base = " " if not state.is_authenticated else f"**Signed in as:** `{state.user_email}`."
    if state.auth_error:
        return f"{base}\n\n{state.auth_error}"
    return base


def auth_message(text: str, success: bool = True) -> str:
    prefix = "**Success:**" if success else "**Error:**"
    return f"{prefix} {text}"


def render_auth(state: Optional[UIState], *, clear_input: bool = False) -> Tuple[Any, ...]:
    """Workspace outputs plus the sign-in controls."""
    state = state or UIState()
    signed_in = bool(state.is_authenticated)
    return (
        *render(state, clear_input=clear_input),
        auth_status_text(state),
        gr.update(visible=signed_in),                      # logout button
        gr.update(visible=not signed_in),                  # login button
        gr.update(interactive=signed_in and state.is_verified),  # new task button
    )
