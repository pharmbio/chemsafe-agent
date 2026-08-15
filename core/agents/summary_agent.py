from langgraph.prebuilt import create_react_agent

from core.agents.context import AgentGraphState
from core.prompts.prompts import SUMMARY_AGENT_SYSTEM_PROMPT
from core.tools.read_files import read_files
from core.tools.python_executor import python_executor


def build_summary_agent(
    llm,
    pre_model_hook=None,
    *,
    name: str = "summary_agent",
    prompt: str | None = None,
):
    """
    Create a summary agent.

    Args:
        llm: Language model instance.
        pre_model_hook: Optional pre-model hook.
        name: Node/agent name used by LangGraph.
        prompt: System prompt. Defaults to the complex-task summary prompt.
    """

    tools = [read_files, python_executor]
    return create_react_agent(
        model=llm,
        tools=tools,
        name=name,
        prompt=prompt if prompt is not None else SUMMARY_AGENT_SYSTEM_PROMPT,
        pre_model_hook=pre_model_hook,
        state_schema=AgentGraphState,
    )
