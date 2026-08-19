"""
Local dev CLI: ask the agent graph one question and print the trace.
Requires mcp-server to already be running and reachable — either
`python -m mcp_server.server` from services/mcp-server locally, or the
`mcp-server` container from docker-compose (in which case set
MCP_SERVER_URL=http://localhost:8001/mcp, matching its published port).
"""
import asyncio
import os
import sys
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT_GUESS = _SERVICE_DIR.parent.parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT_GUESS / ".env")

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.messages import HumanMessage

from agent.graph import build_graph

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp")


async def main():
    if len(sys.argv) < 2:
        print("Usage: python agent/run.py <question>")
        sys.exit(1)

    question = sys.argv[1]

    print(f"Connecting to mcp-server at {MCP_SERVER_URL} ...")
    client = MultiServerMCPClient({
        "vda5050": {"transport": "streamable_http", "url": MCP_SERVER_URL, "timeout": 30}
    })
    tools = await client.get_tools()
    print(f"Loaded {len(tools)} tools: {[t.name for t in tools]}")

    print("Building agent graph...")
    graph = build_graph(tools)

    print(f"\nQuestion: {question}\n")

    async for event in graph.astream({"messages": [HumanMessage(content=question)]}):
        for node, state in event.items():
            print(f"--- Node: {node} ---")
            if "messages" in state and len(state["messages"]) > 0:
                print(state["messages"][-1].content)
            elif "next" in state:
                print(f"Supervisor routed to: {state['next']}")

    print("\nFinished execution.")


if __name__ == "__main__":
    asyncio.run(main())
