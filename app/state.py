from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from gradio.components.chatbot import ChatMessage

from app.app_config import AppRunConfig


@dataclass(slots=True)
class ConversationMeta:
    thread_id: str
    title: str
    created_at: str
    updated_at: str
    user_id: str


@dataclass
class FileRecord:
    path: str
    hash: Optional[str]
    name: str
    uploaded_at: Optional[datetime] = None
    record_id: Optional[str] = None


@dataclass
class UIState:
    """Container for the Gradio UI session state."""

    thread_ids: List[Dict[str, Any]] = field(default_factory=list)
    current_thread_id: Optional[str] = None
    selected_thread_id: Optional[str] = None
    messages: List[ChatMessage] = field(default_factory=list)
    message_lookup: Dict[str, int] = field(default_factory=dict)
    agent_blocks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    last_agent_block_id: Optional[str] = None
    tool_call_block_lookup: Dict[str, str] = field(default_factory=dict)
    message_seq: int = 0
    processed_message_ids: Set[str] = field(default_factory=set)
    processed_tools_ids: Set[str] = field(default_factory=set)
    processed_content_hashes: Set[int] = field(default_factory=set)
    streaming_message_lookup: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # The payload the graph passed to `interrupt()` while paused for plan
    # review, or None when nothing is waiting on the user. This replaces a pair
    # of booleans that were always assigned the same value and never read by
    # anything that rendered, so the approval gate was invisible.
    pending_approval: Optional[Dict[str, Any]] = None
    # Threads that produced output while the user was looking elsewhere.
    stale_threads: Set[str] = field(default_factory=set)
    # The sidebar markup this session was last *sent*. Re-sending identical
    # markup replaces the panel's DOM and discards the user's scroll position,
    # so it is compared before being emitted. None means "nothing sent yet",
    # which must force a send — reset it whenever the panel has to be redrawn.
    last_panel_markup: Optional[str] = None
    # Same treatment for the plan panel: it is a `gr.HTML` too, and it holds a
    # `<details>` the user can collapse, which a re-send would spring open again.
    last_progress_markup: Optional[str] = None
    thread_files: Dict[str, List[FileRecord]] = field(default_factory=dict)
    uploaded_files: List[FileRecord] = field(default_factory=list)
    last_run_at: Dict[str, datetime] = field(default_factory=dict)
    current_app_config: Optional[AppRunConfig] = None
    stop_signals: Dict[str, bool] = field(default_factory=dict)
    running_threads: Set[str] = field(default_factory=set)
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    is_authenticated: bool = False
    is_verified: bool = False
    auth_error: Optional[str] = None
    pending_reset_token: Optional[str] = None
    session_token: Optional[str] = None

    @property
    def is_awaiting_approval(self) -> bool:
        return self.pending_approval is not None

    @property
    def is_running(self) -> bool:
        """True when the thread on screen has a run in flight."""
        return bool(self.current_thread_id and self.current_thread_id in self.running_threads)

    def ensure_thread_storage(self, thread_id: str) -> None:
        if thread_id not in self.thread_files:
            self.thread_files[thread_id] = []

    def next_message_id(self, prefix: str = "msg") -> str:
        """Create a UI-only identifier for ChatMessage metadata."""
        self.message_seq += 1
        return f"{prefix}:{self.message_seq}"
