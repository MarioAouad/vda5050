# mcp-server

A FastMCP server exposing retrieval and document-management tools over
streamable-HTTP, called by `agent-system-a` over the network.

## Tools

| Tool | Purpose |
|---|---|
| `search_protocol_rules` | hybrid (BM25 + dense) + reranked search over the VDA 5050 Markdown spec |
| `search_json_schemas` | same, over the JSON schema files |
| `ingest_document` | chunk + embed a user-uploaded document, tagged with `conversation_id`/`document_id` |
| `delete_document` | remove one uploaded document's chunks |
| `delete_conversation_documents` | remove all chunks uploaded within one conversation |

## Run standalone (outside Docker)

```bash
cd services/mcp-server
pip install -r requirements.txt
export QDRANT_URL=http://localhost:6333   # or omit to use the embedded local-file fallback
python -m server
```

Listens on `http://0.0.0.0:8001/mcp` by default (`MCP_HOST`/`MCP_PORT` to
override). `DATA_DIR` falls back to `../../data` (repo root) if unset.

## Run via Docker

From the repo root: `docker compose up mcp-server` (brings up `vector-db` too).

## Ingestion

The base corpus (`data/raw_docs/`) needs to be embedded into Qdrant before
retrieval works. From inside the container or with the same env vars set
locally:

```bash
python -m core.run_ingestion
```

This is a one-time step per fresh `vector-db` volume — re-run it after
switching `QDRANT_URL` from embedded to standalone, since they're separate
stores.
