from __future__ import annotations
import gradio as gr
from gradio.components.chatbot import ChatMessage
import time
from copy import deepcopy
from html import escape
from typing import Any, Dict, Iterable, List, Optional, Set
from markdown_it import MarkdownIt

_markdown_to_html = None

from app.state import UIState
from app.ui import tool_display
from app.ui.formatters import _derive_message_id


AGENT_TITLES = {
    "task_classifier": "Task Classifier",
    "planning_agent": "Planning Agent",
    "approval_ack": "Approved — starting execution",
    "execute_agent": "Execute Agent",
    "execute_agent_free": "Execute Agent",
    "execute_agent_plan": "Execute Agent",
    "execute_agent_followup": "Execute Agent",
    "plan_init": "Execution Plan",
    "plan_finalize": "Execution Plan",
    "summary_agent": "Summary Agent",
    "summary_agent_simple": "Summary Agent",
    "summary_agent_complex": "Summary Agent",
    "summary_agent_meta": "Response",
    "summary": "Summary",
    "context_summary": "Context Summary",
    "context_summary_simple": "Context Summary",
    "context_summary_complex": "Context Summary",
    "context_summary_meta": "Context Summary",
}

IGNORED_NODES = {
    "task_classifier",
    "human_chat",
    "__start__",
    "__end__",
    "summary",
    "context_summary",
    "context_summary_simple",
    "context_summary_complex",
    "context_summary_meta",
    "plan_init",
    "plan_finalize",
}

# Nodes whose output is the deliverable rather than working narration. 
# Rendered as a report card so the answer does not look like one more grey tool box.
REPORT_NODES = {
    "summary_agent",
    "summary_agent_simple",
    "summary_agent_complex",
    "summary_agent_meta",
}

# The plan under review. Styled so the thing the user is being asked to approve is legible at a glance.
PLAN_NODES = {"planning_agent"}
TIMELINE_SNAPSHOT_VERSION = 1

_MARKDOWN_IT_RENDERER = (
    MarkdownIt(
        "commonmark",
        {"breaks": True, "html": False},
    )
    if MarkdownIt is not None
    else None
)

def reset_chat_messages(state: UIState) -> None:
    """Reset timeline-related structures."""
    state.messages = []
    state.message_lookup = {}
    state.agent_blocks = {}
    state.tool_call_block_lookup = {}
    state.streaming_message_lookup = {}
    state.last_agent_block_id = None
    state.message_seq = 0

def append_user_message(state: UIState, content: str) -> ChatMessage:
    """Append a user bubble to the Chatbot timeline."""
    message = ChatMessage(role="user", content=content)
    state.messages.append(message)
    state.last_agent_block_id = None
    return message


def rebuild_from_plain_messages(
    state: UIState,
    messages: Iterable[Dict[str, str]],
    *,
    skip_texts: Optional[Set[str]] = None,
) -> None:
    """Fallback for conversations without a structured timeline snapshot."""
    reset_chat_messages(state)
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "").strip()
        if not content:
            continue
        if role == "user":
            append_user_message(state, content)
        else:
            if skip_texts and content.strip() in skip_texts:
                continue
            block = _ensure_agent_block(state, "assistant")
            block["items"].append({"type": "message", "content": content})
            _refresh_block_message(state, block["block_id"])
    finalize_active_blocks(state)

def export_timeline_snapshot(state: UIState) -> Dict[str, Any]:
    """Serialize the rendered UI timeline for persistence."""
    entries: List[Dict[str, Any]] = []
    for message in state.messages:
        if message.role == "user":
            entries.append({"kind": "user", "content": str(message.content or "")})
            continue

        metadata = deepcopy(message.metadata) if isinstance(message.metadata, dict) else {}
        block_id = metadata.get("id")
        block = state.agent_blocks.get(block_id) if block_id else None
        if block:
            entries.append(
                {
                    "kind": "agent_block",
                    "block_id": block["block_id"],
                    "agent_name": block["agent_name"],
                    "metadata": metadata or _build_metadata(block["agent_name"], block["block_id"], status="done"),
                    "items": deepcopy(block["items"]),
                }
            )
            continue

        entries.append(
            {
                "kind": "assistant_plain",
                "content": str(message.content or ""),
                "metadata": metadata,
            }
        )

    return {
        "version": TIMELINE_SNAPSHOT_VERSION,
        "message_seq": state.message_seq,
        "entries": entries,
    }


def rebuild_from_timeline_snapshot(state: UIState, snapshot: Dict[str, Any]) -> bool:
    """Rebuild the UI from a persisted snapshot of rendered timeline blocks."""
    if not isinstance(snapshot, dict):
        return False

    entries = snapshot.get("entries")
    if not isinstance(entries, list):
        return False

    reset_chat_messages(state)
    max_seq = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        kind = entry.get("kind")
        if kind == "user":
            content = str(entry.get("content", "")).strip()
            if content:
                append_user_message(state, content)
            continue

        if kind == "assistant_plain":
            metadata = deepcopy(entry.get("metadata")) if isinstance(entry.get("metadata"), dict) else {}
            if metadata.get("status") == "pending":
                metadata["status"] = "done"
            block_id = metadata.get("id")
            state.messages.append(ChatMessage(role="assistant", content=str(entry.get("content", "")), metadata=metadata or None,))
            if block_id:
                state.message_lookup[str(block_id)] = len(state.messages) - 1
                max_seq = max(max_seq, _extract_message_seq(block_id))
            state.last_agent_block_id = None
            continue

        if kind != "agent_block":
            continue

        agent_name = str(entry.get("agent_name") or "assistant").lower()
        block_id = str(entry.get("block_id") or state.next_message_id(agent_name))
        metadata = deepcopy(entry.get("metadata")) if isinstance(entry.get("metadata"), dict) else {}
        metadata.setdefault("id", block_id)
        if metadata.get("status") == "pending":
            metadata["status"] = "done"

        state.messages.append(ChatMessage(role="assistant", content="", metadata=metadata))
        state.message_lookup[block_id] = len(state.messages) - 1

        items = deepcopy(entry.get("items")) if isinstance(entry.get("items"), list) else []
        block = {"agent_name": agent_name, "block_id": block_id, "items": items}
        state.agent_blocks[block_id] = block
        state.last_agent_block_id = block_id
        _restore_tool_lookup_for_block(state, block)
        _refresh_block_message(state, block_id)
        max_seq = max(max_seq, _extract_message_seq(block_id))

    stored_seq = snapshot.get("message_seq")
    state.message_seq = max(max_seq, stored_seq if isinstance(stored_seq, int) else 0)
    return bool(entries)

def process_chunk(state: UIState, chunk: Dict[str, Any]) -> bool:
    """Apply a LangGraph stream chunk. Returns True if timeline updated."""
    updated = False
    for agent_name, payload in chunk.items():
        if not isinstance(payload, dict):
            continue
        messages = payload.get("messages") or []
        if agent_name.lower() in IGNORED_NODES:
            _suppress_messages(state, messages)
            continue
        for msg in messages:
            if _ingest_message(state, msg, agent_name=agent_name):
                updated = True
    return updated

def _suppress_messages(state: UIState, messages: Any) -> None:
    """Drop an ignored node's messages permanently."""
    for msg in messages or []:
        msg_id = _derive_message_id(msg)
        if msg_id:
            state.processed_message_ids.add(msg_id)
        tool_call_id = getattr(msg, "tool_call_id", None)
        if tool_call_id:
            state.processed_tools_ids.add(tool_call_id)

def _append_streaming_text(state: UIState, agent_name: str, chunk: Any) -> bool:
    agent_key = (agent_name or "assistant").lower()
    text = _coerce_stream_text(getattr(chunk, "content", None) if chunk is not None else None)
    if not text:
        return False

    message_id = getattr(chunk, "id", None)
    if isinstance(chunk, dict):
        message_id = message_id or chunk.get("id")

    block = _ensure_agent_block(state, agent_key)
    lookup_key = str(message_id) if message_id else f"{agent_key}:{block['block_id']}:stream"
    stream_entry = state.streaming_message_lookup.get(lookup_key)

    if stream_entry and stream_entry.get("block_id") == block["block_id"]:
        idx = stream_entry.get("item_index")
        if idx is not None and idx < len(block["items"]):
            block["items"][idx]["content"] += text
            entry = stream_entry
        else:
            block["items"].append({"type": "message", "content": text})
            entry = {"block_id": block["block_id"], "item_index": len(block["items"]) - 1}
            state.streaming_message_lookup[lookup_key] = entry
    else:
        block["items"].append({"type": "message", "content": text})
        entry = {"block_id": block["block_id"], "item_index": len(block["items"]) - 1}
        state.streaming_message_lookup[lookup_key] = entry

    # Also track the latest streamed item per block.
    state.streaming_message_lookup[f"{agent_key}:{block['block_id']}:stream"] = entry

    _refresh_block_message(state, block["block_id"])
    return True


def _update_tool_call_item(block: Dict[str, Any], call: Any, *, call_id: Optional[Any] = None) -> bool:
    """Insert or update a tool_call item on a block."""
    resolved_id = call_id or getattr(call, "id", None)
    if isinstance(call, dict):
        resolved_id = resolved_id or call.get("id")
    if resolved_id is None:
        return False
    call_name = getattr(call, "name", None) or (call.get("name") if isinstance(call, dict) else "tool")
    if call_name in tool_display.SUPPRESSED_TOOLS:
        return False
    call_args = (
        getattr(call, "args", None)
        or (call.get("args") if isinstance(call, dict) else None)
        or (call.get("function", {}).get("arguments") if isinstance(call, dict) else None)
        or (call.get("arguments") if isinstance(call, dict) else None)
    )

    call_key = str(resolved_id)
    entry = tool_display.call_metadata(call_name, call_args)

    for idx, item in enumerate(block["items"]):
        if item.get("type") == "tool_call" and item.get("id") == call_key:
            # A call can be seen twice (streamed chunk, then the committed message). Keep whatever the result already recorded.
            item.update({k: v for k, v in entry.items() if k not in ("status", "note", "result_body")})
            return True

    block["items"].append({"type": "tool_call", "id": call_key, "tool_name": call_name, **entry})
    return True


def _ingest_message(state: UIState, raw_msg: Any, agent_name: Optional[str]) -> bool:
    role = _get_role(raw_msg)
    if _is_ai_stream_chunk(raw_msg, role):
        return _append_streaming_text(state, agent_name or getattr(raw_msg, "name", None) or "assistant", raw_msg)

    if role in {"human", "user"}:
        msg_id = _derive_message_id(raw_msg)
        if msg_id:
            state.processed_message_ids.add(msg_id)
        return False

    if role in {"ai", "assistant"}:
        return _ingest_ai_message(state, raw_msg, agent_name)

    if role in {"tool", "function"}:
        return _ingest_tool_result_raw(state, raw_msg)

    return False


def _ingest_ai_message(state: UIState, raw_msg: Any, agent_name: Optional[str]) -> bool:
    agent_key = (agent_name or getattr(raw_msg, "name", None) or "assistant").lower()
    return _ingest_ai_content(state, raw_msg, agent_key=agent_key, tool_calls=None)


def _ingest_ai_content(
    state: UIState,
    message: Any,
    *,
    agent_key: str,
    tool_calls: Any,
) -> bool:
    """Render a completed AI message into its agent block.

    Shared by the `ai_message` event path and the raw chunk path, which were
    two copies of this logic that had to be kept in step by hand.
    """
    message_id = _derive_message_id(message) or state.next_message_id(agent_key)
    if message_id in state.processed_message_ids:
        return False

    block = _ensure_agent_block(state, agent_key)
    updated = False

    text = _coerce_text(getattr(message, "content", None))
    primary_stream_key = str(message_id)
    fallback_stream_key = f"{agent_key}:{block['block_id']}:stream"
    stream_entry = state.streaming_message_lookup.get(
        primary_stream_key
    ) or state.streaming_message_lookup.get(fallback_stream_key)
    if stream_entry and primary_stream_key not in state.streaming_message_lookup:
        state.streaming_message_lookup[primary_stream_key] = stream_entry
        state.streaming_message_lookup.pop(fallback_stream_key, None)

    if text:
        streamed_index = (
            stream_entry.get("item_index")
            if stream_entry and stream_entry.get("block_id") == block["block_id"]
            else None
        )
        if streamed_index is not None and streamed_index < len(block["items"]):
            block["items"][streamed_index]["content"] = text
        else:
            block["items"].append({"type": "message", "content": text})
            state.streaming_message_lookup[primary_stream_key] = {
                "block_id": block["block_id"],
                "item_index": len(block["items"]) - 1,
            }
        updated = True

    call_list = tool_calls or getattr(message, "tool_calls", None) or []
    for call in call_list:
        updated |= _append_tool_call(state, block, call)

    if updated:
        _refresh_block_message(state, block["block_id"])

    state.processed_message_ids.add(message_id)
    return updated


def _append_tool_call(state: UIState, block: Dict[str, Any], call: Any) -> bool:
    call_name = getattr(call, "name", None) or (call.get("name") if isinstance(call, dict) else "tool")
    call_args = getattr(call, "args", None) or (call.get("args") if isinstance(call, dict) else {})
    call_id = getattr(call, "id", None) or (
        call.get("id") if isinstance(call, dict) else state.next_message_id("tool_call")
    )

    updated = _update_tool_call_item(
        block,
        {"id": call_id, "name": call_name, "args": call_args},
        call_id=call_id,
    )
    state.tool_call_block_lookup[str(call_id)] = block["block_id"]
    return updated


def _ingest_tool_result_raw(state: UIState, raw_msg: Any) -> bool:
    msg_id = _derive_message_id(raw_msg) or state.next_message_id("tool_result")
    if msg_id in state.processed_message_ids:
        return False

    tool_call_id = getattr(raw_msg, "tool_call_id", None) or getattr(raw_msg, "name", None)
    if tool_call_id and tool_call_id in state.processed_tools_ids:
        state.processed_message_ids.add(msg_id)
        return False
    block_id = state.tool_call_block_lookup.get(str(tool_call_id)) or state.last_agent_block_id
    if not block_id:
        state.processed_message_ids.add(msg_id)
        return False

    block = state.agent_blocks.get(block_id)
    if not block:
        state.processed_message_ids.add(msg_id)
        return False

    tool_name = getattr(raw_msg, "name", None) or "tool"
    if tool_name in tool_display.SUPPRESSED_TOOLS:
        state.processed_message_ids.add(msg_id)
        if tool_call_id:
            state.tool_call_block_lookup.pop(str(tool_call_id), None)
            state.processed_tools_ids.add(tool_call_id)
        return False

    raw_content = getattr(raw_msg, "content", None)
    status, note = tool_display.describe_result(tool_name, raw_content)
    body = tool_display.render_result_body(tool_name, raw_content)

    # Fold the outcome into the call it answers, so one action reads as one line instead of a "Tools Calling" box followed by a "Tools Result" box.
    call_key = str(tool_call_id) if tool_call_id else None
    merged = False
    if call_key:
        for item in block["items"]:
            if item.get("type") == "tool_call" and item.get("id") == call_key:
                item["status"] = status
                item["note"] = note
                item["result_body"] = body
                merged = True
                break

    if not merged:
        view = tool_display.describe_call(tool_name, None)
        block["items"].append(
            {
                "type": "tool_call",
                "id": call_key,
                "tool_name": tool_name,
                "icon": view.icon,
                "label": view.label,
                "status": status,
                "note": note,
                "call_body": "",
                "result_body": body,
            }
        )

    if tool_call_id:
        state.tool_call_block_lookup.pop(str(tool_call_id), None)
        state.processed_tools_ids.add(tool_call_id)
    _refresh_block_message(state, block_id)
    state.processed_message_ids.add(msg_id)
    return True


def _ensure_agent_block(state: UIState, agent_key: str) -> Dict[str, Any]:
    last_block_id = state.last_agent_block_id
    if last_block_id:
        block = state.agent_blocks.get(last_block_id)
        if block and block["agent_name"] == agent_key:
            return block

    # A different agent is taking over: resolve the previous block's spinner
    # and record how long it ran before starting the new one.
    _finalize_block(state, last_block_id)

    block_id = state.next_message_id(agent_key)
    metadata = _build_metadata(agent_key, block_id, status="pending")
    chat_message = ChatMessage(role="assistant", content="", metadata=metadata)
    state.messages.append(chat_message)
    state.message_lookup[block_id] = len(state.messages) - 1
    block = {"agent_name": agent_key, "block_id": block_id, "items": [], "started_at": time.time()}
    state.agent_blocks[block_id] = block
    state.last_agent_block_id = block_id
    return block


def _set_block_metadata(state: UIState, block_id: str, **updates: Any) -> None:
    """Merge ``updates`` into a rendered block's ChatMessage metadata."""
    idx = state.message_lookup.get(block_id)
    if idx is None or idx >= len(state.messages):
        return
    message = state.messages[idx]
    metadata = dict(message.metadata) if isinstance(message.metadata, dict) else {}
    metadata.update(updates)
    message.metadata = metadata


def _finalize_block(state: UIState, block_id: Optional[str], *, status: str = "done") -> None:
    """Resolve a block's live spinner and stamp its elapsed duration."""
    if not block_id:
        return
    idx = state.message_lookup.get(block_id)
    if idx is None or idx >= len(state.messages):
        return
    current = state.messages[idx].metadata if isinstance(state.messages[idx].metadata, dict) else {}
    if current.get("status") == status and "duration" in current:
        return
    updates: Dict[str, Any] = {"status": status}
    block = state.agent_blocks.get(block_id)
    started_at = block.get("started_at") if block else None
    if started_at:
        updates["duration"] = round(max(0.0, time.time() - started_at), 1)
    _set_block_metadata(state, block_id, **updates)


def finalize_active_blocks(state: UIState, *, status: str = "done") -> None:
    """Mark the currently streaming agent block as finished."""
    _finalize_block(state, state.last_agent_block_id, status=status)


def _append_card_block(
    state: UIState,
    *,
    kind: str,
    title: str,
    message: str,
    detail: Optional[str] = None,
) -> bool:
    """Append a standalone card (error or notice) and resolve any spinner."""
    finalize_active_blocks(state)
    block_id = state.next_message_id(kind)
    metadata = {"title": title, "id": block_id, "status": "done"}
    state.messages.append(ChatMessage(role="assistant", content="", metadata=metadata))
    state.message_lookup[block_id] = len(state.messages) - 1
    state.agent_blocks[block_id] = {
        "agent_name": kind,
        "block_id": block_id,
        "items": [{"type": kind, "title": title, "message": message, "detail": detail}],
    }
    # Reset the active pointer so subsequent content opens a fresh agent block.
    state.last_agent_block_id = None
    _refresh_block_message(state, block_id)
    return True


def append_error_block(
    state: UIState,
    message: str,
    *,
    title: str = "Run interrupted",
    detail: Optional[str] = None,
) -> bool:
    """Append a styled error card to the timeline (and resolve any spinner)."""
    return _append_card_block(
        state, kind="error", title=title, message=message, detail=detail
    )


def append_notice_block(
    state: UIState,
    message: str,
    *,
    title: str = "Notice",
) -> bool:
    """Append a neutral status card — a stopped run, not a failure."""
    return _append_card_block(state, kind="notice", title=title, message=message)


def _refresh_block_message(state: UIState, block_id: str) -> None:
    block = state.agent_blocks.get(block_id)
    if not block:
        return
    idx = state.message_lookup.get(block_id)
    if idx is None or idx >= len(state.messages):
        return
    # Always HTML. Mixing markdown and HTML rendering meant a block's typography changed the moment it gained its first tool call.
    state.messages[idx].content = gr.HTML(
        value=_render_block_html(block["items"], agent_name=block.get("agent_name", "")),
        container=False,
    )


def _restore_tool_lookup_for_block(state: UIState, block: Dict[str, Any]) -> None:
    """Re-register calls that are still awaiting a result after a reload."""
    for item in block["items"]:
        if item.get("type") != "tool_call":
            continue
        call_id = item.get("id")
        if not call_id or item.get("status") in ("ok", "error"):
            continue
        state.tool_call_block_lookup[str(call_id)] = block["block_id"]


def _render_block_html(items: List[Dict[str, Any]], *, agent_name: str = "") -> str:
    kind = "report" if agent_name in REPORT_NODES else (
        "plan" if agent_name in PLAN_NODES else "activity"
    )
    sections: List[str] = []
    for item in items:
        item_type = item.get("type")
        if item_type == "message":
            content = item.get("content", "")
            if content:
                sections.append(_render_message_section_html(content, kind=kind))
        elif item_type == "tool_call":
            sections.append(
                tool_display.render_tool_entry(
                    tool_display.ToolView(
                        label=item.get("label") or item.get("tool_name") or "Tool call",
                        icon=item.get("icon") or "•",
                        status=item.get("status") or "running",
                        note=item.get("note") or "",
                    ),
                    call_body=item.get("call_body", ""),
                    result_body=item.get("result_body", ""),
                )
            )
        elif item_type == "error":
            sections.append(
                _render_error_card(item.get("title"), item.get("message"), item.get("detail"))
            )
        elif item_type == "notice":
            sections.append(_render_notice_card(item.get("title"), item.get("message")))
    return f"<div class='agent-block-content agent-block-content--{kind}'>{''.join(sections)}</div>"


def _render_message_section_html(content: str, *, kind: str = "activity") -> str:
    stripped = content.strip()
    if not stripped:
        return ""
    if _MARKDOWN_IT_RENDERER is not None:
        body = _MARKDOWN_IT_RENDERER.render(stripped)
    elif _markdown_to_html is not None:
        body = _markdown_to_html(escape(stripped), extensions=["extra", "nl2br", "sane_lists"])
    else:
        body = f"<div class='agent-message-inline'>{escape(stripped)}</div>"
    return f"<section class='agent-message-section agent-message-section--{kind}'>{body}</section>"


def _render_error_card(title: Optional[str], message: Optional[str], detail: Optional[str] = None) -> str:
    parts = ["<div class='agent-error-card'>"]
    parts.append(
        f"<div class='agent-error-card__title'>{escape(str(title or 'Something went wrong'))}</div>"
    )
    if message:
        parts.append(f"<div class='agent-error-card__message'>{escape(str(message))}</div>")
    if detail:
        parts.append(
            "<details class='agent-error-card__detail'><summary>Technical details</summary>"
            f"<pre>{escape(str(detail))}</pre></details>"
        )
    parts.append("</div>")
    return "".join(parts)


def _render_notice_card(title: Optional[str], message: Optional[str]) -> str:
    parts = ["<div class='agent-notice-card'>"]
    parts.append(f"<div class='agent-notice-card__title'>{escape(str(title or 'Notice'))}</div>")
    if message:
        parts.append(f"<div class='agent-notice-card__message'>{escape(str(message))}</div>")
    parts.append("</div>")
    return "".join(parts)


def _coerce_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts).strip()
    if isinstance(content, dict) and content.get("type") == "text":
        return str(content.get("text", "")).strip()
    return str(content).strip()


def _coerce_stream_text(content: Any) -> str:
    """Coerce streamed token content without trimming whitespace."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "".join(parts)
    if isinstance(content, dict) and content.get("type") == "text":
        return str(content.get("text", ""))
    return str(content)


def _get_role(msg: Any) -> str:
    role = getattr(msg, "type", None) or getattr(msg, "role", None)
    if isinstance(msg, dict):
        role = role or msg.get("type") or msg.get("role")
    return str(role or "").lower()


def _is_ai_stream_chunk(msg: Any, role: str) -> bool:
    class_name = type(msg).__name__.lower()
    role_value = str(role or "").lower()
    return class_name == "aimessagechunk" or role_value == "aimessagechunk"


def _build_metadata(agent_name: str, block_id: str, *, status: str = "pending") -> Dict[str, Any]:
    label = AGENT_TITLES.get(agent_name, agent_name.replace("_", " ").title())
    return {"title": label, "id": block_id, "status": status}


def _extract_message_seq(block_id: str) -> int:
    try:
        return int(str(block_id).split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return 0