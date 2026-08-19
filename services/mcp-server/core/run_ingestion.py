from __future__ import annotations
import logging
import sys
import textwrap
from pathlib import Path

# Ensure the mcp-server service root (services/mcp-server) is on sys.path
# when running this as a standalone script, e.g. `python core/run_ingestion.py`
# — running it as `python -m core.run_ingestion` from services/mcp-server
# doesn't need this, since -m already puts the current directory on sys.path.
_SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from core.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_NAME,
    EMBEDDING_MODEL_NAME,
    QDRANT_PATH,
    RAW_DOCS_DIR,
)
from core.ingestion import chunk_documents, load_documents
from core.vectorstore import upsert_documents

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ingestion")

# Helpers
def _preview_chunk(chunk, index: int) -> None:

    content = chunk.page_content
    preview = textwrap.shorten(content, width=300, placeholder=" [...]")
    print(f"\n{'=' * 70}")
    print(f"  SAMPLE CHUNK #{index + 1}")
    print(f"{'=' * 70}")
    print(f"  Source   : {chunk.metadata.get('source', 'N/A')}")
    print(f"  File type: {chunk.metadata.get('file_type', 'N/A')}")
    print(f"  Index    : {chunk.metadata.get('chunk_index', 'N/A')}")
    print(f"  Length   : {len(content)} chars")
    print(f"{'- ' * 35}")
    print(preview)
    print(f"{'=' * 70}")

# Main
def main() -> None:
    print("\n" + "=" * 70)
    print("  VDA-5050 RAG — Phase 2 Ingestion Pipeline")
    print("  (Structure-Aware Chunking + Vision Descriptions)")
    print("=" * 70)
    print(f"  Source dir      : {RAW_DOCS_DIR}")
    print(f"  Chunk size      : {CHUNK_SIZE} (fallback/secondary)")
    print(f"  Chunk overlap   : {CHUNK_OVERLAP}")
    print(f"  Embedding model : {EMBEDDING_MODEL_NAME}")
    print(f"  Qdrant path     : {QDRANT_PATH}")
    print(f"  Collection      : {COLLECTION_NAME}")
    print(f"  Strategies      : JSON-aware (.schema) | Markdown-header (.md)")
    print("=" * 70)

    # Step 1: Load
    logger.info("Step 1/3 — Loading documents...")
    documents = load_documents()
    if not documents:
        logger.error("No documents found. Check RAW_DOCS_DIR: %s", RAW_DOCS_DIR)
        sys.exit(1)

    print(f"\n  Loaded {len(documents)} files:")
    for doc in documents:
        src = doc.metadata["source"]
        print(f"    - {src} ({len(doc.page_content):,} chars)")

    # Step 2: Chunk
    logger.info("Step 2/3 — Chunking documents...")
    chunks = chunk_documents(documents)
    print(f"\n  Generated {len(chunks)} chunks total.")

    # Log 2 sample chunks for inspection
    for i in range(min(2, len(chunks))):
        _preview_chunk(chunks[i], i)

    # Step 3: Embed & upsert
    logger.info("Step 3/3 — Embedding and upserting into Qdrant...")
    print(f"\n  Embedding {len(chunks)} chunks (this may take a few minutes on first run)...")
    upsert_documents(chunks)

    # Save chunks for BM25 retrieval
    import pickle
    chunks_path = QDRANT_PATH.parent / "chunks.pkl"
    with open(chunks_path, "wb") as f:
        pickle.dump(chunks, f)
    logger.info(f"Saved {len(chunks)} chunks to {chunks_path} for BM25 retrieval.")

    print("\n" + "=" * 70)
    print("  INGESTION COMPLETE")
    print(f"  {len(chunks)} vectors stored in collection '{COLLECTION_NAME}'")
    print(f"  Chunks saved to {chunks_path}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
