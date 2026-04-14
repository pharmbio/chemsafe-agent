from typing import Optional
from langchain.agents import create_agent
from core.prompts.prompts import SUMMARY_AGENT_SYSTEM_PROMPT
from core.tools.python_executor import python_executor, reset_python_state
from core.tools.read_files import read_files



def build_summary_agent(
    llm
):
    """
    Create core planning agent.
    
    Args:
        llm: Language model instance

    Returns:
        Enhanced or standard planning agent
    """

    tools = [python_executor, reset_python_state, read_files]
    summary_agent = create_agent(
            model=llm,
            tools=tools,
            name='summary_agent',
            system_prompt=SUMMARY_AGENT_SYSTEM_PROMPT,
        )
    return summary_agent

