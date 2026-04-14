from typing import Optional
from langchain.agents import create_agent
from core.prompts.prompts import EXECUTE_AGENT_SYSTEM_PROMPT
from core.tools.python_executor import python_executor, reset_python_state
from core.tools.read_files import read_files



def build_execute_agent(
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
    execute_agent = create_agent(
            model=llm,
            tools=tools,
            name='execute_agent',
            system_prompt=EXECUTE_AGENT_SYSTEM_PROMPT,
        )
    return execute_agent

