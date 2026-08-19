"""
FastAPI chatbot backend. On startup (see `lifespan` below) this connects to
the MCP server ONCE and keeps that connection open for as long as the app
runs — unlike run.py, which connects, asks one question, and disconnects.

Agent System A calls the mcp-server over the network (streamable-HTTP)
rather than spawning it as a stdio subprocess — mcp-server runs in its own
container per the architecture plan (docs/ARCHITECTURE.md, Section 2.3).

Conversation memory comes from LangGraph's checkpointer: every request
passes a `thread_id`, and the graph automatically loads/saves that
conversation's full state around it.
"""
import os
import sys
import json
import uuid
from pathlib import Path
from contextlib import asynccontextmanager, AsyncExitStack

_SERVICE_DIR = Path(__file__).resolve().parent.parent          # services/agent-system-a
_REPO_ROOT_GUESS = _SERVICE_DIR.parent.parent                   # repo root, for local (non-Docker) dev
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT_GUESS / ".env")  # no-op in Docker, where env vars come from docker-compose

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent.graph import build_graph, _as_text
from api import db

# DATA_DIR: local dev defaults to the repo-root data/ folder; docker-compose
# mounts ./data to /app/data and sets DATA_DIR=/app/data instead (shared
# with mcp-server, since checkpoints/uploads/db all live under data/).
DATA_DIR = Path(os.getenv("DATA_DIR", str(_REPO_ROOT_GUESS / "data")))
CHECKPOINT_DB_PATH = str(DATA_DIR / "checkpoints.db")
ALLOWED_UPLOAD_EXTENSIONS = {".md", ".txt", ".schema"}

# Reachable as http://mcp-server:8001/mcp inside docker-compose's network;
# defaults to localhost for running this service outside Docker against a
# locally-started mcp-server.
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:8080")


async def _call_mcp_tool(app: FastAPI, name: str, args: dict) -> str:
    """
    Call an MCP tool directly (ingest/delete), bypassing the LLM/graph.
    Handles both possible LangChain tool return shapes (plain string, or a
    list of text/content blocks) since that's what the adapter documents.
    """
    tool = app.state.mcp_tools_by_name[name]
    result = await tool.ainvoke(args)
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for item in result:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()

    async with AsyncExitStack() as stack:
        checkpointer = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB_PATH)
        )

        mcp_client = MultiServerMCPClient({
            "vda5050": {
                "transport": "streamable_http",
                "url": MCP_SERVER_URL,
                "timeout": 30,
            }
        })
        tools = await mcp_client.get_tools()

        app.state.mcp_client = mcp_client
        app.state.mcp_tools_by_name = {t.name: t for t in tools}
        app.state.graph = build_graph(tools, checkpointer=checkpointer)

        print(f"Startup complete — connected to mcp-server at {MCP_SERVER_URL}, "
              f"loaded {len(tools)} MCP tools, graph ready.")
        yield


app = FastAPI(title="VDA 5050 Oracle Chatbot — Agent System A", lifespan=lifespan)

# chatbot-ui now runs as its own container/origin, so this needs an
# explicit CORS allowance instead of being served from the same origin
# via StaticFiles (which the previous single-container setup used).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


class NewMessage(BaseModel):
    message: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/conversations")
async def create_conversation():
    return db.create_conversation()


@app.get("/conversations")
async def list_conversations():
    return db.list_conversations()


@app.get("/conversations/{conversation_id}")
async def get_conversation_history(conversation_id: str):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    config = {"configurable": {"thread_id": conversation_id}}
    state = await app.state.graph.aget_state(config)

    messages = []
    for m in state.values.get("messages", []):
        msg_type = getattr(m, "type", None)
        if msg_type == "human":
            role = "user"
        elif msg_type == "ai":
            role = "assistant"
        else:
            continue
        if not m.content:
            continue
        messages.append({"role": role, "content": m.content})

    return {**conv, "messages": messages}


# --- Single source of truth for running the graph ---------------------------
#
# Before this, POST /messages called graph.ainvoke() and POST
# /messages/stream separately called graph.astream() — two different ways
# of driving the exact same graph, each with its own copy of "build the
# input state, check the conversation exists, touch the title afterward."
# That's the kind of duplication that quietly drifts: a fix applied to one
# path and forgotten in the other. Now there is exactly one invocation
# path (astream(..., stream_mode="updates")); ainvoke is not called
# anywhere in this file. The two endpoints below differ only in what they
# DO with the events this yields — one collects them into a single JSON
# reply, the other forwards each one as an SSE frame — not in how the
# graph itself is run.
_NODE_STATUS_MESSAGES = {
    "InputGuard": "Checking your message…",
    "Supervisor": "Routing your question…",
    "ProtocolSpecialist": "Searching VDA 5050 protocol rules…",
    "SchemaSpecialist": "Searching VDA 5050 JSON schemas…",
    "DiagnosticsSpecialist": "Asking the diagnostics agent…",
    "protocol_tools": "Running a search…",
    "schema_tools": "Running a search…",
    "diagnostics_tools": "Running a search…",
    "OutputGuard": "Finalizing the answer…",
}


async def _run_graph(app: FastAPI, conversation_id: str, message: str):
    """
    Yields one dict per graph node update: {"node", "status", "text"}.
    "status" is a human-readable label for that node (or None if this node
    has no status text). "text" is only set when this update is a real,
    final, user-facing answer — not a tool-call placeholder, not raw tool
    output — so callers never have to re-derive that filtering logic
    themselves.
    """
    config = {"configurable": {"thread_id": conversation_id}}
    input_state = {
        "messages": [HumanMessage(content=message)],
        "iterations": 0,
        "tool_steps": 0,
        "conversation_id": conversation_id,
    }
    async for step in app.state.graph.astream(input_state, config=config, stream_mode="updates"):
        for node_name, update in step.items():
            text = None
            msgs = update.get("messages") if isinstance(update, dict) else None
            if msgs:
                last = msgs[-1]
                candidate = _as_text(getattr(last, "content", ""))
                if candidate and getattr(last, "type", None) == "ai" and not getattr(last, "tool_calls", None):
                    text = candidate
            yield {"node": node_name, "status": _NODE_STATUS_MESSAGES.get(node_name), "text": text}


@app.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, payload: NewMessage):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    final_reply = None
    async for event in _run_graph(app, conversation_id, payload.message):
        if event["text"]:
            final_reply = event["text"]

    reply = final_reply or "The graph finished without producing a reply."
    new_title = payload.message[:50] if conv["title"] == "New conversation" else None
    db.touch_conversation(conversation_id, title=new_title)

    return {"reply": reply}


# --- SSE streaming endpoint -------------------------------------------------
#
# Per docs/PROPOSAL.md Section 2.5. Design choice worth stating explicitly,
# since it's not the obvious one: this streams per GRAPH STEP (Supervisor
# routed → ProtocolSpecialist searching → answer ready), not per LLM TOKEN.
#
# Why not token-level: every specialist node calls its chat model with a
# plain, synchronous `.invoke()` (see agent/graph.py's agent_node) because
# it needs the complete response up front to inspect `.tool_calls` before
# deciding whether to loop back through a ToolNode or stop — you can't
# make that branching decision on a half-received token stream. Switching
# those calls to `.astream()` would mean either buffering the whole
# response anyway before checking tool_calls (defeating the purpose) or
# restructuring the ReAct loop entirely. Step-level streaming needs no
# changes to that logic at all: LangGraph's `stream_mode="updates"` yields
# a real event over the wire the moment each node actually finishes,
# so the connection is genuinely incremental (not one big blob at the end)
# even though each individual node's own text still arrives all at once.
def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@app.post("/conversations/{conversation_id}/messages/stream")
async def send_message_stream(conversation_id: str, payload: NewMessage):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_generator():
        final_reply = None
        try:
            async for event in _run_graph(app, conversation_id, payload.message):
                if event["status"]:
                    yield _sse({"status": event["status"], "node": event["node"]})
                if event["text"]:
                    final_reply = event["text"]
        except Exception as e:
            yield _sse({"error": f"{type(e).__name__}: {e}"})
            return

        reply = final_reply or "The graph finished without producing a reply."
        new_title = payload.message[:50] if conv["title"] == "New conversation" else None
        db.touch_conversation(conversation_id, title=new_title)
        yield _sse({"done": True, "reply": reply})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await _call_mcp_tool(app, "delete_conversation_documents", {"conversation_id": conversation_id})
    for doc in db.list_documents(conversation_id):
        Path(doc["file_path"]).unlink(missing_ok=True)
    db.delete_documents_for_conversation(conversation_id)

    db.delete_conversation(conversation_id)
    return {"status": "deleted"}


@app.post("/conversations/{conversation_id}/documents")
async def upload_document(conversation_id: str, file: UploadFile = File(...)):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Upload .md, .txt, or .schema "
                   f"(convert PDFs/images to .md first).",
        )

    contents = await file.read()
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 text.")

    document_id = str(uuid.uuid4())
    upload_dir = DATA_DIR / "user_uploads" / conversation_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest_path = upload_dir / f"{document_id}{suffix}"
    dest_path.write_bytes(contents)

    summary = await _call_mcp_tool(app, "ingest_document", {
        "text": text, "filename": file.filename,
        "conversation_id": conversation_id, "document_id": document_id,
    })
    try:
        chunk_count = int(summary.split()[1])
    except Exception:
        chunk_count = 0

    return db.create_document(document_id, conversation_id, file.filename, str(dest_path), chunk_count)


@app.get("/conversations/{conversation_id}/documents")
async def list_documents(conversation_id: str):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return db.list_documents(conversation_id)


@app.delete("/conversations/{conversation_id}/documents/{document_id}")
async def delete_document(conversation_id: str, document_id: str):
    doc = db.get_document(document_id)
    if doc is None or doc["conversation_id"] != conversation_id:
        raise HTTPException(status_code=404, detail="Document not found")

    await _call_mcp_tool(app, "delete_document", {"document_id": document_id})

    Path(doc["file_path"]).unlink(missing_ok=True)
    db.delete_document_row(document_id)
    return {"status": "deleted"}


# --- Global (knowledge-base) documents --------------------------------------
#
# Per-conversation uploads above are scoped to one chat and disappear with
# it. These endpoints are the "real" upload path for anything that should
# behave like part of the actual VDA 5050 corpus — a manufacturer's
# factsheet extension, a fleet's custom error codes, etc: ingested once,
# searchable from every conversation, managed from a knowledge-base /
# settings screen rather than a chat window. Same ingest_document MCP tool
# as conversation uploads, just called without a conversation_id (see
# services/mcp-server/server.py's ingest_document docstring for exactly how
# that changes the chunk's metadata and therefore its search scope).
GLOBAL_UPLOAD_DIR = DATA_DIR / "raw_docs" / "global_uploads"


@app.post("/documents")
async def upload_global_document(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Upload .md, .txt, or .schema "
                   f"(convert PDFs/images to .md first).",
        )

    contents = await file.read()
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 text.")

    document_id = str(uuid.uuid4())
    GLOBAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = GLOBAL_UPLOAD_DIR / f"{document_id}{suffix}"
    dest_path.write_bytes(contents)

    # No conversation_id here — this is exactly what makes it a global,
    # every-conversation-visible document rather than a scoped one.
    summary = await _call_mcp_tool(app, "ingest_document", {
        "text": text, "filename": file.filename, "document_id": document_id,
    })
    try:
        chunk_count = int(summary.split()[1])
    except Exception:
        chunk_count = 0

    return db.create_global_document(document_id, file.filename, str(dest_path), chunk_count)


@app.get("/documents")
async def list_global_documents():
    return db.list_global_documents()


@app.delete("/documents/{document_id}")
async def delete_global_document(document_id: str):
    doc = db.get_global_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    await _call_mcp_tool(app, "delete_document", {"document_id": document_id})

    Path(doc["file_path"]).unlink(missing_ok=True)
    db.delete_global_document_row(document_id)
    return {"status": "deleted"}
