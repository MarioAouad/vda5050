"""
The Google ADK layer for Agent System B.

Before this file: agent-system-a called /validate-payload and /lookup-error
directly as plain HTTP endpoints — a specialist LLM inside agent-system-a's
own LangGraph decided when to call them, but nothing inside agent-system-b
itself was making tool-use decisions.

After this file: agent-system-b has its own ADK LlmAgent that receives a
natural-language request and decides for itself which of the two tools
(app/tools.py) to call and how to phrase the result. The two tools
themselves are unchanged and still fully deterministic — ADK only adds the
decision-making layer on top, exactly as described in README.md's
"Current state" section.

Both call paths remain available (see app/main.py):
  - POST /validate-payload, POST /lookup-error — raw, structured, no LLM.
    agent-system-a keeps using these today; nothing about them changed.
  - POST /agent/ask — new. Natural-language in, goes through this ADK
    agent, natural-language reply out.

Session model: per docs/PROPOSAL.md Section 2.5, Agent System B performs
bounded, single-shot tasks rather than holding a conversation, so each
call gets its own throwaway session (session_id = request_id) instead of a
persistent one — request-level traceability without pretending this is a
stateful chat the way agent-system-a's chatbot is.
"""
import os
import uuid
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.tools import lookup_error_tool, validate_payload_tool

APP_NAME = "vda5050-diagnostics-agent"
# Matches the naming convention already used for the Gemini fallbacks in
# agent-system-a/agent/graph.py (ChatGoogleGenerativeAI fb1/fb2). Override
# with ADK_AGENT_MODEL if you want to point this at a different Gemini
# model without a code change.
AGENT_MODEL = os.getenv("ADK_AGENT_MODEL", "gemini-3.1-flash-lite")

# Requirements 2.5 ("timeouts on every external call, including calls to
# your own other service") and 2.6 ("iteration limits on every agent
# loop") both apply here, same as agent-system-a's MAX_ITERATIONS /
# MAX_TOOL_STEPS in graph.py — this agent's loop needs the same two
# safety nets, just expressed through ADK's own config surface instead of
# LangGraph's.
AGENT_TIMEOUT_SECONDS = float(os.getenv("ADK_AGENT_TIMEOUT_SECONDS", "20"))
MAX_LLM_CALLS = int(os.getenv("ADK_AGENT_MAX_LLM_CALLS", "4"))  # 1 route + up to 2 tool calls + 1 final answer

root_agent = LlmAgent(
    name="fleet_diagnostics_agent",
    model=AGENT_MODEL,
    instruction=(
        "You are the Fleet Diagnostics & Validation Agent for the VDA 5050 "
        "standard. You have two tools: validate_payload_tool checks whether "
        "a JSON payload conforms to a real VDA 5050 schema, and "
        "lookup_error_tool returns the standard's defined severity level and "
        "handling guidance for a given errorType. These are deterministic "
        "checks against the real standard, not opinions — report exactly "
        "what the tools return. Call each tool AT MOST ONCE per distinct "
        "value the user asked about. If lookup_error_tool reports an "
        "errorType was not found, that is the final, correct answer — do "
        "not retry it with different capitalization or spelling. "
        "Always reply in the same language the user's request was written "
        "in — translate your explanation fully into that language, do not "
        "default to any other language. The one exception: JSON field "
        "names, schema/property names, and errorType codes (e.g. headerId, "
        "NODE_UNREACHABLE) are literal identifiers from the standard itself "
        "— keep those exactly as written even inside an otherwise "
        "translated sentence."
    ),
    description="Validates VDA 5050 payloads and looks up VDA 5050 error codes.",
    tools=[validate_payload_tool, lookup_error_tool],
    # Per-invocation timeout — this agent's own equivalent of the
    # httpx.AsyncClient(timeout=10) already used for A's calls into B.
    timeout=AGENT_TIMEOUT_SECONDS,
)

# Caps total model calls within one run — this agent's equivalent of
# graph.py's MAX_ITERATIONS/MAX_TOOL_STEPS, so a confused loop can't spin
# forever burning Gemini calls.
_run_config = RunConfig(max_llm_calls=MAX_LLM_CALLS)

# Created once at import time and reused across requests — Runner and
# InMemorySessionService are async-safe for concurrent calls as long as
# each call gets its own session_id, so there's no need to build a fresh
# one per request (see google/adk-python discussion #3924).
_session_service = InMemorySessionService()
_runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=_session_service)


async def run_diagnostics_agent(user_text: str, request_id: Optional[str] = None) -> str:
    """
    Run one single-shot natural-language query through the ADK agent and
    return its final text reply. Creates and effectively discards a
    throwaway session per call — see module docstring for why.
    """
    request_id = request_id or str(uuid.uuid4())
    user_id = "agent-system-a"  # the only caller today

    await _session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=request_id
    )

    message = types.Content(role="user", parts=[types.Part(text=user_text)])

    final_text = None
    async for event in _runner.run_async(
        user_id=user_id, session_id=request_id, new_message=message, run_config=_run_config
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "text", None):
                    final_text = part.text

    return final_text or "The diagnostics agent did not return a response."
