# VDA 5050 Fleet Operations System

A multi-agent AI system that answers questions about, and validates
messages against, the **VDA 5050** standard — the open interface spec
used for communication between fleets of AGVs/AMRs (mobile robots) and a
central fleet control system in warehouses and factories.

**Who it's for:** an operations or integration engineer working with a
VDA 5050-based fleet who needs a fast, grounded answer to "what does the
spec say about X" or "is this JSON payload valid," without manually
searching a 2,175-line specification document and 8 separate JSON
schemas by hand. It answers from the real spec text — cited to source,
not guessed at — and for validation-shaped questions, checks
deterministically against the actual schema rather than asking a
language model to eyeball it.

---

## Architecture

Five containers, one `docker-compose up`:

```mermaid
flowchart LR
    U["Operations engineer<br/>(browser)"] --> UI["chatbot-ui<br/>(static frontend)"]
    UI -- "HTTP + SSE" --> A["agent-system-a<br/>LangGraph Supervisor<br/>+ Protocol / Schema / Diagnostics specialists<br/>(FastAPI)"]
    A -- "MCP (streamable-http)" --> M["mcp-server<br/>FastMCP: search + document tools"]
    A -- "HTTP: POST /agent/ask" --> B["agent-system-b<br/>Google ADK diagnostics agent<br/>(FastAPI)"]
    M -- "dense + BM25 + rerank" --> V[("vector-db<br/>Qdrant")]
    M --> D[("data/raw_docs<br/>VDA5050_EN.md + 8 JSON schemas")]
    B --> D
    A -. "Groq (primary)<br/>Gemini (fallback)" .-> L(("LLM providers"))
```

| Container | What it runs |
|---|---|
| `agent-system-a` | LangGraph Supervisor + Protocol/Schema/Diagnostics specialists, FastAPI, SSE streaming |
| `agent-system-b` | Google ADK agent wrapping two deterministic tools (schema validation, error-type lookup) |
| `mcp-server` | FastMCP server exposing the RAG search + document-management tools |
| `vector-db` | Qdrant, standalone |
| `chatbot-ui` | Static HTML/CSS/JS frontend |

---

## Quickstart (from a clean clone)

```bash
git clone <this-repo>
cd vda5050
cp .env.example .env      # fill in GROQ_API_KEY and GOOGLE_API_KEY
docker compose up --build
```

First run only — the `vector-db` container starts empty, so embed the
spec into it:

```bash
docker compose exec mcp-server python -m core.run_ingestion
```

Then open **`http://localhost:8080`**.

### Verifying it's actually working

```bash
curl http://localhost:8000/health          # agent-system-a
curl http://localhost:8002/health          # agent-system-b (if exposed; else check container logs)
docker compose logs mcp-server | tail -20  # should show "Hybrid Retriever ready" after ingestion
```

Then ask the UI something like *"What does NODE_UNREACHABLE mean?"* — a
grounded answer citing the spec's error-type table means the whole chain
(UI → Agent A → MCP → Qdrant → LLM) is working end to end.

---

## Streaming (SSE)

`agent-system-a` exposes two message endpoints per conversation:

- `POST /conversations/{id}/messages` — waits for the full reply, returns `{"reply": "..."}`.
- `POST /conversations/{id}/messages/stream` — Server-Sent Events. Streams
  one `data: {...}` frame per graph step as it actually finishes (routing,
  searching, finalizing), then a closing `data: {"done": true, "reply": "..."}`
  frame. This is **step-level** streaming, not token-level — see "Technical
  decisions" below for why. The chatbot UI uses this endpoint by default.

  Try it directly:
  ```bash
  curl -N -X POST http://localhost:8000/conversations/<id>/messages/stream \
    -H "Content-Type: application/json" \
    -d '{"message": "What does NODE_UNREACHABLE mean?"}'
  ```

---

## Technical decisions and justifications

- **Structure-aware chunking (Markdown headers + JSON-schema
  parent-child splitting) instead of one generic character-count
  splitter.** Measured, not assumed: it beats every naive fixed-size
  configuration tested on Precision@3, using 40-60% fewer chunks.
- **`BAAI/bge-m3` as the embedding model** — chosen specifically because
  it's multilingual.
- **Hybrid (BM25 + dense) retrieval + cross-encoder reranking as retrieval design** measurably the best-performing
  configuration in `docs/EVALUATION.md`.
- **Two independent agent systems on different frameworks (LangGraph +
  Google ADK), talking over a real HTTP call**. Agent System B's job (deterministic
  validation/lookup) is also architecturally distinct from Agent
  System A's job (open-ended retrieval/conversation). A degrades
  gracefully instead of crashing if B is down.
- **Step-level SSE streaming, not token-level** — the ReAct tool-calling
  loop needs each LLM call's complete response before it can check
  whether a tool should be called.

---

## Evaluation

Full report: [`docs/EVALUATION.md`](docs/EVALUATION.md) — test set,
retrieval metrics across 7 configurations, generation eval (LLM-judge),
agent routing accuracy (confusion matrix + per-tool breakdown), two
required configuration comparisons with numbers, and 3 documented
failure cases with root-cause analysis. Raw, timestamped run data behind
every number: [`evaluation/*/results/`](evaluation/).

---

## Known limitations

1. **The hybrid+reranked retrieval pipeline — the better-performing
   configuration per `docs/EVALUATION.md` — isn't what's actually
   served in production.** A correctness fix (making per-conversation
   uploads discoverable) requires passing `conversation_id` on every
   search, which routes to a simpler dense-only path. Measured cost:
   Precision@3 drops from 0.648 to 0.574.
2. **Deleting a document doesn't erase a conversation's memory of it.**
   Deletion itself (removing chunks from Qdrant) works correctly — but a
   conversation that already discussed that content keeps its own
   answer in its checkpointed history. Only *new* searches are affected.
3. **14 of 22 spec diagrams have zero text representation in the RAG
   corpus.** No image/vision pipeline exists.
4. **True token-by-token streaming isn't implemented** — see "Technical
   decisions" above.
5. **The small router model occasionally misroutes** grammatically
   unusual or genuinely dual-intent questions — see `docs/EVALUATION.md`
   for a concrete, measured example (`a03`) and why it's more a test
   labeling nuance than a routing bug in that specific case.
6. **`InputGuard`/`OutputGuard` are shallow, pattern-based checks**, not
   a comprehensive content-safety system — an appropriate scope for an
   internal technical assistant, described accurately rather than
   oversold.

---

## Project structure

```
├── docker-compose.yml
├── docs/                    # proposal, architecture, project guide, evaluation, failure analysis
├── services/
│   ├── agent-system-a/      # LangGraph supervisor + FastAPI (incl. SSE streaming)
│   ├── agent-system-b/      # Google ADK diagnostics/validation agent
│   ├── mcp-server/          # FastMCP retrieval + document tools
│   ├── vector-db/           # Qdrant config notes (no app code)
│   └── chatbot-ui/          # static frontend
├── evaluation/              # test sets, retrieval/generation/agent-routing/config-comparison harness + results
└── data/                    # VDA 5050 spec + schemas (committed); runtime data (gitignored)
```

Each service has its own README with standalone (non-Docker) run
instructions and its endpoint list.
