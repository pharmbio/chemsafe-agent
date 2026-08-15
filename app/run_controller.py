from __future__ import annotations

import asyncio
import contextvars
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from app import timeline_store
from app.app_config import AppRunConfig
from app.config import DEFAULT_CONVERSATION_TITLE, logger
from app.conversation_store import update_thread_title
from app.files import refresh_thread_files
from app.langgraph_runner import (
    build_stream_input,
    read_pending_approval,
    stream_langgraph_events,
)
from app.state import UIState
from backend.utils.output_paths import set_current_conversation_id, set_current_user_id
from app.ui.approval import APPROVE_TEXT
from app.ui.projection import render
from app.ui.chat_timeline import (
    append_error_block,
    append_notice_block,
    append_user_message,
    finalize_active_blocks,
    process_chunk,
)
from app.ui.conversation_panel import append_file_paths

FILE_LIST_REFRESH_INTERVAL_SECONDS = 1.0


# --- Event application -------------------------------------------------------


def parse_complete_payload(payload: Any) -> Tuple[bool, Optional[Dict[str, Any]], Optional[datetime]]:
    if isinstance(payload, dict):
        interrupted = bool(payload.get("interrupted"))
        approval = payload.get("approval") if interrupted else None
        completed_at = payload.get("completed_at")
    else:
        interrupted, approval, completed_at = bool(payload), None, None

    completed_dt = None
    if isinstance(completed_at, (int, float)):
        completed_dt = datetime.fromtimestamp(completed_at, tz=timezone.utc)
    elif isinstance(completed_at, str):
        try:
            completed_dt = datetime.fromisoformat(completed_at)
        except ValueError:
            completed_dt = None
    if interrupted and not isinstance(approval, dict):
        approval = {"type": "plan_review"}
    return interrupted, approval, completed_dt


def apply_stream_event(event_type: str, payload: Any, state: UIState) -> bool:
    """Fold one streamed event into a timeline. Returns True when it changed.

    The runner emits three kinds: `chunk` (what a node committed — AI messages,
    tool calls, tool results), `token` (partial model text), and `complete`.
    Both message kinds go through `process_chunk`, which de-duplicates by
    message id.
    """
    if event_type in ("chunk", "token"):
        return process_chunk(state, payload)
    if event_type == "complete":
        _, approval, _ = parse_complete_payload(payload)
        state.pending_approval = approval
        # Resolve the live spinner on the last agent block once the run settles
        # (either fully done or paused for plan approval).
        finalize_active_blocks(state)
        return True
    return False


def record_stream_error(state: UIState, exc: Exception) -> bool:
    state.pending_approval = None
    message = (
        "The run stopped before finishing because a tool raised an unhandled error. "
        "Your conversation is preserved — send another message to adjust the request and continue."
    )
    detail = f"{type(exc).__name__}: {exc}"
    return append_error_block(state, message, title="Run interrupted", detail=detail)


# --- Concurrency -------------------------------------------------------------

_thread_locks: Dict[str, asyncio.Lock] = {}
_thread_locks_guard = asyncio.Lock()


async def _get_thread_lock(thread_id: str) -> asyncio.Lock:
    async with _thread_locks_guard:
        lock = _thread_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            _thread_locks[thread_id] = lock
        return lock


@asynccontextmanager
async def thread_execution_lock(thread_id: Optional[str]):
    """One run at a time per conversation."""
    if not thread_id:
        yield
        return
    lock = await _get_thread_lock(thread_id)
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()


def build_conversation_context(
    user_id: Optional[str], conversation_id: Optional[str]
) -> contextvars.Context:
    """A context with this run's output scope already bound into it.

    Returned rather than entered, because the scope has to survive across the
    generator's yield boundaries and `contextvars` mutations do not: the layers
    above pump these generators by creating a Task per `__anext__`, and each
    Task starts from its own copy of whatever the ambient context happened to
    be. Spawning the run's tasks *in* this context sidesteps that entirely.
    """
    context = contextvars.copy_context()
    context.run(set_current_user_id, user_id)
    context.run(set_current_conversation_id, conversation_id)
    return context


def _spawn(coro, context: Optional[contextvars.Context]):
    if context is not None:
        try:
            return asyncio.get_running_loop().create_task(coro, context=context)
        except TypeError:
            # `context=` on create_task needs Python 3.11+. Older runtimes lose
            # the pinned scope, which the graph state still carries.
            logger.debug("create_task(context=...) unsupported; falling back")
    return asyncio.ensure_future(coro)


async def _events_with_ticks(
    stream: AsyncIterator[Tuple[str, Any]],
    interval: float,
    *,
    context: Optional[contextvars.Context] = None,
) -> AsyncIterator[Tuple[str, Any]]:
    """Merge a stream with a periodic tick.

    A single tool call can take minutes without emitting anything, and files
    written during it — figures especially — should appear while it runs rather
    than all at once when it returns. Isolating the two-task race here keeps the
    run loop below a flat `async for`.

    Every `__anext__` runs in `context`, so the graph — and every tool it calls —
    sees the same output scope for the whole run.
    """
    iterator = stream.__aiter__()
    pending = _spawn(iterator.__anext__(), context)
    timer = asyncio.ensure_future(asyncio.sleep(interval))
    try:
        while True:
            done, _ = await asyncio.wait({pending, timer}, return_when=asyncio.FIRST_COMPLETED)
            if timer in done:
                timer = asyncio.ensure_future(asyncio.sleep(interval))
                yield ("tick", None)
            if pending in done:
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    return
                yield ("event", event)
                pending = _spawn(iterator.__anext__(), context)
    finally:
        for task in (pending, timer):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


# --- The run -----------------------------------------------------------------


async def _prepare_submission(prompt: str, state: UIState) -> Tuple[Optional[str], bool]:
    """Record the user's message and return what to send to the graph.

    Returns `(payload, resume)`, with a None payload when there is nothing to
    run. `resume` says whether this continues a paused approval rather than
    starting a fresh turn.
    """
    thread_id = state.current_thread_id
    if not state.user_id or not thread_id:
        return None, False

    prompt = (prompt or "").strip()
    if not prompt:
        return None, False

    # Whether this message resumes a pending approval is decided by the graph,
    # never by session state: the UI's copy is reset by thread switches and page
    # reloads, and is never set at all when the interrupt lands while the user
    # is viewing another thread. Sending plain input to an interrupted thread
    # makes LangGraph restart from START and re-plan.
    resume = await read_pending_approval(thread_id) is not None

    final_prompt = prompt if resume else append_file_paths(prompt, state)
    append_user_message(state, prompt)
    await timeline_store.persist(thread_id, state)

    user_messages = [message for message in state.messages if message.role == "user"]
    if len(user_messages) == 1:
        title = prompt[:60].strip() or DEFAULT_CONVERSATION_TITLE
        await update_thread_title(state.user_id, thread_id, title)
        for thread in state.thread_ids:
            if thread["thread_id"] == thread_id:
                thread["title"] = title
                break

    state.pending_approval = None
    state.current_app_config = AppRunConfig(
        user_request=final_prompt,
        user_id=state.user_id,
        conversation_id=thread_id,
    )
    return (prompt if resume else final_prompt), resume


async def _stream_run(prompt: str, state: UIState):
    thread_id = state.current_thread_id
    final_prompt, resume = await _prepare_submission(prompt, state)
    if final_prompt is None:
        yield render(state)
        return

    state.selected_thread_id = thread_id
    state.running_threads.add(thread_id)
    state.stop_signals[thread_id] = False
    yield render(state, clear_input=True)

    writer = timeline_store.DetachedTimelineWriter(state.user_id, thread_id)
    attached = True
    stopped = False

    # An explicit context carrying this run's output scope. Each `__anext__`
    # below is wrapped in a Task so it can race the tick timer, and a Task
    # copies the ambient context when it is created — so scope set *inside* the
    # streaming generator lives only in the first task's copy, and from the
    # second event on every tool call falls back to
    # `anonymous-user`/`default-thread`. Pinning one context and spawning every
    # task in it makes the scope independent of whatever the outer layers
    # (Gradio's queue included) do to the ambient context between yields.
    run_context = build_conversation_context(state.user_id, thread_id)

    stream = stream_langgraph_events(
        state.current_app_config,
        build_stream_input(
            final_prompt,
            user_id=state.user_id,
            conversation_id=thread_id,
            resume=resume,
        ),
        thread_id,
        check_for_interrupts=True,
    )

    try:
        async for kind, item in _events_with_ticks(
            stream, FILE_LIST_REFRESH_INTERVAL_SECONDS, context=run_context
        ):
            if state.stop_signals.get(thread_id):
                stopped = True
                break

            viewing = (state.selected_thread_id or state.current_thread_id) == thread_id
            if viewing and not attached:
                # The user came back. Adopt whatever the detached buffer
                # recorded while they were away, then keep writing to session
                # state again.
                await _reattach(state, thread_id, writer)
                attached = True
                yield render(state)
            elif not viewing and attached:
                attached = False
                state.stale_threads.add(thread_id)

            if kind == "tick":
                if attached:
                    if refresh_thread_files(state, thread_id):
                        yield render(state)
                else:
                    await writer.maybe_flush()
                continue

            event_type, payload = item
            if event_type == "complete":
                _, _, completed_at = parse_complete_payload(payload)
                if not (isinstance(payload, dict) and payload.get("interrupted")):
                    state.last_run_at[thread_id] = completed_at or datetime.now(timezone.utc)

            if attached:
                if apply_stream_event(event_type, payload, state):
                    # Token events are display-only: the completed message
                    # arrives moments later and is what gets persisted. Writing
                    # a snapshot and rescanning files every ~120ms during
                    # generation would cost more than it shows — and so would
                    # redrawing the side panels, which `live=True` leaves alone.
                    is_token = event_type == "token"
                    if not is_token:
                        await timeline_store.persist(thread_id, state)
                        refresh_thread_files(state, thread_id)
                    yield render(state, live=is_token)
            elif event_type != "token":
                # Nobody is watching, so tokens are pure cost: the completed
                # message carries the same text.
                detached = await writer.state()
                if apply_stream_event(event_type, payload, detached):
                    writer.mark_dirty()
                    await writer.maybe_flush()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
        logger.exception("Run failed for thread %s", thread_id)
        target = state if attached else await writer.state()
        record_stream_error(target, exc)
        if attached:
            await timeline_store.persist(thread_id, state)
            yield render(state)
        else:
            writer.mark_dirty()
            await writer.maybe_flush(force=True)
    finally:
        with suppress(Exception):
            await stream.aclose()
        state.running_threads.discard(thread_id)
        state.stop_signals.pop(thread_id, None)
        await writer.maybe_flush(force=True)

    if stopped:
        await _record_stop(state, thread_id, writer, attached=attached)
        yield render(state)
        return

    viewing = (state.selected_thread_id or state.current_thread_id) == thread_id
    if not viewing:
        state.stale_threads.add(thread_id)
    else:
        refresh_thread_files(state, thread_id)
    yield render(state)


async def _reattach(state: UIState, thread_id: str, writer: "timeline_store.DetachedTimelineWriter") -> None:
    """Pull the detached buffer's work into the state the user can see."""
    await writer.maybe_flush(force=True)
    writer.discard()
    payload = await timeline_store.load_timeline(state.user_id, thread_id)
    timeline_store.apply_snapshot(state, payload)
    state.stale_threads.discard(thread_id)
    refresh_thread_files(state, thread_id)


async def _record_stop(
    state: UIState,
    thread_id: str,
    writer: "timeline_store.DetachedTimelineWriter",
    *,
    attached: bool,
) -> None:
    """Leave a visible mark that the user stopped this run.

    Stopping used to just resolve the spinner, which is indistinguishable from
    the run finishing normally. It also matters that the graph checkpoint is
    left mid-run: the next message continues from there rather than starting
    clean, and the user should know that.
    """
    target = state if attached else await writer.state()
    target.pending_approval = None
    finalize_active_blocks(target)
    append_notice_block(
        target,
        "You stopped this run. Work already finished is saved, and any files it "
        "produced are listed in the sidebar. Send another message to continue "
        "from here.",
        title="Run stopped",
    )
    if attached:
        state.pending_approval = None
        state.current_app_config = None
        await timeline_store.persist(thread_id, state)
        refresh_thread_files(state, thread_id)
    else:
        writer.mark_dirty()
        await writer.maybe_flush(force=True)


async def run_user_message(prompt: str, state: UIState):
    async with thread_execution_lock(state.current_thread_id):
        async for update in _stream_run(prompt, state):
            yield update


# --- Handlers ----------------------------------------------------------------


async def on_send_message(prompt: str, state: UIState):
    if state is None:
        state = UIState()
    async for update in run_user_message(prompt, state):
        yield update


async def on_approve_plan(state: UIState):
    """Approve the paused plan without making the user phrase it."""
    if state is None:
        state = UIState()
    async for update in run_user_message(APPROVE_TEXT, state):
        yield update


def on_request_changes(state: UIState):
    """Focus the conversation on revising the plan.

    No graph call: the plan is revised by describing the change, so this just
    clears the gate's buttons and hands the user back the textbox with a prompt
    explaining what to write.
    """
    if state is None:
        state = UIState()
    if state.pending_approval is not None:
        payload = dict(state.pending_approval)
        payload["message"] = (
            "Describe what should change about the plan, then send. The planner "
            "will revise it and bring it back for another review."
        )
        state.pending_approval = payload
    return render(state)


async def on_stop_run(state: UIState):
    if state is None:
        state = UIState()
    thread_id = state.current_thread_id
    if thread_id and thread_id in state.running_threads:
        state.stop_signals[thread_id] = True
    return render(state)
