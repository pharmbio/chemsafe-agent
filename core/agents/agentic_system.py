from __future__ import annotations

import json
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.config import (
    APPROVAL_JUDGE_MODEL,
    CONTEXT_SUMMARY_MODEL,
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
    latest_summary_record,
    latest_user_request_text,
    make_summary_message,
    normalize_memory,
    render_transcript,
    should_summarize,
)
from backend.utils import plan_store
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


# AgentGraphState lives in core.agents.context because every react agent must be
# built with it as `state_schema`; see the note on the class.


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

    # Routing a follow-up ("now redo it with the peak value") is impossible from
    # the bare message, so the classifier sees the goal and the last exchange.
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
        # The approval belongs to the plan it was given for. A new task —
        # whether it gets its own plan or not — is not governed by it. Only
        # follow-ups continue under the standing approval; meta queries are left
        # alone so an aside does not discard a plan a later follow-up needs.
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

    # The human's words are kept on the record either way. Previously an
    # approval carrying a qualifier ("approved, but use the STEL") was reduced
    # to a bare status flag and the qualifier never reached the executor.
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


def plan_init_node(state: AgentGraphState) -> dict[str, Any]:
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
        # Routes with no approved plan still get a section, so the document
        # stays a complete record of the conversation and follow-ups can read it.
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
        # Never let bookkeeping stop the actual work.
        logger.warning("Could not write the plan file: %s", exc)
        return {}

    path = str(plan_store.plan_file_path(**_plan_scope(state)))
    return {
        "messages": [
            AIMessage(content=render_ledger(run, path=path), name="plan_init")
        ],
        "plan_path": path,
        "plan_run_id": run.run_id,
        "plan_progress": plan_store.progress_line(run),
    }


def plan_finalize_node(state: AgentGraphState) -> dict[str, Any]:
    """Reconcile the plan file after execution and record the run outcome.

    Reads back what actually happened rather than asking the model to report it.
    """
    scope = _plan_scope(state)
    try:
        document = plan_store.load_document(**scope)
    except OSError as exc:
        logger.warning("Could not read the plan file: %s", exc)
        return {}

    run = document.run(state.get("plan_run_id")) if state.get("plan_run_id") else document.active
    if run is None:
        return {}

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
    plan_store.set_outcome(outcome=outcome, run_id=run.run_id, **scope)

    return {
        "messages": [
            AIMessage(
                content=f"Plan file updated — {outcome}\n{state.get('plan_path', '')}".strip(),
                name="plan_finalize",
            )
        ],
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


async def create_app(
    checkpointer,
    *,
    use_context_compression: bool = True,
):
    planning_llm = init_chat_model(PLANNING_MODEL, model_provider="openai", api_key=OPENAI_API_KEY)
    execute_llm = init_chat_model(EXECUTE_MODEL, model_provider="openai", api_key=OPENAI_API_KEY)
    summary_llm = init_chat_model(SUMMARY_MODEL, model_provider="openai", api_key=OPENAI_API_KEY)

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
    graph.add_node("plan_init", plan_init_node)
    graph.add_node("plan_finalize", plan_finalize_node)
    graph.add_node("execute_agent_free", execute_agent_free)
    graph.add_node("execute_agent_plan", execute_agent_plan)
    graph.add_node("execute_agent_followup", execute_agent_followup)
    graph.add_node("summary_agent_simple", summary_agent_simple)
    graph.add_node("summary_agent_complex", summary_agent_complex)
    graph.add_node("summary_agent_meta", summary_agent_meta)

    if use_context_compression:
        # Separate context_summary node per terminal branch so the graph stays acyclic
        # and the per-branch streaming label remains meaningful.
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
            # Execution routes go through plan_init so every run is recorded in
            # plan.md before any work starts, whatever route it took.
            "execute_agent_free": "plan_init",
            "execute_agent_followup": "plan_init",
            "summary_agent_meta": "summary_agent_meta",
        },
    )

    # Complex branch: plan → human_chat → (revise loop | approve → execute_plan → summary_complex)
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

    # Every execution route: plan_init (write + display) → execute → plan_finalize
    # (reconcile + record outcome) → the branch's summary agent.
    graph.add_conditional_edges(
        "plan_init",
        _route_after_plan_init,
        {
            "execute_agent_plan": "execute_agent_plan",
            "execute_agent_free": "execute_agent_free",
            "execute_agent_followup": "execute_agent_followup",
        },
    )
    graph.add_edge("execute_agent_plan", "plan_finalize")
    graph.add_edge("execute_agent_free", "plan_finalize")
    graph.add_edge("execute_agent_followup", "plan_finalize")
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
