# vector-db

No application code here — this is the official `qdrant/qdrant` image,
configured in the root `docker-compose.yml`. Documented as its own
`services/` entry to match the container table in `docs/ARCHITECTURE.md`
and the proposal's Section 2.4, even though there's nothing to build.

- **Collection name:** `vda5050_baseline` (override via `QDRANT_COLLECTION`)
- **Persistence:** a named Docker volume (`qdrant_storage`), not a bind
  mount — survives `docker compose down`, cleared with `docker compose down -v`
- **Reachable from:** `mcp-server` (`http://vector-db:6333` inside the
  compose network), and from the host at `http://localhost:6333` for
  debugging (e.g. the Qdrant dashboard at `http://localhost:6333/dashboard`)

## Migrating from the old embedded mode

The Sub-Project 2 / chatbot-phase version ran Qdrant embedded (a local
file, single-process, via `QdrantClient(path=...)`). That data does **not**
carry over automatically — the standalone server starts empty. Re-run
ingestion against it:

```bash
docker compose up -d vector-db mcp-server
docker compose exec mcp-server python -m core.run_ingestion
```
