import hashlib
import json
from typing import Any, Dict, Optional


TOOL_CALL_START_MARKER = "<!--TOOL_CALL_START-->"
TOOL_CALL_END_MARKER = "<!--TOOL_CALL_END-->"
TOOL_BLOCK_META_PREFIX = "<!--TOOL_BLOCK_META:"
TOOL_BLOCK_META_SUFFIX = "-->"


def pretty_print_tool_call(name: str, args: Dict[str, Any]) -> str:
    """Format tool call information for display."""
    output = f"🔧 Tool: `{name}`\n\n"

    for key, value in args.items():
        if key == "code" or (isinstance(value, str) and value.strip().startswith(("import", "def", "#"))):
            output += f"**📦 Args:** `{key}`:\n"
            output += f"```python\n{value}\n```\n"
        elif isinstance(value, (dict, list)):
            output += f"**📦 Args:** `{key}`:\n"
            output += f"```json\n{json.dumps(value, indent=2)}\n```\n"
        else:
            output += f"**📦 Args:** `{key}`: `{value}`\n\n"

    return _wrap_tool_block(output, kind="call", source=name)


def _wrap_tool_block(content: str, *, kind: str = "call", source: Optional[str] = None) -> str:
    """Wrap any tool-related block so the UI can relocate it."""
    body = content.strip()
    if not body:
        return ""
    meta_parts = [f"kind={kind}"]
    if source:
        meta_parts.append(f"source={source}")
    metadata = f"{TOOL_BLOCK_META_PREFIX}{'|'.join(meta_parts)}{TOOL_BLOCK_META_SUFFIX}\n"
    return f"{TOOL_CALL_START_MARKER}\n{metadata}{body}\n{TOOL_CALL_END_MARKER}\n"


def _derive_message_id(msg) -> Optional[str]:
    """Get a stable identifier for messages; fall back for tool outputs."""
    msg_id = getattr(msg, "id", None)
    if msg_id:
        return msg_id

    if getattr(msg, "type", None) == "tool":
        tool_call_id = getattr(msg, "tool_call_id", None)
        if tool_call_id:
            return f"tool_call:{tool_call_id}"

        name = getattr(msg, "name", "tool")
        content = getattr(msg, "content", "")
        signature = f"{name}:{repr(content)[:200]}"
        digest = hashlib.sha1(signature.encode("utf-8", errors="ignore")).hexdigest()[:16]
        return f"tool_signature:{digest}"

    return None


def reconstruct_assistant_response(ai_messages):
    """Reconstruct formatted assistant response maintaining chronological order."""
    try:
        output = ""
        last_agent = None

        for msg in ai_messages:
            if not hasattr(msg, "content"):
                continue

            msg_type = getattr(msg, "type", None)
            agent_name = getattr(msg, "name", "supervisor")

            if msg_type == "tool" and last_agent:
                agent_name = last_agent
            elif msg_type == "tool" and not last_agent:
                agent_name = getattr(msg, "name", "tool_results")

            if agent_name != last_agent:
                output += f"\n\n**{agent_name.upper()}**\n"
                output += "-" * 40 + "\n\n"
                last_agent = agent_name

            if msg_type == "tool":
                tool_name = getattr(msg, "name", "tool")
                tool_content = getattr(msg, "content", "")

                if isinstance(tool_content, (dict, list)):
                    formatted = json.dumps(tool_content, indent=2)
                    result_block = f"```json\n{formatted}\n```\n\n"
                else:
                    result_block = f"{tool_content}\n\n"
                output += _wrap_tool_block(result_block, kind="result", source=tool_name)
                continue

            if msg.content:
                if isinstance(msg.content, list):
                    for chunk in msg.content:
                        if isinstance(chunk, dict) and chunk.get("type") == "text":
                            output += f"{chunk.get('text')}\n\n"
                else:
                    output += f"{msg.content}\n\n"

            tool_calls = getattr(msg, "tool_calls", None)
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if isinstance(call, dict):
                        name = call.get("name", "unknown_tool")
                        args = call.get("args", {})
                        output += pretty_print_tool_call(name, args)

        return output.strip()

    except Exception:
        return ""
