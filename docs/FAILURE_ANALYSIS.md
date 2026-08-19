# Failure analysis

Four documented failures, in the format the requirement asks for. Full
narrative for each is in `docs/PROJECT_GUIDE.md` Part 11.3 (Failures 1–3)
and Part 11.3's addendum (Failure 4) — this file is the compact report
version.

## Failure 1: Cross-language diagnostics answers
- **Query:** "What does the error type BANANA_ERROR mean?" (asked in English)
- **Expected:** An English reply
- **Actual:** A German reply
- **Classification:** Prompt failure — Agent System B's ADK agent instruction had no language-matching rule, while Agent System A's specialists already did (inconsistency between the two systems, not a model limitation)
- **Fix:** Added the same explicit language-matching instruction to Agent System B's `LlmAgent` instruction
- **Regression check:** `evaluation/00_shared/test_set_agent.json` case `a15` (French query, routed through Agent A) and case `a12`/`a14` (English diagnostics queries through Agent B) both run every time `03_agent_eval` is run

## Failure 2: Document deletion silently not deleting
- **Query:** Upload a document, ask about it (correct answer), delete it, ask again
- **Expected:** The system should no longer find/answer about the deleted content
- **Actual:** It kept answering as if the document were still present
- **Classification:** Design failure — an ID-generation mismatch between the SQLite metadata row and the actual Qdrant chunk tags (two different random IDs generated for what should have been the same document)
- **Fix:** Both the database layer and vector-store layer now use the exact same document ID, generated once at upload time

## Failure 3: Small-talk hard-blocked by the input guardrail
- **Query:** "Hi"
- **Expected:** A friendly greeting reply
- **Actual:** A guardrail block (`GUARDRAIL_BLOCK: The question does not seem related to VDA 5050...`)
- **Classification:** Design failure — a static keyword-list guardrail can't distinguish a harmless greeting from a genuinely off-topic question
- **Fix:** Removed topic-relevance judgment from the input guardrail; added a `SmallTalk` route so greetings get a warm, on-topic-redirecting reply instead of a block
- **Regression check:** `evaluation/00_shared/test_set_agent.json` case `a11` ("Hi" → expected route `SmallTalk`), part of `03_agent_eval`'s BLOCK-row tracking

## Failure 4: The hybrid+reranker retrieval pipeline is never actually used in production
- **Query:** any query — systemic, not a single bad answer
- **Expected:** every search goes through the full hybrid (dense+BM25) + cross-encoder reranker pipeline
- **Actual:** `get_retriever()` only builds that pipeline when `conversation_id` is NOT passed. The upload-scoping fix made every specialist pass `conversation_id` on every search, so every search — including ones with zero uploaded documents involved — silently drops to dense-only, with no error or visible symptom
- **Classification:** Design failure — a correctness fix (making per-conversation uploads discoverable) was implemented as an all-or-nothing branch rather than a merge of both retrieval paths, so fixing one requirement silently regressed a different one
- **Measured impact (confirmed, post-fix re-run):** `evaluation/04_config_comparisons/results/comparison_a_hybrid_vs_dense_latest.json` — Precision@3 deployed vs. intended: 0.574 vs. 0.648 (−0.074); Recall@3: 0.833 vs. 0.889 (−0.056). Both numbers are now confirmed stable, not artifacts — see `docs/EVALUATION.md` §7 for the before/after proof.
- **A second, distinct bug found while measuring this one — now fixed and confirmed:** building this comparison surfaced that `get_retriever()`'s `conversation_id` branch was also hardcoded to fetch **10** chunks regardless of the caller's requested `k=3`, with no reranking step to cut it back down — so every real production call wasn't just missing hybrid+rerank, it was also returning 3x more unranked context than requested. Fixed (`core/retriever.py`, one line — `search_kwargs = {"k": k if conversation_id else fetch_k}`) and **confirmed by re-run**: the deployed path's metrics are now identical to a plain dense-only search on every metric, proving the overfetch is gone and the only remaining gap is the intentional hybrid+rerank trade-off, not an extra accidental one on top of it.
- **Fix (bigger one, still deliberately deferred):** see `docs/PROJECT_GUIDE.md` Part 3.5 for the scoped fix (merge hybrid-over-base-corpus with dense-only-over-conversation-chunks instead of branching all-or-nothing) and why it wasn't made this close to the deadline. Turned into the evaluation suite's primary configuration comparison instead of being silently left unmeasured.

---
A fifth candidate, if one more is wanted: the Windows `UnicodeDecodeError` in
the Agent System B pytest suite — a **model/environment failure**
(platform-dependent default text encoding), distinct in character from the
four prompt/design failures above.
