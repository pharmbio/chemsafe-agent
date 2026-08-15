"""Turning raw tool traffic into something a scientist can skim.

A run makes dozens of tool calls. Rendered literally they are a wall of
identical grey boxes labelled `python_executor`, and the reader has to open each
one to find out what happened — including whether it failed. Here each call is
reduced to a line that says what was done and how it went, with the raw detail
still one click away.

Two calls are dropped entirely: `plan_update` and `plan_status` exist to move
the plan file, and the plan is shown live in its own panel, so echoing them into
the transcript is noise that competes with the panel for attention.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Tools whose entire purpose is bookkeeping the progress panel already shows.
SUPPRESSED_TOOLS = {"plan_update", "plan_status"}

MAX_INLINE_RESULT_CHARS = 4000


@dataclass
class ToolView:
    """How one tool call should appear in the transcript."""

    label: str
    icon: str = "•"
    status: str = "running"  # running | ok | error
    note: str = ""  # short outcome shown on the summary line

    @property
    def suppressed(self) -> bool:
        return False


def _short_path(value: Any, *, keep: int = 2) -> str:
    """Trailing path segments — enough to identify a file, short enough to scan."""
    text = str(value or "").strip()
    if not text:
        return ""
    parts = [p for p in Path(text).parts if p not in ("/", "\\")]
    return "/".join(parts[-keep:]) if len(parts) > keep else text


def _first_meaningful_line(code: str) -> str:
    """A comment or import that hints at what a snippet is for."""
    for raw in (code or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            return line.lstrip("#").strip()
        if line.startswith(("import ", "from ")):
            return line
        return line
    return ""


def describe_call(tool_name: Optional[str], args: Any) -> ToolView:
    """The summary line for a tool call, before its result is known."""
    name = (tool_name or "tool").strip()
    parsed = _parse_args(args)

    if name == "read_files":
        target = _short_path(parsed.get("file_path") if isinstance(parsed, dict) else None)
        offset = parsed.get("offset") if isinstance(parsed, dict) else None
        limit = parsed.get("limit") if isinstance(parsed, dict) else None
        span = ""
        if offset or limit:
            start = int(offset or 1)
            span = f" (lines {start}–{start + int(limit) - 1})" if limit else f" (from line {start})"
        return ToolView(icon="📄", label=f"Read {target or 'a file'}{span}")

    if name == "python_executor":
        code = parsed.get("code") if isinstance(parsed, dict) else None
        hint = _first_meaningful_line(code or "")
        label = "Ran Python"
        if hint:
            label = f"Ran Python — {hint[:70]}" + ("…" if len(hint) > 70 else "")
        return ToolView(icon="▶", label=label)

    if name == "reset_python_state":
        return ToolView(icon="↺", label="Reset the Python session")

    return ToolView(icon="•", label=name.replace("_", " ").strip().capitalize() or "Tool call")


def coerce_result(result: Any) -> Any:
    """Recover `python_executor`'s failure envelope from its serialized form."""
    if isinstance(result, (dict, list)):
        return result
    if not isinstance(result, str):
        return result
    text = result.strip()
    if not text.startswith("{") or "error_type" not in text[:200]:
        return result
    for parse in (json.loads, ast.literal_eval):
        try:
            decoded = parse(text)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(decoded, dict) and "ok" in decoded:
            return decoded
    return result


def describe_result(tool_name: Optional[str], result: Any) -> Tuple[str, str]:
    """`(status, note)` for a finished call, so failure is visible unexpanded."""
    name = (tool_name or "").strip()
    result = coerce_result(result)

    if isinstance(result, dict) and result.get("ok") is False:
        kind = str(result.get("error_type") or "Error")
        message = str(result.get("error") or "").strip().splitlines()
        first = message[0] if message else ""
        return "error", f"{kind}: {first[:120]}" if first else kind

    text = result if isinstance(result, str) else None
    if text is not None:
        stripped = text.strip()
        if stripped.startswith("Error:"):
            return "error", stripped[:140]
        if name == "python_executor":
            if "[figures]" in text:
                count = text.count("\n- ")
                return "ok", f"wrote {count} figure{'s' if count != 1 else ''}"
            if not stripped:
                return "ok", "no output"
            return "ok", f"{len(stripped.splitlines())} line(s) of output"
        if name == "read_files":
            if "PREVIEW" in stripped[:400].upper():
                return "ok", "preview (file is large)"
            return "ok", f"{len(stripped.splitlines())} line(s)"

    return "ok", ""


def render_call_body(tool_name: Optional[str], args: Any) -> str:
    """The expandable detail for a call: the code, or the arguments."""
    name = (tool_name or "").strip()
    parsed = _parse_args(args)

    if name == "python_executor":
        code = parsed.get("code") if isinstance(parsed, dict) else None
        if isinstance(code, str) and code.strip():
            return _code_block(code.rstrip("\n"), language="python")

    if isinstance(parsed, (dict, list)):
        if not parsed:
            return ""
        return _code_block(json.dumps(parsed, indent=2, default=str), language="json")
    return _code_block(str(parsed), language="text") if parsed else ""


def render_result_body(tool_name: Optional[str], result: Any) -> str:
    """The expandable detail for a result, bounded so one dump cannot fill the page."""
    result = coerce_result(result)
    if isinstance(result, dict) and result.get("ok") is False:
        parts = []
        error = str(result.get("error") or "").strip()
        if error:
            parts.append(_code_block(_clip(error), language="text"))
        recovery = str(result.get("recovery") or "").strip()
        if recovery:
            parts.append(f"<p class='tool-recovery'>{escape(recovery)}</p>")
        return "".join(parts)

    if isinstance(result, (dict, list)):
        return _code_block(_clip(json.dumps(result, indent=2, default=str)), language="json")

    text = str(result or "")
    return _code_block(_clip(text), language="text") if text.strip() else ""


def _clip(text: str) -> str:
    if len(text) <= MAX_INLINE_RESULT_CHARS:
        return text
    head = int(MAX_INLINE_RESULT_CHARS * 0.7)
    tail = MAX_INLINE_RESULT_CHARS - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n… [{omitted:,} characters hidden] …\n\n{text[-tail:]}"


def _parse_args(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, dict):
        # LangChain sometimes nests the real arguments.
        for key in ("args", "arguments"):
            inner = value.get(key)
            if isinstance(inner, (dict, str)):
                return _parse_args(inner)
    return value


def _code_block(content: str, *, language: str) -> str:
    return (
        "<div class='tool-code-block'>"
        f"<div class='tool-code-label'>{escape(language.upper())}</div>"
        f"<pre>{escape(content)}</pre>"
        "</div>"
    )


STATUS_MARK = {"running": "", "ok": "✓", "error": "✕"}


def render_tool_entry(
    view: ToolView,
    *,
    call_body: str = "",
    result_body: str = "",
) -> str:
    """One collapsible line: icon, what was done, and how it went."""
    mark = STATUS_MARK.get(view.status, "")
    note = f"<span class='tool-entry__note'>{escape(view.note)}</span>" if view.note else ""
    mark_html = (
        f"<span class='tool-entry__mark tool-entry__mark--{view.status}'>{mark}</span>"
        if mark
        else "<span class='tool-entry__mark tool-entry__mark--running'></span>"
    )
    body = "".join(part for part in (call_body, result_body) if part)
    # Every entry starts collapsed, including failures. What went wrong is on
    # the summary line — the ✕ mark and the error text — so nothing is hidden by
    # the default; only the stack trace and the recovery hint are. Opening is
    # the reader's decision, and auto-expanding failures made a run that
    # recovered from several errors unfold into a wall of stack traces.
    return (
        f"<details class='tool-entry tool-entry--{view.status}'>"
        "<summary>"
        f"<span class='tool-entry__icon' aria-hidden='true'>{escape(view.icon)}</span>"
        f"<span class='tool-entry__label'>{escape(view.label)}</span>"
        f"{note}{mark_html}"
        "</summary>"
        f"<div class='tool-entry__body'>{body}</div>"
        "</details>"
    )


def call_metadata(tool_name: Optional[str], args: Any) -> Dict[str, Any]:
    """Everything the timeline needs to store for one call, snapshot-safe."""
    view = describe_call(tool_name, args)
    return {
        "icon": view.icon,
        "label": view.label,
        "status": "running",
        "note": "",
        "call_body": render_call_body(tool_name, args),
        "result_body": "",
    }
