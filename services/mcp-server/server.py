from mcp.server.fastmcp import FastMCP
from core.config import MCP_HOST, MCP_PORT
from core.retriever import get_retriever

# Runs as its own container, called by Agent System A over the network
# (streamable-http) rather than spawned as a stdio subprocess — see
# services/agent-system-a/api/main.py for the client side of this.
mcp = FastMCP("vda-5050-oracle", host=MCP_HOST, port=MCP_PORT)

@mcp.tool()
def search_protocol_rules(query: str, conversation_id: str = "") -> str:
    """
    Search the VDA-5050 standard protocol rules (Markdown documents).
    Use this tool when you need to understand rules, constraints, logic, and standard definitions.
    Pass conversation_id (given to you in your system prompt) to ALSO search
    both (a) documents uploaded globally to the knowledge base, visible to
    every conversation, and (b) documents uploaded specifically to this
    conversation.
    """
    retriever = get_retriever(file_type=".md", conversation_id=conversation_id or None)
    docs = retriever.invoke(query)
    if not docs:
        return "No relevant protocol rules found."
    return "\n\n---\n\n".join([doc.page_content for doc in docs])

@mcp.tool()
def search_json_schemas(query: str, conversation_id: str = "") -> str:
    """
    Search the VDA-5050 JSON schema specifications.
    Use this tool when you need to know exact required fields, data types, and JSON structures.
    Pass conversation_id (given to you in your system prompt) to ALSO search
    both (a) documents uploaded globally to the knowledge base, visible to
    every conversation, and (b) documents uploaded specifically to this
    conversation.
    """
    retriever = get_retriever(file_type=".schema", conversation_id=conversation_id or None)
    docs = retriever.invoke(query)
    if not docs:
        return "No relevant JSON schemas found."
    return "\n\n---\n\n".join([doc.page_content for doc in docs])


@mcp.tool()
def ingest_document(text: str, filename: str, document_id: str, conversation_id: str = "") -> str:
    """
    Chunk a document's text and store it in the vector database, tagged
    with document_id so it can later be found or deleted.

    conversation_id is now OPTIONAL and controls the upload's scope:
      - conversation_id set: this document is scoped to ONE conversation
        only. get_retriever()'s conversation_id branch matches it there,
        and nowhere else.
      - conversation_id omitted/empty (the new "global" upload path): the
        chunk metadata simply does not get a conversation_id key at all.
        get_retriever()'s IsEmptyCondition base-corpus check treats
        "conversation_id absent" the same as the shipped VDA 5050 spec
        docs — meaning a global upload becomes part of the base corpus and
        is findable from EVERY conversation, immediately, without needing
        conversation_id passed at all. This is the mechanism behind the
        "global knowledge base" upload tab: same ingestion pipeline, same
        chunking, same collection — just without the per-conversation tag.

    Called directly by the backend when a user uploads a document — not
    intended to be called by the routing agent during normal Q&A.
    """
    from pathlib import Path
    from langchain_core.documents import Document
    from langchain_qdrant import QdrantVectorStore
    from core.ingestion import chunk_documents
    from core.vectorstore import get_qdrant_client, get_embedding_model, ensure_collection
    from core.config import COLLECTION_NAME

    suffix = Path(filename).suffix.lower()
    file_type = suffix if suffix == ".schema" else ".md"
    base_doc = Document(page_content=text, metadata={"source": filename, "file_type": file_type})

    chunks = chunk_documents([base_doc])
    for i, c in enumerate(chunks):
        c.metadata["document_id"] = document_id
        c.metadata["chunk_index"] = i
        if conversation_id:
            c.metadata["conversation_id"] = conversation_id
        # else: leave conversation_id unset entirely — see docstring above.

    client = get_qdrant_client()
    ensure_collection(client, COLLECTION_NAME)
    vectorstore = QdrantVectorStore(
        client=client, collection_name=COLLECTION_NAME, embedding=get_embedding_model()
    )
    vectorstore.add_documents(chunks)  # adds only — never wipes existing data
    scope = f"conversation {conversation_id}" if conversation_id else "the global knowledge base"
    return f"Ingested {len(chunks)} chunks from {filename} into {scope}."


@mcp.tool()
def delete_document(document_id: str) -> str:
    """
    Remove every chunk belonging to one uploaded document from the vector
    database. Called directly by the backend when a user deletes one document.
    """
    from qdrant_client.http import models
    from core.vectorstore import get_qdrant_client
    from core.config import COLLECTION_NAME

    client = get_qdrant_client()
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.FilterSelector(
            filter=models.Filter(must=[
                models.FieldCondition(key="metadata.document_id", match=models.MatchValue(value=document_id))
            ])
        ),
    )
    return f"Deleted all chunks for document {document_id}."


@mcp.tool()
def delete_conversation_documents(conversation_id: str) -> str:
    """
    Remove every chunk from every document uploaded within one conversation.
    Called directly by the backend when an entire conversation is deleted.
    """
    from qdrant_client.http import models
    from core.vectorstore import get_qdrant_client
    from core.config import COLLECTION_NAME

    client = get_qdrant_client()
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.FilterSelector(
            filter=models.Filter(must=[
                models.FieldCondition(key="metadata.conversation_id", match=models.MatchValue(value=conversation_id))
            ])
        ),
    )
    return f"Deleted all documents for conversation {conversation_id}."


@mcp.resource("overview://vda_5050")
def vda_5050_overview() -> str:
    """
    Returns a high-level overview of the VDA-5050 standard.
    """
    return (
        "The VDA-5050 standard establishes a universal communication interface "
        "leveraging lightweight MQTT messaging and structured JSON payloads to coordinate "
        "transport orders and robot states regardless of the vehicle's manufacturer.\n"
        "It consists of Protocol Rules (Markdown) and JSON Schemas."
    )

if __name__ == "__main__":
    # Exposes a streamable-HTTP endpoint at http://<MCP_HOST>:<MCP_PORT>/mcp
    mcp.run(transport="streamable-http")