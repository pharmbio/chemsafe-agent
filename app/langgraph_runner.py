from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from langchain_core.messages import convert_to_messages
from langgraph.types import Command

from langchain_core.messages import AIMessageChunk

from app.app_config import AppRunConfig
from app.config import (
    RECURSION_LIMIT,
    STREAM_FLUSH_CHARS,
    STREAM_FLUSH_SECONDS,
    STREAM_TOKENS,
    logger,
)
from backend.db import get_postgres_checkpointer
from backend.utils.output_paths import (
    reset_current_conversation_id,
    reset_current_user_id,
    set_current_conversation_id,
    set_current_user_id,
)
from core.agents.agentic_system import create_app
from core.agents.context import mark_user_request


_INTERNAL_NODES = {"agent", "tools", "__start__", "__end__", "__pregel_pull"}


def _resolve_agent_name(
    namespace: Any,
    metadata: Optional[dict] = None,
    default: Optional[str] = None,
) -> str:
    """Which graph node produced this event, in the UI's vocabulary.

    Inside a `create_react_agent` the node is always called `agent` or `tools`,
    which says nothing a user would recognise. The subgraph namespace carries
    the parent's name — `('execute_agent_plan:<uuid>',)` — so streaming with
    `subgraphs=True` makes this a lookup rather than the inference it used to
    be: the previous version guessed from `langgraph_triggers` strings and
    `langgraph_checkpoint_ns` prefixes because the flat event stream had thrown
    the hierarchy away.
    """
    for entry in tuple(namespace or ()):
        if isinstance(entry, str) and entry:
            candidate = entry.split(":", 1)[0]
            if candidate and candidate not in _INTERNAL_NODES:
                return candidate

    node = (metadata or {}).get("langgraph_node")
    if node and node not in _INTERNAL_NODES:
        return node
    return node or default or "agent"


def _stream_chunk_text(chunk: Any) -> str:
    """Text carried by a streamed model chunk, ignoring tool-call deltas."""
    if chunk is None:
        return ""
    content = getattr(chunk, "content", None)
    if content is None and isinstance(chunk, dict):
        content = chunk.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


_checkpointer_lock: asyncio.Lock | None = None
_app_cache: dict[bool, Any] = {}
_app_cache_lock: asyncio.Lock | None = None


async def _get_checkpointer():
    global _checkpointer_lock
    if _checkpointer_lock is None:
        _checkpointer_lock = asyncio.Lock()

    async with _checkpointer_lock:
        return await get_postgres_checkpointer()


async def get_compiled_app(use_context_compression: bool = True):
    """Return the compiled graph, building it at most once per variant.

    The checkpointer is a process-wide singleton, so the compiled graph is safe
    to reuse. Rebuilding it per run meant re-instantiating three chat models and
    six react agents on every user message.
    """
    global _app_cache_lock
    if _app_cache_lock is None:
        _app_cache_lock = asyncio.Lock()

    key = bool(use_context_compression)
    cached = _app_cache.get(key)
    if cached is not None:
        return cached

    async with _app_cache_lock:
        cached = _app_cache.get(key)
        if cached is not None:
            return cached
        checkpointer = await _get_checkpointer()
        app = await create_app(checkpointer, use_context_compression=key)
        _app_cache[key] = app
        return app


@asynccontextmanager
async def app_session(app_config: AppRunConfig):
    yield await get_compiled_app(app_config.use_context_compression)


def _interrupt_payloads(snapshot: Any) -> list[dict[str, Any]]:
    """The values passed to `interrupt()` by whatever paused this graph.

    `human_chat_node` raises a structured payload — `{"type": "plan_review",
    "plan": ..., "message": ...}` — and that is what the approval panel is built
    from. Reading only `snapshot.next` told us *that* the graph had paused but
    threw away everything about *why*, so the UI could not say what it was
    waiting for.

    LangGraph exposes interrupts on the snapshot directly in recent versions and
    on each pending task in older ones; both are read so the payload survives a
    checkpointer upgrade.
    """
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _collect(interrupts: Any) -> None:
        for item in tuple(interrupts or ()):
            value = getattr(item, "value", item)
            if not isinstance(value, dict):
                continue
            # The same interrupt is reachable from both the snapshot and its
            # pending task; key on content so it is counted once even when the
            # two paths hand back distinct objects.
            key = json.dumps(value, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            payloads.append(value)

    _collect(getattr(snapshot, "interrupts", ()))
    for task in tuple(getattr(snapshot, "tasks", ()) or ()):
        _collect(getattr(task, "interrupts", ()))
    return payloads


async def read_pending_approval(
    thread_id: str,
    *,
    use_context_compression: bool = True,
) -> Optional[dict[str, Any]]:
    """The plan-review payload if `thread_id` is paused for approval, else None.

    The graph is the single source of truth for this. The UI's own flag lives in
    per-browser Gradio session state, which is reset by thread switches, page
    reloads and app restarts; when it disagreed with the graph, the next message
    was sent as fresh input instead of a resume, and LangGraph silently
    restarted the run from START — re-classifying, re-planning and interrupting
    again.
    """
    if not thread_id:
        return None
    try:
        app = await get_compiled_app(use_context_compression)
        snapshot = await app.aget_state({"configurable": {"thread_id": thread_id}})
    except Exception as exc:
        logger.warning("Could not read graph state for thread %s: %s", thread_id, exc)
        return None
    if "human_chat" not in tuple(getattr(snapshot, "next", ()) or ()):
        return None
    payloads = _interrupt_payloads(snapshot)
    review = next((item for item in payloads if item.get("type") == "plan_review"), None)
    # Paused at human_chat but without a readable payload: still an approval
    # gate, so report one rather than leaving the user stuck with no affordance.
    return review or (payloads[0] if payloads else {"type": "plan_review"})


async def thread_awaits_approval(
    thread_id: str,
    *,
    use_context_compression: bool = True,
) -> bool:
    return (
        await read_pending_approval(thread_id, use_context_compression=use_context_compression)
    ) is not None


def _is_interrupt_exception(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(keyword in message for keyword in ("interrupt", "interrupted", "human input required"))


async def stream_langgraph_events(
    app_config: AppRunConfig,
    stream_input: Any,
    thread_id: str,
    *,
    user_id: Optional[str] = None,
    check_for_interrupts: bool = False,
):
    if not thread_id:
        raise ValueError("No active conversation thread is selected.")

    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }

    # Binding the scope here only reaches consumers that iterate this generator
    # with a plain `async for`. `run_controller` pumps it with a Task per
    # `__anext__` (to race the file-refresh tick), and a Task starts from its own
    # copy of the ambient context — so what is set here survives only the first
    # event. That caller pins the scope into an explicit context instead; see
    # `build_conversation_context`. Kept for direct consumers.
    conversation_token = set_current_conversation_id(thread_id)
    user_token = set_current_user_id(user_id or app_config.user_id)

    # Coalesced token buffers, keyed by (node, message id).
    token_buffers: dict[tuple[str, str], str] = {}
    token_last_flush: dict[tuple[str, str], float] = {}
    # Interrupt payloads seen on the `updates` stream during this run.
    streamed_interrupts: list[dict[str, Any]] = []

    try:
        async with app_session(app_config) as app:
            # `messages` carries token-level model output; `updates` carries what
            # each node committed, including `__interrupt__`. `subgraphs=True` is
            # what makes the react agents' events attributable: without it every
            # event inside one is labelled `agent`/`tools` and the producing node
            # has to be guessed from metadata strings.
            event_iterator = app.astream(
                stream_input,
                config=config,
                stream_mode=["messages", "updates"],
                subgraphs=True,
            )

            async for item in event_iterator:
                if not isinstance(item, tuple) or len(item) != 3:
                    continue
                namespace, mode, data = item

                if mode == "updates":
                    if not isinstance(data, dict):
                        continue
                    if "__interrupt__" in data:
                        # In-band, so recovering the payload no longer depends on
                        # a post-stream state read.
                        for entry in tuple(data.get("__interrupt__") or ()):
                            value = getattr(entry, "value", entry)
                            if isinstance(value, dict):
                                streamed_interrupts.append(value)
                        continue
                    for node_name, update in data.items():
                        if not isinstance(update, dict) or not update.get("messages"):
                            continue
                        agent_name = _resolve_agent_name(namespace, None, node_name)
                        # A node wrapping a subgraph re-emits every message that
                        # subgraph produced. Those ids were already rendered from
                        # the subgraph's own updates, so the timeline's id
                        # de-duplication is what stops them appearing twice.
                        yield ("chunk", {agent_name: {"messages": list(update["messages"])}})
                    continue

                if mode != "messages" or not isinstance(data, tuple) or len(data) != 2:
                    continue

                message, metadata = data
                agent_name = _resolve_agent_name(namespace, metadata or {})

                # Completed messages from non-streaming nodes also arrive here,
                # but the node's `updates` entry carries the same message, so
                # they are left to that path rather than rendered twice.
                if not isinstance(message, AIMessageChunk) or not STREAM_TOKENS:
                    continue

                # Show text as it is generated instead of only when a whole model
                # response completes. The completed message that follows replaces
                # the accumulated text, so an unflushed remainder self-corrects.
                text = _stream_chunk_text(message)
                if not text:
                    continue
                key = (agent_name, str(getattr(message, "id", "") or ""))
                buffered = token_buffers.get(key, "") + text
                now = time.monotonic()
                elapsed = now - token_last_flush.get(key, 0.0)
                if elapsed >= STREAM_FLUSH_SECONDS or len(buffered) >= STREAM_FLUSH_CHARS:
                    token_last_flush[key] = now
                    token_buffers[key] = ""
                    yield (
                        "token",
                        {
                            agent_name: {
                                "messages": [AIMessageChunk(content=buffered, id=key[1] or None)]
                            }
                        },
                    )
                else:
                    token_buffers[key] = buffered

            interrupted = bool(streamed_interrupts)
            approval: Optional[dict[str, Any]] = None
            if streamed_interrupts:
                # Captured in-band from the `updates` stream — no extra round
                # trip to Postgres to find out why the run stopped.
                approval = next(
                    (item for item in streamed_interrupts if item.get("type") == "plan_review"),
                    streamed_interrupts[0],
                )
            elif check_for_interrupts:
                try:
                    # Fallback for an interrupt that carried no readable payload.
                    # Read the snapshot only after the stream iterator is done:
                    # a persistent checkpointer may not have the checkpoint that
                    # backs the interrupt visible until then, and would answer
                    # with a stale, pre-interrupt state.
                    current_state = await app.aget_state(config)
                    if "human_chat" in tuple(getattr(current_state, "next", ()) or ()):
                        interrupted = True
                        payloads = _interrupt_payloads(current_state)
                        approval = next(
                            (item for item in payloads if item.get("type") == "plan_review"),
                            payloads[0] if payloads else {"type": "plan_review"},
                        )
                except Exception as exc:
                    logger.warning("Failed to inspect graph state for interrupts: %s", exc)

            yield (
                "complete",
                {
                    "interrupted": interrupted,
                    "approval": approval,
                    "completed_at": time.time(),
                },
            )
    except Exception as exc:
        if check_for_interrupts and _is_interrupt_exception(exc):
            yield (
                "complete",
                {
                    "interrupted": True,
                    "approval": {"type": "plan_review"},
                    "completed_at": time.time(),
                },
            )
            return
        raise
    finally:
        reset_current_conversation_id(conversation_token)
        reset_current_user_id(user_token)


def build_stream_input(
    user_message: str,
    *,
    user_id: str | None = None,
    conversation_id: str | None = None,
    resume: bool = False,
) -> Any:
    if resume:
        return Command(resume=user_message)

    # Tagged so the context layer can tell a new user turn apart from the
    # HumanMessages that plan review writes into the same transcript.
    messages = [mark_user_request(message) for message in convert_to_messages([user_message])]
    payload: dict[str, Any] = {"messages": messages}
    if user_id:
        payload["user_id"] = user_id
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return payload
