"""
Shared metric functions used by every evaluation script in this suite.

Design note on "ground truth" for retrieval (read this before touching
test_set_retrieval.json):

We are running the SAME question through multiple different chunking
strategies (01_retrieval_progressive). Different chunking means different
chunk boundaries, which means a chunk_id ground truth ("the relevant chunk
is chunk #47") is meaningless across configs — chunk #47 does not mean the
same thing in a 500-char config as it does in the header-aware config.

Instead, ground truth here is defined at the (source_file, keywords) level:
a retrieved chunk counts as "relevant" to a question if BOTH:
  1. its metadata['source'] matches (or startswith) the question's
     expected_source, AND
  2. its page_content contains at least one of the question's
     required_keywords (case-insensitive substring match).
This is coarser than exact chunk-ID matching, but it is the only ground
truth definition that stays valid and comparable across every chunking
strategy we test — which is the whole point of the progressive comparison.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Relevance judgment
# ---------------------------------------------------------------------------
def is_relevant(doc_source: str, doc_content: str, expected_source: str, required_keywords: list[str]) -> bool:
    """A chunk is relevant if it's from the right file AND mentions at
    least one required keyword/phrase. See module docstring for why this
    (not chunk-ID matching) is the right ground-truth definition here."""
    source_ok = expected_source.lower() in (doc_source or "").lower()
    if not source_ok:
        return False
    content_lower = (doc_content or "").lower()
    return any(kw.lower() in content_lower for kw in required_keywords)


def relevance_list(retrieved_docs: list[Any], expected_source: str, required_keywords: list[str]) -> list[int]:
    """retrieved_docs: list of langchain Document (or dict with 'source'/'content').
    Returns a list of 0/1 in retrieval order."""
    labels = []
    for d in retrieved_docs:
        if hasattr(d, "metadata"):
            src = d.metadata.get("source", "")
            content = d.page_content
        else:
            src = d.get("source", "")
            content = d.get("content", "")
        labels.append(1 if is_relevant(src, content, expected_source, required_keywords) else 0)
    return labels


# ---------------------------------------------------------------------------
# Retrieval metrics (all take a 0/1 relevance list, already in rank order)
# ---------------------------------------------------------------------------
def precision_at_k(relevance: list[int], k: int) -> float:
    top_k = relevance[:k]
    if not top_k:
        return 0.0
    return sum(top_k) / len(top_k)


def recall_at_k(relevance: list[int], k: int, total_relevant: int) -> float:
    if total_relevant == 0:
        return 0.0
    return sum(relevance[:k]) / total_relevant


def reciprocal_rank(relevance: list[int]) -> float:
    for i, r in enumerate(relevance, start=1):
        if r == 1:
            return 1.0 / i
    return 0.0


def ndcg_at_k(relevance: list[int], k: int) -> float:
    """Binary-relevance NDCG. DCG uses the standard 1/log2(rank+1)
    discount; IDCG is the DCG of the ideal (all-relevant-first) ordering."""
    top_k = relevance[:k]
    dcg = sum(rel / math.log2(idx + 2) for idx, rel in enumerate(top_k))
    ideal = sorted(relevance, reverse=True)[:k]
    idcg = sum(rel / math.log2(idx + 2) for idx, rel in enumerate(ideal))
    if idcg == 0:
        return 0.0
    return dcg / idcg


def score_one_query(retrieved_docs: list[Any], expected_source: str, required_keywords: list[str],
                     total_relevant_hint: int | None = None, k: int = 3) -> dict:
    """Computes all four retrieval metrics for one query's results.

    total_relevant_hint: if you know how many truly-relevant chunks exist
    in the corpus for this question, pass it (more honest Recall@K). If
    omitted, we fall back to "however many relevant chunks we happened to
    retrieve" as the denominator, which UNDERESTIMATES how bad recall
    could be (it can never show a corpus miss) — acceptable for a student
    project's relative config comparisons, but call this out explicitly
    in the write-up rather than presenting Recall@K as if it were exact.
    """
    relevance = relevance_list(retrieved_docs, expected_source, required_keywords)
    total_relevant = total_relevant_hint if total_relevant_hint is not None else max(sum(relevance), 1)
    return {
        "precision_at_k": round(precision_at_k(relevance, k), 4),
        "recall_at_k": round(recall_at_k(relevance, k, total_relevant), 4),
        "mrr": round(reciprocal_rank(relevance), 4),
        "ndcg_at_k": round(ndcg_at_k(relevance, k), 4),
        "relevance_pattern": relevance,
    }


def aggregate(per_query_scores: list[dict]) -> dict:
    if not per_query_scores:
        return {}
    keys = ["precision_at_k", "recall_at_k", "mrr", "ndcg_at_k"]
    return {f"mean_{k}": round(sum(s[k] for s in per_query_scores) / len(per_query_scores), 4) for k in keys}


# ---------------------------------------------------------------------------
# Confusion matrix (agent routing)
# ---------------------------------------------------------------------------
def build_confusion_matrix(pairs: list[tuple[str, str]]) -> dict:
    """pairs: list of (expected_label, actual_label). Returns a nested
    dict matrix[expected][actual] = count, plus overall accuracy."""
    matrix: dict[str, dict[str, int]] = {}
    correct = 0
    for expected, actual in pairs:
        matrix.setdefault(expected, {})
        matrix[expected][actual] = matrix[expected].get(actual, 0) + 1
        if expected == actual:
            correct += 1
    accuracy = round(correct / len(pairs), 4) if pairs else 0.0
    return {"matrix": matrix, "accuracy": accuracy, "n": len(pairs)}


# ---------------------------------------------------------------------------
# Results I/O — every run gets its own timestamped file, nothing is ever
# overwritten. A `<name>_latest.json` copy is also written for convenience
# (e.g. for 04_config_comparisons to read the most recent 01 run without
# you having to type a timestamp), but the timestamped file is the record.
# ---------------------------------------------------------------------------
def save_result(results: dict | list, results_dir: str | Path, name: str) -> Path:
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = results_dir / f"{name}_{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    latest_path = results_dir / f"{name}_latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  -> saved {out_path.name}  (and updated {latest_path.name})")
    return out_path


def load_latest(results_dir: str | Path, name: str) -> dict | list:
    path = Path(results_dir) / f"{name}_latest.json"
    if not path.exists():
        raise FileNotFoundError(f"No prior run found at {path}. Run that eval script first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
