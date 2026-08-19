"""
Tests for the new pieces: app/tools.py (the extracted, shared tool
functions) and app/agent.py (the ADK wrapper). No live LLM call is made
here — GOOGLE_API_KEY may not be set in CI, and these tests only need to
confirm the deterministic tool functions are correct and that the ADK
agent is built and wired to the right tools, not that Gemini responds
sensibly (that's what TEST_CHECKLIST.md section 3, run against a live
container, is for).

Run from services/agent-system-b/: pytest
"""
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_APP_DIR))

from app.tools import lookup_error_tool, validate_payload_tool


# --- app/tools.py — same behavior the old inline main.py logic had --------

def test_validate_payload_tool_valid_order():
    payload = {
        "headerId": 1, "timestamp": "2026-08-13T12:00:00.000Z", "version": "2.0.0",
        "manufacturer": "RobotCorp", "serialNumber": "ABC123", "orderId": "order-1",
        "orderUpdateId": 0, "nodes": [], "edges": [],
    }
    result = validate_payload_tool("order", payload)
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_payload_tool_invalid_type():
    result = validate_payload_tool("order", {"headerId": "should-be-a-number"})
    assert result["valid"] is False
    assert any("headerId" in e for e in result["errors"])


def test_validate_payload_tool_unknown_schema_name():
    result = validate_payload_tool("not_a_real_schema", {})
    assert result["valid"] is False
    assert "Unknown schema_name" in result["errors"][0]


def test_lookup_error_tool_known_type():
    result = lookup_error_tool("NODE_UNREACHABLE")
    assert result["found"] is True
    assert result["level"] == "CRITICAL"


def test_lookup_error_tool_case_insensitive():
    assert lookup_error_tool("node_unreachable")["found"] is True


def test_lookup_error_tool_unknown_type():
    result = lookup_error_tool("BANANA_ERROR")
    assert result["found"] is False


# --- app/agent.py — ADK agent is built and wired to the right tools -------

def test_adk_agent_imports_and_has_both_tools():
    from app.agent import root_agent
    tool_names = {t.__name__ for t in root_agent.tools}
    assert tool_names == {"validate_payload_tool", "lookup_error_tool"}


def test_adk_agent_has_an_instruction_and_model():
    from app.agent import root_agent
    assert root_agent.model
    assert "deterministic" in root_agent.instruction.lower()
