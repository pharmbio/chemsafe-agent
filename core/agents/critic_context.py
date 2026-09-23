from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.config import (
    CONTEXT_ARTIFACT_MAX_ITEMS,
    CONTEXT_GOAL_MAX_CHARS,
    CRITIC_EVIDENCE_MAX_CHARS,
    CRITIC_EVIDENCE_MESSAGE_MAX_CHARS,
    CRITIC_FINDING_MAX_CHARS,
    TOOL_RESULT_MAX_CHARS,
    TOOL_RESULT_RECENT_FULL,
)
from backend.utils import plan_store
from backend.utils.output_paths import describe_output_artifacts, describe_output_scope
from core.agents.context import render_transcript
from core.prompts.prompts import CRITIC_STEPWISE_SYSTEM_PROMPT


def _clip(text: str, limit: int) -> str:
    """Shorten from the middle, keeping the head and the tail.

    A clipped tool result usually has its shape at the top and its answer at the
    bottom; a plain truncation keeps the least useful half.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    if limit < 400:
        return text[:limit] + f"\n… [{len(text) - limit:,} more characters omitted]"
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head]}\n… [{len(text) - head - tail:,} characters omitted] …\n{text[-tail:]}"


def _numbers(values: Iterable[int]) -> str:
    listed = [str(value) for value in values]
    return ", ".join(listed) if listed else "none"


# The evidence slice


def clip_evidence(messages: Sequence[BaseMessage]) -> str:
    """The executor's traffic for this step, as a flat transcript.

    Newest-first under a character budget, then re-ordered, so a step whose last
    call returned a 2 MB dataframe dump still arrives with its earlier reasoning
    intact. Rendered to text rather than passed as messages: the critic is not
    continuing this exchange, it is reading it, and a live `ToolMessage` whose
    `tool_call` was budgeted away would be rejected by the API.
    """
    budget = CRITIC_EVIDENCE_MAX_CHARS
    selected: List[BaseMessage] = []
    for message in reversed(list(messages)):
        content = getattr(message, "content", None)
        if isinstance(content, str) and len(content) > CRITIC_EVIDENCE_MESSAGE_MAX_CHARS:
            message = message.model_copy(
                update={"content": _clip(content, CRITIC_EVIDENCE_MESSAGE_MAX_CHARS)}
            )
        cost = len(str(getattr(message, "content", "") or ""))
        if selected and cost > budget:
            break
        budget -= cost
        selected.append(message)
    selected.reverse()
    return render_transcript(selected)


# The case file


def _plan_section(run: Optional[plan_store.PlanRun], scope: Sequence[int]) -> str:
    if run is None or not run.steps:
        return "The plan file could not be read. Use `plan_status` before judging anything."

    lines: List[str] = []
    for step in run.steps:
        lines.extend(plan_store.render_step(step))

    in_scope = set(scope)
    settled = [s.number for s in run.steps if s.is_terminal and s.number not in in_scope]
    not_started = [s.number for s in run.steps if s.status == plan_store.PENDING]

    lines.append("")
    lines.append(f"Settled: {_numbers(settled)}")
    lines.append(f"Under review now: {_numbers(sorted(in_scope))}")
    lines.append(f"Not started yet: {_numbers(not_started)}")
    if not_started:
        lines.append(
            "The steps that have not started are *next*, not missing. Nothing they "
            "are due to produce is absent yet."
        )
    return "\n".join(lines)


def _step_section(
    run: Optional[plan_store.PlanRun], number: int, *, attempts: int
) -> str:
    step = run.step(number) if run is not None else None
    if step is None:
        return f"Step {number} could not be read from the plan file."

    lines = [f"Step {step.number} — {step.title}"]
    if step.details:
        lines.append(f"What the plan asked for: {step.details}")
    if step.depends_on:
        lines.append(f"Depends on: {step.depends_on}")
    lines.append(f"Status the executor recorded: {step.status}")
    if step.updated_at:
        lines.append(f"Recorded at: {step.updated_at}")
    if step.note:
        lines.append(f"The executor's note: {step.note}")
    if attempts >= 1:
        lines.append(
            f"This step has been sent back {attempts} time(s) already. This is its "
            "last review before the system records it unresolved, so a blocking "
            "finding here ends the step rather than retrying it."
        )
    else:
        lines.append("This is the step's first review.")
    return "\n".join(lines)


def build_stepwise_case_file(
    *,
    run: Optional[plan_store.PlanRun],
    scope_steps: Sequence[int],
    triggers: Sequence[str],
    evidence: Sequence[BaseMessage],
    prior_findings: Sequence[str],
    attempts: int,
    goal: str,
    constraints: Sequence[str],
    user_id: Any,
    conversation_id: Any,
) -> str:
    """Everything the critic is given, in the order it should read it."""
    scope = sorted({int(number) for number in scope_steps})
    primary = scope[0] if scope else 0
    noun = f"step {primary}" if len(scope) <= 1 else f"steps {_numbers(scope)}"

    sections: List[str] = [
        f"# Review request · run {getattr(run, 'run_id', '?')} · {noun}",
        (
            "## 1. Assignment\n"
            f"The executor has just resolved {noun} of an approved plan and handed "
            f"back. Review {noun} and nothing else, then return your verdict with "
            "`step` set to the step each finding concerns."
        ),
    ]

    purpose = ["## 2. What this run is for"]
    if goal.strip():
        purpose.append(f"Goal: {_clip(goal.strip(), CONTEXT_GOAL_MAX_CHARS)}")
    conditions = [str(item).strip() for item in constraints if str(item).strip()]
    if conditions:
        purpose.append(
            "Conditions the human attached when approving the plan. They override "
            "the corresponding plan steps, and a step that ignored one is blocking:"
        )
        purpose.extend(f"- {item}" for item in conditions)
    sections.append("\n".join(purpose))

    sections.append("## 3. The plan as it stands\n" + _plan_section(run, scope))
    sections.append(
        "## 4. The step under review\n" + _step_section(run, primary, attempts=attempts)
    )

    open_findings = [str(item).strip() for item in prior_findings if str(item).strip()]
    if open_findings:
        sections.append(
            "## 5. What this step was sent back for last time\n"
            "Check each one first: whether it was actually resolved is the main "
            "question in front of you.\n"
            + "\n".join(f"- {_clip(item, CRITIC_FINDING_MAX_CHARS)}" for item in open_findings)
        )
    else:
        sections.append(
            "## 5. What this step was sent back for last time\n"
            "Nothing — this step has not been reviewed before."
        )

    reasons = [f"- {str(item).strip()}" for item in triggers if str(item).strip()]
    sections.append(
        "## 6. Why the gate flagged this\n"
        + "\n".join(
            reasons
            or ["- no specific trigger; every step of an approved plan is checked"]
        )
        + "\n\nThese are where the evidence says something may be wrong. They are a "
        "starting point, not a checklist and not a conclusion — a trigger that "
        "turns out to be benign is reported as benign."
    )

    transcript = clip_evidence(evidence)
    sections.append(
        "## 7. What the executor did for this step\n"
        "Its own account, clipped. Treat it as claims to test, not as findings.\n\n"
        + (transcript or "(no tool traffic was recorded for this step)")
    )

    where = ["## 8. Where the work lives"]
    where.append(describe_output_scope(user_id=user_id, conversation_id=conversation_id))
    artifacts = describe_output_artifacts(
        user_id=user_id,
        conversation_id=conversation_id,
        max_items=CONTEXT_ARTIFACT_MAX_ITEMS,
    )
    if artifacts:
        where.append("Files produced in this conversation so far:\n" + artifacts)
    where.append(
        "Your Python session is the executor's own: its variables are still bound, "
        "in the state this step left them."
    )
    sections.append("\n".join(where))

    return "\n\n".join(sections)


def build_stepwise_messages(case_file: str) -> List[BaseMessage]:
    """The isolated critic's entire input.

    The system prompt travels as a real `SystemMessage` rather than through
    `create_react_agent(prompt=...)` on purpose. LangGraph's structured-response
    node — the call that produces the verdict the system acts on — invokes the
    bare model on `state["messages"]`, stripping both the `prompt` runnable and
    the `pre_model_hook`. A prompt passed as a message is in `messages`, so the
    rules that govern the verdict are present on the call that produces it.
    """
    return [
        SystemMessage(content=CRITIC_STEPWISE_SYSTEM_PROMPT),
        HumanMessage(content=case_file),
    ]


# The critic's own working context


def critic_pre_model_state(state) -> dict:
    """Bound the critic's *own* tool traffic across its react loop.

    Its input is already sized, so this only stops a review that reads several
    large artifacts from growing without limit. Nothing is pinned and nothing is
    summarized: a review is short by construction, and the case file that opens
    it is never touched.
    """
    messages = list(state.get("messages") or [])
    tool_positions = [
        index for index, message in enumerate(messages) if isinstance(message, ToolMessage)
    ]
    if not tool_positions:
        return {"llm_input_messages": messages}
    keep_full = (
        set(tool_positions[-TOOL_RESULT_RECENT_FULL:]) if TOOL_RESULT_RECENT_FULL > 0 else set()
    )

    pruned: List[BaseMessage] = []
    for index, message in enumerate(messages):
        content = getattr(message, "content", None)
        if not isinstance(message, ToolMessage) or not isinstance(content, str):
            pruned.append(message)
            continue
        limit = TOOL_RESULT_MAX_CHARS if index in keep_full else CRITIC_EVIDENCE_MESSAGE_MAX_CHARS
        pruned.append(
            message if len(content) <= limit
            else message.model_copy(update={"content": _clip(content, limit)})
        )
    return {"llm_input_messages": pruned}
