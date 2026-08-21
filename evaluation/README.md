# Evaluation suite

## Folder order (run in this order — later ones assume earlier data exists)

| Folder | What it measures | LLM calls needed? |
|---|---|---|
| `00_shared/` | test sets + metric functions (not run directly) | none |
| `01_retrieval_progressive/` | Precision@3, Recall@3, MRR, NDCG@3 across 7 configs, naive → current | none (embeddings only, no LLM) |
| `02_generation/` | Faithfulness, correctness, relevance (LLM-judge) | yes — kept cheap, see below |
| `03_agent_eval/` | Routing confusion matrix, tool accuracy, trajectory, task completion, step efficiency | yes — one Groq/Gemini call per test case, same as your existing `evaluate_multi_agent.py` |
| `04_config_comparisons/` | The two required config comparisons, pulled/re-run from 01's code | none |

## Every run creates a new file, nothing is overwritten

Every script writes `<name>_<YYYYMMDD_HHMMSS>.json` into its own
`results/` folder, plus a `<name>_latest.json` that always points at the
most recent run. Run any script as many times as you want (e.g. after
tuning a prompt) — your history of runs stays on disk. `EVALUATION.md`
should cite the specific timestamped files you're reporting from.

## What needs to be running

- **01 and 04** (retrieval only): just Qdrant. Either `docker compose up
  vector-db -d`, or nothing at all (falls back to the embedded local
  store the same way running `mcp-server` outside Docker already does).
  No mcp-server, no agent-system-b, no ingestion required first — these
  scripts build and embed their own throwaway `eval_cfg_*` collections
  from `data/raw_docs` directly; they never touch your `vda5050_baseline`
  production collection.
- **02** (generation): the production `vda5050_baseline` collection must
  already be ingested (`docker compose exec mcp-server python -m
  core.run_ingestion`, if you haven't already) — reads it directly via
  `core.retriever`, no running mcp-server HTTP server needed. Plus
  whichever `GEN_BACKEND`/`JUDGE_BACKEND` you pick (see below).
- **03** (agent eval): `mcp-server` AND `agent-system-b` must actually be
  running and reachable, since this drives the real graph (imported
  in-process, same as `evaluate_multi_agent.py` already does) and the
  graph makes real HTTP calls out to both. Quickest:
  `docker compose up vector-db mcp-server agent-system-b -d`
  (three containers, not five), then run the script locally with:
  ```
  MCP_SERVER_URL=http://localhost:8001/mcp AGENT_B_URL=http://localhost:8002 python 03_agent_eval/run_agent_eval.py
  ```

## Avoiding Groq/Gemini rate limits

- `01`/`04` make **zero** LLM calls — safe to run as often as you like.
- `02` defaults to a **local Ollama model** (`JUDGE_BACKEND=ollama`,
  `GEN_BACKEND=ollama`, model `llama3.1:8b` since you already have it
  pulled) for BOTH the answer-generation step and the judging step — so
  the whole generation eval costs zero Groq/Gemini quota. Switch either
  one independently with env vars, e.g. to sanity-check the judge isn't
  just agreeing with itself:
  ```
  JUDGE_BACKEND=gemini python 02_generation/run_generation_eval.py
  ```
  Requires Ollama running locally (`ollama serve`, default
  `http://localhost:11434`) with the model pulled (`ollama pull
  llama3.1:8b`, which you already have).
- `03` is the one that still needs Groq/Gemini, because it's testing the
  *real deployed graph's* routing — that can't be swapped to Ollama
  without changing what's actually being measured. It's cheap enough
  (15 cases × 1-2 calls) to not be a practical risk, but if you hit a
  rate limit mid-run, just re-run — each case is independent and a
  partial failure only shows up as `"error"` on that one row, not a
  crashed run.

## Install

```
pip install -r evaluation/requirements.txt
```
(into the same environment you already use for `mcp-server`/`agent-system-a`
— this suite imports their code directly, it does not reinstall their
dependencies.)

## Adding a multilingual/embedding-model comparison (optional)

Not built by default — with the Aug 21 deadline, it wasn't worth the
extra download/runtime risk for what it would add on top of what's
already covered. But if you want one and have time:
- `test_set_agent.json` case `a15` already round-trips a French query
  through the real graph, and `docs/PROJECT_GUIDE.md`'s Failure 1
  (Agent B replying in German to an English query, now fixed) is
  already a real, defensible multilingual data point for "why this
  embedding model" — `BAAI/bge-m3` was chosen specifically because it's
  multilingual, and that's the evidence for it.
- To go further, add an 8th entry to `01_retrieval_progressive/configs.py`
  that reuses `04_structure_aware_dense`'s chunks but re-embeds them with
  a different `EMBEDDING_MODEL_NAME` (e.g. a non-multilingual English-only
  model) and compares retrieval metrics on `q15` (French-adjacent) vs. the
  rest — same pattern as every other config in that file.
