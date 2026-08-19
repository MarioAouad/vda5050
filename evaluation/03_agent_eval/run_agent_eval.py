"""
Run: python 03_agent_eval/run_agent_eval.py
Requires a live mcp-server (MCP_SERVER_URL, default http://localhost:8001/mcp)
AND a live agent-system-b (AGENT_B_URL, default http://localhost:8002) --
several test cases route to DiagnosticsSpecialist, which makes a real HTTP
call to Agent System B, same as evaluate_multi_agent.py already does for
the MCP connection. Neither needs Docker specifically -- `docker compose
up vector-db mcp-server agent-system-b -d`, or run them as plain local
processes, either works.

Implements the six things Session 11 (Part 5, "Six Things to Measure on
an Agent") says to measure, using this project's actual graph:
  1. Routing accuracy       -- confusion matrix, expected vs actual route
  2. Tool accuracy          -- name correctness, invented-tool detection,
                                broken down PER TOOL, not just an average
  3. Trajectory match       -- in-order match (session 11's own stated
                                practical default over exact match)
  4. Task completion        -- fact-checklist coverage in the final answer
  5. Step efficiency        -- actual graph steps / minimum_steps
  6. (Cost/latency per task -- logged as call_count/duration but not the
     focus here, since Groq/Gemini token costs are already covered in
     docs/PROJECT_GUIDE.md 4.7's rate-limit discussion)

This does not replace evaluate_multi_agent.py -- it supersedes it with
the same core routing check plus the additional metrics above, and folds
in Agent System B + guardrail-BLOCK-row cases explicitly.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_EVAL_ROOT = _THIS_DIR.parent
_REPO_ROOT = _EVAL_ROOT.parent
_AGENT_A_DIR = _REPO_ROOT / "services" / "agent-system-a"
for p in (_EVAL_ROOT / "00_shared", _AGENT_A_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / ".env")

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.messages import HumanMessage

from agent.graph import build_graph
from metrics import build_confusion_matrix, save_result

RESULTS_DIR = _THIS_DIR / "results"
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp")

with open(_EVAL_ROOT / "00_shared" / "test_set_agent.json") as f:
    TEST_DATA = json.load(f)
VALID_TOOLS = set(TEST_DATA["valid_tools"])
CASES = TEST_DATA["cases"]


async def run_case(graph, case: dict) -> dict:
    trail: list[str] = []
    tool_calls: list[tuple[str, dict]] = []
    final_response = ""
    start = time.monotonic()
    error_msg = None

    try:
        async for event in graph.astream({"messages": [HumanMessage(content=case["query"])]}):
            for node, state in event.items():
                trail.append(node)
                msgs = state.get("messages") or []
                for m in msgs:
                    for tc in getattr(m, "tool_calls", []) or []:
                        tool_calls.append((tc["name"], tc.get("args", {})))
                if node in ("InputGuard", "OutputGuard") and msgs and "GUARDRAIL_BLOCK" in msgs[-1].content:
                    trail.append("GUARDRAIL_BLOCK")
                    final_response = msgs[-1].content
                elif msgs and node not in ("protocol_tools", "schema_tools", "diagnostics_tools"):
                    final_response = msgs[-1].content
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"

    duration = time.monotonic() - start

    agent_nodes = [n for n in trail if n.endswith("Specialist") or n == "SmallTalk"]
    if "GUARDRAIL_BLOCK" in trail:
        primary_route = "END"
    elif agent_nodes:
        primary_route = agent_nodes[0]
    else:
        primary_route = "FINISH" if not error_msg else "ERROR"

    return {
        "trail": trail, "tool_calls": tool_calls, "answer": final_response,
        "primary_route": primary_route, "duration_sec": round(duration, 2),
        "error": error_msg, "steps": len(trail),
    }


def _tool_accuracy(actual_tools: list[str], expected_tools: list[str]) -> dict:
    invented = [t for t in actual_tools if t not in VALID_TOOLS]
    name_correct = set(actual_tools) == set(expected_tools) if expected_tools or actual_tools else True
    return {"name_correct": name_correct, "invented_tools": invented, "actual_tools": actual_tools, "expected_tools": expected_tools}


def _in_order_match(actual_route_list: list[str], expected: list[str]) -> bool:
    it = iter(actual_route_list)
    return all(step in it for step in expected)


async def main():
    print(f"Connecting to mcp-server at {MCP_SERVER_URL} ...")
    client = MultiServerMCPClient({"vda5050": {"transport": "streamable_http", "url": MCP_SERVER_URL, "timeout": 30}})
    tools = await client.get_tools()
    graph = build_graph(tools)

    rows = []
    route_pairs = []  # for confusion matrix
    per_tool_tally: dict[str, dict[str, int]] = {t: {"expected": 0, "correct": 0} for t in VALID_TOOLS}
    invented_tool_count = 0

    for case in CASES:
        print(f"\n[{case['id']}] {case['query'][:70]}")
        r = await run_case(graph, case)
        actual_tool_names = [t[0] for t in r["tool_calls"]]

        expected_route = case["expected_route"]
        route_pairs.append((expected_route, r["primary_route"]))

        tool_acc = _tool_accuracy(actual_tool_names, case["expected_tools"])
        invented_tool_count += len(tool_acc["invented_tools"])
        for t in case["expected_tools"]:
            if t in per_tool_tally:
                per_tool_tally[t]["expected"] += 1
                if t in actual_tool_names:
                    per_tool_tally[t]["correct"] += 1

        # Practical "in-order" trajectory check (Session 11's stated default over exact match):
        # does the expected specialist node actually appear in the trail, in position,
        # rather than the route only showing up as a same-turn coincidence.
        expected_trajectory = [] if expected_route in ("END", "FINISH") else [expected_route]
        trajectory_ok = _in_order_match(r["trail"], expected_trajectory)
        facts_found = sum(1 for f in case["facts"] if f.lower() in (r["answer"] or "").lower())
        facts_needed = len(case["facts"])
        step_efficiency = round(r["steps"] / case["minimum_steps"], 2) if case["minimum_steps"] else None

        row = {
            "id": case["id"], "query": case["query"],
            "expected_route": expected_route, "actual_route": r["primary_route"],
            "route_correct": expected_route == r["primary_route"],
            "tool_accuracy": tool_acc,
            "trajectory_in_order": trajectory_ok,
            "facts_found": facts_found, "facts_needed": facts_needed,
            "task_completion": (facts_found / facts_needed) if facts_needed else None,
            "steps": r["steps"], "minimum_steps": case["minimum_steps"], "step_efficiency": step_efficiency,
            "duration_sec": r["duration_sec"], "error": r["error"],
            "answer_snippet": (r["answer"] or "")[:150],
        }
        print(f"  route: expected={expected_route} actual={r['primary_route']}  route_ok={row['route_correct']}  "
              f"tools_ok={tool_acc['name_correct']}  facts={facts_found}/{facts_needed}  steps={r['steps']} (min {case['minimum_steps']})")
        rows.append(row)

    confusion = build_confusion_matrix(route_pairs)
    tool_name_accuracy = sum(1 for r in rows if r["tool_accuracy"]["name_correct"]) / len(rows)
    trajectory_accuracy = sum(1 for r in rows if r["trajectory_in_order"]) / len(rows)
    task_rows = [r for r in rows if r["task_completion"] is not None]
    mean_task_completion = round(sum(r["task_completion"] for r in task_rows) / len(task_rows), 4) if task_rows else None
    mean_step_efficiency = round(sum(r["step_efficiency"] for r in rows if r["step_efficiency"]) / len([r for r in rows if r["step_efficiency"]]), 3)

    per_tool_summary = {
        t: {"expected": v["expected"], "correct": v["correct"],
            "accuracy": round(v["correct"] / v["expected"], 3) if v["expected"] else None}
        for t, v in per_tool_tally.items()
    }

    block_row_cases = [r for r in rows if r["expected_route"] in ("END", "FINISH")]
    block_row_accuracy = round(sum(1 for r in block_row_cases if r["route_correct"]) / len(block_row_cases), 3) if block_row_cases else None

    summary = {
        "routing_confusion_matrix": confusion,
        "tool_name_accuracy_overall": round(tool_name_accuracy, 3),
        "invented_tool_calls_total": invented_tool_count,
        "per_tool_accuracy": per_tool_summary,
        "trajectory_in_order_accuracy": round(trajectory_accuracy, 3),
        "mean_task_completion_fact_coverage": mean_task_completion,
        "mean_step_efficiency": mean_step_efficiency,
        "block_row_accuracy_guardrails_and_offtopic": block_row_accuracy,
        "n_cases": len(rows),
        "rows": rows,
    }

    print("\n" + "=" * 78 + "\nSUMMARY\n" + "=" * 78)
    print(f"Routing accuracy: {confusion['accuracy']:.0%}  (confusion matrix diagonal)")
    print(f"Tool name accuracy: {tool_name_accuracy:.0%}   Invented tool calls: {invented_tool_count}")
    print(f"Per-tool accuracy: {json.dumps(per_tool_summary, indent=2)}")
    print(f"Trajectory in-order match: {trajectory_accuracy:.0%}")
    print(f"Mean task completion (fact coverage): {mean_task_completion}")
    print(f"Mean step efficiency (actual/minimum): {mean_step_efficiency}")
    print(f"BLOCK row accuracy (off-topic + guardrail cases correctly blocked): {block_row_accuracy}")

    save_result(summary, RESULTS_DIR, "agent_eval")


if __name__ == "__main__":
    asyncio.run(main())
