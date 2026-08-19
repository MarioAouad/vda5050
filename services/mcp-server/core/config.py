from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

_THIS_DIR = Path(__file__).resolve().parent          # services/mcp-server/core
SERVICE_DIR = _THIS_DIR.parent                        # services/mcp-server
REPO_ROOT = SERVICE_DIR.parent.parent                 # repo root (services/mcp-server/../..)
load_dotenv(REPO_ROOT / ".env")                       # optional, for standalone (non-Docker) runs -- single source of truth, same file docker-compose reads for GROQ_API_KEY etc.

# --- Data directory -------------------------------------------------------
# Local dev (running this service directly, outside Docker): the root .env
# sets DATA_DIR=./data, QDRANT_PATH=./data/qdrant_db as paths RELATIVE TO
# THE REPO ROOT (not the current working directory) — resolved explicitly
# below so this works the same whether you run a script from the repo
# root, from evaluation/, or from services/mcp-server/.
# In Docker: docker-compose mounts the repo's `./data` to `/app/data` inside
# the container and sets DATA_DIR=/app/data (absolute) — see docker-compose.yml.
def _resolve_against_repo_root(value: str, default: Path) -> Path:
    p = Path(value) if value else default
    return p if p.is_absolute() else (REPO_ROOT / p)

DATA_DIR: Path = _resolve_against_repo_root(os.getenv("DATA_DIR", ""), REPO_ROOT / "data")
RAW_DOCS_DIR: Path = DATA_DIR / "raw_docs"

# --- Vector database --------------------------------------------------
# QDRANT_URL takes priority when set (standalone `vector-db` container,
# e.g. http://vector-db:6333). QDRANT_PATH is only used as an embedded,
# single-process fallback for local dev without Docker.
QDRANT_URL: str | None = os.getenv("QDRANT_URL") or None
QDRANT_PATH: Path = _resolve_against_repo_root(os.getenv("QDRANT_PATH", ""), DATA_DIR / "qdrant_db")

COLLECTION_NAME: str = os.getenv("QDRANT_COLLECTION", "vda5050_baseline")

# --- Embedding model --------------------------------------------------
EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")

# --- LLM (Groq) ---------------------------------------------------------
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
LLM_MODEL_NAME: str = os.getenv("LLM_MODEL_NAME", "llama-3.3-70b-versatile")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "2048"))

# --- Chunking defaults --------------------------------------------------
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))

# --- Retriever defaults --------------------------------------------------
RETRIEVER_K: int = int(os.getenv("RETRIEVER_K", "3"))

# --- This service's own network address ----------------------------------
# Used by server.py when it starts in streamable-http mode. Inside Docker,
# MCP_HOST must be 0.0.0.0 so the port is reachable from other containers.
MCP_HOST: str = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT: int = int(os.getenv("MCP_PORT", "8001"))
