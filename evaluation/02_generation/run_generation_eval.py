"""
Run: python 02_generation/run_generation_eval.py
Env vars (all optional):
  GEN_BACKEND    = ollama (default) | gemini | groq   -- which model WRITES the answer
  JUDGE_BACKEND  = ollama (default) | gemini | groq   -- which model SCORES the answer
  OLLAMA_MODEL   = llama3.1:8b (default)

Requires mcp-server's production Qdrant collection to already be ingested
(the real `vda5050_baseline` collection -- this script reads it directly
via core.retriever, it does not need mcp-server's HTTP server running).

Deliberately uses conversation_id set on every call, same as the real
specialists do today (per docs/PROJECT_GUIDE.md 3.5) -- so this eval
measures what users actually receive, dense-only retrieval included, not
an idealized best-case. That's intentional: it keeps this number honest
and consistent with 04_config_comparisons' "deployed reality" numbers,
instead of accidentally grading a retrieval pipeline nobody is served.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_EVAL_ROOT = _THIS_DIR.parent
_REPO_ROOT = _EVAL_ROOT.parent
_MCP_DIR = _REPO_ROOT / "services" / "mcp-server"
for p in (_EVAL_ROOT / "00_shared", _MCP_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from metrics import save_result  # 00_shared
from judges import call_judge, parse_judge_json, RUBRIC_PROMPT_TEMPLATE

from core.retriever import get_retriever

RESULTS_DIR = _THIS_DIR / "results"
GEN_BACKEND_DEFAULT = "ollama"

with open(_EVAL_ROOT / "00_shared" / "test_set_generation.json") as f:
    TEST_SET = json.load(f)["questions"]

GROUNDED_PROMPT = """You are the VDA 5050 protocol assistant. Answer the question using ONLY the \
retrieved context below. If the context does not contain the answer, say so plainly instead of \
guessing -- do not use outside knowledge.

Retrieved context:
---
{context}
---

Question: {query}

Answer:"""


def main():
    import os
    gen_backend = os.getenv("GEN_BACKEND", GEN_BACKEND_DEFAULT)
    print(f"Generation model backend: {gen_backend}  |  Judge backend: {os.getenv('JUDGE_BACKEND', 'ollama')}")

    retriever = get_retriever(k=3, file_type=None, conversation_id="eval-generation-fake-conversation")

    rows = []
    for q in TEST_SET:
        print(f"\n[{q['id']}] {q['query']}")
        docs = retriever.invoke(q["query"])
        context = "\n\n---\n\n".join(d.page_content for d in docs) or "(no relevant chunks retrieved)"

        gen_prompt = GROUNDED_PROMPT.format(context=context, query=q["query"])
        try:
            answer = call_judge(gen_prompt, backend=gen_backend)
        except Exception as e:
            answer = f"GENERATION ERROR: {type(e).__name__}: {e}"
        print(f"  answer: {answer[:150]}...")

        judge_prompt = RUBRIC_PROMPT_TEMPLATE.format(
            query=q["query"], context=context, reference_facts=q["reference_facts"],
            must_refuse=q["must_refuse"], answer=answer,
        )
        try:
            raw_verdict = call_judge(judge_prompt)  # uses JUDGE_BACKEND
            verdict = parse_judge_json(raw_verdict)
        except Exception as e:
            verdict = {"faithfulness": None, "correctness": None, "relevance": None, "justification": f"JUDGE ERROR: {type(e).__name__}: {e}"}
        print(f"  judge: {verdict}")

        rows.append({
            "id": q["id"], "query": q["query"], "must_refuse": q["must_refuse"],
            "num_context_chunks": len(docs), "answer": answer, "judge_verdict": verdict,
        })

    scored = [r for r in rows if r["judge_verdict"].get("faithfulness") is not None]
    n = len(scored) or 1
    summary = {
        "gen_backend": gen_backend,
        "judge_backend": os.getenv("JUDGE_BACKEND", "ollama"),
        "n_questions": len(rows),
        "n_scored": len(scored),
        "n_unparseable": len(rows) - len(scored),
        "mean_faithfulness": round(sum(r["judge_verdict"]["faithfulness"] for r in scored) / n, 3) if scored else None,
        "mean_correctness": round(sum(r["judge_verdict"]["correctness"] for r in scored) / n, 3) if scored else None,
        "mean_relevance": round(sum(r["judge_verdict"]["relevance"] for r in scored) / n, 3) if scored else None,
        "rows": rows,
    }
    print(f"\nMean faithfulness={summary['mean_faithfulness']}  correctness={summary['mean_correctness']}  relevance={summary['mean_relevance']}")
    if summary["n_unparseable"]:
        print(f"WARNING: {summary['n_unparseable']} judge responses were unparseable -- inspect 'rows' for 'UNPARSEABLE JUDGE OUTPUT'. Common with small local judges; consider a stricter prompt or JUDGE_BACKEND=gemini as a cross-check.")

    save_result(summary, RESULTS_DIR, f"generation_eval_{gen_backend}")


if __name__ == "__main__":
    main()
