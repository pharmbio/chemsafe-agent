from langgraph.prebuilt import create_react_agent

from core.agents.context import AgentGraphState
from core.prompts.prompts import PLANNING_AGENT_SYSTEM_PROMPT



def build_planning_agent(
    llm,
    pre_model_hook=None,
):
    """
    Create core planning agent.

    The planning agent is prompt-only: it holds no tools and loads no skills.
    It reasons from the conversation context and produces the plan for human review.

    Args:
        llm: Language model instance

    Returns:
        Planning agent
    """

    return create_react_agent(
        model=llm,
        tools=[],
        name="planning_agent",
        prompt=PLANNING_AGENT_SYSTEM_PROMPT,
        pre_model_hook=pre_model_hook,
        state_schema=AgentGraphState,
    )
