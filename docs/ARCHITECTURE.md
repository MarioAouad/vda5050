# Architecture

This expands on `docs/PROPOSAL.md` Section 2 with the concrete state of the
implementation, including what changed during the services/* restructuring.

## Containers

| Container | What it runs | Port (published) |
|---|---|---|
| `agent-system-a` | LangGraph Supervisor + ProtocolSpecialist/SchemaSpecialist, wrapped in FastAPI | 8000 |
| `agent-system-b` | Google ADK Fleet Diagnostics & Validation agent, own FastAPI wrapper — **scaffold, logic pending** | 8002 |
| `mcp-server` | FastMCP server: retrieval + document-management tools, streamable-HTTP transport | 8001 |
| `vector-db` | Qdrant, standalone (official `qdrant/qdrant` image, not embedded) | 6333 |
| `chatbot-ui` | Static frontend served by nginx | 8080 (host) → 80 (container) |

Start all five with `docker compose up --build` from the repo root.

## How the pieces talk to each other

```
browser → chatbot-ui (nginx, static) ─┐
                                       ▼
                          agent-system-a (FastAPI + LangGraph, 3 specialists)
                                       │  MCP tool calls (streamable-HTTP)
                                       ▼
                                  mcp-server (FastMCP)
                                       │  Qdrant client
                                       ▼
                                  vector-db (Qdrant)

agent-system-a  ──HTTP (validate-payload / lookup-error)──►  agent-system-b
```

`chatbot-ui`'s JS calls `agent-system-a` directly on its published port
(`http://localhost:8000`) rather than through nginx — see `services/chatbot-ui/index.html`'s
`API` constant. `agent-system-a` never calls `chatbot-ui`.

## What changed in the services/* restructuring (11 Aug)

The single-repo, single-container version connected to `mcp_server` as a
**stdio subprocess** (`api/main.py` spawned `python -m mcp_server.server`
directly and talked to it over stdin/stdout). That only works when both
processes share a filesystem and a parent/child process relationship —
it can't cross a container boundary. Splitting `mcp-server` into its own
container required a real code change, not just a folder move:

- `services/mcp-server/server.py` now starts with
  `mcp.run(transport="streamable-http")` instead of the stdio default,
  bound to `MCP_HOST`/`MCP_PORT` (`0.0.0.0:8001` inside Docker).
- `services/agent-system-a/api/main.py` now connects via
  `langchain_mcp_adapters.client.MultiServerMCPClient` with a
  `streamable_http` connection to `MCP_SERVER_URL`
  (`http://mcp-server:8001/mcp` inside Docker), instead of `stdio_client`.
- Internal imports inside `mcp-server` changed from `mcp_server.core.X` to
  `core.X`, since that package no longer lives inside a shared repo-root
  namespace — each service's container root is its own `/app`.

Qdrant moved from an embedded, single-process local file
(`QdrantClient(path=...)`) to the standalone `vector-db` container
(`QdrantClient(url="http://vector-db:6333")`). `services/mcp-server/core/vectorstore.py`
picks whichever is configured via `QDRANT_URL` (unset → embedded fallback,
for running mcp-server outside Docker without spinning up a separate
Qdrant container).

Both services read a shared `DATA_DIR` (mounted from the repo's `./data`
folder by `docker-compose.yml`) rather than assuming they live next to a
`data/` sibling folder, which was true in the single-repo layout but isn't
now that each service is under `services/<name>/`.

## Known gaps (see the roadmap in the top-level README)

- **Evaluation**: methodology is written (`docs/EVALUATION.md`) but the
  actual retrieval-metrics / RAGAS-or-judge / routing-accuracy runs
  haven't executed yet. This is the one remaining piece before the demo.
- **Known limitation, not yet resolved**: deleting an uploaded document
  (per-conversation or global) correctly removes it from future searches
  and from brand-new conversations, but a conversation that already
  discussed that document's content before the delete can keep answering
  about it from its own checkpointed message history, since LangGraph's
  per-thread memory holds the earlier answer independently of whatever is
  still in Qdrant. Not a data-leak (nothing new is retrieved) — just a
  "the chat remembers what it already said" edge case worth naming in the
  report rather than silently leaving unexplained.

## SSE streaming — design notes

`POST /conversations/{id}/messages/stream` streams **per graph step**
(`stream_mode="updates"`), not per LLM token. Each specialist's own chat
model call (`agent/graph.py`'s `agent_node`) is a plain synchronous
`.invoke()` on purpose — it needs the complete response before it can
check `.tool_calls` and decide whether to loop back through a `ToolNode`
or stop, which a partial token stream can't answer. Switching those calls
to `.astream()` would mean either buffering the full response anyway
before reading tool_calls (no actual benefit) or restructuring the whole
ReAct loop. Step-level streaming needed none of that: LangGraph emits a
real event over the connection the instant each node (`Supervisor`,
`ProtocolSpecialist`, a `ToolNode`, `OutputGuard`, …) actually finishes —
genuinely incremental, just chunked by graph step rather than by token.
The frontend shows each step's status line live (`Routing your
question…`, `Searching VDA 5050 protocol rules…`, …) while waiting for
the final answer frame.
