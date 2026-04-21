from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from langchain_core.messages import convert_to_messages
from langgraph.types import Command

from app.app_config import AppRunConfig
from app.config import RECURSION_LIMIT, logger
from backend.db import get_postgres_checkpointer
from backend.utils.output_paths import (
    reset_current_conversation_id,
    reset_current_user_id,
    set_current_conversation_id,
    set_current_user_id,
)
from core.agents.agentic_system import create_app


def _resolve_agent_name(metadata: dict, default: str | None = None) -> str:
    node = metadata.get("langgraph_node")
    if node and node != "agent":
        return node

    triggers = metadata.get("langgraph_triggers") or ()
    for trigger in triggers:
        if isinstance(trigger, str) and "branch:to:" in trigger:
            candidate = trigger.split("branch:to:", 1)[-1]
            if candidate and candidate not in {"agent", "__start__", "__end__", "__pregel_pull"}:
                return candidate

    checkpoint_ns = metadata.get("langgraph_checkpoint_ns")
    if isinstance(checkpoint_ns, str) and ":" in checkpoint_ns:
        prefix = checkpoint_ns.split(":", 1)[0]
        if prefix and prefix not in {"agent", "__start__", "__end__", "__pregel_pull"}:
            return prefix

    return node or default or "agent"


_checkpointer_lock: asyncio.Lock | None = None


async def _get_checkpointer():
    global _checkpointer_lock
    if _checkpointer_lock is None:
        _checkpointer_lock = asyncio.Lock()

    async with _checkpointer_lock:
        return await get_postgres_checkpointer()


@asynccontextmanager
async def app_session(app_config: AppRunConfig):
    checkpointer = await _get_checkpointer()
    app = await create_app(
        checkpointer,
        user_request=app_config.user_request,
        use_context_compression=app_config.use_context_compression,
    )
    yield app


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

    conversation_token = set_current_conversation_id(thread_id)
    user_token = set_current_user_id(user_id or app_config.user_id)

    try:
        async with app_session(app_config) as app:
            event_iterator = app.astream_events(stream_input, config=config, version="v1")

            async for event in event_iterator:
                event_type = event.get("event")
                data = event.get("data") or {}
                metadata = event.get("metadata") or {}
                agent_name = _resolve_agent_name(metadata, event.get("name"))

                if event_type == "on_chat_model_end":
                    message = data.get("output")
                    if message:
                        tool_calls = getattr(message, "tool_calls", None) or (
                            message.get("tool_calls") if isinstance(message, dict) else None
                        )
                        yield (
                            "ai_message",
                            {"agent": agent_name, "message": message, "tool_calls": tool_calls},
                        )
                elif event_type == "on_tool_start":
                    call_args = data.get("input")
                    call_id = None
                    if isinstance(call_args, dict):
                        call_id = call_args.get("id")
                    else:
                        call_id = getattr(call_args, "id", None)
                    call_name = event.get("name")
                    if call_id and call_name:
                        yield (
                            "tool_call_start",
                            {
                                "agent": agent_name,
                                "call": {
                                    "id": str(call_id),
                                    "name": call_name,
                                    "args": call_args,
                                },
                            },
                        )
                elif event_type == "on_tool_end":
                    result = data.get("output")
                    call = data.get("input")
                    call_id = None
                    if isinstance(result, dict):
                        call_id = result.get("tool_call_id")
                    if call_id is None:
                        if isinstance(call, dict):
                            call_id = call.get("id")
                        else:
                            call_id = getattr(call, "id", None)
                    tool_name = event.get("name")
                    if result is not None:
                        normalized_result = result
                        if not isinstance(result, dict):
                            normalized_result = {"name": tool_name, "content": result}
                        else:
                            normalized_result = dict(result)
                            if tool_name and "name" not in normalized_result:
                                normalized_result["name"] = tool_name
                        payload = {"agent": agent_name, "result": normalized_result}
                        if call_id:
                            payload["call_id"] = str(call_id)
                        yield ("tool_result", payload)
                elif event_type == "on_chain_stream":
                    chunk = data.get("chunk")
                    if not chunk:
                        continue
                    if metadata.get("langgraph_node"):
                        payload = chunk
                        if not isinstance(chunk, dict) or "messages" not in chunk:
                            payload = {"messages": [chunk]}
                        yield ("chunk", {agent_name: payload})
                    else:
                        # LangGraph emits both a node-local chunk (with langgraph_node
                        # metadata) and a graph-level aggregate wrapper for the same
                        # message payload. The wrapper causes duplicate persistence when
                        # a timeline is rebuilt into a fresh UIState, because processed
                        # message ids are not restored from snapshot state. Skip these
                        # aggregate wrappers and keep the node-local chunk as the single
                        # source of truth.
                        if (
                            isinstance(chunk, dict)
                            and chunk
                            and all(
                                isinstance(value, dict) and "messages" in value
                                for value in chunk.values()
                            )
                        ):
                            continue
                        yield ("chunk", chunk)

            interrupted = False
            if check_for_interrupts:
                try:
                    current_state = await app.aget_state(config)
                    if getattr(current_state, "next", None):
                        interrupted = "human_chat" in current_state.next
                except Exception as exc:
                    logger.warning("Failed to inspect graph state for interrupts: %s", exc)

            yield ("complete", {"interrupted": interrupted, "completed_at": time.time()})
    except Exception as exc:
        if check_for_interrupts and _is_interrupt_exception(exc):
            yield ("complete", {"interrupted": True, "completed_at": time.time()})
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

    payload: dict[str, Any] = {"messages": convert_to_messages([user_message])}
    if user_id:
        payload["user_id"] = user_id
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return payload
