from __future__ import annotations

import time
from typing import Any, Dict, Optional

from app.conversation_store import load_timeline, save_timeline
from app.state import UIState
from app.ui.chat_timeline import (
    export_timeline_snapshot,
    rebuild_from_plain_messages,
    rebuild_from_timeline_snapshot,
    reset_chat_messages,
)

TIMELINE_STATE_VERSION = 1

# How long a detached run may buffer timeline changes before writing them.
# Every event used to cost a load + full rebuild + save round trip; a long run
# with nobody watching turned into hundreds of those.
DETACHED_FLUSH_SECONDS = 2.0


def serialize_timeline_state(state: UIState) -> Dict[str, Any]:
    return {
        "timeline_state_version": TIMELINE_STATE_VERSION,
        "timeline_snapshot": export_timeline_snapshot(state),
        "processed_message_ids": sorted(str(msg_id) for msg_id in state.processed_message_ids if msg_id),
        "processed_tools_ids": sorted(str(tool_id) for tool_id in state.processed_tools_ids if tool_id),
    }


def extract_timeline_snapshot(payload: Any) -> Any:
    if isinstance(payload, dict) and "timeline_snapshot" in payload:
        return payload["timeline_snapshot"]
    return payload


def restore_timeline_processing_state(state: UIState, payload: Any) -> None:
    """Restore the de-duplication sets that go with a snapshot.

    Without these a rebuilt timeline re-ingests messages it has already
    rendered, because "have I seen this message id" is the only thing stopping
    a replayed chunk from being appended twice.
    """
    if not isinstance(payload, dict) or "timeline_snapshot" not in payload:
        return
    state.processed_message_ids = {
        str(msg_id) for msg_id in (payload.get("processed_message_ids") or []) if msg_id
    }
    state.processed_tools_ids = {
        str(tool_id) for tool_id in (payload.get("processed_tools_ids") or []) if tool_id
    }


def apply_snapshot(state: UIState, payload: Any) -> bool:
    """Rebuild ``state``'s timeline from a persisted payload."""
    snapshot = extract_timeline_snapshot(payload)
    rebuilt = False
    if isinstance(snapshot, dict):
        rebuilt = rebuild_from_timeline_snapshot(state, snapshot)
    elif isinstance(snapshot, list):
        rebuild_from_plain_messages(state, snapshot)
        rebuilt = True
    if not rebuilt:
        reset_chat_messages(state)
    restore_timeline_processing_state(state, payload)
    return rebuilt


async def persist(thread_id: Optional[str], state: UIState) -> None:
    if not thread_id or not state.user_id:
        return
    await save_timeline(state.user_id, thread_id, serialize_timeline_state(state))


async def load_detached(user_id: str, thread_id: str) -> UIState:
    """A standalone UIState carrying only this thread's rendered timeline.

    Used while the user is looking at a different conversation: the run keeps
    writing into this object instead of into the session state they can see.
    """
    detached = UIState()
    detached.user_id = user_id
    payload = await load_timeline(user_id, thread_id)
    apply_snapshot(detached, payload)
    return detached


class DetachedTimelineWriter:
    """Buffers timeline updates for a run whose thread nobody is watching.

    Holds one in-memory `UIState` for the whole detached stretch and flushes it
    on a timer, rather than reloading and rewriting the entire timeline for
    every streamed event.
    """

    def __init__(self, user_id: str, thread_id: str) -> None:
        self._user_id = user_id
        self._thread_id = thread_id
        self._state: Optional[UIState] = None
        self._dirty = False
        self._last_flush = 0.0

    async def state(self) -> UIState:
        if self._state is None:
            self._state = await load_detached(self._user_id, self._thread_id)
            self._last_flush = time.monotonic()
        return self._state

    def mark_dirty(self) -> None:
        self._dirty = True

    async def maybe_flush(self, *, force: bool = False) -> None:
        if not self._dirty or self._state is None:
            return
        now = time.monotonic()
        if not force and now - self._last_flush < DETACHED_FLUSH_SECONDS:
            return
        await save_timeline(
            self._user_id,
            self._thread_id,
            serialize_timeline_state(self._state),
        )
        self._dirty = False
        self._last_flush = now

    def discard(self) -> None:
        """Drop the buffer after the viewer reattached and took over."""
        self._state = None
        self._dirty = False
