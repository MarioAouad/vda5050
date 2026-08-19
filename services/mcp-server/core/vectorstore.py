from __future__ import annotations
import logging
from pathlib import Path
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_core.documents import Document
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from core.config import (
    COLLECTION_NAME,
    EMBEDDING_MODEL_NAME,
    QDRANT_PATH,
    QDRANT_URL,
)

logger = logging.getLogger(__name__)

# Embedding model (singleton-style cache at module level)
_embedding_model: HuggingFaceEmbeddings | None = None

def get_embedding_model() -> HuggingFaceEmbeddings:

    global _embedding_model
    if _embedding_model is None:
        logger.info("Loading embedding model: %s ...", EMBEDDING_MODEL_NAME)
        _embedding_model = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_NAME,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        logger.info("Embedding model loaded successfully.")
    return _embedding_model

# Qdrant client helpers
def get_qdrant_client() -> QdrantClient:
    """
    Standalone server mode (QDRANT_URL set, e.g. by docker-compose pointing
    at the `vector-db` container) takes priority. Falls back to an embedded,
    single-process local file store for running this service without Docker.
    """
    if QDRANT_URL:
        client = QdrantClient(url=QDRANT_URL)
        logger.info("Qdrant client connected at: %s (standalone server)", QDRANT_URL)
        return client

    Path(QDRANT_PATH).mkdir(parents=True, exist_ok=True)
    client = QdrantClient(path=QDRANT_PATH)
    logger.info("Qdrant client connected at: %s (embedded, local dev only)", QDRANT_PATH)
    return client

def ensure_collection(
    client: QdrantClient,
    collection_name: str = COLLECTION_NAME,
    vector_size: int | None = None,
) -> None:

    existing = [c.name for c in client.get_collections().collections]
    if collection_name in existing:
        logger.info("Collection '%s' already exists — skipping creation.", collection_name)
        return

    if vector_size is None:
        embeddings = get_embedding_model()
        sample_vector = embeddings.embed_query("dimension probe")
        vector_size = len(sample_vector)
        logger.info("Detected embedding dimension: %d", vector_size)

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    logger.info("Created collection '%s' (dim=%d, cosine).", collection_name, vector_size)

# Upsert documents
def upsert_documents(
    chunks: list[Document],
    collection_name: str = COLLECTION_NAME,
) -> QdrantVectorStore:

    embeddings = get_embedding_model()

    logger.info(
        "Upserting %d chunks into collection '%s' ...",
        len(chunks),
        collection_name,
    )

    # Goes through get_qdrant_client() so this respects QDRANT_URL the same
    # way every other function here does (from_documents(path=...) would
    # silently ignore QDRANT_URL and always write to the embedded store).
    client = get_qdrant_client()
    vector_size = len(embeddings.embed_query("dimension probe"))
    client.recreate_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    vectorstore = QdrantVectorStore(
        client=client, collection_name=collection_name, embedding=embeddings
    )
    vectorstore.add_documents(chunks)

    logger.info("Upsert complete — %d vectors stored.", len(chunks))
    return vectorstore

# Load an existing vector store (for query-time use)
def load_vectorstore(
    collection_name: str = COLLECTION_NAME,
) -> QdrantVectorStore:

    embeddings = get_embedding_model()
    client = get_qdrant_client()

    # Verify the collection exists
    existing = [c.name for c in client.get_collections().collections]
    if collection_name not in existing:
        raise RuntimeError(
            f"Collection '{collection_name}' not found in Qdrant at "
            f"'{QDRANT_PATH}'.  Run ingestion first."
        )

    vectorstore = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embeddings,
    )
    logger.info("Loaded existing vectorstore '%s'.", collection_name)
    return vectorstore
