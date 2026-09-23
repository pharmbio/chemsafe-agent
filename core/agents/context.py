from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.prebuilt.chat_agent_executor import AgentState

from app.config import (
    CONTEXT_ANCHOR_REPORT_MAX_CHARS,
    CONTEXT_ANCHOR_REQUEST_MAX_CHARS,
    CONTEXT_ARTIFACT_MAX_ITEMS,
    CONTEXT_GOAL_MAX_CHARS,
    CONTEXT_KEEP_TURNS,
    CRITIC_FINDING_MAX_CHARS,
    CRITIC_FINDINGS_MAX,
    MEMORY_MAX_ITEMS,
    MEMORY_OUTPUTS_MAX_ITEMS,
    SUMMARY_MAX_MESSAGES,
    SUMMARY_TRIGGER_CHAR_LIMIT,
    SUMMARY_SOURCE_MAX_CHARS,
    SUMMARY_SOURCE_MESSAGE_MAX_CHARS,
    SUMMARY_TRIGGER_MIN_MESSAGES,
    SUMMARY_TRIGGER_MIN_MESSAGES_FIRST,
    TOOL_RESULT_ELIDED_CHARS,
    TOOL_RESULT_MAX_CHARS,
    TOOL_RESULT_RECENT_FULL,
)
from backend.utils.output_paths import describe_output_artifacts, describe_output_scope


SUMMARY_AGENT_NAME = "context_summary"
SUMMARY_MEMORY_KEY = "summary_memory"
PLANNING_AGENT_NAME = "planning_agent"
SUMMARY_AGENT_PREFIX = "summary_agent"
# Stamped on the HumanMessage opening a user turn, so turn boundaries stay clear
# next to the HumanMessages plan review produces.
TURN_ROLE_KEY = "chemsafe_turn_role"
TURN_ROLE_REQUEST = "user_request"


class AgentGraphState(AgentState, total=False):
    """Shared state for the top-level graph and every react agent inside it.

    Subclasses the prebuilt AgentState (keeping `messages` and `remaining_steps`)
    and must be handed to every `create_react_agent` as `state_schema`. Without
    that, LangGraph filters the parent state down to AgentState before invoking
    the sub-agent and these keys never reach the pre_model_hook.
    """

    user_id: str
    conversation_id: str
    plan_status: str
    task_category: str
    # Pinned execution contract: survives compression so the executor sees exactly
    # one authoritative plan, not every draft.
    approved_plan: str
    approval_constraints: List[str]
    # The plan lives on disk; state carries only a pointer and a progress line.
    plan_path: str
    plan_run_id: int
    plan_progress: str
    # Open blocking findings the executor must still act on. full_run pins them like
    # approval_constraints; stepwise delivers them as the handoff message instead and
    # keeps this list only for the routers and the next case file. Cleared by
    # plan_init and on an accepted verdict.
    critic_findings: List[str]
    # Findings no pass will resolve: advisories, and blocking ones out of budget.
    # Never sent to the executor, but the report needs them in Open Issues, and in
    # stepwise the message that raised them is gone, so state is the only record.
    critic_open_findings: List[str]
    # Ids of handoff messages in the transcript; stepwise keeps at most one. Tracked
    # rather than searched because RemoveMessage raises on an absent id.
    critic_feedback_ids: List[str]
    # The gate's reasons for the pending review, as "code: detail" lines. Passed
    # forward, not written to the transcript: it is the critic's input alone.
    critic_triggers: List[str]
    # Start of the pending review's evidence slice: the watermark as it stood before
    # this gate pass moved it, since the critic node needs the same window.
    critic_evidence_after_id: str
    critic_rounds: int
    critic_summary: str
    # "stepwise" (each step checked as it resolves), "full_run" (one review of the
    # finished run), or "" when the critic is off. Set by plan_init, fixed per run.
    critic_mode: str
    # Set by the gate for one routing decision. A flag, not a transcript scan: the
    # previous round's message is still there with no user turn between.
    critic_pending: bool
    # Where the loop goes with no review pending: "execute" or "finalize". Written by
    # the gate and review node so both edges agree, and a resumed run routes by state.
    critic_next: str
    # Stepwise watermark: a terminal step not listed is new work to check. The review
    # node removes a step it reopens, so the redone version is checked again.
    critic_reviewed_steps: List[int]
    critic_scope_steps: List[int]
    critic_revised_steps: List[int]
    critic_unresolved_steps: List[int]
    # Send-backs keyed by step number as a string ("0" = the whole run). Per step so
    # one bad step cannot spend the budget later steps need.
    critic_step_attempts: Dict[str, int]
    critic_reviews: int
    # Consecutive passes that resolved nothing new. The loop's only liveness guard:
    # either unresolved steps fall or this rises, so it terminates.
    critic_stall: int
    # Last message the previous review saw, so the next step is judged on the traffic
    # that produced it, not the whole turn.
    critic_watermark_id: str
    # The verdict, between critic_agent and critic_review. Written by the full-run
    # critic's response_format, or by the node invoking the isolated stepwise critic.
    # Read once and cleared.
    structured_response: Any


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


def mark_user_request(message: BaseMessage) -> BaseMessage:
    """Tag a HumanMessage as the start of a user turn."""
    kwargs = dict(getattr(message, "additional_kwargs", None) or {})
    kwargs[TURN_ROLE_KEY] = TURN_ROLE_REQUEST
    message.additional_kwargs = kwargs
    return message


def _is_user_request(messages: Sequence[BaseMessage], index: int) -> bool:
    """True when messages[index] opens a new user turn.

    Explicit marker first; otherwise fall back to structure, so conversations
    checkpointed before the marker existed still split correctly. `human_chat`
    appends its HumanMessage directly after the plan it is responding to, which
    makes plan feedback distinguishable from a fresh request.
    """
    message = messages[index]
    if not isinstance(message, HumanMessage):
        return False
    kwargs = getattr(message, "additional_kwargs", None) or {}
    if kwargs.get(TURN_ROLE_KEY) == TURN_ROLE_REQUEST:
        return True
    if kwargs.get(TURN_ROLE_KEY):
        return False
    if index == 0:
        return True
    previous = messages[index - 1]
    return str(getattr(previous, "name", "") or "") != PLANNING_AGENT_NAME


def _turn_start_indices(messages: Sequence[BaseMessage]) -> List[int]:
    return [index for index in range(len(messages)) if _is_user_request(messages, index)]


def _turn_final_report(body: Sequence[BaseMessage]) -> str:
    """The polished answer a turn ended on, if it produced one."""
    for message in reversed(body):
        if not isinstance(message, AIMessage):
            continue
        if getattr(message, "tool_calls", None):
            continue
        name = str(getattr(message, "name", "") or "")
        if name.startswith(SUMMARY_AGENT_PREFIX):
            text = _coerce_text(getattr(message, "content", None))
            if text:
                return text
    return ""


def _shorten(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit < 400:
        return text[:limit] + f"\n… [{len(text) - limit:,} more characters omitted]"
    head = int(limit * 0.7)
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n… [{omitted:,} characters omitted] …\n{text[-tail:]}"


def build_turn_anchors(
    messages: Sequence[BaseMessage],
    *,
    upto_index: int,
    keep: int = CONTEXT_KEEP_TURNS,
    skip_first_turn: bool = False,
) -> List[BaseMessage]:
    """Verbatim (request, answer) pairs for completed turns in messages[:upto_index].

    This is what stops a follow-up from starting cold. Everything else in the
    compressed region is genuinely disposable — tool traffic, tracking blocks,
    superseded plan drafts — but the user's own words and the answer they were
    given are exactly what the next request refers to, and they are small.
    """
    if keep <= 0 or upto_index <= 0:
        return []
    region = list(messages[:upto_index])
    starts = _turn_start_indices(region)
    if not starts:
        return []

    selected = starts[-keep:]
    if skip_first_turn and selected and selected[0] == starts[0]:
        selected = selected[1:]

    anchors: List[BaseMessage] = []
    for position, start in enumerate(selected):
        following = [value for value in starts if value > start]
        end = following[0] if following else len(region)
        request_text = _coerce_text(getattr(region[start], "content", None))
        if request_text:
            anchors.append(
                HumanMessage(content=_shorten(request_text, CONTEXT_ANCHOR_REQUEST_MAX_CHARS))
            )
        report = _turn_final_report(region[start + 1 : end])
        if report:
            anchors.append(
                AIMessage(content=_shorten(report, CONTEXT_ANCHOR_REPORT_MAX_CHARS))
            )
        elif position == len(selected) - 1 and request_text:
            anchors.append(
                AIMessage(content="(That request did not reach a final answer.)")
            )
    return anchors


def prune_tool_traffic(messages: Sequence[BaseMessage]) -> List[BaseMessage]:
    """Bound tool-result size in the live run without breaking tool_call pairing.

    Contents are shortened in place rather than dropped: removing a ToolMessage
    would orphan its AIMessage tool_call and fail create_react_agent's history
    validation.
    """
    tool_positions = [
        index for index, message in enumerate(messages) if isinstance(message, ToolMessage)
    ]
    if not tool_positions:
        return list(messages)
    keep_full = set(tool_positions[-TOOL_RESULT_RECENT_FULL:]) if TOOL_RESULT_RECENT_FULL > 0 else set()

    pruned: List[BaseMessage] = []
    for index, message in enumerate(messages):
        if not isinstance(message, ToolMessage):
            pruned.append(message)
            continue
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            pruned.append(message)
            continue
        recent = index in keep_full
        limit = TOOL_RESULT_MAX_CHARS if recent else TOOL_RESULT_ELIDED_CHARS
        if len(content) <= limit:
            pruned.append(message)
            continue
        shortened = _shorten(content, limit)
        if not recent:
            shortened += (
                "\n[Older tool result, trimmed to keep the working context small. "
                "Re-run the call if you need its full output again.]"
            )
        pruned.append(message.model_copy(update={"content": shortened}))
    return pruned


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
    """Bound what the compressor reads: newest-first, by message count and chars.

    A single run can hold megabytes of tool output; handing all of it to the
    summarizer was slow and could exceed its own context.
    """
    source_messages = messages_since_last_summary(messages)
    if len(source_messages) > SUMMARY_MAX_MESSAGES:
        source_messages = source_messages[-SUMMARY_MAX_MESSAGES:]

    budget = SUMMARY_SOURCE_MAX_CHARS
    selected: List[BaseMessage] = []
    for message in reversed(source_messages):
        content = getattr(message, "content", None)
        if isinstance(content, str) and len(content) > SUMMARY_SOURCE_MESSAGE_MAX_CHARS:
            message = message.model_copy(
                update={"content": _shorten(content, SUMMARY_SOURCE_MESSAGE_MAX_CHARS)}
            )
        cost = _estimate_message_chars(message)
        if selected and cost > budget:
            break
        budget -= cost
        selected.append(message)
    selected.reverse()
    return selected


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


def latest_user_request_text(messages: Sequence[BaseMessage]) -> str:
    """Text of the newest user turn, ignoring plan-review replies."""
    starts = _turn_start_indices(messages)
    if not starts:
        return ""
    return _coerce_text(getattr(messages[starts[-1]], "content", None))


def describe_prior_context(messages: Sequence[BaseMessage], *, max_chars: int = 1500) -> str:
    """Goal plus the most recent completed exchange, for routing decisions.

    The classifier used to see only the newest message, which makes a request
    like "now redo it with the peak value" impossible to route correctly.
    """
    starts = _turn_start_indices(messages)
    if len(starts) < 2:
        return ""

    goal = _coerce_text(getattr(messages[starts[0]], "content", None))
    previous_start, current_start = starts[-2], starts[-1]
    previous_request = _coerce_text(getattr(messages[previous_start], "content", None))
    previous_answer = _turn_final_report(messages[previous_start + 1 : current_start])

    parts: List[str] = []
    if goal:
        parts.append("Conversation goal (first request):\n" + _shorten(goal, max_chars))
    if previous_request and previous_start != starts[0]:
        parts.append("Most recent previous request:\n" + _shorten(previous_request, max_chars))
    if previous_answer:
        parts.append("Answer already delivered for it:\n" + _shorten(previous_answer, max_chars))
    return "\n\n".join(parts)


def messages_in_current_turn(messages: Sequence[BaseMessage]) -> List[BaseMessage]:
    """Everything produced since the newest user request opened this turn.

    The critic gate scans this rather than the whole transcript: a tool failure
    or an ungrounded limit from three turns ago has already been reviewed, and
    re-triggering on it would make the critic fire forever.
    """
    starts = _turn_start_indices(messages)
    if not starts:
        return list(messages)
    return list(messages[starts[-1] :])


def messages_after_id(messages: Sequence[BaseMessage], marker_id: str) -> List[BaseMessage]:
    """Everything appended after the message with `marker_id`.

    The step-wise gate judges one step at a time, so it must see the traffic
    that produced *that* step. Scored against the whole turn, a recovered
    failure in step 1 would re-flag every later step for the rest of the run.

    Falls back to the whole turn when the marker is missing — a first review, or
    a thread checkpointed before the watermark existed. That is the safe
    direction: too much evidence makes the gate fire, too little makes it miss.
    """
    if marker_id:
        for index in range(len(messages) - 1, -1, -1):
            if str(getattr(messages[index], "id", "") or "") == marker_id:
                return list(messages[index + 1 :])
    return messages_in_current_turn(messages)


def last_message_id(messages: Sequence[BaseMessage]) -> str:
    """Id of the newest message, for use as a review watermark."""
    for message in reversed(messages):
        identifier = str(getattr(message, "id", "") or "")
        if identifier:
            return identifier
    return ""


def has_completed_turn(messages: Sequence[BaseMessage]) -> bool:
    """True when at least one earlier user turn exists to follow up on."""
    return len(_turn_start_indices(messages)) >= 2


def _conversation_goal(messages: Sequence[BaseMessage]) -> str:
    starts = _turn_start_indices(messages)
    if not starts:
        return ""
    return _coerce_text(getattr(messages[starts[0]], "content", None))


def _step_numbers(value) -> List[int]:
    """Sorted, de-duplicated step numbers from a state list, ignoring junk."""
    numbers: List[int] = []
    for item in value or []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in numbers:
            numbers.append(number)
    return sorted(numbers)


def build_pinned_context_block(state, messages: Sequence[BaseMessage], *, include_goal: bool) -> str:
    """The part of context that must never be summarized away."""
    user_id = state.get("user_id")
    conversation_id = state.get("conversation_id")

    sections: List[str] = [
        describe_output_scope(user_id=user_id, conversation_id=conversation_id)
    ]

    if include_goal:
        goal = _conversation_goal(messages)
        if goal:
            sections.append(
                "Original request for this conversation (the standing goal):\n"
                + _shorten(goal, CONTEXT_GOAL_MAX_CHARS)
            )

    # The plan lives in plan.md; only a pointer and progress line are pinned.
    # plan_status returns the authoritative copy.
    plan_path = _coerce_text(state.get("plan_path"))
    if plan_path:
        plan_lines = [f"Execution plan for this conversation: {plan_path}"]
        progress = _coerce_text(state.get("plan_progress"))
        if progress:
            plan_lines.append(progress)
        plan_lines.append(
            "This file is the authoritative record of what has been done. Call "
            "`plan_status` to read it and `plan_update` to record a step's "
            "outcome; do not restate progress from memory."
        )
        sections.append("\n".join(plan_lines))

    constraints = state.get("approval_constraints") or []
    constraint_lines = [f"- {item}" for item in constraints if str(item).strip()]
    if constraint_lines:
        sections.append(
            "Conditions the human attached when approving. They override the "
            "corresponding plan steps:\n" + "\n".join(constraint_lines)
        )

    # Stepwise review changes the executor's pacing, so the fact that it is active
    # must survive compression. Only the fact and accounting live here; the implied
    # behaviour is in the execute prompt.
    stepwise = _coerce_text(state.get("critic_mode")) == "stepwise"
    if stepwise:
        review_lines = [
            "Review mode: step-wise. Each plan step is checked by a reviewer as "
            "soon as you record it, before the next step starts. The verdict "
            "comes back as a single Review message in the conversation — that "
            "message is the handoff, and it says what to do next."
        ]
        accepted = _step_numbers(state.get("critic_reviewed_steps"))
        if accepted:
            review_lines.append(
                "Steps already reviewed: "
                + ", ".join(str(number) for number in accepted)
            )
        sent_back = _step_numbers(state.get("critic_revised_steps"))
        if sent_back:
            review_lines.append(
                "Steps sent back at least once: "
                + ", ".join(str(number) for number in sent_back)
            )
        settled = _step_numbers(state.get("critic_unresolved_steps"))
        if settled:
            review_lines.append(
                "Steps the review settled unresolved (out of revision budget — "
                "finished with, do not retry them, and do not stop the run over "
                "them): " + ", ".join(str(number) for number in settled)
            )
        sections.append("\n".join(review_lines))

    # Open findings outrank the plan: the step they name is not done, whatever the
    # executor concluded. Stepwise uses the handoff message instead; pinning as well
    # would show the same instruction twice from sources that go stale separately.
    if not stepwise:
        finding_lines = [
            f"- {_shorten(str(item).strip(), CRITIC_FINDING_MAX_CHARS)}"
            for item in (state.get("critic_findings") or [])[:CRITIC_FINDINGS_MAX]
            if str(item).strip()
        ]
        if finding_lines:
            sections.append(
                "Review findings on work already done in this run. Each one names "
                "something that must be corrected or grounded before the run can "
                "finish; address them before starting anything new:\n"
                + "\n".join(finding_lines)
            )

    # Findings nothing will fix. Pinned all run: the report needs them in Open
    # Issues, and in stepwise the message that raised them is already gone.
    open_lines = [
        f"- {_shorten(str(item).strip(), CRITIC_FINDING_MAX_CHARS)}"
        for item in (state.get("critic_open_findings") or [])[:CRITIC_FINDINGS_MAX]
        if str(item).strip()
    ]
    if open_lines:
        sections.append(
            "Findings this run's reviews left open. They are not work items — "
            "no further execution pass is going to resolve them — but they are "
            "part of what the run produced and belong in the report's open "
            "issues, stated as what is wrong or unverified:\n"
            + "\n".join(open_lines)
        )

    artifacts = describe_output_artifacts(
        user_id=user_id,
        conversation_id=conversation_id,
        max_items=CONTEXT_ARTIFACT_MAX_ITEMS,
    )
    if artifacts:
        sections.append(
            "Files already produced in this conversation. Reuse them instead of "
            "regenerating equivalent work, and read them when you need their "
            "contents:\n" + artifacts
        )

    return "\n\n".join(sections)


def build_llm_input_messages(state) -> List[BaseMessage]:
    messages = state.get("messages") or []
    if not messages:
        return []

    idx, summary_text, memory = latest_summary_record(messages)
    recent = messages_since_last_summary(messages) if idx >= 0 else list(messages)

    # Completed exchanges that compression would otherwise have erased.
    anchors = build_turn_anchors(messages, upto_index=idx + 1 if idx >= 0 else 0)
    anchor_covers_first_turn = False
    if anchors and idx >= 0:
        starts = _turn_start_indices(list(messages[: idx + 1]))
        anchor_covers_first_turn = bool(starts) and len(starts) <= CONTEXT_KEEP_TURNS

    prefix: List[BaseMessage] = [
        SystemMessage(
            content=build_pinned_context_block(
                state,
                messages,
                include_goal=not anchor_covers_first_turn,
            )
        )
    ]

    if summary_text:
        prefix.append(
            SystemMessage(
                content=(
                    "Compressed summary of earlier work in this conversation. The "
                    "verbatim exchanges that follow are more reliable than this "
                    "summary where they overlap:\n" + summary_text
                )
            )
        )
    memory_text = _format_memory_for_prompt(memory)
    if memory_text:
        prefix.append(SystemMessage(content="Structured memory:\n" + memory_text))

    return prefix + anchors + prune_tool_traffic(recent)


def build_uncompressed_input_messages(state) -> List[BaseMessage]:
    messages = state.get("messages") or []
    if not messages:
        return []

    visible_messages = [message for message in messages if not is_summary_message(message)]

    return [
        SystemMessage(content=build_pinned_context_block(state, messages, include_goal=True)),
        *prune_tool_traffic(visible_messages),
    ]


def build_pre_model_state(state) -> Dict[str, Any]:
    return {"llm_input_messages": build_llm_input_messages(state)}


def build_uncompressed_pre_model_state(state) -> Dict[str, Any]:
    return {"llm_input_messages": build_uncompressed_input_messages(state)}


def render_transcript(messages: Sequence[BaseMessage]) -> str:
    """Flatten messages into a labelled transcript for the compressor.

    Passing raw message objects risked sending an orphaned ToolMessage once the
    window was clipped, which the OpenAI API rejects. The compressor only needs
    to read the exchange, not participate in it.
    """
    lines: List[str] = []
    for message in messages:
        name = str(getattr(message, "name", "") or "")
        if isinstance(message, HumanMessage):
            label = "USER"
        elif isinstance(message, ToolMessage):
            label = f"TOOL RESULT ({getattr(message, 'name', '') or 'tool'})"
        elif isinstance(message, AIMessage):
            label = f"ASSISTANT ({name})" if name else "ASSISTANT"
        elif isinstance(message, SystemMessage):
            label = "SYSTEM"
        else:
            label = type(message).__name__.upper()

        text = _coerce_text(getattr(message, "content", None))
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            requested = ", ".join(str(call.get("name", "?")) for call in tool_calls)
            text = (text + "\n" if text else "") + f"[called tools: {requested}]"
        if not text:
            continue
        lines.append(f"### {label}\n{text}")
    return "\n\n".join(lines)


def make_summary_message(summary_text: str, memory: Dict[str, Any]) -> AIMessage:
    return AIMessage(
        content=summary_text,
        name=SUMMARY_AGENT_NAME,
        response_metadata={"is_summary": True, SUMMARY_MEMORY_KEY: memory},
    )
