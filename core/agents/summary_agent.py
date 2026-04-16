from langgraph.prebuilt import create_react_agent

from core.prompts.prompts import SUMMARY_AGENT_SYSTEM_PROMPT
from core.tools.read_files import read_files
from core.tools.python_executor import python_executor


def build_summary_agent(
    llm,
    pre_model_hook=None,
):
    """
    Create core planning agent.
    
    Args:
        llm: Language model instance

    Returns:
        Enhanced or standard planning agent
    """

    tools = [read_files, python_executor]
    return create_react_agent(
        model=llm,
        tools=tools,
        name="summary_agent",
        prompt=SUMMARY_AGENT_SYSTEM_PROMPT,
        pre_model_hook=pre_model_hook,
    )
