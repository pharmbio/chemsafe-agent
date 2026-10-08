from __future__ import annotations

import asyncio
import json
from typing import Optional

import gradio as gr

from app import timeline_store
from app.config import DEFAULT_CONVERSATION_TITLE, logger
from app.conversation_store import create_thread, delete_thread, load_threads, load_timeline
from app.downloads import is_data_path
from app.files import (
    clear_thread_uploads,
    delete_thread_data,
    hash_file,
    list_upload_files,
    refresh_thread_files,
    save_uploaded_file,
)
from app.langgraph_runner import read_pending_approval
from app.state import ACTIVE_RUNS, UIState
from app.ui.chat_timeline import reset_chat_messages
from app.ui.conversation_panel import conversation_panel_update, thread_to_dict
from app.ui.progress_panel import progress_update
from app.ui.projection import auth_message, render, render_auth
from backend.auth.service import AuthService

AUTH_SERVICE = AuthService()
PASSWORD_MIN_LENGTH = 8
# How often a page following another page's run checks whether it saved.
FOLLOW_POLL_SECONDS = 0.5


def initialize_state() -> UIState:
    return UIState()


def _validate_password_strength(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long")
    if password.isdigit() or password.isalpha():
        raise ValueError("Password must include both letters and numbers")


def reset_user_state(state: UIState) -> None:
    state.user_id = None
    state.user_email = None
    state.is_authenticated = False
    state.is_verified = False
    state.auth_error = None
    state.pending_reset_token = None
    state.session_token = None
    state.thread_ids = []
    state.current_thread_id = None
    state.selected_thread_id = None
    state.thread_files.clear()
    state.uploaded_files = []
    state.current_app_config = None
    state.stop_signals = {}
    state.follower = None
    state.pending_approval = None
    state.stale_threads = set()
    # Nothing sent to the signed-out panel yet, so the next render must emit it
    # rather than diff against a previous user's markup.
    state.last_panel_markup = None
    state.last_progress_markup = None
    state.last_run_at = {}
    state.processed_message_ids = set()
    state.processed_tools_ids = set()
    state.processed_content_hashes = set()
    reset_chat_messages(state)


# Conversations


async def refresh_conversation(state: UIState, thread_id: str) -> None:
    """Load one conversation into view: timeline, files, approval state."""
    state.current_thread_id = thread_id
    state.stale_threads.discard(thread_id)
    state.processed_message_ids = set()
    state.processed_tools_ids = set()
    state.processed_content_hashes = set()

    payload = await load_timeline(state.user_id, thread_id) if state.user_id else None
    timeline_store.apply_snapshot(state, payload)

    state.ensure_thread_storage(thread_id)
    refresh_thread_files(state, thread_id)
    state.uploaded_files = [
        record for record in state.thread_files.get(thread_id, []) if is_data_path(record.path)
    ]

    # Restore the approval gate from the graph instead of clearing it, so a
    # conversation paused for review stays resumable across switches and reloads.
    state.pending_approval = await read_pending_approval(thread_id)


async def sync_user_threads(state: UIState, ensure_one: bool = True) -> None:
    if not state.user_id:
        state.thread_ids = []
        state.current_thread_id = None
        state.selected_thread_id = None
        state.thread_files = {}
        state.uploaded_files = []
        reset_chat_messages(state)
        return

    threads = await load_threads(state.user_id)
    if not threads and ensure_one:
        await create_thread(state.user_id, DEFAULT_CONVERSATION_TITLE)
        threads = await load_threads(state.user_id)

    state.thread_ids = [thread_to_dict(meta) for meta in threads]
    valid_ids = {thread["thread_id"] for thread in state.thread_ids}
    for thread_id in valid_ids:
        state.ensure_thread_storage(thread_id)

    state.stale_threads = {tid for tid in state.stale_threads if tid in valid_ids}

    if state.current_thread_id not in valid_ids:
        state.current_thread_id = state.thread_ids[0]["thread_id"] if state.thread_ids else None
    if state.selected_thread_id not in valid_ids:
        state.selected_thread_id = state.current_thread_id

    # Only the on-screen conversation is scanned; walking every thread put an
    # O(conversations) filesystem crawl on login and on each new conversation.
    if state.current_thread_id:
        await refresh_conversation(state, state.current_thread_id)
    else:
        reset_chat_messages(state)
        state.uploaded_files = []


async def activate_thread(thread_id: Optional[str], state: UIState):
    if not thread_id or thread_id not in {thread["thread_id"] for thread in state.thread_ids}:
        return render(state)
    state.selected_thread_id = thread_id
    await refresh_conversation(state, thread_id)
    state.current_app_config = None
    # Switching clears the composer: a draft for one thread reads as a mistake
    # once another is on screen.
    return render(state, clear_input=True)


async def new_task(state: UIState):
    if not state.user_id:
        state.auth_error = auth_message("Please sign in first.", success=False)
        return render(state)
    meta = await create_thread(state.user_id, DEFAULT_CONVERSATION_TITLE)
    state.current_thread_id = meta.thread_id
    state.selected_thread_id = meta.thread_id
    state.thread_ids.insert(0, thread_to_dict(meta))
    state.thread_files[meta.thread_id] = []
    state.stale_threads.discard(meta.thread_id)
    state.uploaded_files = []
    reset_chat_messages(state)
    state.pending_approval = None
    return render(state, clear_input=True)


async def _delete_thread_action(thread_id: Optional[str], state: UIState):
    if not thread_id or not state.user_id or len(state.thread_ids) <= 1:
        return render(state)
    await delete_thread(state.user_id, thread_id)
    delete_thread_data(state.user_id, thread_id)
    await sync_user_threads(state, ensure_one=True)
    return render(state)


def _sign_in(state: UIState, user, session_token: str) -> None:
    state.user_id = str(user.id)
    state.user_email = user.email
    state.is_authenticated = True
    state.is_verified = True
    state.session_token = session_token


# Gradio handlers


async def on_app_load(session_token: Optional[str]):
    """Open the workspace, signed in again if the browser holds a live session.

    Every page load starts a fresh Gradio session, so without the stored token a
    reload, or a visit to /resources and back, signed the user out.
    """
    state = initialize_state()
    stored_token = gr.skip()
    if session_token:
        try:
            user = await AUTH_SERVICE.restore_session(session_token)
        except Exception:  # noqa: BLE001 - keep the token; the database may be back next load
            logger.exception("Could not restore the stored session")
        else:
            if user:
                _sign_in(state, user, session_token)
            else:
                stored_token = None  # expired or revoked
    await sync_user_threads(state, ensure_one=state.is_authenticated)
    return (*render_auth(state, clear_input=True), gr.update(value=""), stored_token)


async def on_new_task(state: UIState):
    return await new_task(state or initialize_state())


async def on_conversation_action(action_payload: str, state: UIState):
    """Handle a click routed through the sidebar's hidden action bus."""
    state = state or initialize_state()
    payload = (action_payload or "").strip()
    if not payload:
        return (*render(state), gr.update(value=""))
    try:
        action = json.loads(payload)
    except json.JSONDecodeError:
        return (*render(state), gr.update(value=""))

    action_type = action.get("type")
    thread_id = action.get("thread_id")
    if action_type == "delete":
        result = await _delete_thread_action(thread_id, state)
    elif action_type == "activate":
        result = await activate_thread(thread_id, state)
    else:
        result = render(state)
    # Clear the bus so the same click is not replayed on the next change event.
    return (*result, gr.update(value=""))


async def on_register(email: str, password: str, confirm: str, state: UIState):
    state = state or initialize_state()
    email = (email or "").strip()
    password = password or ""
    confirm = confirm or ""
    if not email or not password:
        state.auth_error = auth_message("Email and password are required.", success=False)
        return render_auth(state)
    if password != confirm:
        state.auth_error = auth_message("Passwords do not match.", success=False)
        return render_auth(state)
    try:
        _validate_password_strength(password)
        await AUTH_SERVICE.register_user(email, password)
        state.auth_error = auth_message(
            "Registration submitted. Approval pending. You will be notified by "
            "email once your account is ready.",
            success=True,
        )
    except Exception as exc:  # noqa: BLE001 - shown to the user verbatim
        state.auth_error = auth_message(str(exc), success=False)
    return render_auth(state)


async def on_login(email: str, password: str, state: UIState):
    state = state or initialize_state()
    email = (email or "").strip()
    password = password or ""
    if not email or not password:
        state.auth_error = auth_message("Email and password are required.", success=False)
        return (*render_auth(state), gr.skip())
    try:
        user = await AUTH_SERVICE.login(email, password)
        _sign_in(state, user, await AUTH_SERVICE.create_session(user.id))
        state.auth_error = auth_message("Signed in successfully.", success=True)
        await sync_user_threads(state)
    except Exception as exc:  # noqa: BLE001 - shown to the user verbatim
        reset_user_state(state)
        state.auth_error = auth_message(str(exc), success=False)
    return (*render_auth(state), state.session_token)


async def on_logout(state: UIState):
    state = state or initialize_state()
    await AUTH_SERVICE.logout(state.session_token)
    reset_user_state(state)
    state.auth_error = auth_message("Logged out.", success=True)
    return (*render_auth(state), None)


# Files


async def on_files_uploaded(files, state: UIState):
    state = state or initialize_state()
    if not files or not state.user_id or not state.current_thread_id:
        return state, conversation_panel_update(state)
    existing_hashes = {
        hash_file(path)
        for path in list_upload_files(state.current_thread_id, user_id=state.user_id)
    }
    for file_obj in files:
        destination, file_hash = save_uploaded_file(
            file_obj, user_id=state.user_id, thread_id=state.current_thread_id
        )
        if file_hash in existing_hashes:
            destination.unlink(missing_ok=True)
            continue
        existing_hashes.add(file_hash)
    refresh_thread_files(state, state.current_thread_id)
    return state, conversation_panel_update(state)


async def on_clear_files(state: UIState):
    state = state or initialize_state()
    if not state.user_id or not state.current_thread_id:
        return state, conversation_panel_update(state)
    clear_thread_uploads(state.user_id, state.current_thread_id)
    refresh_thread_files(state, state.current_thread_id)
    return state, conversation_panel_update(state)


async def on_periodic_file_refresh(state: UIState):
    """Keep the sidebar and the plan panel current while a run is in flight.

    Gated on there being a run in flight. This ticks once a second for every
    connected browser; unconditionally walking the conversation's directories
    meant idle sessions paid for a filesystem crawl forever.

    The plan panel refreshes on the same tick so a step that resolves during a
    long stretch of tool calls shows up promptly rather than at the end.
    """
    if state is None or not state.current_thread_id or not state.is_running:
        return state, gr.skip(), gr.skip()
    files_changed = refresh_thread_files(state, state.current_thread_id)
    return (
        state,
        conversation_panel_update(state) if files_changed else gr.skip(),
        progress_update(state),
    )


async def on_follow_run(state: UIState):
    """Keep the conversation on screen in step with a run another page started.

    That run writes into the state of the page that started it, which may be
    gone (a reload, or a visit to /resources), and saves the conversation as it
    goes. This reloads the saved copy whenever a newer one lands, and once more
    when the run ends, for the final answer, its files and any plan review.
    Chained after each handler that can put such a conversation on screen.
    """
    # Before anything else: when a generator ends without yielding, Gradio sends
    # None to every output, which blanked the chat and the sidebar the handler
    # this is chained after had just drawn.
    yield gr.skip()
    if state is None or not state.user_id or not state.current_thread_id:
        return
    thread_id = state.current_thread_id
    run_state = ACTIVE_RUNS.get(thread_id)
    if run_state is None or run_state is state:
        return  # nothing running, or this page's own run, which streams to it directly
    token = state.follower = object()
    loaded = None
    try:
        while state.follower is token and state.current_thread_id == thread_id:
            run_state = ACTIVE_RUNS.get(thread_id)
            if run_state is state:
                return
            finished = run_state is None
            revision = timeline_store.revision(thread_id)
            if finished or revision != loaded:
                payload = await load_timeline(state.user_id, thread_id)
                approval = await read_pending_approval(thread_id) if finished else None
                if state.current_thread_id != thread_id:
                    return  # another conversation was opened while this loaded
                timeline_store.apply_snapshot(state, payload, live=not finished)
                state.processed_content_hashes = set()
                refresh_thread_files(state, thread_id)
                state.pending_approval = approval
                loaded = revision
                yield render(state)
                if finished:
                    return
            await asyncio.sleep(FOLLOW_POLL_SECONDS)
    finally:
        if state.follower is token:
            state.follower = None
