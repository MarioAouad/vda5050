"""
Two small SQLite tables: `conversations` (title/timestamps for the sidebar)
and `documents` (metadata about files uploaded into a conversation — the
actual searchable content lives in Qdrant, tagged with document_id).
"""
from __future__ import annotations
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Same DATA_DIR convention as api/main.py: repo-root data/ for local dev,
# /app/data (docker-compose volume) inside Docker.
_SERVICE_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.getenv("DATA_DIR", str(_SERVICE_DIR.parent.parent / "data")))
DB_PATH = _DATA_DIR / "conversations.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL
        )
        """
    )
    # Global (knowledge-base) uploads — deliberately a separate table, not
    # a nullable conversation_id column on `documents`, so the two upload
    # scopes (one conversation vs. every conversation) can never be
    # confused by a stray NULL/empty-string check. See api/main.py's
    # /documents endpoints and core/server.py's ingest_document tool.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS global_documents (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def create_conversation() -> dict:
    conn = _get_conn()
    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conv_id, "New conversation", now, now),
    )
    conn.commit()
    conn.close()
    return {"id": conv_id, "title": "New conversation", "created_at": now, "updated_at": now}


def list_conversations() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversation(conv_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def touch_conversation(conv_id: str, title: str | None = None) -> None:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    if title:
        conn.execute("UPDATE conversations SET updated_at = ?, title = ? WHERE id = ?", (now, title, conv_id))
    else:
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))
    conn.commit()
    conn.close()


def delete_conversation(conv_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()


def create_document(document_id: str, conversation_id: str, filename: str, file_path: str, chunk_count: int) -> dict:
    # document_id is passed in — it MUST be the exact same id already used to
    # tag the chunks in Qdrant (see api/main.py's upload_document), not a
    # fresh one generated here. Generating a second, different id in this
    # function used to be the actual bug behind "delete does nothing": the
    # DB row and the Qdrant chunks ended up with two different ids, so
    # DELETE /documents/{id} filtered Qdrant on an id that matched zero
    # chunks — nothing was ever actually removed, even though the DB row
    # (and therefore the UI) looked deleted.
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO documents (id, conversation_id, filename, file_path, chunk_count, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (document_id, conversation_id, filename, file_path, chunk_count, now),
    )
    conn.commit()
    conn.close()
    return {
        "id": document_id, "conversation_id": conversation_id, "filename": filename,
        "file_path": file_path, "chunk_count": chunk_count, "uploaded_at": now,
    }


def list_documents(conversation_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM documents WHERE conversation_id = ? ORDER BY uploaded_at DESC", (conversation_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_document(document_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_document_row(document_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()
    conn.close()


def delete_documents_for_conversation(conversation_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM documents WHERE conversation_id = ?", (conversation_id,))
    conn.commit()
    conn.close()


# --- Global (knowledge-base) documents -------------------------------------

def create_global_document(document_id: str, filename: str, file_path: str, chunk_count: int) -> dict:
    # Same fix as create_document above: document_id must be the id already
    # used to tag the Qdrant chunks, not a fresh one generated here.
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO global_documents (id, filename, file_path, chunk_count, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (document_id, filename, file_path, chunk_count, now),
    )
    conn.commit()
    conn.close()
    return {
        "id": document_id, "filename": filename, "file_path": file_path,
        "chunk_count": chunk_count, "uploaded_at": now,
    }


def list_global_documents() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM global_documents ORDER BY uploaded_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_global_document(document_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM global_documents WHERE id = ?", (document_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_global_document_row(document_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM global_documents WHERE id = ?", (document_id,))
    conn.commit()
    conn.close()