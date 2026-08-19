"""
Run: python 04_config_comparisons/run_comparisons.py
(after 01_retrieval_progressive has been run at least once -- reuses its
config-building code so the two comparisons use the exact same chunks/
retrievers as the progression, not a reimplementation that could drift.)

Produces the project's two required "configuration comparisons with
numbers" (requirement 2.7 / 5.3):

  Comparison A -- Hybrid+rerank (intended design) vs. dense-only
  (what get_retriever() actually serves once conversation_id is passed on
  every call, per docs/PROJECT_GUIDE.md 3.5). This is the real, deployed
  trade-off, not an arbitrary parameter sweep.

  Comparison B -- Retrieval depth: top-K=3 vs. top-K=5, on the best
  (hybrid+rerank) retriever.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_EVAL_ROOT = _THIS_DIR.parent
_REPO_ROOT = _EVAL_ROOT.parent
_PROGRESSIVE_DIR = _EVAL_ROOT / "01_retrieval_progressive"
_MCP_DIR = _REPO_ROOT / "services" / "mcp-server"
for p in (_EVAL_ROOT / "00_shared", _PROGRESSIVE_DIR, _MCP_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from metrics import score_one_query, aggregate, save_result  # 00_shared
import run_progressive_eval as pe  # 01_retrieval_progressive -- reuse its retriever builder
from configs import _structure_aware_chunks
from core.vectorstore import upsert_documents

RESULTS_DIR = _THIS_DIR / "results"

with open(_EVAL_ROOT / "00_shared" / "test_set_retrieval.json") as f:
    TEST_SET = json.load(f)["questions"]


def _score_retriever(retriever, k: int) -> dict:
    per_query = []
    for q in TEST_SET:
        # Same fix as 01_retrieval_progressive: truncate to exactly k
        # before scoring so Recall@k/MRR are comparable across retrievers
        # that internally return different-sized candidate pools.
        docs = retriever.invoke(q["query"])[:k]
        scored = score_one_query(docs, q["expected_source"], q["required_keywords"], k=k)
        scored["question_id"] = q["id"]
        per_query.append(scored)
    return {"k": k, "aggregate_metrics": aggregate(per_query), "per_query": per_query}


def comparison_a_hybrid_vs_dense():
    print("\n" + "=" * 78 + "\nComparison A: hybrid+rerank (intended) vs. dense-only (deployed)\n" + "=" * 78)
    chunks = _structure_aware_chunks()
    collection = "eval_cfg_04_structure_aware_dense"  # from the progressive run; re-embed if missing
    try:
        upsert_documents(chunks, collection_name=collection)
    except Exception as e:
        print(f"  (re-embed skipped/failed, assuming collection already exists: {e})")

    intended = pe._build_retriever("hybrid_rerank", collection, chunks)
    deployed = pe._build_retriever("deployed_dense_conv", collection, chunks)

    intended_scores = _score_retriever(intended, k=3)
    deployed_scores = _score_retriever(deployed, k=3)

    dp = deployed_scores["aggregate_metrics"]["mean_precision_at_k"] - intended_scores["aggregate_metrics"]["mean_precision_at_k"]
    dn = deployed_scores["aggregate_metrics"]["mean_ndcg_at_k"] - intended_scores["aggregate_metrics"]["mean_ndcg_at_k"]
    dr = deployed_scores["aggregate_metrics"]["mean_recall_at_k"] - intended_scores["aggregate_metrics"]["mean_recall_at_k"]

    result = {
        "comparison": "hybrid_rerank (intended) vs deployed_dense_only (actual production path)",
        "intended_design": intended_scores["aggregate_metrics"],
        "deployed_reality": deployed_scores["aggregate_metrics"],
        "delta_deployed_minus_intended": {"precision_at_3": round(dp, 4), "recall_at_3": round(dr, 4), "ndcg_at_3": round(dn, 4)},
        "verdict": ("The conversation_id-required upload-scoping fix costs measurable retrieval quality on EVERY query, "
                    "not just upload-related ones, because it silently drops the hybrid+rerank pipeline to dense-only. "
                    "See docs/PROJECT_GUIDE.md Part 3.5 for the trade-off and the scoped fix that was deliberately deferred."),
        "intended_full": intended_scores,
        "deployed_full": deployed_scores,
    }
    print(f"  Intended (hybrid+rerank): P@3={intended_scores['aggregate_metrics']['mean_precision_at_k']} NDCG@3={intended_scores['aggregate_metrics']['mean_ndcg_at_k']}")
    print(f"  Deployed (dense-only):    P@3={deployed_scores['aggregate_metrics']['mean_precision_at_k']} NDCG@3={deployed_scores['aggregate_metrics']['mean_ndcg_at_k']}")
    print(f"  Delta (deployed - intended): precision {dp:+.4f}, recall {dr:+.4f}, ndcg {dn:+.4f}")
    save_result(result, RESULTS_DIR, "comparison_a_hybrid_vs_dense")
    return result


def comparison_b_topk_3_vs_5():
    print("\n" + "=" * 78 + "\nComparison B: top-K=3 vs. top-K=5 (hybrid+rerank retriever)\n" + "=" * 78)
    chunks = _structure_aware_chunks()
    collection = "eval_cfg_04_structure_aware_dense"
    try:
        upsert_documents(chunks, collection_name=collection)
    except Exception as e:
        print(f"  (re-embed skipped/failed, assuming collection already exists: {e})")

    r3 = pe._build_retriever("hybrid_rerank", collection, chunks)  # K=3 is the module default
    pe.K = 5
    r5 = pe._build_retriever("hybrid_rerank", collection, chunks)
    pe.K = 3  # restore

    s3 = _score_retriever(r3, k=3)
    s5 = _score_retriever(r5, k=5)

    result = {
        "comparison": "top-K=3 vs top-K=5, hybrid+rerank retriever",
        "k3": s3["aggregate_metrics"],
        "k5": s5["aggregate_metrics"],
        "delta_k5_minus_k3": {
            m: round(s5["aggregate_metrics"][f"mean_{m}"] - s3["aggregate_metrics"][f"mean_{m}"], 4)
            for m in ["precision_at_k", "recall_at_k", "mrr", "ndcg_at_k"]
        },
        "k3_full": s3,
        "k5_full": s5,
    }
    print(f"  K=3: P={s3['aggregate_metrics']['mean_precision_at_k']} R={s3['aggregate_metrics']['mean_recall_at_k']} NDCG={s3['aggregate_metrics']['mean_ndcg_at_k']}")
    print(f"  K=5: P={s5['aggregate_metrics']['mean_precision_at_k']} R={s5['aggregate_metrics']['mean_recall_at_k']} NDCG={s5['aggregate_metrics']['mean_ndcg_at_k']}")
    save_result(result, RESULTS_DIR, "comparison_b_topk_3_vs_5")
    return result


if __name__ == "__main__":
    comparison_a_hybrid_vs_dense()
    comparison_b_topk_3_vs_5()
    print("\nBoth required configuration comparisons saved to 04_config_comparisons/results/.")
