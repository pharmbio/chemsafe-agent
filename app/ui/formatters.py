import hashlib
from typing import Optional


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