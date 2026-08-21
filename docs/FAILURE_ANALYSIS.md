# Failure analysis

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
- **Measured impact (confirmed, post-fix re-run):** `evaluation/04_config_comparisons/results/comparison_a_hybrid_vs_dense_latest.json` — Precision@3 deployed vs. intended: 0.574 vs. 0.648 (−0.074); Recall@3: 0.833 vs. 0.889 (−0.056). Both numbers are now confirmed stable, not artifacts — see `docs/EVALUATION.md` for the before/after proof.
