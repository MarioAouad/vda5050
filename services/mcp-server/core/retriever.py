from __future__ import annotations
import logging
import pickle
from pathlib import Path
from langchain_classic.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder
from langchain_community.retrievers import BM25Retriever
from langchain_core.retrievers import BaseRetriever
from core.config import COLLECTION_NAME, QDRANT_PATH, RETRIEVER_K
from core.vectorstore import load_vectorstore

logger = logging.getLogger(__name__)

def get_retriever(
    collection_name: str = COLLECTION_NAME,
    k: int = RETRIEVER_K,
    file_type: str | None = None,
    conversation_id: str | None = None,
) -> BaseRetriever:
    """
    conversation_id: when set, also matches chunks uploaded into that
    specific conversation (in addition to the base corpus matched by
    file_type). This is an OR condition, not AND — a chunk needs to satisfy
    either the file_type match OR the conversation_id match.
    """
    fetch_k = 10
    vectorstore = load_vectorstore(collection_name=collection_name)

    # NOTE: fetch_k=10 is only meaningful for the hybrid branch below,
    # where BM25 + dense candidates get merged and THEN reranked down to
    # the caller's requested k -- overfetching there is the point. The
    # conversation_id branch returns qdrant_retriever directly with no
    # reranking step, so it must search for k (not fetch_k), or every
    # conversation_id-scoped call silently returns 10 chunks instead of
    # the k the caller asked for -- found via evaluation/01_retrieval_
    # progressive (config 07): its retrieved-pool size was 10, not the
    # k=3 requested, which is a second, separate bug from the known
    # dense-only-instead-of-hybrid trade-off in docs/PROJECT_GUIDE.md 3.5.
    # Qdrant's similarity search already returns results sorted by score,
    # so requesting k directly here returns the identical top-k as
    # fetching fetch_k and slicing -- this is a pure fix, not a behavior
    # change to what "correct" looks like.
    search_kwargs = {"k": k if conversation_id else fetch_k}
    if conversation_id:
        from qdrant_client.http import models
        # Two acceptable matches: (a) this exact conversation's own uploads
        # (any file_type — an uploaded .schema might still be relevant to a
        # protocol-rules search and vice versa), or (b) the base corpus,
        # correctly restricted to file_type AND to chunks with no
        # conversation_id at all. The base-corpus branch previously had no
        # conversation_id exclusion, which meant "OR match file_type" alone
        # was broad enough to also match every OTHER conversation's uploads
        # (they share the same file_type tag as the base corpus) — a
        # cross-conversation leak. Confirmed in testing: a brand-new
        # conversation that never had anything uploaded to it could still
        # answer questions about a different conversation's uploaded
        # document.
        base_corpus_must = [
            models.IsEmptyCondition(is_empty=models.PayloadField(key="metadata.conversation_id"))
        ]
        if file_type:
            base_corpus_must.append(
                models.FieldCondition(key="metadata.file_type", match=models.MatchValue(value=file_type))
            )
        search_kwargs["filter"] = models.Filter(should=[
            models.FieldCondition(key="metadata.conversation_id", match=models.MatchValue(value=conversation_id)),
            models.Filter(must=base_corpus_must),
        ])
    else:
        # No conversation_id was passed for this search — restricted to the
        # base corpus ONLY, for the same reason as above: file_type alone
        # isn't a safe filter, since uploads share the base corpus's
        # file_type values.
        from qdrant_client.http import models
        must_conditions = [
            models.IsEmptyCondition(is_empty=models.PayloadField(key="metadata.conversation_id"))
        ]
        if file_type:
            must_conditions.append(
                models.FieldCondition(key="metadata.file_type", match=models.MatchValue(value=file_type))
            )
        search_kwargs["filter"] = models.Filter(must=must_conditions)

    qdrant_retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs=search_kwargs,
    )

    # BM25's chunks.pkl only contains the base corpus (uploads aren't in it),
    # so when searching a specific conversation's uploads we skip the hybrid
    # path and return the dense retriever directly.
    if conversation_id:
        return qdrant_retriever

    chunks_path = QDRANT_PATH.parent / "chunks.pkl"
    if not chunks_path.exists():
        logger.warning(
            "chunks.pkl not found at %s. Did you run ingestion? "
            "Falling back to pure dense retriever.", chunks_path
        )
        return vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": k})

    with open(chunks_path, "rb") as f:
        chunks = pickle.load(f)

    if file_type:
        chunks = [c for c in chunks if c.metadata.get("file_type") == file_type]
        if not chunks:
            logger.warning(f"No chunks found for file_type={file_type}. Returning pure dense retriever.")
            return qdrant_retriever

    bm25_retriever = BM25Retriever.from_documents(chunks)
    bm25_retriever.k = fetch_k

    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, qdrant_retriever],
        weights=[0.5, 0.5]
    )
    model = HuggingFaceCrossEncoder(model_name="BAAI/bge-reranker-base")
    compressor = CrossEncoderReranker(model=model, top_n=k)

    compression_retriever = ContextualCompressionRetriever(
        base_compressor=compressor,
        base_retriever=ensemble_retriever
    )

    logger.info(
        "Hybrid Retriever ready — dense+sparse(k=%d), reranked to top_n=%d",
        fetch_k,
        k,
    )
    return compression_retriever