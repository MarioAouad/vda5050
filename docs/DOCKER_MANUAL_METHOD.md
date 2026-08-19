# Manual Docker deployment (Method 2)

This is the manual, command-by-command equivalent of `docker-compose.yml`
— same 5 containers, same images, same env vars, same ports, same
volumes, same startup order. Every command below exists specifically
*because* of a line in `docker-compose.yml`; read `docs/DOCKER_EXPLAINED.md`
alongside this for *why* each command is shaped the way it is — that's
the part your instructor will actually ask about.

Run everything from the repo root. Assumes `.env` already has
`GROQ_API_KEY` and `GOOGLE_API_KEY` set (same file Docker Compose reads).

---

## Step 0 — clean slate (skip this the very first time)

```bash
docker compose down          # stop the compose version if it's running — can't have both on the same ports
docker stop vector-db mcp-server agent-system-b agent-system-a chatbot-ui 2>/dev/null
docker rm   vector-db mcp-server agent-system-b agent-system-a chatbot-ui 2>/dev/null
```

## Step 1 — create the network

```bash
docker network create vda5050-net
```

**Why this has to happen first, manually:** Compose creates a network
for you automatically (`docker-compose.yml` never mentions one, because
it doesn't have to). Doing this by hand is the whole point of "Method
2" — it's the part that makes container-name-based addressing
(`http://mcp-server:8001`, etc.) actually work. Full explanation:
`docs/DOCKER_EXPLAINED.md` §2.

## Step 2 — create the two named volumes

```bash
docker volume create qdrant_storage
docker volume create hf_cache
```

Matches the `volumes:` block at the bottom of `docker-compose.yml`.
`qdrant_storage` persists the vector database; `hf_cache` persists the
downloaded embedding/reranker model weights so you don't re-download
~2GB from Hugging Face every time you recreate the `mcp-server`
container.

## Step 3 — build the 4 custom images

(`vector-db` isn't built — it's a public image, pulled in Step 4.)

```bash
docker build -t vda5050-mcp-server   ./services/mcp-server
docker build -t vda5050-agent-a      ./services/agent-system-a
docker build -t vda5050-agent-b      ./services/agent-system-b
docker build -t vda5050-chatbot-ui   ./services/chatbot-ui
```

Each of these reads that service's own `Dockerfile` — identical to what
`build: context: ./services/<name>` does in Compose, just spelled out.

## Step 4 — run the containers, in dependency order

Order matters here in a way Compose partly hides from you: Compose reads
`depends_on` and starts things in that order automatically. Doing it by
hand, *you* are the thing enforcing the order — start `vector-db`
before `mcp-server`, and both `mcp-server` + `agent-system-b` before
`agent-system-a`, or the later containers will fail their first
connection attempt (they don't retry-with-backoff on startup).

**4.1 — vector-db**
```bash
docker run -d \
  --name vector-db \
  --network vda5050-net \
  -p 6333:6333 \
  -v qdrant_storage:/qdrant/storage \
  --restart unless-stopped \
  qdrant/qdrant:latest
```

**4.2 — mcp-server** (waits on vector-db)
```bash
docker run -d \
  --name mcp-server \
  --network vda5050-net \
  --env-file .env \
  -e DATA_DIR=/app/data \
  -e QDRANT_URL=http://vector-db:6333 \
  -e QDRANT_COLLECTION=vda5050_baseline \
  -e EMBEDDING_MODEL_NAME=BAAI/bge-m3 \
  -e MCP_HOST=0.0.0.0 \
  -e MCP_PORT=8001 \
  -v "$(pwd)/data:/app/data" \
  -v hf_cache:/root/.cache/huggingface \
  -p 8001:8001 \
  --restart unless-stopped \
  vda5050-mcp-server
```

**4.3 — agent-system-b** (independent — no dependencies of its own)
```bash
docker run -d \
  --name agent-system-b \
  --network vda5050-net \
  --env-file .env \
  -e DATA_DIR=/app/data \
  -v "$(pwd)/data:/app/data" \
  -p 8002:8002 \
  --restart unless-stopped \
  vda5050-agent-b
```

**4.4 — agent-system-a** (waits on mcp-server AND agent-system-b)
```bash
docker run -d \
  --name agent-system-a \
  --network vda5050-net \
  --env-file .env \
  -e DATA_DIR=/app/data \
  -e MCP_SERVER_URL=http://mcp-server:8001/mcp \
  -e AGENT_B_URL=http://agent-system-b:8002 \
  -e FRONTEND_ORIGIN=http://localhost:8080 \
  -v "$(pwd)/data:/app/data" \
  -p 8000:8000 \
  --restart unless-stopped \
  vda5050-agent-a
```

**4.5 — chatbot-ui** (waits on agent-system-a, though only because the
browser calling it needs A to actually be up — nginx itself doesn't care)
```bash
docker run -d \
  --name chatbot-ui \
  --network vda5050-net \
  -p 8080:80 \
  --restart unless-stopped \
  vda5050-chatbot-ui
```

## Step 5 — one-time: ingest the spec into Qdrant

Same as the Compose workflow — the `vector-db` container starts empty:

```bash
docker exec mcp-server python -m core.run_ingestion
```

---

## Testing it (this is the part to actually run before the review, not just read)

**5.1 — all 5 containers are up:**
```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```
Expect 5 rows, all `Up`.

**5.2 — prove the network is real** (this is the actual thing "Method 2"
is testing your understanding of — do this one, it's the good one):
```bash
docker network inspect vda5050-net
```
Look at the `"Containers"` block — all 5 should be listed with their
internal IPs. Then prove DNS resolution actually works, not just IP
routing:
```bash
docker exec agent-system-a ping -c 2 mcp-server
docker exec agent-system-a ping -c 2 agent-system-b
```
Both should resolve and get replies — proof that `--name` on `docker
run` is what makes `http://mcp-server:8001` resolvable from inside
`agent-system-a`, which is the entire mechanism the code relies on
(`MCP_SERVER_URL=http://mcp-server:8001/mcp` in Step 4.4 only works
*because* of this).

**5.3 — health checks from the host:**
```bash
curl http://localhost:8000/health   # agent-system-a
curl http://localhost:8002/health   # agent-system-b
```
(`mcp-server` doesn't expose a plain REST `/health` — it speaks MCP over
streamable-HTTP, not plain REST. Check it's alive with logs instead:
`docker logs mcp-server --tail 20` — look for the retriever/tool
registration lines, no tracebacks.)

**5.4 — end to end, through the browser:**
Open `http://localhost:8080`, ask *"What does NODE_UNREACHABLE mean?"*
A grounded answer citing the spec proves the entire chain: browser →
`chatbot-ui` (nginx) → `agent-system-a` → `mcp-server` → `vector-db`,
all across the manually-created network.

**5.5 — one more, if you want a strong answer for "what if a container
restarts":**
```bash
docker restart mcp-server
sleep 5
docker exec agent-system-a ping -c 2 mcp-server   # still resolves — Docker's embedded DNS updates automatically
```

---

## Teardown

```bash
docker stop chatbot-ui agent-system-a agent-system-b mcp-server vector-db
docker rm   chatbot-ui agent-system-a agent-system-b mcp-server vector-db
docker network rm vda5050-net
# Only if you actually want to wipe the ingested vector data + model cache:
# docker volume rm qdrant_storage hf_cache
```

---

## Side-by-side: what each Compose feature became by hand

| `docker-compose.yml` feature | Manual equivalent |
|---|---|
| (implicit) shared network | `docker network create vda5050-net` + `--network vda5050-net` on every `run` |
| `build: context: ./services/X` | `docker build -t vda5050-X ./services/X` |
| `depends_on:` | Nothing enforces it automatically — you just run things in the right order yourself |
| `environment:` list | `-e KEY=value` per line, or `--env-file .env` for the ones already in `.env` |
| `${GROQ_API_KEY}` (reads `.env` automatically) | `--env-file .env` (Compose does this implicitly; `docker run` needs it spelled out) |
| `volumes: - ./data:/app/data` | `-v "$(pwd)/data:/app/data"` |
| `volumes: - qdrant_storage:/qdrant/storage` | `docker volume create qdrant_storage` once, then `-v qdrant_storage:/qdrant/storage` |
| `ports: - "8000:8000"` | `-p 8000:8000` |
| `restart: unless-stopped` | `--restart unless-stopped` |
| `container_name: mcp-server` | `--name mcp-server` (this is also what makes it resolvable by that name on the network) |
| `docker compose up` (one command, right order, automatically) | Steps 1-5 above, in that exact order, by hand |
