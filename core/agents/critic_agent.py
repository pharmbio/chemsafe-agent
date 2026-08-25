from langgraph.prebuilt import create_react_agent

from core.agents.context import AgentGraphState
from core.agents.critic_context import critic_pre_model_state
from core.prompts.prompts import CRITIC_AGENT_SYSTEM_PROMPT
from core.tools.plan_tools import plan_status
from core.tools.python_executor import python_executor
from core.tools.read_files import read_files

# Verification, not repair. The critic lands in the *executor's own* interpreter
# session — sessions are keyed by `(user_id, conversation_id)` and every tool
# reads that scope from contextvars — so it can print the value of a variable
# the executor computed rather than trusting the transcript's account of it.
# That is the difference between a critic that checks and one that opines.
#
# It deliberately does not get `plan_update`. The critic supplies the verdict;
# code applies it to the plan file, the same division used everywhere else in
# the execution routine.
CRITIC_TOOLS = [python_executor, read_files, plan_status]


def build_critic_agent(
    llm,
    pre_model_hook=None,
    *,
    response_format,
    name: str = "critic_agent",
    prompt: str | None = None,
):
    """The full-run critic: one review of a finished run, sharing its transcript.

    Used by `simple` and `follow_up`, where the run is one step long and the
    turn it has to judge is the turn it can see. Complex runs use
    `build_stepwise_critic_agent` instead — see the note there.

    Args:
        llm: Language model instance.
        pre_model_hook: Optional pre-model hook.
        response_format: Schema the verdict is forced into.
        name: Node/agent name used by LangGraph.
        prompt: System prompt. Defaults to the critic prompt.
    """

    return create_react_agent(
        model=llm,
        tools=CRITIC_TOOLS,
        name=name,
        prompt=prompt if prompt is not None else CRITIC_AGENT_SYSTEM_PROMPT,
        pre_model_hook=pre_model_hook,
        state_schema=AgentGraphState,
        response_format=response_format,
    )


def build_stepwise_critic_agent(llm, *, response_format, name: str = "critic_agent"):
    """The step-wise critic: isolated, stateless, one step at a time.

    Three differences from the full-run critic above, and each is the point:

    - **No parent state schema.** It is not a node of the main graph. It is
      invoked from inside one with a case file built by `critic_context`, so it
      never receives — and cannot accumulate — the executor's transcript. Its
      tools still scope correctly because they read `(user_id, conversation_id)`
      from contextvars, which the run controller pins for the whole run, not
      from graph state.
    - **No system prompt argument.** The prompt travels as a `SystemMessage` in
      the input instead. LangGraph's structured-response node calls the bare
      model on `state["messages"]`, bypassing both the `prompt` runnable and the
      `pre_model_hook`; a prompt in the message list is present on the call that
      actually produces the verdict.
    - **`checkpointer=False`.** Nothing about one review may survive into the
      next. Without this a nested invocation inherits the parent's checkpointer
      and could resume from a previous pass's state.
    """
    return create_react_agent(
        model=llm,
        tools=CRITIC_TOOLS,
        name=name,
        prompt=None,
        pre_model_hook=critic_pre_model_state,
        response_format=response_format,
        checkpointer=False,
    )
