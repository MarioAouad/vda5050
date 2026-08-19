"""
Run: python 01_retrieval_progressive/run_progressive_eval.py
Requires: vector-db (Qdrant) reachable -- either `docker compose up vector-db -d`
(QDRANT_URL set) or nothing running at all (falls back to the embedded
local QDRANT_PATH store, same as running mcp-server outside Docker).

What this does, per config in configs.CONFIGS, IN ORDER:
  1. Build the chunks for that config (real chunking code, not simulated).
  2. Embed + upsert into a throwaway collection `eval_cfg_<name>` (skipped
     if this config shares chunks with an earlier one -- see configs.py).
  3. Build the real retriever for that config's "retriever_kind".
  4. Run every question in test_set_retrieval.json through it, k=3.
  5. Score with 00_shared/metrics.py, print a narrative verdict, and save
     a NEW timestamped results file (never overwrites a prior run).
  6. After all configs: write one consolidated comparison file with the
     deltas between consecutive configs, and an auto-generated narrative
     line per step (built FROM the measured numbers, not asserted).
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

from metrics import score_one_query, aggregate, save_result  # 00_shared
from configs import CONFIGS  # this folder

from core.vectorstore import get_qdrant_client, get_embedding_model, upsert_documents
from langchain_qdrant import QdrantVectorStore
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder

RESULTS_DIR = _THIS_DIR / "results"
K = 3
FETCH_K = 10

with open(_EVAL_ROOT / "00_shared" / "test_set_retrieval.json") as f:
    TEST_SET = json.load(f)["questions"]

_cross_encoder = None  # loaded lazily, once, only if a config needs it


def _get_reranker(top_n: int):
    global _cross_encoder
    if _cross_encoder is None:
        print("  (loading cross-encoder reranker model, first time only...)")
        _cross_encoder = HuggingFaceCrossEncoder(model_name="BAAI/bge-reranker-base")
    return CrossEncoderReranker(model=_cross_encoder, top_n=top_n)


def _build_retriever(kind: str, collection_name: str, chunks: list):
    """Mirrors services/mcp-server/core/retriever.py's own logic, kept
    separate here so each config can point at its own throwaway
    collection/chunk-set instead of the production chunks.pkl file."""
    client = get_qdrant_client()
    embeddings = get_embedding_model()
    vectorstore = QdrantVectorStore(client=client, collection_name=collection_name, embedding=embeddings)
    dense = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": FETCH_K if kind != "dense" else K})

    if kind == "dense":
        return dense

    if kind == "deployed_dense_conv":
        # The literal shipped function, on the real deployed code path --
        # not reimplemented here, because this config's whole point is to
        # prove what the ACTUAL code does today.
        sys.path.insert(0, str(_MCP_DIR))
        import core.config as _cfg
        _cfg.COLLECTION_NAME = collection_name  # point the shipped function at our eval collection
        from core.retriever import get_retriever
        return get_retriever(collection_name=collection_name, k=K, file_type=None, conversation_id="eval-progressive-fake-conversation")

    bm25 = BM25Retriever.from_documents(chunks)
    bm25.k = FETCH_K
    ensemble = EnsembleRetriever(retrievers=[bm25, dense], weights=[0.5, 0.5])

    if kind == "hybrid":
        return ensemble
    if kind == "hybrid_rerank":
        return ContextualCompressionRetriever(base_compressor=_get_reranker(K), base_retriever=ensemble)
    raise ValueError(f"unknown retriever_kind: {kind}")


def run_config(cfg: dict, ingested_collections: dict) -> dict:
    print(f"\n{'='*78}\nConfig: {cfg['name']}\nHypothesis: {cfg['hypothesis']}\n{'='*78}")

    chunks = cfg["chunks_fn"]()
    print(f"  chunks built: {len(chunks)}")

    if cfg["shares_chunks_with"]:
        collection_name = ingested_collections[cfg["shares_chunks_with"]]
        print(f"  reusing embeddings/collection from '{cfg['shares_chunks_with']}' ({collection_name}) -- retrieval layer differs, chunking doesn't")
    else:
        collection_name = f"eval_cfg_{cfg['name']}"
        print(f"  embedding + upserting into throwaway collection '{collection_name}' ...")
        upsert_documents(chunks, collection_name=collection_name)
    ingested_collections[cfg["name"]] = collection_name

    retriever = _build_retriever(cfg["retriever_kind"], collection_name, chunks)

    per_query = []
    for q in TEST_SET:
        # Truncate to exactly K before scoring, regardless of how many
        # candidates this retriever's search_kwargs/fetch_k actually
        # returned internally (dense configs return exactly K already;
        # hybrid/ensemble configs return many more raw candidates before
        # any top-N cut). Without this, Recall@K's denominator becomes
        # "how many relevant docs were in this retriever's whole raw
        # candidate pool" instead of "in the top K" -- which makes Recall
        # incomparable across configs with different pool sizes (larger
        # pools look artificially worse on Recall, not because retrieval
        # was worse, but because they were scored against more candidates
        # in the first place). Precision@K and NDCG@K were already safe
        # (they slice to K internally); this fixes Recall@K and MRR to be
        # on the same footing.
        docs = retriever.invoke(q["query"])[:K]
        scored = score_one_query(docs, q["expected_source"], q["required_keywords"], k=K)
        scored["question_id"] = q["id"]
        scored["query"] = q["query"]
        per_query.append(scored)

    agg = aggregate(per_query)
    result = {
        "config_name": cfg["name"],
        "hypothesis": cfg["hypothesis"],
        "retriever_kind": cfg["retriever_kind"],
        "k": K,
        "num_chunks": len(chunks),
        "aggregate_metrics": agg,
        "per_query": per_query,
    }
    print(f"  Precision@{K}={agg.get('mean_precision_at_k')}  Recall@{K}={agg.get('mean_recall_at_k')}  MRR={agg.get('mean_mrr')}  NDCG@{K}={agg.get('mean_ndcg_at_k')}")
    save_result(result, RESULTS_DIR, cfg["name"])
    return result


def _narrative(prev: dict | None, curr: dict) -> str:
    if prev is None:
        return f"Starting point. Precision@{K}={curr['aggregate_metrics']['mean_precision_at_k']}, NDCG@{K}={curr['aggregate_metrics']['mean_ndcg_at_k']}."
    dp = curr["aggregate_metrics"]["mean_precision_at_k"] - prev["aggregate_metrics"]["mean_precision_at_k"]
    dn = curr["aggregate_metrics"]["mean_ndcg_at_k"] - prev["aggregate_metrics"]["mean_ndcg_at_k"]
    dr = curr["aggregate_metrics"]["mean_recall_at_k"] - prev["aggregate_metrics"]["mean_recall_at_k"]
    verdict = "IMPROVED" if (dp + dn + dr) > 0.01 else ("REGRESSED" if (dp + dn + dr) < -0.01 else "NO MEANINGFUL CHANGE")
    return (f"vs. '{prev['config_name']}': Precision@{K} {dp:+.3f}, Recall@{K} {dr:+.3f}, NDCG@{K} {dn:+.3f} -> {verdict}. "
            f"Decision: {'kept this change going forward' if verdict != 'REGRESSED' else 'this change made things worse -- flagged for the write-up'}.")


def main():
    ingested_collections: dict[str, str] = {}
    all_results = []
    for cfg in CONFIGS:
        all_results.append(run_config(cfg, ingested_collections))

    print(f"\n{'='*78}\nCONSOLIDATED PROGRESSION SUMMARY\n{'='*78}")
    summary_rows = []
    for i, res in enumerate(all_results):
        prev = all_results[i - 1] if i > 0 else None
        note = _narrative(prev, res)
        print(f"\n[{res['config_name']}] {res['hypothesis']}")
        print(f"  {note}")
        summary_rows.append({
            "config_name": res["config_name"],
            "retriever_kind": res["retriever_kind"],
            "hypothesis": res["hypothesis"],
            "aggregate_metrics": res["aggregate_metrics"],
            "narrative": note,
        })

    save_result({"k": K, "progression": summary_rows}, RESULTS_DIR, "progression_summary")
    print("\nDone. Every config's full per-query results + this summary are in 01_retrieval_progressive/results/.")
    print("Feed 'progression_summary_latest.json' directly into docs/EVALUATION.md section 5.")


if __name__ == "__main__":
    main()
