# agent-system-b

The Fleet Diagnostics & Validation Agent, independent from `agent-system-a`
— its own process, own FastAPI wrapper, own container. `agent-system-a`
calls it over HTTP for tasks that need a deterministic check rather than
conceptual Q&A.

Two tools, both real and tested — not stubs:

- **Schema validation** (`POST /validate-payload`) — validates a JSON
  payload against the real VDA 5050 JSON Schema for a given topic
  (`data/raw_docs/json_schemas/`), using `jsonschema.Draft7Validator`.
  Returns whether it's valid and, if not, every specific field-level error.
- **Error-code lookup** (`POST /lookup-error`) — returns the standard's own
  defined severity level and handling guidance for a given `errorType`.
  The data (`app/data.py`) is transcribed directly from spec section
  6.6.5.4's predefined error type table — 22 entries, verified against the
  source line-by-line, not generated.

## Current state

Both endpoints are real and pass their tests (`services/agent-system-b/tests/`,
run with `pytest`). What's still pending is the **Google ADK wrapper** —
right now `agent-system-a` calls these two endpoints directly as plain
LangChain tools (see `validate_vda5050_payload` / `lookup_vda5050_error` in
`services/agent-system-a/agent/graph.py`), rather than through an ADK agent
making its own tool-use decisions inside this service. The deterministic
logic works either way; ADK would change *how this service decides which
tool to call*, not the tools themselves.

## Run standalone (outside Docker)

```bash
cd services/agent-system-b
pip install -r requirements.txt
export DATA_DIR=../../data   # so it can find data/raw_docs/json_schemas
uvicorn app.main:app --reload --port 8002
```

## Run via Docker

From the repo root: `docker compose up agent-system-b`.

## Tests

```bash
cd services/agent-system-b
pytest tests/ -v
```

8 tests — schema files parse, valid/invalid payloads behave correctly,
type-mismatch detection, error-level cross-references, and an exact count
check against the spec's 22 predefined error types.

## Endpoints

| Method | Path | Status |
|---|---|---|
| GET | `/health` | working |
| POST | `/validate-payload` | working — real jsonschema validation |
| POST | `/lookup-error` | working — real spec data |
