# agent-system-a

The primary system end users talk to. A LangGraph Supervisor routes each
question to `ProtocolSpecialist` or `SchemaSpecialist`, both grounded via
tools served by `mcp-server`. Wrapped in FastAPI, with LangGraph's
`AsyncSqliteSaver` giving every conversation persistent, multi-turn memory.

## Run standalone (outside Docker)

Requires `mcp-server` already running and reachable (see its own README).

```bash
cd services/agent-system-a
pip install -r requirements.txt
export MCP_SERVER_URL=http://localhost:8001/mcp   # or wherever mcp-server is listening
export GROQ_API_KEY=...
export GOOGLE_API_KEY=...
uvicorn api.main:app --reload --port 8000
```

By default `DATA_DIR` falls back to `../../data` (the repo root's `data/`
folder) — override with the `DATA_DIR` env var if needed.

## Run via Docker

From the repo root: `docker compose up agent-system-a` (brings up its
dependencies too). See the root `docker-compose.yml` for the full
environment variable list.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | liveness check |
| POST | `/conversations` | create a new conversation |
| GET | `/conversations` | list conversations |
| GET | `/conversations/{id}` | full message history for one conversation |
| POST | `/conversations/{id}/messages` | send a message, get the agent's reply |
| DELETE | `/conversations/{id}` | delete a conversation and its uploaded documents |
| POST | `/conversations/{id}/documents` | upload a `.md`/`.txt`/`.schema` file into a conversation |
| GET | `/conversations/{id}/documents` | list documents uploaded to a conversation |
| DELETE | `/conversations/{id}/documents/{doc_id}` | delete one uploaded document |

## Dev CLI

`python agent/run.py "<question>"` — asks the graph one question directly
and prints the node-by-node trace, without going through the HTTP API.
Also needs `mcp-server` reachable at `MCP_SERVER_URL`.
