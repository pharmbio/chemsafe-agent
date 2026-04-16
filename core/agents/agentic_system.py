from __future__ import annotations

import json
from typing import Any, Literal, Optional

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.types import interrupt
from typing_extensions import Annotated, TypedDict

from app.config import (
    APPROVAL_JUDGE_MODEL,
    CONTEXT_SUMMARY_MODEL,
    EXECUTE_MODEL,
    OPENAI_API_KEY,
    PLANNING_MODEL,
    SUMMARY_MODEL,
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


class AgentGraphState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    llm_input_messages: list[BaseMessage]
    user_id: str
    conversation_id: str
    plan_status: Literal["pending", "approved", "revise"]


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


def _route_after_human(state: AgentGraphState) -> Literal["planning_agent", "execute_agent"]:
    return "execute_agent" if state.get("plan_status") == "approved" else "planning_agent"


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
    execute_agent = build_execute_agent(execute_llm, pre_model_hook=pre_model_hook)
    summary_agent = build_summary_agent(summary_llm, pre_model_hook=pre_model_hook)

    graph = StateGraph(AgentGraphState)
    graph.add_node("planning_agent", planning_agent)
    graph.add_node("human_chat", human_chat_node)
    graph.add_node("execute_agent", execute_agent)
    graph.add_node("summary_agent", summary_agent)
    if use_context_compression:
        graph.add_node("context_summary", _compress_context)

    graph.add_edge(START, "planning_agent")
    graph.add_edge("planning_agent", "human_chat")
    graph.add_conditional_edges(
        "human_chat",
        _route_after_human,
        {
            "planning_agent": "planning_agent",
            "execute_agent": "execute_agent",
        },
    )
    graph.add_edge("execute_agent", "summary_agent")
    if use_context_compression:
        graph.add_edge("summary_agent", "context_summary")
        graph.add_edge("context_summary", END)
    else:
        graph.add_edge("summary_agent", END)

    return graph.compile(checkpointer=checkpointer)
