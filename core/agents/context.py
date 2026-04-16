from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from app.config import (
    MEMORY_MAX_ITEMS,
    MEMORY_OUTPUTS_MAX_ITEMS,
    SUMMARY_MAX_MESSAGES,
    SUMMARY_TRIGGER_CHAR_LIMIT,
    SUMMARY_TRIGGER_MIN_MESSAGES,
    SUMMARY_TRIGGER_MIN_MESSAGES_FIRST,
)
from backend.utils.output_paths import describe_output_scope


SUMMARY_AGENT_NAME = "context_summary"
SUMMARY_MEMORY_KEY = "summary_memory"


def _coerce_text(content) -> str:
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


def is_summary_message(message: BaseMessage) -> bool:
    name = getattr(message, "name", None)
    if name and str(name).lower() == SUMMARY_AGENT_NAME:
        return True
    metadata = getattr(message, "response_metadata", None) or {}
    return bool(metadata.get("is_summary"))


def latest_summary_record(
    messages: Sequence[BaseMessage],
) -> Tuple[int, Optional[str], Optional[Dict[str, Any]]]:
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if is_summary_message(msg):
            metadata = getattr(msg, "response_metadata", None) or {}
            return idx, _coerce_text(getattr(msg, "content", None)), metadata.get(SUMMARY_MEMORY_KEY)
    return -1, None, None


def messages_since_last_summary(messages: Sequence[BaseMessage]) -> List[BaseMessage]:
    if not messages:
        return []
    idx, _, _ = latest_summary_record(messages)
    if idx < 0:
        return list(messages)
    return list(messages[idx + 1 :])


def _estimate_message_chars(message: BaseMessage) -> int:
    return len(_coerce_text(getattr(message, "content", None)))


def should_summarize(messages: Sequence[BaseMessage]) -> bool:
    idx, prev_summary, _ = latest_summary_record(messages)
    source_messages = messages_since_last_summary(messages)
    if not source_messages:
        return False
    min_messages = (
        SUMMARY_TRIGGER_MIN_MESSAGES_FIRST if idx < 0 or not prev_summary else SUMMARY_TRIGGER_MIN_MESSAGES
    )
    if len(source_messages) >= min_messages:
        return True
    total_chars = sum(_estimate_message_chars(message) for message in source_messages)
    return total_chars >= SUMMARY_TRIGGER_CHAR_LIMIT


def clipped_messages_for_summary(messages: Sequence[BaseMessage]) -> List[BaseMessage]:
    source_messages = messages_since_last_summary(messages)
    if len(source_messages) > SUMMARY_MAX_MESSAGES:
        return source_messages[-SUMMARY_MAX_MESSAGES:]
    return source_messages


def _coerce_str_list(value, fallback) -> List[str]:
    if value is None:
        return list(fallback or [])
    if isinstance(value, list):
        result = []
        for item in value:
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    text = str(value).strip()
    return [text] if text else list(fallback or [])


def _normalize_outputs(value, fallback) -> List[Dict[str, str]]:
    outputs: List[Dict[str, str]] = []
    if value is None:
        return list(fallback or [])
    if not isinstance(value, list):
        value = [value]
    for item in value:
        if isinstance(item, dict):
            path = str(item.get("path", "")).strip()
            description = str(item.get("description", "") or item.get("detail", "")).strip()
            if path or description:
                outputs.append({"path": path, "description": description})
            continue
        text = str(item).strip()
        if text:
            outputs.append({"path": "", "description": text})
    return outputs or list(fallback or [])


def _merge_str_lists(new_items: List[str], prior_items: List[str], max_items: int) -> List[str]:
    seen = set()
    merged: List[str] = []
    for item in new_items + prior_items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= max_items:
            break
    return merged


def _merge_output_lists(
    new_items: List[Dict[str, str]],
    prior_items: List[Dict[str, str]],
    max_items: int,
) -> List[Dict[str, str]]:
    seen = set()
    merged: List[Dict[str, str]] = []
    for item in new_items + prior_items:
        path = str(item.get("path", "")).strip().lower()
        description = str(item.get("description", "")).strip().lower()
        key = f"{path}|{description}"
        if not key.strip("|") or key in seen:
            continue
        seen.add(key)
        merged.append({"path": item.get("path", ""), "description": item.get("description", "")})
        if len(merged) >= max_items:
            break
    return merged


def normalize_memory(candidate, prior) -> Dict[str, Any]:
    prior = prior if isinstance(prior, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}

    facts = _coerce_str_list(candidate.get("facts"), prior.get("facts"))
    outputs = _normalize_outputs(candidate.get("outputs"), prior.get("outputs"))
    decisions = _coerce_str_list(candidate.get("decisions"), prior.get("decisions"))
    open_questions = _coerce_str_list(candidate.get("open_questions"), prior.get("open_questions"))

    return {
        "facts": _merge_str_lists(facts, prior.get("facts", []), MEMORY_MAX_ITEMS),
        "outputs": _merge_output_lists(outputs, prior.get("outputs", []), MEMORY_OUTPUTS_MAX_ITEMS),
        "decisions": _merge_str_lists(decisions, prior.get("decisions", []), MEMORY_MAX_ITEMS),
        "open_questions": _merge_str_lists(open_questions, prior.get("open_questions", []), MEMORY_MAX_ITEMS),
    }


def extract_json_payload(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = cleaned[start : end + 1]
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _format_memory_for_prompt(memory: Optional[Dict[str, Any]]) -> str:
    if not isinstance(memory, dict):
        return ""
    lines: List[str] = []

    if memory.get("facts"):
        lines.append("Facts:")
        lines.extend(f"- {item}" for item in memory["facts"] if str(item).strip())

    if memory.get("outputs"):
        lines.append("Outputs:")
        for item in memory["outputs"]:
            path = str(item.get("path", "")).strip()
            description = str(item.get("description", "")).strip()
            if path and description:
                lines.append(f"- {path} | {description}")
            elif path:
                lines.append(f"- {path}")
            elif description:
                lines.append(f"- {description}")

    if memory.get("decisions"):
        lines.append("Decisions:")
        lines.extend(f"- {item}" for item in memory["decisions"] if str(item).strip())

    if memory.get("open_questions"):
        lines.append("Open questions:")
        lines.extend(f"- {item}" for item in memory["open_questions"] if str(item).strip())

    return "\n".join(lines).strip()


def build_llm_input_messages(state) -> List[BaseMessage]:
    messages = state.get("messages") or []
    if not messages:
        return []

    user_id = state.get("user_id")
    conversation_id = state.get("conversation_id")

    idx, summary_text, memory = latest_summary_record(messages)
    recent = messages_since_last_summary(messages) if idx >= 0 else list(messages)

    prefix: List[BaseMessage] = [
        SystemMessage(content=describe_output_scope(user_id=user_id, conversation_id=conversation_id))
    ]

    memory_text = _format_memory_for_prompt(memory)
    if memory_text:
        prefix.append(SystemMessage(content="Structured memory:\n" + memory_text))
    if summary_text:
        prefix.append(SystemMessage(content="Summary of previous workflow:\n" + summary_text))

    return prefix + recent


def build_uncompressed_input_messages(state) -> List[BaseMessage]:
    messages = state.get("messages") or []
    if not messages:
        return []

    user_id = state.get("user_id")
    conversation_id = state.get("conversation_id")
    visible_messages = [message for message in messages if not is_summary_message(message)]

    return [
        SystemMessage(content=describe_output_scope(user_id=user_id, conversation_id=conversation_id)),
        *visible_messages,
    ]


def build_pre_model_state(state) -> Dict[str, Any]:
    return {"llm_input_messages": build_llm_input_messages(state)}


def build_uncompressed_pre_model_state(state) -> Dict[str, Any]:
    return {"llm_input_messages": build_uncompressed_input_messages(state)}


def make_summary_message(summary_text: str, memory: Dict[str, Any]) -> AIMessage:
    return AIMessage(
        content=summary_text,
        name=SUMMARY_AGENT_NAME,
        response_metadata={"is_summary": True, SUMMARY_MEMORY_KEY: memory},
    )
