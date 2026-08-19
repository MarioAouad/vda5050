"""
Agent System B — Fleet Diagnostics & Validation Agent.

Two deterministic tools, per the proposal (now living in app/tools.py so
both call paths below share one implementation):
  - validate_payload_tool: checks a JSON payload against the real VDA 5050
    JSON Schema for the given topic (data/raw_docs/json_schemas/).
  - lookup_error_tool: returns the standard's own defined meaning and
    handling guidance for a given errorType, sourced from spec section
    6.6.5.4 (see app/data.py — every entry there is transcribed from the
    spec table, not generated).

Two ways to call them:
  - POST /validate-payload, POST /lookup-error — raw, structured, no LLM
    involved. Kept for callers that want to skip the LLM routing layer
    entirely and pass already-structured arguments (e.g. direct testing,
    or a future caller that already knows exactly which tool it needs).
    agent-system-a no longer calls these directly — see below.
  - POST /agent/ask — what agent-system-a actually calls now (see
    ask_diagnostics_agent in services/agent-system-a/agent/graph.py). Goes
    through app/agent.py's Google ADK LlmAgent, which decides for itself
    which tool(s) to call from a plain natural-language question.
"""
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent import run_diagnostics_agent
from app.data import SCHEMA_FILES
from app.tools import lookup_error_tool, validate_payload_tool

DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent.parent / "data")))
SCHEMAS_DIR = DATA_DIR / "raw_docs" / "json_schemas"

app = FastAPI(title="VDA 5050 Fleet Diagnostics & Validation Agent — Agent System B")


def _new_request_id() -> str:
    return str(uuid.uuid4())


@app.get("/health")
async def health():
    return {"status": "ok", "schemas_dir_exists": SCHEMAS_DIR.exists()}


# --- Tool 1: schema validation (raw, no LLM) -------------------------------

class ValidatePayloadRequest(BaseModel):
    schema_name: str  # one of SCHEMA_FILES' keys, e.g. "order", "state"
    payload: dict
    request_id: str = Field(default_factory=_new_request_id)


class ValidatePayloadResponse(BaseModel):
    request_id: str
    schema_name: str
    valid: bool
    errors: list[str]


@app.post("/validate-payload", response_model=ValidatePayloadResponse)
async def validate_payload(req: ValidatePayloadRequest):
    if req.schema_name not in SCHEMA_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown schema_name '{req.schema_name}'. Valid options: {sorted(SCHEMA_FILES)}",
        )
    result = validate_payload_tool(req.schema_name, req.payload)
    return ValidatePayloadResponse(
        request_id=req.request_id,
        schema_name=req.schema_name,
        valid=result["valid"],
        errors=result["errors"],
    )


# --- Tool 2: error-code lookup (raw, no LLM) -------------------------------

class ErrorLookupRequest(BaseModel):
    error_type: str  # e.g. "NODE_UNREACHABLE"
    request_id: str = Field(default_factory=_new_request_id)


class ErrorLookupResponse(BaseModel):
    request_id: str
    error_type: str
    found: bool
    level: str | None = None
    level_meaning: str | None = None
    robot_behavior: str | None = None
    description: str | None = None
    typical_reference: str | None = None
    report_duration: str | None = None


@app.post("/lookup-error", response_model=ErrorLookupResponse)
async def lookup_error(req: ErrorLookupRequest):
    result = lookup_error_tool(req.error_type)
    if not result["found"]:
        return ErrorLookupResponse(request_id=req.request_id, error_type=req.error_type, found=False)
    return ErrorLookupResponse(request_id=req.request_id, **result)


# --- ADK agent entry point --------------------------------------------------

class AgentAskRequest(BaseModel):
    query: str
    request_id: str = Field(default_factory=_new_request_id)


class AgentAskResponse(BaseModel):
    request_id: str
    reply: str


@app.post("/agent/ask", response_model=AgentAskResponse)
async def agent_ask(req: AgentAskRequest):
    """
    Natural-language entry point, routed through the ADK agent in
    app/agent.py rather than calling validate-payload/lookup-error
    directly. The agent decides which tool(s) to call.
    """
    try:
        reply = await run_diagnostics_agent(req.query, request_id=req.request_id)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Diagnostics agent failed: {type(e).__name__}: {e}",
        )
    return AgentAskResponse(request_id=req.request_id, reply=reply)
