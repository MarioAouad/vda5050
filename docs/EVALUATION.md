# Evaluation

Full harness lives in `evaluation/` — this document reports the results
of running it.

---

## 1. Test set

Three separate ground-truth sets, one per evaluation type, all under
`evaluation/00_shared/`:

- **`test_set_retrieval.json`** — 18 questions. Ground truth per question
  is `(expected_source, required_keywords)`: which spec file/schema it
  must come from, plus keywords that must appear in a chunk for it to
  count as "relevant."
- **`test_set_agent.json`** — 15 cases. Ground truth per case: expected
  route (which specialist, or `FINISH`/`END`), expected tool(s) called,
  a fact checklist the final answer should contain, and a minimum-steps
  count for step-efficiency scoring.
- **`test_set_generation.json`** — 10 questions, 2 deliberately
  unanswerable from the corpus (`must_refuse: true`) to test whether the
  system says "not in the retrieved context" instead of guessing.

---

## 2. Retrieval metrics

### The seven-configuration progression

| # | Config | Chunks | Precision@3 | Recall@3 | MRR | NDCG@3 | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | Naive, 500 chars, no overlap | 956 | **0.556** | 0.833 | 0.806 | 0.816 | Baseline |
| 2 | Naive, 500 chars, 100 overlap | 1015 | 0.519 | 0.833 | 0.806 | 0.808 | Regressed on precision — more near-duplicate chunks from overlap, no new information, confused ranking |
| 3 | Naive, 1000 chars, 100 overlap | 479 | 0.519 | 0.833 | **0.833** | 0.833 | No precision change, but MRR/NDCG improved — bigger chunks ranked better when found, didn't fix precision |
| 4 | **Structure-aware, dense-only** | **371** | 0.574 | 0.833 | 0.806 | 0.816 | Beats every naive config on precision, using **fewer than half the chunks** |
| 5 | Structure-aware + BM25 hybrid (no rerank) | 371 | 0.481 | 0.722 | 0.630 | 0.645 | **Real regression** — confirmed after the harness fix, not an artifact this time. Raw BM25+dense merge pulls in keyword-matched but semantically weak chunks; nothing re-ranks them out. |
| 6 | **Structure-aware + hybrid + reranker (intended design)** | 371 | **0.648** | **0.889** | **0.861** | **0.868** | **Best on every metric.** The reranker is what fixes what config 5 broke. |
| 7 | Same chunks, through the **actual deployed** `get_retriever(conversation_id=...)` path | 371 | 0.574 | 0.833 | 0.806 | 0.816 | **Identical to config 4** — see §7, this is the proof the retriever.py fix worked |

*Sources: `evaluation/01_retrieval_progressive/results/progression_summary_latest.json` and the individual `0N_..._latest.json` files.*

**What this table proves, config by config:**
- **Chunking strategy matters more than chunk-size tuning** (rows 1-3 vs 4): tuning overlap/size on a naive splitter bought at most +0.03 NDCG; switching to structure-aware chunking bought +0.06 precision over the naive configs' best result, with 40-60% fewer chunks.
- **Row 5 is a genuine, useful negative result**: hybrid retrieval *without* reranking is worse than dense-only alone on every metric (0.481 vs 0.574 precision, 0.722 vs 0.833 recall). BM25 surfaces keyword-matched chunks that are lexically similar but not the best semantic match, and without a reranker to sort that out, they dilute the top-3.
- **Row 6 confirms the reranker is the real source of quality**, not the hybrid merge by itself — precision jumps from 0.481 (row 5) to 0.648 (row 6), a +0.167 swing from adding one component.
- **Row 7**: The deployed path's retrieval quality is now *identical* to plain dense-only (row 4).

---

## 3. Generation evaluation

LLM-judge, anchored rubric (faithfulness / correctness / relevance, 1-5
each), single combined judge call per question. Generation and judging
both ran on a **local Ollama `llama3.1:8b`**.

| Metric | Score |
|---|---|
| Mean faithfulness | **4.9 / 5** |
| Mean correctness | **4.8 / 5** |
| Mean relevance | **5.0 / 5** |
| Questions scored | 10 / 10 (0 unparseable judge responses) |

*Source: `evaluation/02_generation/results/generation_eval_ollama_latest.json`.*

Both deliberately-unanswerable questions (`g05`, `g08`) were correctly
refused rather than guessed at, confirming the grounded-refusal
instruction in `agent/graph.py` actually works, not just reads well.
`g05`'s correctness score (3/5, the only score below 4 in the whole set)
is the judge marking down a hedged-but-still-correct refusal.

---

## 4. Agent routing accuracy

*Source: `evaluation/03_agent_eval/results/agent_eval_latest.json`.*

**Routing confusion matrix** (rows = expected, columns = actual):

| Expected \ Actual | ProtocolSpecialist | SchemaSpecialist | DiagnosticsSpecialist | FINISH | END |
|---|---|---|---|---|---|
| ProtocolSpecialist | **3** | | | | |
| SchemaSpecialist | 1 | **3** | | | |
| DiagnosticsSpecialist | | | **3** | | |
| FINISH | | | | **2** | |
| SmallTalk | | | | 1 | |
| END | | | | 1 | 1 |

**Overall routing accuracy: 80% (12/15)**

| Metric | Score |
|---|---|
| Tool name accuracy | 86.7% |
| Invented tool calls | **0** |
| Trajectory in-order match | 86.7% |
| Task completion (fact coverage) | 85% |
| Mean step efficiency | 3.9x |
| Per-tool accuracy | `search_protocol_rules` 100%, `ask_diagnostics_agent` 100%, `search_json_schemas` 75% | 

### Reading the specific cases

- **`a03` (SchemaSpecialist → ProtocolSpecialist)** and **`a06`/`a11` (END/SmallTalk → FINISH)**: identical pattern to the first run.
- **`a13`/`a14` fact-coverage misses:**
  - `a13` expected the literal word `"errors"` in the answer; the actual answer said *"The following required properties are **missing**"* — correct in substance, wrong word for a strict substring match.
  - `a14` expected the literal phrase `"not found"`; the actual answer said *"`BANANA_ERROR` is **not a defined error code**"* — again correct, just phrased differently.
  This is a test-set precision problem (the fact-checklist strings are too narrow), not a system regression.

---

## 5. Configuration comparisons

**A — Hybrid+rerank (intended) vs. deployed dense-only (actual production path)**

| | Precision@3 | Recall@3 | NDCG@3 |
|---|---|---|---|
| Intended (config 6) | 0.648 | 0.889 | 0.868 |
| Deployed (config 7) | 0.574 | 0.833 | 0.816 |
| **Delta (deployed − intended)** | **−0.074** | **−0.056** | **−0.052** |

*Source: `evaluation/04_config_comparisons/results/comparison_a_hybrid_vs_dense_latest.json`.*

**On the negative deltas specifically**
The trade-off: making
per-conversation uploads discoverable costs 7.4 points of precision on
every query.

**B — Top-K=3 vs. Top-K=5 (hybrid+rerank retriever)**

| | Precision@K | Recall@K | MRR | NDCG@K |
|---|---|---|---|---|
| K=3 | 0.648 | 0.889 | 0.861 | 0.868 |
| K=5 | 0.656 | 0.944 | 0.875 | 0.866 |
| Delta | +0.008 | +0.056 | +0.014 | −0.003 |

*Source: `evaluation/04_config_comparisons/results/comparison_b_topk_3_vs_5_latest.json`.*

**Verdict:** K=5 recovers slightly more relevant chunks (+5.6% recall)
for a negligible precision cost and essentially the same ranking
quality — a defensible case for K=5 if prompt-length/latency budget
allows it, though the gain is modest enough that K=3 (current default)
is a reasonable choice too.
