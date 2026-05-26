from __future__ import annotations

import json
from typing import Any, Literal, Optional

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from typing_extensions import Annotated, TypedDict

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
    build_pre_model_state,
    build_uncompressed_pre_model_state,
    clipped_messages_for_summary,
    extract_json_payload,
    latest_summary_record,
    make_summary_message,
    normalize_memory,
    should_summarize,
)
from core.agents.execute_agent import build_execute_agent
from core.agents.planning_agent import build_planning_agent
from core.agents.summary_agent import build_summary_agent
from core.prompts.prompts import (
    EXECUTE_AGENT_FREE_SYSTEM_PROMPT,
    EXECUTE_AGENT_SYSTEM_PROMPT,
    SUMMARY_AGENT_META_SYSTEM_PROMPT,
    SUMMARY_AGENT_SIMPLE_SYSTEM_PROMPT,
    SUMMARY_AGENT_SYSTEM_PROMPT,
    TASK_CLASSIFIER_SYSTEM_PROMPT,
)


TaskCategory = Literal["simple", "complex", "meta_query"]


class TaskClassification(BaseModel):
    """Structured output for the task classifier."""

    category: TaskCategory = Field(
        description="The classification of the user's latest request."
    )


class AgentGraphState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    llm_input_messages: list[BaseMessage]
    user_id: str
    conversation_id: str
    plan_status: Literal["pending", "approved", "revise"]
    task_category: TaskCategory


SUMMARY_PROMPT = (
    "You update two artifacts for ongoing context.\n"
    "Inputs: existing_summary, existing_memory_json, and new_messages.\n"
    "1) summary: concise narrative of the workflow so far.\n"
    "   Include user goal, key steps, decisions, outputs with file paths, errors, and open questions.\n"
    "2) memory: structured facts for future steps.\n"
    "Return ONLY valid JSON with keys:\n"
    "- summary (string)\n"
    "- memory (object)\n"
    "memory schema:\n"
    "- facts: list of strings\n"
    "- outputs: list of objects with keys {path, description}\n"
    "- decisions: list of strings\n"
    "- open_questions: list of strings\n"
    "No markdown fences. No commentary."
)

_approval_judge_llm = None
_context_summary_llm = None
_task_classifier_llm = None


def _get_approval_judge_llm():
    global _approval_judge_llm
    if _approval_judge_llm is None:
        _approval_judge_llm = init_chat_model(
            APPROVAL_JUDGE_MODEL,
            model_provider="openai",
            api_key=OPENAI_API_KEY,
        )
    return _approval_judge_llm


def _get_context_summary_llm():
    global _context_summary_llm
    if _context_summary_llm is None:
        _context_summary_llm = init_chat_model(
            CONTEXT_SUMMARY_MODEL,
            model_provider="openai",
            api_key=OPENAI_API_KEY,
        )
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


def _judge_plan_feedback(feedback: str) -> Literal["approved", "revise"]:
    if not feedback:
        return "revise"
    llm = _get_approval_judge_llm()
    prompt = (
        "You evaluate a human's feedback on an execution plan.\n"
        "Reply with EXACTLY one word:\n"
        "- APPROVE -> the human explicitly authorizes execution now.\n"
        "- REVISE -> the human requests changes, clarifications, or is uncertain.\n"
        f"Feedback: {feedback}\n"
    )
    try:
        response = llm.invoke(prompt)
        content = getattr(response, "content", str(response)).strip().lower()
    except Exception as exc:
        logger.warning("Approval judge failed; defaulting to revise: %s", exc)
        return "revise"
    if content.startswith("approve"):
        return "approved"
    return "revise"


def _latest_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = getattr(message, "content", "")
            if isinstance(content, str):
                return content
            return str(content)
    return ""


async def task_classifier_node(state: AgentGraphState) -> dict[str, Any]:
    """Classify the latest user request into simple / complex / meta_query."""
    user_text = _latest_user_text(state.get("messages") or [])
    if not user_text:
        return {"task_category": "complex"}

    llm = _get_task_classifier_llm()
    try:
        result: TaskClassification = await llm.ainvoke(
            [
                SystemMessage(content=TASK_CLASSIFIER_SYSTEM_PROMPT),
                HumanMessage(content=user_text),
            ]
        )
        category: TaskCategory = result.category
    except Exception as exc:
        logger.warning("Task classifier failed; defaulting to complex: %s", exc)
        category = "complex"

    return {"task_category": category}


async def _compress_context(state: AgentGraphState) -> dict[str, Any]:
    messages = state.get("messages") or []
    if not messages or not should_summarize(messages):
        return {}

    source_messages = clipped_messages_for_summary(messages)
    _, prev_summary, prev_memory = latest_summary_record(messages)
    llm = _get_context_summary_llm()
    memory_json = json.dumps(prev_memory or {}, ensure_ascii=True)
    context_message = SystemMessage(
        content=(
            "Existing summary:\n"
            f"{prev_summary or ''}\n\n"
            "Existing structured memory JSON:\n"
            f"{memory_json}\n"
        )
    )

    try:
        response = await llm.ainvoke([SystemMessage(content=SUMMARY_PROMPT), context_message] + list(source_messages))
    except Exception as exc:
        logger.warning("Context compression failed: %s", exc)
        return {}

    raw_text = getattr(response, "content", str(response)).strip()
    if not raw_text:
        return {}

    payload = extract_json_payload(raw_text)
    if payload is None:
        summary_text = raw_text
        memory = normalize_memory(prev_memory, prev_memory)
    else:
        summary_text = str(payload.get("summary", "")).strip() or (prev_summary or "")
        memory = normalize_memory(payload.get("memory"), prev_memory)

    if not summary_text:
        return {}

    return {"messages": [make_summary_message(summary_text, memory)]}


def _route_after_classifier(
    state: AgentGraphState,
) -> Literal["planning_agent", "execute_agent_free", "summary_agent_meta"]:
    category = state.get("task_category", "complex")
    if category == "simple":
        return "execute_agent_free"
    if category == "meta_query":
        return "summary_agent_meta"
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

    decision = _judge_plan_feedback((human_input or "").strip())
    if decision == "approved":
        return {"plan_status": "approved"}
    return {
        "messages": [HumanMessage(content=human_input or "Please revise the plan.")],
        "plan_status": "revise",
    }


def approval_ack_node(_: AgentGraphState) -> dict[str, Any]:
    return {
        "messages": [
            AIMessage(
                content="Thanks for approval. I'll start executing the approved plan now.",
                name="approval_ack",
            )
        ]
    }


async def create_app(
    checkpointer,
    *,
    user_request: Optional[str] = None,
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
    graph.add_node("execute_agent_free", execute_agent_free)
    graph.add_node("execute_agent_plan", execute_agent_plan)
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
            "execute_agent_free": "execute_agent_free",
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
    graph.add_edge("approval_ack", "execute_agent_plan")
    graph.add_edge("execute_agent_plan", "summary_agent_complex")

    # Simple branch: execute_free → summary_simple
    graph.add_edge("execute_agent_free", "summary_agent_simple")

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
