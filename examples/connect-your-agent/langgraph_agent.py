"""Connecting an existing LangGraph agent (illustrative; needs `pip install langgraph langchain-openai`).

Two changes to the customer's code: decorate the tools with @genai_evals.tool (on top of LangChain's @tool), and
wrap the graph call in a function decorated with @genai_evals.agent. The genai-evals session id becomes the
LangGraph thread id, so the checkpointer keeps the conversation exactly as it does in production.
"""
from langchain_core.tools import tool as lc_tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

import genai_evals


@lc_tool
@genai_evals.tool
def lookup_order(order_id: str) -> dict:
    """Look up an order by id."""
    return {"status": "delivered", "amount": 900}       # the customer's real implementation goes here


graph = create_react_agent("openai:gpt-5-mini", tools=[lookup_order], checkpointer=MemorySaver())


@genai_evals.agent
def support_agent(message: str, session_id: str, user: str | None = None) -> str:
    out = graph.invoke({"messages": [("user", message)]}, config={"configurable": {"thread_id": session_id}})
    return out["messages"][-1].content
