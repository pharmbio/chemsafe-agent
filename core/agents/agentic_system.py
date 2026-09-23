from __future__ import annotations

import json
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.config import (
    APPROVAL_JUDGE_MODEL,
    CONTEXT_SUMMARY_MODEL,
    CRITIC_FAILURE_TRIGGER,
    CRITIC_FINDING_MAX_CHARS,
    CRITIC_FINDINGS_MAX,
    CRITIC_MAX_REVIEWS,
    CRITIC_MAX_ROUNDS,
    CRITIC_MIN_STEPS,
    CRITIC_MODEL,
    CRITIC_RECURSION_LIMIT,
    CRITIC_STALL_LIMIT,
    CRITIC_STEP_MAX_ROUNDS,
    CRITIC_STEPWISE_ALWAYS,
    CRITIC_STEPWISE_ENABLED,
    CRITIC_STEPWISE_ISOLATED,
    EXECUTE_MODEL,
    OPENAI_API_KEY,
    PLANNING_MODEL,
    SUMMARY_MODEL,
    TASK_CLASSIFIER_MODEL,
    logger,
)
from core.agents.context import (
    AgentGraphState,
    build_pre_model_state,
    build_uncompressed_pre_model_state,
    clipped_messages_for_summary,
    describe_prior_context,
    has_completed_turn,
    last_message_id,
    latest_summary_record,
    latest_user_request_text,
    make_summary_message,
    messages_after_id,
    normalize_memory,
    render_transcript,
    should_summarize,
)
from backend.utils import critic_gate, plan_store
from core.agents import critic_context
from core.agents.critic_agent import build_critic_agent, build_stepwise_critic_agent
from core.agents.execute_agent import build_execute_agent
from core.agents.planning_agent import build_planning_agent
from core.agents.summary_agent import build_summary_agent
from core.tools.plan_tools import render_ledger
from core.prompts.prompts import (
    EXECUTE_AGENT_FOLLOWUP_SYSTEM_PROMPT,
    EXECUTE_AGENT_FREE_SYSTEM_PROMPT,
    EXECUTE_AGENT_SYSTEM_PROMPT,
    SUMMARY_AGENT_META_SYSTEM_PROMPT,
    SUMMARY_AGENT_SIMPLE_SYSTEM_PROMPT,
    SUMMARY_AGENT_SYSTEM_PROMPT,
    TASK_CLASSIFIER_SYSTEM_PROMPT,
)


TaskCategory = Literal["simple", "complex", "meta_query", "follow_up"]

# Review mode: derived from the task category, stamped on the run by plan_init
# so it cannot drift mid-loop.
STEPWISE = "stepwise"
FULL_RUN = "full_run"


class TaskClassification(BaseModel):
    """Structured output for the task classifier."""

    category: TaskCategory = Field(
        description="The classification of the user's latest request."
    )


class PlanFeedbackVerdict(BaseModel):
    """Structured output for the approval judge."""

    decision: Literal["approve", "revise"] = Field(
        description=(
            "approve = the human authorizes execution now (even if they attach "
            "conditions). revise = they want the plan changed before it runs, or "
            "they are uncertain."
        )
    )
    constraints: list[str] = Field(
        default_factory=list,
        description=(
            "Conditions, corrections or preferences the human attached to an "
            "approval, each as one imperative instruction for the executor. "
            "Empty when the approval was unconditional."
        ),
    )


# AgentGraphState lives in core.agents.context: every react agent needs it as
# state_schema.
SUMMARY_PROMPT = (
    "You maintain the carry-forward record for a chemical safety workflow so "
    "that later turns can continue the work without re-reading the transcript.\n\n"
    "You are given the existing summary, the existing structured memory, and the "
    "messages added since. Fold the new messages into an updated record.\n\n"
    "Write for a colleague who will pick this up cold and be asked to refine it. "
    "Retain what a follow-up request would need:\n"
    "- Every substantive result, with its numeric value, unit and identifier "
    "(concentrations, limits, thresholds, CAS numbers, endpoints, scores).\n"
    "- The source each result came from — SOP name and section, database, "
    "literature reference, or the file the computation read.\n"
    "- Decisions taken and the reason, including anything explicitly ruled out.\n"
    "- Artifacts produced, by full path, and what each one contains.\n"
    "- What is unverified, blocked, assumed or still open.\n\n"
    "Do not write a chronological narrative of which agent ran when. Steps matter "
    "only where they explain or qualify a result. Never round, generalize or drop "
    "a number to save space, and never introduce a fact that is not in the "
    "messages. Prefer omitting process detail over omitting evidence."
)


class ContextOutputRecord(BaseModel):
    path: str = Field(default="", description="Full path of a produced artifact.")
    description: str = Field(default="", description="What the artifact contains.")


class ContextMemory(BaseModel):
    facts: list[str] = Field(
        default_factory=list,
        description="Established findings with their values, units and sources.",
    )
    outputs: list[ContextOutputRecord] = Field(default_factory=list)
    decisions: list[str] = Field(
        default_factory=list, description="Choices made, each with its reason."
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Unverified, blocked, assumed or unresolved items.",
    )


class ContextDigest(BaseModel):
    """Structured output for context compression."""

    summary: str = Field(description="Evidence-first record of the work so far.")
    memory: ContextMemory = Field(default_factory=ContextMemory)


class CriticFinding(BaseModel):
    """One defect the critic established by evidence."""

    step: int = Field(
        default=0,
        description=(
            "Plan step number the defect belongs to, or 0 when it concerns the "
            "run as a whole."
        ),
    )
    severity: Literal["blocking", "advisory"] = Field(
        default="advisory",
        description=(
            "blocking = the deliverable is wrong or unsafe as it stands and the "
            "step must be redone. advisory = worth reporting, not worth another "
            "execution pass."
        ),
    )
    claim: str = Field(default="", description="What the run asserted or produced.")
    evidence: str = Field(
        default="",
        description="How the defect was established: file read, value printed, query re-run.",
    )
    required_action: str = Field(
        default="",
        description="The one concrete thing the executor must do to resolve it.",
    )


class CriticVerdict(BaseModel):
    """Structured output for the critic."""

    decision: Literal["accept", "revise"] = Field(
        default="accept",
        description=(
            "revise = at least one blocking finding, send the work back. "
            "accept = nothing blocking was demonstrated."
        ),
    )
    findings: list[CriticFinding] = Field(default_factory=list)
    verified: str = Field(
        default="",
        description="One line naming what was checked and found sound.",
    )

_approval_judge_llm = None
_context_summary_llm = None
_task_classifier_llm = None


def _get_approval_judge_llm():
    global _approval_judge_llm
    if _approval_judge_llm is None:
        base = init_chat_model(
            APPROVAL_JUDGE_MODEL,
            model_provider="openai",
            api_key=OPENAI_API_KEY,
        )
        _approval_judge_llm = base.with_structured_output(PlanFeedbackVerdict)
    return _approval_judge_llm


def _get_context_summary_llm():
    global _context_summary_llm
    if _context_summary_llm is None:
        base = init_chat_model(
            CONTEXT_SUMMARY_MODEL,
            model_provider="openai",
            api_key=OPENAI_API_KEY,
        )
        _context_summary_llm = base.with_structured_output(ContextDigest)
    return _context_summary_llm


def _get_task_classifier_llm():
    global _task_classifier_llm
    if _task_classifier_llm is None:
        base = init_chat_model(
            TASK_CLASSIFIER_MODEL,
            model_provider="openai",
            api_key=OPENAI_API_KEY,
        )
        _task_classifier_llm = base.with_structured_output(TaskClassification)
    return _task_classifier_llm


def _judge_plan_feedback(
    feedback: str,
    plan: str = "",
) -> tuple[Literal["approved", "revise"], list[str]]:
    """Map free-text plan feedback to a decision plus any attached conditions.

    The judge sees the plan it is judging, not just the bare reply, so that
    "approved, but use the STEL not the TWA" is recognised as an approval that
    carries a constraint rather than as a bare yes.
    """
    if not feedback:
        return "revise", []
    llm = _get_approval_judge_llm()
    prompt = (
        "You evaluate a human's feedback on a proposed execution plan.\n\n"
        "Decide:\n"
        "- approve -> the human authorizes execution now. This still counts as an "
        "approval when they attach conditions, corrections or preferences "
        "(\"go ahead, but ...\", \"yes, just use X instead of Y\").\n"
        "- revise -> the human wants the plan itself reworked first, asks a "
        "question, or is uncertain.\n\n"
        "If the decision is approve, list every condition they attached as a "
        "separate imperative instruction for the executor. Preserve their exact "
        "numbers, units and identifiers. Return an empty list for an "
        "unconditional approval. Never invent a constraint they did not state.\n\n"
        f"--- PLAN UNDER REVIEW ---\n{plan or '(plan text unavailable)'}\n\n"
        f"--- HUMAN FEEDBACK ---\n{feedback}\n"
    )
    try:
        verdict: PlanFeedbackVerdict = llm.invoke(prompt)
    except Exception as exc:
        logger.warning("Approval judge failed; defaulting to revise: %s", exc)
        return "revise", []
    if verdict.decision == "approve":
        constraints = [item.strip() for item in (verdict.constraints or []) if str(item).strip()]
        return "approved", constraints
    return "revise", []


def _latest_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = getattr(message, "content", "")
            if isinstance(content, str):
                return content
            return str(content)
    return ""


async def task_classifier_node(state: AgentGraphState) -> dict[str, Any]:
    """Route the latest user request: simple / complex / meta_query / follow_up."""
    messages = state.get("messages") or []
    user_text = latest_user_request_text(messages) or _latest_user_text(messages)
    if not user_text:
        return {"task_category": "complex"}

    # A follow-up ("now redo it with the peak value") is unroutable from the bare
    # message, so the classifier gets the goal and the last exchange.
    prior_context = describe_prior_context(messages)
    can_follow_up = has_completed_turn(messages)
    if prior_context:
        classifier_input = (
            f"{prior_context}\n\n--- NEW USER MESSAGE ---\n{user_text}"
        )
    else:
        classifier_input = (
            "This is the first request in the conversation; there is no prior "
            f"exchange.\n\n--- NEW USER MESSAGE ---\n{user_text}"
        )

    llm = _get_task_classifier_llm()
    try:
        result: TaskClassification = await llm.ainvoke(
            [
                SystemMessage(content=TASK_CLASSIFIER_SYSTEM_PROMPT),
                HumanMessage(content=classifier_input),
            ]
        )
        category: TaskCategory = result.category
    except Exception as exc:
        logger.warning("Task classifier failed; defaulting to complex: %s", exc)
        category = "complex"

    if category == "follow_up" and not can_follow_up:
        category = "complex"

    updates: dict[str, Any] = {"task_category": category}
    if category in ("complex", "simple"):
        # An approval binds only its own plan. Follow-ups continue under it; meta
        # queries are left alone so an aside does not discard a still-needed plan.
        updates["approved_plan"] = ""
        updates["approval_constraints"] = []
    return updates


async def _compress_context(state: AgentGraphState) -> dict[str, Any]:
    messages = state.get("messages") or []
    if not messages or not should_summarize(messages):
        return {}

    source_messages = clipped_messages_for_summary(messages)
    _, prev_summary, prev_memory = latest_summary_record(messages)
    llm = _get_context_summary_llm()
    memory_json = json.dumps(prev_memory or {}, ensure_ascii=True)
    transcript = render_transcript(source_messages)
    if not transcript.strip():
        return {}

    context_message = HumanMessage(
        content=(
            "Existing summary:\n"
            f"{prev_summary or '(none)'}\n\n"
            "Existing structured memory JSON:\n"
            f"{memory_json}\n\n"
            "New messages since that summary:\n"
            f"{transcript}\n"
        )
    )

    try:
        digest: ContextDigest = await llm.ainvoke(
            [SystemMessage(content=SUMMARY_PROMPT), context_message]
        )
    except Exception as exc:
        logger.warning("Context compression failed: %s", exc)
        return {}

    summary_text = (digest.summary or "").strip() or (prev_summary or "")
    if not summary_text:
        return {}

    memory = normalize_memory(digest.memory.model_dump(), prev_memory)
    return {"messages": [make_summary_message(summary_text, memory)]}


def _route_after_classifier(
    state: AgentGraphState,
) -> Literal[
    "planning_agent",
    "execute_agent_free",
    "execute_agent_followup",
    "summary_agent_meta",
]:
    category = state.get("task_category", "complex")
    if category == "simple":
        return "execute_agent_free"
    if category == "meta_query":
        return "summary_agent_meta"
    if category == "follow_up":
        return "execute_agent_followup"
    return "planning_agent"


def _route_after_human(state: AgentGraphState) -> Literal["planning_agent", "approval_ack"]:
    return "approval_ack" if state.get("plan_status") == "approved" else "planning_agent"


def _latest_plan(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if getattr(message, "name", None) == "planning_agent":
            return getattr(message, "content", "") or ""
    return ""


def human_chat_node(state: AgentGraphState) -> dict[str, Any]:
    plan = _latest_plan(state.get("messages") or [])
    human_input = interrupt(
        {
            "type": "plan_review",
            "plan": plan,
            "message": (
                "Review the plan. Ask for changes to refine it, or approve it to continue into execution."
            ),
        }
    )

    feedback = (human_input or "").strip()
    decision, constraints = _judge_plan_feedback(feedback, plan)

    # Keep the human's words: a qualifier ("approved, but use the STEL") must not
    # be flattened into a bare status flag before it reaches the executor.
    messages: list[BaseMessage] = []
    if feedback:
        messages.append(HumanMessage(content=feedback))

    if decision == "approved":
        return {
            "messages": messages,
            "plan_status": "approved",
            "approved_plan": plan,
            "approval_constraints": constraints,
        }

    if not messages:
        messages.append(HumanMessage(content="Please revise the plan."))
    return {"messages": messages, "plan_status": "revise"}


def _plan_scope(state: AgentGraphState) -> dict[str, Any]:
    return {
        "user_id": state.get("user_id"),
        "conversation_id": state.get("conversation_id"),
    }


def _mode_for_category(category: Any) -> str:
    """The review mode a route gets.

    Complex plans are reviewed step by step: the steps are numbered because the
    later ones build on the earlier ones, so a defect in step 2 is worth
    catching before steps 3-8 assume it. `simple` and `follow_up` carry a
    one-step plan seeded from the request, so a step-wise pass over them would
    be the same single review with a loop around it.
    """
    if str(category or "complex") == "complex" and CRITIC_STEPWISE_ENABLED:
        return STEPWISE
    return FULL_RUN


def _fresh_review_state(mode: str) -> dict[str, Any]:
    """Review state belongs to a run, not to a conversation.

    Left standing, a finding from the previous run would be pinned into this
    one's context as an instruction the executor cannot act on, and the round
    budget would already be spent before the critic had seen anything.
    """
    # critic_feedback_ids is deliberately absent: dropping the ids would strand a
    # spent handoff in the transcript. plan_finalize clears them per run.
    return {
        "critic_mode": mode,
        "critic_findings": [],
        "critic_open_findings": [],
        "critic_triggers": [],
        "critic_rounds": 0,
        "critic_reviews": 0,
        "critic_summary": "",
        "critic_pending": False,
        "critic_next": "",
        "critic_reviewed_steps": [],
        "critic_scope_steps": [],
        "critic_revised_steps": [],
        "critic_unresolved_steps": [],
        "critic_step_attempts": {},
        "critic_stall": 0,
        "critic_watermark_id": "",
        "critic_evidence_after_id": "",
        "structured_response": None,
    }


def make_plan_init_node(*, use_critic: bool):
    """`plan_init` bound to the review mode it should stamp on the run.

    A closure rather than a state lookup because the mode depends on how the
    graph was compiled: with the critic off there is nothing to hand a step back
    to, and an executor told to pause after each step would end the run one step
    in. A plain nested function, not a `partial`, so LangGraph still sees an
    ordinary `(state) -> dict` node.
    """

    def plan_init(state: AgentGraphState) -> dict[str, Any]:
        return plan_init_node(state, use_critic=use_critic)

    return plan_init


def plan_init_node(state: AgentGraphState, *, use_critic: bool = True) -> dict[str, Any]:
    """Write this run's section of `plan.md`, then display it.

    Deterministic: the steps are parsed from the approved plan (or seeded from
    the request on the routes that have no plan), the file is written by code,
    and the rendered ledger below is generated from the file rather than by a
    model. This is the load → read → display half of the execution routine.
    """
    category = state.get("task_category", "complex")
    messages = state.get("messages") or []
    request = latest_user_request_text(messages) or _latest_user_text(messages)

    if category == "complex":
        plan_text = _coerce_plan_text(state.get("approved_plan")) or _latest_plan(messages)
        steps = plan_store.parse_plan_steps(plan_text)
        goal = plan_store.parse_plan_goal(plan_text) or request
    else:
        # Planless routes still get a section, so the document stays a complete
        # record for follow-ups to read.
        steps = [plan_store.PlanStep(number=1, title=_plan_title(request))]
        goal = request

    try:
        run = plan_store.start_run(
            goal=goal,
            steps=steps,
            kind=str(category),
            constraints=list(state.get("approval_constraints") or []),
            **_plan_scope(state),
        )
    except OSError as exc:
        # Bookkeeping must not stop the work. Step-wise review needs the step
        # statuses from the file that just failed to write, so fall back to full_run.
        logger.warning("Could not write the plan file: %s", exc)
        return _fresh_review_state(FULL_RUN if use_critic else "")

    path = str(plan_store.plan_file_path(**_plan_scope(state)))
    return {
        "messages": [
            AIMessage(content=render_ledger(run, path=path), name="plan_init")
        ],
        "plan_path": path,
        "plan_run_id": run.run_id,
        "plan_progress": plan_store.progress_line(run),
        **_fresh_review_state(_mode_for_category(category) if use_critic else ""),
    }


def plan_finalize_node(state: AgentGraphState) -> dict[str, Any]:
    """Reconcile the plan file after execution and record the run outcome.

    Reads back what actually happened rather than asking the model to report it.
    """
    scope = _plan_scope(state)
    # The standing handoff must go even when the accounting cannot be written:
    # it instructs on a run that is over.
    spent = {"messages": _feedback_removals(state), "critic_feedback_ids": []}
    try:
        document = plan_store.load_document(**scope)
    except OSError as exc:
        logger.warning("Could not read the plan file: %s", exc)
        return spent

    run = document.run(state.get("plan_run_id")) if state.get("plan_run_id") else document.active
    if run is None:
        return spent

    done, total = run.progress()
    unresolved = [step for step in run.steps if not step.is_terminal]
    if not unresolved:
        outcome = f"All {total} steps resolved."
    else:
        listed = ", ".join(str(step.number) for step in unresolved[:8])
        outcome = (
            f"{done}/{total} steps resolved; left unresolved: {listed}"
            + ("…" if len(unresolved) > 8 else "")
        )

    # Record the review next to the step accounting, or the critic's effect
    # vanishes as soon as the conversation scrolls.
    review = _coerce_plan_text(state.get("critic_summary")).strip()
    if review:
        outcome = f"{outcome} Review: {review}."
    plan_store.set_outcome(outcome=outcome, run_id=run.run_id, **scope)

    # Drop the last handoff: "continue with step 7" must not reach the summary
    # agent or the next turn. Unresolved findings are pinned in critic_open_findings.
    return {
        "messages": _feedback_removals(state)
        + [
            AIMessage(
                content=f"Plan file updated — {outcome}\n{state.get('plan_path', '')}".strip(),
                name="plan_finalize",
            )
        ],
        "critic_feedback_ids": [],
        "plan_progress": plan_store.progress_line(run),
    }


def _coerce_plan_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _plan_title(request: str) -> str:
    text = " ".join((request or "Handle the request").split())
    return text if len(text) <= 120 else text[:119].rstrip() + "…"


def _route_after_plan_init(
    state: AgentGraphState,
) -> Literal["execute_agent_plan", "execute_agent_free", "execute_agent_followup"]:
    category = state.get("task_category", "complex")
    if category == "simple":
        return "execute_agent_free"
    if category == "follow_up":
        return "execute_agent_followup"
    return "execute_agent_plan"


def _route_after_plan_finalize(
    state: AgentGraphState,
) -> Literal["summary_agent_complex", "summary_agent_simple"]:
    category = state.get("task_category", "complex")
    return "summary_agent_simple" if category in ("simple", "follow_up") else "summary_agent_complex"


def approval_ack_node(state: AgentGraphState) -> dict[str, Any]:
    constraints = state.get("approval_constraints") or []
    content = "Thanks for approval. I'll start executing the approved plan now."
    if constraints:
        conditions = "\n".join(f"- {item}" for item in constraints)
        content += (
            "\n\nThe approval carried these conditions, which override the "
            f"corresponding plan steps:\n{conditions}"
        )
    return {"messages": [AIMessage(content=content, name="approval_ack")]}


# Critic. Two review shapes over one set of parts:
#
#   full_run (simple, follow_up)  execute → gate → [critic → review] → plan_finalize
#   stepwise (complex)            execute → gate → [critic → review] → execute → …
#                                        ↑______________________________________|
#
# full_run shares the run's transcript. Step-wise cannot: the transcript only grows
# inside a run, so an inheriting critic would read more of the *other* steps every
# time. It runs isolated on a case file from critic_context and returns a verdict.
# The executor therefore holds its own traffic plus at most the current handoff,
# removed once its step settles, so review never rewrites its working context.
#
# The loop is driven by plan.md, not a prompt counter: terminal-but-unreviewed steps
# are work to check, unresolved steps are work to do. Three steps resolved in a pass
# are reviewed together; none resolved and critic_stall ends the loop.


def _load_review_run(state: AgentGraphState) -> plan_store.PlanRun | None:
    """This run's section of the plan file, or None if it cannot be read."""
    try:
        document = plan_store.load_document(**_plan_scope(state))
    except OSError:
        return None
    run_id = state.get("plan_run_id")
    return document.run(run_id) if run_id else document.active


def _int_list(value: Any) -> list[int]:
    numbers: list[int] = []
    for item in value or []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in numbers:
            numbers.append(number)
    return sorted(numbers)


def _terminal_steps(run: plan_store.PlanRun | None) -> list[int]:
    return [step.number for step in (run.steps if run else []) if step.is_terminal]


def _unresolved_steps(run: plan_store.PlanRun | None) -> list[int]:
    return [step.number for step in (run.steps if run else []) if not step.is_terminal]


def _step_attempts(state: AgentGraphState) -> dict[str, int]:
    raw = state.get("critic_step_attempts") or {}
    attempts: dict[str, int] = {}
    for key, value in raw.items() if isinstance(raw, dict) else ():
        try:
            attempts[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return attempts


def _critic_mode(state: AgentGraphState) -> str:
    """The review mode of the run in flight.

    `plan_init` stamps it, so this is normally a read. The fallback covers a
    thread checkpointed before the field existed.
    """
    mode = str(state.get("critic_mode") or "")
    if mode in (STEPWISE, FULL_RUN):
        return mode
    return _mode_for_category(state.get("task_category"))


def _loop_decision(*, unresolved: list[int], findings: Any, stall: int) -> str:
    """Whether the executor gets another pass.

    The loop terminates because both of its inputs are monotonic: unresolved
    steps only fall (a step cannot leave a terminal status except by a review
    reopening it, which is bounded per step), and `stall` only rises. Neither
    depends on the model choosing to stop.
    """
    if stall >= CRITIC_STALL_LIMIT:
        return "finalize"
    if findings:
        return "execute"
    return "execute" if unresolved else "finalize"


def critic_gate_node(state: AgentGraphState) -> dict[str, Any]:
    """Decide whether to review, and where the loop goes next. No LLM involved."""
    messages = state.get("messages") or []
    run = _load_review_run(state)
    watermark = last_message_id(messages)
    previous = str(state.get("critic_watermark_id") or "")
    since = messages_after_id(messages, previous)

    if _critic_mode(state) == STEPWISE:
        return _stepwise_gate(
            state, run=run, since=since, watermark=watermark, previous=previous
        )
    return _full_run_gate(state, run=run, since=since, watermark=watermark)


def _full_run_gate(
    state: AgentGraphState,
    *,
    run: plan_store.PlanRun | None,
    since: list[BaseMessage],
    watermark: str,
) -> dict[str, Any]:
    rounds = int(state.get("critic_rounds") or 0)
    if rounds >= CRITIC_MAX_ROUNDS:
        # Reviewed and remediated once already; a second opinion stops paying off.
        return {"critic_pending": False, "critic_findings": [], "critic_next": "finalize"}

    triggers = critic_gate.evaluate(
        run=run,
        task_category=str(state.get("task_category") or "complex"),
        turn_messages=since,
        approval_constraints=state.get("approval_constraints") or [],
        min_steps=CRITIC_MIN_STEPS,
        failure_trigger=CRITIC_FAILURE_TRIGGER,
    )

    run_id = state.get("plan_run_id")
    if not triggers:
        logger.info("Critic gate: no triggers, skipping review (run %s)", run_id)
        return {"critic_pending": False, "critic_findings": [], "critic_next": "finalize"}

    codes = ", ".join(trigger.code for trigger in triggers)
    logger.info("Critic gate: full-run review triggered by %s (run %s)", codes, run_id)

    request = (
        "Execution has finished and this run was selected for review.\n\n"
        "The gate flagged it for these reasons:\n"
        f"{critic_gate.describe(triggers)}\n\n"
        "Check each one against the artifacts and the live interpreter state, "
        "then return your verdict. Accept the run if nothing blocking holds up."
    )
    return {
        "messages": [AIMessage(content=request, name="critic_gate")],
        "critic_pending": True,
        "critic_summary": f"reviewed ({codes})",
        "critic_scope_steps": [],
        "critic_watermark_id": watermark,
        # Overwritten by the review node; safe default if no decision is produced.
        "critic_next": "finalize",
    }


def _stepwise_gate(
    state: AgentGraphState,
    *,
    run: plan_store.PlanRun | None,
    since: list[BaseMessage],
    watermark: str,
    previous: str,
) -> dict[str, Any]:
    """Review the steps that just resolved, or send the executor on to the next.

    `critic_reviewed_steps` is the watermark over the plan: a terminal step not
    in it is work no review has seen. That is read from the file rather than
    counted in the prompt, so a pass that resolved two steps, or none, is
    handled by the same arithmetic.
    """
    reviewed = set(_int_list(state.get("critic_reviewed_steps")))
    resolved = [number for number in _terminal_steps(run) if number not in reviewed]
    unresolved = _unresolved_steps(run)
    reviews = int(state.get("critic_reviews") or 0)

    # Liveness: resolving nothing new is the only way this loop fails to advance,
    # so it is the only thing counted, including when the plan file is unreadable.
    stall = 0 if resolved else int(state.get("critic_stall") or 0) + 1

    updates: dict[str, Any] = {
        "critic_pending": False,
        "critic_stall": stall,
        "critic_watermark_id": watermark,
    }
    run_id = state.get("plan_run_id")

    if resolved and reviews >= CRITIC_MAX_REVIEWS:
        # Out of review budget, not out of work: continue unreviewed, and record it.
        logger.warning(
            "Critic gate: review budget (%s) spent; steps %s go unreviewed (run %s)",
            CRITIC_MAX_REVIEWS,
            resolved,
            run_id,
        )
        updates["critic_reviewed_steps"] = sorted(reviewed | set(resolved))
        updates["critic_next"] = _loop_decision(
            unresolved=unresolved, findings=state.get("critic_findings"), stall=stall
        )
        return updates

    triggers = (
        critic_gate.evaluate_steps(
            run=run,
            step_numbers=resolved,
            step_messages=since,
            approval_constraints=state.get("approval_constraints") or [],
            failure_trigger=CRITIC_FAILURE_TRIGGER,
            always=CRITIC_STEPWISE_ALWAYS,
        )
        if resolved
        else []
    )

    if triggers:
        codes = ", ".join(trigger.code for trigger in triggers)
        logger.info(
            "Critic gate: step-wise review of step(s) %s triggered by %s (run %s)",
            resolved,
            codes,
            run_id,
        )
        updates.update(
            {
                "critic_pending": True,
                "critic_scope_steps": resolved,
                "critic_triggers": [str(trigger) for trigger in triggers],
                # Window that produced these steps; critic_watermark_id has moved on.
                "critic_evidence_after_id": previous,
                "critic_next": "finalize",
            }
        )
        # The brief enters the transcript only if the critic will read it. An isolated
        # critic gets it in its case file; duplicating it here tells the executor
        # nothing it needs.
        if not CRITIC_STEPWISE_ISOLATED:
            updates["messages"] = [
                AIMessage(
                    content=_stepwise_brief(
                        scope=resolved,
                        reviewed=sorted(reviewed),
                        unresolved=unresolved,
                        triggers=triggers,
                    ),
                    name="critic_gate",
                )
            ]
        return updates

    if resolved:
        # Cleared without a model call. Record them or every later pass re-evaluates
        # the same steps.
        logger.info(
            "Critic gate: step(s) %s cleared without review (run %s)", resolved, run_id
        )
        updates["critic_reviewed_steps"] = sorted(reviewed | set(resolved))

    updates["critic_next"] = _loop_decision(
        unresolved=unresolved, findings=state.get("critic_findings"), stall=stall
    )
    return updates


def _numbers(values: list[int]) -> str:
    return ", ".join(str(value) for value in values) if values else "none"


def _stepwise_brief(
    *,
    scope: list[int],
    reviewed: list[int],
    unresolved: list[int],
    triggers: list[critic_gate.CriticTrigger],
) -> str:
    """The critic's brief for a step-wise pass, when it reads the transcript.

    Only reached with `CRITIC_STEPWISE_ISOLATED` off — the trial's baseline. An
    isolated critic gets the same bounds structurally, from sections 3 and 4 of
    its case file, rather than as a request to stay inside them.

    The bounds matter more than the triggers here. A critic handed step 2 of an
    8-step plan and no scope will report that the report is missing and the
    analysis is incomplete — both true, both simply not yet due. Saying which
    steps are settled, which are in scope, and which have not started keeps the
    verdict about work that actually happened.
    """
    noun = f"step {scope[0]}" if len(scope) == 1 else f"steps {_numbers(scope)}"
    return (
        f"Step-wise review. The executor has just resolved {noun} and handed back. "
        "The rest of the plan has not been attempted yet.\n\n"
        f"The gate flagged this for these reasons:\n{critic_gate.describe(triggers)}\n\n"
        f"Scope: review {noun} only.\n"
        f"- Steps already reviewed and settled: {_numbers(reviewed)}. Do not re-open one "
        "unless you can demonstrate it is wrong and that it affects the work in scope.\n"
        f"- Steps not started yet: {_numbers(unresolved)}. These are next, not missing — "
        "never report them as incomplete, and never report a deliverable a later step "
        "produces as absent.\n\n"
        "Check the step against the artifacts and the live interpreter state, then "
        f"return your verdict with `step` set to the step it concerns. Accept {noun} if "
        "nothing blocking holds up — that is the expected outcome for a sound step."
    )


# Invoking the critic


def make_critic_agent_node(*, shared_agent, isolated_agent):
    """The `critic_agent` node, in whichever shape the run's mode calls for.

    A plain node rather than the compiled agent itself, because the two review
    modes need opposite things from the graph. Full-run review *wants* the
    conversation — it is judging the turn it can see. Step-wise review must not
    have it: the transcript it would inherit grows with every step while the
    step it was asked to judge does not, and its own verification calls then
    push that step's evidence out of the window it is read through.
    """

    async def critic_agent_node(state: AgentGraphState) -> dict[str, Any]:
        if isolated_agent is not None and _critic_mode(state) == STEPWISE:
            return await _run_isolated_critic(state, isolated_agent)
        return await _run_shared_critic(state, shared_agent)

    return critic_agent_node


async def _run_shared_critic(state: AgentGraphState, agent) -> dict[str, Any]:
    """Full-run review: the critic reads this run's own transcript.

    Invoked here rather than wired in as a node so that one node name covers
    both modes. The messages it produced are returned by id-diff against what it
    was given: returning the whole list would work too — `add_messages` is keyed
    by id — but it would re-emit the entire conversation on the stream for the
    UI to de-duplicate again.
    """
    seen = {
        str(getattr(message, "id", "") or "") for message in (state.get("messages") or [])
    }
    result = await agent.ainvoke(dict(state))
    produced = [
        message
        for message in (result.get("messages") or [])
        if str(getattr(message, "id", "") or "") not in seen
    ]
    return {
        "messages": produced,
        "structured_response": result.get("structured_response"),
    }


async def _run_isolated_critic(state: AgentGraphState, agent) -> dict[str, Any]:
    """Step-wise review: the critic reads a case file, not the conversation.

    Nothing of this invocation reaches the main flow. Its input is built by
    `critic_context`, its messages live and die inside this call, and the only
    thing that comes back is the verdict — which `critic_review_node` turns into
    plan-file writes and one handoff message. The executor's transcript is
    therefore executor traffic plus that handoff, at any point in the run.
    """
    run = _load_review_run(state)
    scope = _int_list(state.get("critic_scope_steps"))
    target = scope[0] if scope else 0
    messages = state.get("messages") or []
    evidence = messages_after_id(
        messages, str(state.get("critic_evidence_after_id") or "")
    )
    case_file = critic_context.build_stepwise_case_file(
        run=run,
        scope_steps=scope,
        triggers=[str(item) for item in (state.get("critic_triggers") or [])],
        evidence=evidence,
        prior_findings=state.get("critic_findings") or [],
        attempts=_step_attempts(state).get(str(target), 0),
        goal=_coerce_plan_text(getattr(run, "goal", ""))
        or latest_user_request_text(messages),
        constraints=state.get("approval_constraints")
        or list(getattr(run, "constraints", None) or []),
        user_id=state.get("user_id"),
        conversation_id=state.get("conversation_id"),
    )
    logger.info(
        "Critic: isolated review of step(s) %s — %s-char case file over %s evidence "
        "message(s) (run %s)",
        _numbers(scope),
        len(case_file),
        len(evidence),
        state.get("plan_run_id"),
    )
    try:
        result = await agent.ainvoke(
            {"messages": critic_context.build_stepwise_messages(case_file)},
            {
                "recursion_limit": CRITIC_RECURSION_LIMIT,
                "run_name": f"critic_review · step {_numbers(scope)}",
            },
        )
    except Exception as exc: 
        logger.warning("Isolated critic failed; continuing without a verdict: %s", exc)
        return {"structured_response": None}
    return {"structured_response": result.get("structured_response")}


# The handoff message
def _tracks_feedback(state: AgentGraphState) -> bool:
    """Whether this run manages the handoff message's lifecycle.

    Only the isolated step-wise flow does. With isolation off the critic writes
    into the transcript anyway, and removing its verdicts while leaving its
    working messages would produce a transcript that is neither shape — the
    baseline this trial measures against has to stay the baseline.
    """
    return bool(CRITIC_STEPWISE_ISOLATED) and _critic_mode(state) == STEPWISE


def _feedback_removals(state: AgentGraphState) -> list[RemoveMessage]:
    """Drop the handoff messages earlier reviews left standing.

    Only ids actually present are removed: `add_messages` raises on an id it
    cannot find, and bookkeeping must not be able to fail a run.
    """
    if not _tracks_feedback(state):
        return []
    tracked = [
        str(item) for item in (state.get("critic_feedback_ids") or []) if str(item).strip()
    ]
    if not tracked:
        return []
    present = {
        str(getattr(message, "id", "") or "") for message in (state.get("messages") or [])
    }
    return [RemoveMessage(id=item) for item in tracked if item in present]


def _handoff_instruction(
    *, reopened: list[int], exhausted: list[int], unresolved: list[int]
) -> str:
    """What the executor should do next, said explicitly.

    Left implicit, an executor that reads `blocked` in the plan file and its own
    guardrail about blocked steps concludes the run is over — which is precisely
    what a step-wise review must not cause.
    """
    if reopened:
        listed = _numbers(reopened)
        return (
            f"**Step {listed} is open again in `plan.md`.** Resolve the blocking "
            "finding(s) above before anything else, then call `plan_update` for "
            f"step {listed} and hand back."
        )

    parts: list[str] = []
    if exhausted:
        parts.append(
            f"**Step {_numbers(exhausted)} is out of revision budget** and is "
            "recorded `blocked` in `plan.md`. It is settled: do not retry it, and "
            "do not stop the run over it — the finding is recorded and will be "
            "reported to the user."
        )
    if unresolved:
        parts.append(
            f"Continue with step {unresolved[0]}, the lowest-numbered unresolved step."
        )
    else:
        parts.append(
            "Every plan step is now resolved; there is nothing further to execute."
        )
    return " ".join(parts)


def _handoff_message(
    state: AgentGraphState,
    *,
    reviews: int,
    headline: str,
    verified: str,
    bullets: list[str],
    instruction: str,
) -> AIMessage:
    lines: list[str] = []
    if verified.strip():
        lines.append(f"**Verified:** {verified.strip()}")
    lines.extend(bullets)
    if not lines:
        lines.append("No defects established.")
    lines.append("")
    lines.append(instruction)
    return AIMessage(
        content=f"**Review — {headline}.**\n\n" + "\n".join(lines),
        name="critic_review",
        # Deterministic and unique within the run, so the next review can remove
        # exactly this message without having to search the transcript for it.
        id=f"critic-review-{state.get('plan_run_id') or 0}-{reviews}",
    )


def _merge_open_findings(prior: Any, additions: list[str]) -> list[str]:
    """Findings no execution pass will resolve, accumulated across the run.

    Order-preserving and de-duplicated: the same advisory note re-raised by two
    consecutive reviews is one open issue, not two.
    """
    merged: list[str] = []
    for item in list(prior or []) + additions:
        text = str(item).strip()
        if text and text not in merged:
            merged.append(text)
    return merged[:CRITIC_FINDINGS_MAX]


def _route_after_critic_gate(
    state: AgentGraphState,
) -> Literal[
    "critic_agent",
    "execute_agent_plan",
    "execute_agent_free",
    "execute_agent_followup",
    "plan_finalize",
]:
    # Read explicit flags, not the gate's message: the previous round's message is
    # still in the transcript, so scanning back would re-enter a critic just declined.
    if state.get("critic_pending"):
        return "critic_agent"
    return _route_after_review(state)


def _route_after_critic_review(
    state: AgentGraphState,
) -> Literal[
    "execute_agent_plan",
    "execute_agent_free",
    "execute_agent_followup",
    "plan_finalize",
]:
    return _route_after_review(state)


def _route_after_review(
    state: AgentGraphState,
) -> Literal[
    "execute_agent_plan",
    "execute_agent_free",
    "execute_agent_followup",
    "plan_finalize",
]:
    """Back to the executor, or on to `plan_finalize`.

    One function behind both edges so the gate and the review node cannot
    disagree about what "continue" means. The budget checks are belt and braces:
    the nodes already refuse to ask for a pass they are not entitled to, and
    this makes a future change to either of them degrade into "finish the run"
    rather than into a loop against RECURSION_LIMIT.
    """
    if str(state.get("critic_next") or "") != "execute":
        return "plan_finalize"
    if _critic_mode(state) == STEPWISE:
        if int(state.get("critic_stall") or 0) >= CRITIC_STALL_LIMIT:
            return "plan_finalize"
    elif int(state.get("critic_rounds") or 0) > CRITIC_MAX_ROUNDS:
        return "plan_finalize"
    return _route_after_plan_init(state)


def _coerce_verdict(value: Any) -> CriticVerdict | None:
    if isinstance(value, CriticVerdict):
        return value
    if isinstance(value, dict):
        try:
            return CriticVerdict.model_validate(value)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not read the critic verdict: %s", exc)
    return None


def _finding_line(finding: CriticFinding) -> str:
    where = f"Step {finding.step}" if finding.step else "Run"
    parts = [f"{where}: {finding.claim.strip()}".rstrip(": ")]
    if finding.required_action.strip():
        parts.append(f"Required: {finding.required_action.strip()}")
    if finding.evidence.strip():
        parts.append(f"Evidence: {finding.evidence.strip()}")
    text = " — ".join(part for part in parts if part)
    return text[:CRITIC_FINDING_MAX_CHARS]


def _verdict_message(
    *,
    headline: str,
    verified: str,
    bullets: list[str],
) -> AIMessage:
    lines: list[str] = []
    if verified.strip():
        lines.append(f"**Verified:** {verified.strip()}")
    lines.extend(bullets)
    if not lines:
        lines.append("No defects established.")
    return AIMessage(
        content=f"**Review — {headline}.**\n\n" + "\n".join(lines), name="critic_review"
    )


def critic_review_node(state: AgentGraphState) -> dict[str, Any]:
    """Apply the critic's verdict. Deterministic; the model supplied judgement.

    The critic names the defective steps; code reopens them in `plan.md`. This
    is the same division as everywhere else in the execution routine — the
    agent supplies *which* step reached *which* status, and the file is written
    by something that cannot misremember.
    """
    verdict = _coerce_verdict(state.get("structured_response"))
    reviews = int(state.get("critic_reviews") or 0) + 1
    stepwise = _critic_mode(state) == STEPWISE

    if verdict is None:
        # A critic that produced no readable verdict must not silently hold the
        # run back; the work stands and the failure is recorded. In step-wise
        # mode the reviewed steps are marked seen even so, because retrying the
        # same call is how one broken verdict becomes an endless loop.
        logger.warning("Critic returned no structured verdict; accepting the run.")
        updates: dict[str, Any] = {
            "critic_pending": False,
            "critic_findings": [],
            "critic_reviews": reviews,
            "structured_response": None,
        }
        if not stepwise:
            updates["critic_rounds"] = int(state.get("critic_rounds") or 0) + 1
            updates["critic_summary"] = "review inconclusive (no verdict returned)"
            updates["critic_next"] = "finalize"
            return updates

        scope = _int_list(state.get("critic_scope_steps"))
        reviewed = sorted(set(_int_list(state.get("critic_reviewed_steps"))) | set(scope))
        unresolved_after = _unresolved_steps(_load_review_run(state))
        # The executor must still be told what to do next; silence leaves it holding
        # a handed-back step, waiting for a review that is not coming.
        handoff = _handoff_message(
            state,
            reviews=reviews,
            headline=f"step {_numbers(scope)} — review inconclusive",
            verified="",
            bullets=[
                "- ⚪ the reviewer returned no readable verdict; the work stands "
                "as recorded and this is noted as unreviewed."
            ],
            instruction=_handoff_instruction(
                reopened=[], exhausted=[], unresolved=unresolved_after
            ),
        )
        updates["messages"] = _feedback_removals(state) + [handoff]
        updates["critic_feedback_ids"] = (
            [handoff.id] if _tracks_feedback(state) else []
        )
        updates["critic_reviewed_steps"] = reviewed
        updates["critic_scope_steps"] = []
        updates["critic_triggers"] = []
        updates["critic_open_findings"] = _merge_open_findings(
            state.get("critic_open_findings"),
            [
                f"unreviewed — step {_numbers(scope)} was selected for review but "
                "the review returned no verdict"
            ],
        )
        updates["critic_summary"] = _stepwise_tally(
            state, reviewed=reviewed, reviews=reviews, note="1 review inconclusive"
        )
        updates["critic_next"] = _loop_decision(
            unresolved=unresolved_after,
            findings=None,
            stall=int(state.get("critic_stall") or 0),
        )
        return updates

    if stepwise:
        return _apply_stepwise_verdict(state, verdict, reviews=reviews)
    return _apply_full_run_verdict(state, verdict, reviews=reviews)


def _apply_full_run_verdict(
    state: AgentGraphState, verdict: CriticVerdict, *, reviews: int
) -> dict[str, Any]:
    rounds = int(state.get("critic_rounds") or 0) + 1
    blocking = [f for f in verdict.findings if f.severity == "blocking"]
    advisory = [f for f in verdict.findings if f.severity != "blocking"]

    # Reopen rejected steps so the panel and the plan file stop claiming them done.
    reopened: list[int] = []
    for finding in blocking:
        if finding.step <= 0:
            continue
        if _reopen_step(state, finding.step, finding):
            reopened.append(finding.step)

    accepted = verdict.decision == "accept" or not blocking
    # Unreachable: the gate stops invoking the critic at budget. Kept so a future
    # gate change degrades into "unresolved" rather than an unbounded critic loop.
    exhausted = rounds > CRITIC_MAX_ROUNDS

    # Keep the gate's reasons from critic_summary: the plan file should record both
    # why the run was reviewed and what came of it.
    triggered_by = _coerce_plan_text(state.get("critic_summary")).strip()

    if accepted:
        summary = "review passed"
        if advisory:
            summary += f" with {len(advisory)} advisory finding(s)"
    elif exhausted:
        summary = (
            f"review found {len(blocking)} blocking issue(s); revision budget "
            f"({CRITIC_MAX_ROUNDS}) exhausted, reported unresolved"
        )
    else:
        summary = (
            f"review sent {len(blocking)} issue(s) back; steps reopened: "
            f"{_numbers(reopened)}"
        )

    # The plan file also records why the run was picked, which makes the gate
    # tunable after the fact.
    recorded = f"{triggered_by} → {summary}" if triggered_by else summary

    # Advisory findings never go back to the executor but must not vanish: they
    # stay on the transcript for the report's Open Issues.
    bullets = [f"- 🔴 **blocking** — {_finding_line(f)}" for f in blocking]
    bullets += [f"- 🟡 advisory — {_finding_line(f)}" for f in advisory]

    updates: dict[str, Any] = {
        "messages": [
            _verdict_message(
                headline=summary, verified=verdict.verified, bullets=bullets
            )
        ],
        "critic_pending": False,
        "critic_rounds": rounds,
        "critic_reviews": reviews,
        "critic_summary": recorded,
        "structured_response": None,
    }

    # Pin only open blocking findings; pinned advisories would keep instructing an
    # executor that can do nothing about them.
    if accepted or exhausted:
        updates["critic_findings"] = []
        updates["critic_next"] = "finalize"
    else:
        updates["critic_findings"] = [
            _finding_line(finding) for finding in blocking[:CRITIC_FINDINGS_MAX]
        ]
        updates["critic_next"] = "execute"
    return updates


def _apply_stepwise_verdict(
    state: AgentGraphState, verdict: CriticVerdict, *, reviews: int
) -> dict[str, Any]:
    """Apply a verdict scoped to the steps that just resolved.

    Three things can happen to a blocking finding, and the difference is the
    whole point of doing this per step:

    - it names a step with revision budget left → the step is reopened and the
      finding is pinned, so the executor's next pass redoes that step before it
      moves on;
    - the step is out of budget → it is recorded `blocked` with the finding as
      its note, which is terminal, so the run continues and `plan_finalize` and
      the report both carry it as unresolved;
    - it names a step that has not been attempted yet → it is reported and
      nothing is reopened. A critic looking at step 2 of 8 has no business
      failing step 7.
    """
    scope = _int_list(state.get("critic_scope_steps"))
    reviewed = set(_int_list(state.get("critic_reviewed_steps")))
    revised = set(_int_list(state.get("critic_revised_steps")))
    unresolved_after_review = set(_int_list(state.get("critic_unresolved_steps")))
    attempts = _step_attempts(state)

    # Terminal steps are fair game even out of scope: an earlier step shown wrong
    # is the cross-step defect per-step review would miss. Unattempted steps are not.
    reviewable = set(_terminal_steps(_load_review_run(state))) | set(scope)
    fallback = scope[0] if scope else 0

    blocking = [f for f in verdict.findings if f.severity == "blocking"]
    advisory = [f for f in verdict.findings if f.severity != "blocking"]

    sent_back: list[tuple[int, CriticFinding]] = []
    exhausted: list[tuple[int, CriticFinding]] = []
    premature: list[CriticFinding] = []

    # Group by step: one pass is one attempt however many findings it left. Per
    # finding, two on one step would reopen it and exhaust it in the same pass.
    grouped: dict[int, list[CriticFinding]] = {}
    for finding in blocking:
        target = finding.step if finding.step > 0 else fallback
        if target <= 0 or target not in reviewable:
            premature.append(finding)
            continue
        grouped.setdefault(target, []).append(finding)

    for target, group in sorted(grouped.items()):
        key = str(target)
        attempts[key] = attempts.get(key, 0) + 1
        if attempts[key] > CRITIC_STEP_MAX_ROUNDS:
            # Out of budget; blocked is honest, since it is done and did not hold up.
            _write_step_status(
                state, target, plan_store.BLOCKED, _group_note("Unresolved after review", group)
            )
            exhausted.extend((target, finding) for finding in group)
            reviewed.add(target)
            unresolved_after_review.add(target)
            continue

        if _write_step_status(
            state, target, plan_store.IN_PROGRESS, _group_note("Reopened by review", group)
        ):
            sent_back.extend((target, finding) for finding in group)
            revised.add(target)
            reviewed.discard(target)
        else:
            # Update rejected (unknown step, or unwritable); stop spending passes on it.
            logger.warning("Could not reopen step %s for review; leaving it as is", target)
            reviewed.add(target)

    reopened = sorted({number for number, _ in sent_back})
    exhausted_steps = sorted({number for number, _ in exhausted})
    reviewed.update(number for number in scope if number not in reopened)

    # A finding is a work item exactly when a step was reopened for it; those go
    # back in the handoff. Everything else (advisories, exhausted steps, objections
    # to unstarted steps) is a report item for the summary agent.
    pinned = [_finding_line(finding) for _, finding in sent_back][:CRITIC_FINDINGS_MAX]
    left_open = _merge_open_findings(
        state.get("critic_open_findings"),
        [f"unresolved — {_finding_line(finding)}" for _, finding in exhausted]
        + [f"advisory — {_finding_line(finding)}" for finding in advisory]
        + [f"noted, out of scope — {_finding_line(finding)}" for finding in premature],
    )

    bullets = [
        f"- 🔴 **blocking** — {_finding_line(finding)}" for _, finding in sent_back
    ]
    bullets += [
        f"- 🔴 **blocking, unresolved** (step {number} is out of revision budget) — "
        f"{_finding_line(finding)}"
        for number, finding in exhausted
    ]
    bullets += [f"- 🟡 advisory — {_finding_line(f)}" for f in advisory]
    bullets += [
        f"- ⚪ noted, outside this review's scope — {_finding_line(f)}" for f in premature
    ]

    scope_label = f"step {_numbers(scope)}" if len(scope) == 1 else f"steps {_numbers(scope)}"
    if sent_back:
        headline = f"{scope_label}: {len(sent_back)} issue(s) sent back, step(s) {_numbers(reopened)} reopened"
    elif exhausted:
        headline = f"{scope_label}: {len(exhausted)} issue(s) left unresolved, revision budget spent"
    elif advisory:
        headline = f"{scope_label} accepted with {len(advisory)} advisory finding(s)"
    else:
        headline = f"{scope_label} accepted"

    # Re-read the plan after the writes: reopening a step makes the run unfinished
    # again, and the handoff and loop decision must see that, not the earlier state.
    unresolved_after = _unresolved_steps(_load_review_run(state))
    handoff = _handoff_message(
        state,
        reviews=reviews,
        headline=headline,
        verified=verdict.verified,
        bullets=bullets,
        instruction=_handoff_instruction(
            reopened=reopened,
            exhausted=exhausted_steps,
            unresolved=unresolved_after,
        ),
    )

    reviewed_sorted = sorted(reviewed)
    updates: dict[str, Any] = {
        "messages": _feedback_removals(state) + [handoff],
        "critic_feedback_ids": [handoff.id] if _tracks_feedback(state) else [],
        "critic_pending": False,
        "critic_reviews": reviews,
        "critic_findings": pinned,
        "critic_open_findings": left_open,
        "critic_triggers": [],
        "critic_reviewed_steps": reviewed_sorted,
        "critic_revised_steps": sorted(revised),
        "critic_unresolved_steps": sorted(unresolved_after_review),
        "critic_step_attempts": attempts,
        "critic_scope_steps": [],
        "structured_response": None,
    }
    updates["critic_summary"] = _stepwise_tally(
        state,
        reviewed=reviewed_sorted,
        reviews=reviews,
        revised=sorted(revised),
        unresolved=sorted(unresolved_after_review),
    )
    updates["critic_next"] = _loop_decision(
        unresolved=unresolved_after,
        findings=pinned,
        stall=int(state.get("critic_stall") or 0),
    )
    return updates


def _stepwise_tally(
    state: AgentGraphState,
    *,
    reviewed: list[int],
    reviews: int,
    revised: list[int] | None = None,
    unresolved: list[int] | None = None,
    note: str = "",
) -> str:
    """The run's review record for `plan.md`, recomputed on every pass.

    Accumulated as counters rather than appended text: a step-wise run produces
    one verdict per step, and `plan_store` clips the outcome line, so an
    appended log would lose its own tail.
    """
    run = _load_review_run(state)
    total = len(run.steps) if run and run.steps else 0
    parts = [
        f"step-wise: {len(reviewed)}/{total} step(s) checked in {reviews} review(s)"
        if total
        else f"step-wise: {len(reviewed)} step(s) checked in {reviews} review(s)"
    ]
    revised = revised if revised is not None else _int_list(state.get("critic_revised_steps"))
    unresolved = (
        unresolved
        if unresolved is not None
        else _int_list(state.get("critic_unresolved_steps"))
    )
    if revised:
        parts.append(f"sent back: {_numbers(revised)}")
    if unresolved:
        parts.append(f"left unresolved: {_numbers(unresolved)}")
    if note:
        parts.append(note)
    return "; ".join(parts)


def _group_note(prefix: str, findings: list[CriticFinding]) -> str:
    """One plan-file note covering every finding a review left on one step."""
    actions = [
        (finding.required_action or finding.claim).strip()
        for finding in findings
        if (finding.required_action or finding.claim).strip()
    ]
    return f"{prefix}: " + ("; ".join(actions) if actions else "see the review")


def _reopen_step(
    state: AgentGraphState, step_number: int, finding: CriticFinding
) -> bool:
    return _write_step_status(
        state,
        step_number,
        plan_store.IN_PROGRESS,
        _group_note("Reopened by review", [finding]),
    )


def _write_step_status(
    state: AgentGraphState, step_number: int, status: str, note: str
) -> bool:
    try:
        ok, _, _ = plan_store.update_step(
            step_number=step_number,
            status=status,
            note=note,
            run_id=state.get("plan_run_id"),
            **_plan_scope(state),
        )
    except OSError as exc:
        logger.warning("Could not set step %s to %s: %s", step_number, status, exc)
        return False
    return bool(ok)


async def create_app(
    checkpointer,
    *,
    use_context_compression: bool = True,
    use_critic: bool = True,
):
    planning_llm = init_chat_model(PLANNING_MODEL, model_provider="openai", api_key=OPENAI_API_KEY)
    execute_llm = init_chat_model(EXECUTE_MODEL, model_provider="openai", api_key=OPENAI_API_KEY)
    summary_llm = init_chat_model(SUMMARY_MODEL, model_provider="openai", api_key=OPENAI_API_KEY)
    critic_llm = init_chat_model(CRITIC_MODEL, model_provider="openai", api_key=OPENAI_API_KEY)

    pre_model_hook = build_pre_model_state if use_context_compression else build_uncompressed_pre_model_state

    planning_agent = build_planning_agent(planning_llm, pre_model_hook=pre_model_hook)

    # Two execute variants share the same script; only the system prompt differs.
    execute_agent_free = build_execute_agent(
        execute_llm,
        pre_model_hook=pre_model_hook,
        name="execute_agent_free",
        prompt=EXECUTE_AGENT_FREE_SYSTEM_PROMPT,
    )
    execute_agent_plan = build_execute_agent(
        execute_llm,
        pre_model_hook=pre_model_hook,
        name="execute_agent_plan",
        prompt=EXECUTE_AGENT_SYSTEM_PROMPT,
    )
    execute_agent_followup = build_execute_agent(
        execute_llm,
        pre_model_hook=pre_model_hook,
        name="execute_agent_followup",
        prompt=EXECUTE_AGENT_FOLLOWUP_SYSTEM_PROMPT,
    )

    critic_agent_node = None
    if use_critic:
        # Two critics behind one node: full-run shares the transcript (right when the
        # run is one step long), step-wise is isolated on a per-step case file so its
        # input does not grow with the plan. Picked per run from critic_mode.
        critic_agent_node = make_critic_agent_node(
            shared_agent=build_critic_agent(
                critic_llm,
                pre_model_hook=pre_model_hook,
                response_format=CriticVerdict,
            ),
            isolated_agent=(
                build_stepwise_critic_agent(critic_llm, response_format=CriticVerdict)
                if CRITIC_STEPWISE_ISOLATED
                else None
            ),
        )

    # Three summary variants share the same script; only the system prompt differs.
    summary_agent_simple = build_summary_agent(
        summary_llm,
        pre_model_hook=pre_model_hook,
        name="summary_agent_simple",
        prompt=SUMMARY_AGENT_SIMPLE_SYSTEM_PROMPT,
    )
    summary_agent_complex = build_summary_agent(
        summary_llm,
        pre_model_hook=pre_model_hook,
        name="summary_agent_complex",
        prompt=SUMMARY_AGENT_SYSTEM_PROMPT,
    )
    summary_agent_meta = build_summary_agent(
        summary_llm,
        pre_model_hook=pre_model_hook,
        name="summary_agent_meta",
        prompt=SUMMARY_AGENT_META_SYSTEM_PROMPT,
    )

    graph = StateGraph(AgentGraphState)
    graph.add_node("task_classifier", task_classifier_node)
    graph.add_node("planning_agent", planning_agent)
    graph.add_node("human_chat", human_chat_node)
    graph.add_node("approval_ack", approval_ack_node)
    graph.add_node("plan_init", make_plan_init_node(use_critic=use_critic))
    graph.add_node("plan_finalize", plan_finalize_node)
    graph.add_node("execute_agent_free", execute_agent_free)
    graph.add_node("execute_agent_plan", execute_agent_plan)
    graph.add_node("execute_agent_followup", execute_agent_followup)
    graph.add_node("summary_agent_simple", summary_agent_simple)
    graph.add_node("summary_agent_complex", summary_agent_complex)
    graph.add_node("summary_agent_meta", summary_agent_meta)

    if use_critic:
        graph.add_node("critic_gate", critic_gate_node)
        graph.add_node("critic_agent", critic_agent_node)
        graph.add_node("critic_review", critic_review_node)

    if use_context_compression:
        # One node per terminal branch keeps the graph acyclic and the streaming
        # label meaningful.
        graph.add_node("context_summary_simple", _compress_context)
        graph.add_node("context_summary_complex", _compress_context)
        graph.add_node("context_summary_meta", _compress_context)

    # Entry: classify the user's request.
    graph.add_edge(START, "task_classifier")
    graph.add_conditional_edges(
        "task_classifier",
        _route_after_classifier,
        {
            "planning_agent": "planning_agent",
            # All execution routes pass plan_init, so plan.md records the run before
            # any work starts.
            "execute_agent_free": "plan_init",
            "execute_agent_followup": "plan_init",
            "summary_agent_meta": "summary_agent_meta",
        },
    )

    # Complex: plan → human_chat → (revise | approve → execute_plan → summary).
    graph.add_edge("planning_agent", "human_chat")
    graph.add_conditional_edges(
        "human_chat",
        _route_after_human,
        {
            "planning_agent": "planning_agent",
            "approval_ack": "approval_ack",
        },
    )
    graph.add_edge("approval_ack", "plan_init")

    # Every execution route: plan_init → execute → plan_finalize → summary agent.
    graph.add_conditional_edges(
        "plan_init",
        _route_after_plan_init,
        {
            "execute_agent_plan": "execute_agent_plan",
            "execute_agent_free": "execute_agent_free",
            "execute_agent_followup": "execute_agent_followup",
        },
    )
    execute_nodes = ("execute_agent_plan", "execute_agent_free", "execute_agent_followup")

    if use_critic:
        # Review sits on execute → plan_finalize, before the outcome is recorded:
        # after it, plan.md would claim steps resolved that the critic is about to
        # reject. meta_query never traverses this edge, so it is exempt structurally.
        #
        # The gate can also send the executor straight back unreviewed, which is what
        # makes step-wise affordable: most passes are "checked, carry on" and cost only
        # the gate. _route_after_review is the single decision point for both edges.
        execute_targets = {node: node for node in execute_nodes}
        for node in execute_nodes:
            graph.add_edge(node, "critic_gate")
        graph.add_conditional_edges(
            "critic_gate",
            _route_after_critic_gate,
            {
                "critic_agent": "critic_agent",
                **execute_targets,
                "plan_finalize": "plan_finalize",
            },
        )
        graph.add_edge("critic_agent", "critic_review")
        graph.add_conditional_edges(
            "critic_review",
            _route_after_critic_review,
            {**execute_targets, "plan_finalize": "plan_finalize"},
        )
    else:
        for node in execute_nodes:
            graph.add_edge(node, "plan_finalize")

    graph.add_conditional_edges(
        "plan_finalize",
        _route_after_plan_finalize,
        {
            "summary_agent_complex": "summary_agent_complex",
            "summary_agent_simple": "summary_agent_simple",
        },
    )

    # Terminal wiring per branch (with or without context compression).
    if use_context_compression:
        graph.add_edge("summary_agent_simple", "context_summary_simple")
        graph.add_edge("context_summary_simple", END)
        graph.add_edge("summary_agent_complex", "context_summary_complex")
        graph.add_edge("context_summary_complex", END)
        graph.add_edge("summary_agent_meta", "context_summary_meta")
        graph.add_edge("context_summary_meta", END)
    else:
        graph.add_edge("summary_agent_simple", END)
        graph.add_edge("summary_agent_complex", END)
        graph.add_edge("summary_agent_meta", END)

    return graph.compile(checkpointer=checkpointer)
