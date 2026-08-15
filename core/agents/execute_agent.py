from langgraph.prebuilt import create_react_agent

from core.agents.context import AgentGraphState
from core.prompts.prompts import EXECUTE_AGENT_SYSTEM_PROMPT
from core.tools.plan_tools import plan_status, plan_update
from core.tools.python_executor import python_executor, reset_python_state
from core.tools.read_files import read_files


def build_execute_agent(
    llm,
    pre_model_hook=None,
    *,
    name: str = "execute_agent",
    prompt: str | None = None,
):
    """
    Create an execute agent.

    Args:
        llm: Language model instance.
        pre_model_hook: Optional pre-model hook.
        name: Node/agent name used by LangGraph.
        prompt: System prompt. Defaults to the plan-following prompt.
    """

    tools = [
        python_executor,
        reset_python_state,
        read_files,
        plan_status,
        plan_update,
    ]
    return create_react_agent(
        model=llm,
        tools=tools,
        name=name,
        prompt=prompt if prompt is not None else EXECUTE_AGENT_SYSTEM_PROMPT,
        pre_model_hook=pre_model_hook,
        state_schema=AgentGraphState,
    )
