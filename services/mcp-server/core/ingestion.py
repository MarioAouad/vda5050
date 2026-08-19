
from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from core.config import CHUNK_OVERLAP, CHUNK_SIZE, RAW_DOCS_DIR

logger = logging.getLogger(__name__)

# File extensions we ingest
_INGEST_EXTENSIONS: set[str] = {".md", ".schema"}

# Loading
def load_documents(source_dir: Path = RAW_DOCS_DIR) -> list[Document]:

    documents: list[Document] = []

    if not source_dir.exists():
        logger.error("Source directory does not exist: %s", source_dir)
        return documents

    for file_path in sorted(source_dir.rglob("*")):
        if file_path.suffix.lower() not in _INGEST_EXTENSIONS:
            continue
        if not file_path.is_file():
            continue

        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping non-UTF-8 file: %s", file_path)
            continue

        if not text.strip():
            logger.warning("Skipping empty file: %s", file_path)
            continue

        rel_path = file_path.relative_to(source_dir)
        documents.append(
            Document(
                page_content=text,
                metadata={
                    "source": str(rel_path),
                    "file_type": file_path.suffix.lower(),
                },
            )
        )
        logger.info("Loaded %s (%d chars)", rel_path, len(text))

    logger.info("Total files loaded: %d", len(documents))
    return documents

# JSON Schema Chunking (Parent-Child)
def _chunk_json_schema(doc: Document) -> list[Document]:

    source = doc.metadata.get("source", "unknown")
    chunks: list[Document] = []

    try:
        schema = json.loads(doc.page_content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON schema: %s — falling back to text splitter", source)
        return _chunk_text_fallback(doc)

    schema_title = schema.get("title", source)
    schema_desc = schema.get("description", "")
    schema_required = schema.get("required", [])

    # Schema overview chunk
    overview_lines = [
        f"JSON Schema: {schema_title}",
        f"Source file: {source}",
        f"Description: {schema_desc}",
        f"Required top-level fields: {', '.join(schema_required)}",
    ]
    # Add subtopic if present
    if "subtopic" in schema:
        overview_lines.append(f"MQTT Subtopic: {schema['subtopic']}")

    overview_text = "\n".join(overview_lines)
    chunks.append(Document(
        page_content=overview_text,
        metadata={
            "source": source,
            "file_type": ".schema",
            "chunk_type": "schema_overview",
            "schema_title": schema_title,
        },
    ))

    # One chunk per top-level property
    parent_header = (
        f"[Schema: {schema_title} | Source: {source} | "
        f"Required fields: {', '.join(schema_required)}]\n\n"
    )
    properties = schema.get("properties", {})
    if properties:
        prop_text = _format_properties(properties, indent=0)
        if len(prop_text) < 2000:
            # Small enough to keep together
            chunk_text = parent_header + "Top-level properties:\n\n" + prop_text
            chunks.append(Document(
                page_content=chunk_text,
                metadata={
                    "source": source,
                    "file_type": ".schema",
                    "chunk_type": "schema_properties",
                    "schema_title": schema_title,
                },
            ))
        else:
            # Split into individual property chunks
            for prop_name, prop_def in properties.items():
                prop_chunk_text = (
                    parent_header
                    + f"Property: {prop_name}\n"
                    + _format_single_property(prop_name, prop_def, indent=0)
                )
                chunks.append(Document(
                    page_content=prop_chunk_text,
                    metadata={
                        "source": source,
                        "file_type": ".schema",
                        "chunk_type": "schema_property",
                        "schema_title": schema_title,
                        "property_name": prop_name,
                    },
                ))

    # One chunk per definition (node, edge, action, etc.)
    definitions = schema.get("definitions", {})
    for def_name, def_body in definitions.items():
        def_title = def_body.get("title", def_name)
        def_required = def_body.get("required", [])
        def_desc = def_body.get("description", "")

        def_header = (
            f"[Schema: {schema_title} | Source: {source} | "
            f"Definition: {def_title} | "
            f"Required fields: {', '.join(def_required)}]\n\n"
        )

        # Format the definition overview
        def_overview = f"Definition: {def_title}\n"
        if def_desc:
            def_overview += f"Description: {def_desc}\n"
        def_overview += f"Required fields: {', '.join(def_required)}\n\n"

        # Format all properties of this definition
        def_properties = def_body.get("properties", {})
        def_props_text = _format_properties(def_properties, indent=0)

        full_def_text = def_header + def_overview + def_props_text

        # If the definition is very large, split it further
        if len(full_def_text) > 2500:
            # First: definition overview chunk
            chunks.append(Document(
                page_content=def_header + def_overview + f"This definition has {len(def_properties)} properties. See individual property chunks for details.",
                metadata={
                    "source": source,
                    "file_type": ".schema",
                    "chunk_type": "definition_overview",
                    "schema_title": schema_title,
                    "definition_name": def_name,
                },
            ))
            # Then: one chunk per property within the definition
            for prop_name, prop_def in def_properties.items():
                prop_text = (
                    def_header
                    + f"Property '{prop_name}' in definition '{def_title}':\n"
                    + _format_single_property(prop_name, prop_def, indent=0)
                )
                chunks.append(Document(
                    page_content=prop_text,
                    metadata={
                        "source": source,
                        "file_type": ".schema",
                        "chunk_type": "definition_property",
                        "schema_title": schema_title,
                        "definition_name": def_name,
                        "property_name": prop_name,
                    },
                ))
        else:
            chunks.append(Document(
                page_content=full_def_text,
                metadata={
                    "source": source,
                    "file_type": ".schema",
                    "chunk_type": "definition",
                    "schema_title": schema_title,
                    "definition_name": def_name,
                },
            ))

    logger.info("JSON schema '%s' → %d structural chunks", source, len(chunks))
    return chunks

def _format_properties(properties: dict, indent: int = 0) -> str:

    lines = []
    prefix = "  " * indent
    for name, defn in properties.items():
        lines.append(_format_single_property(name, defn, indent))
    return "\n".join(lines)

def _format_single_property(name: str, defn: dict, indent: int = 0) -> str:

    prefix = "  " * indent
    lines = [f"{prefix}- {name}:"]
    if isinstance(defn, dict):
        prop_type = defn.get("type", defn.get("$ref", "unknown"))
        lines.append(f"{prefix}    Type: {prop_type}")
        if "description" in defn:
            lines.append(f"{prefix}    Description: {defn['description']}")
        if "enum" in defn:
            lines.append(f"{prefix}    Enum values: {', '.join(str(v) for v in defn['enum'])}")
        if "minimum" in defn:
            lines.append(f"{prefix}    Minimum: {defn['minimum']}")
        if "maximum" in defn:
            lines.append(f"{prefix}    Maximum: {defn['maximum']}")
        if "format" in defn:
            lines.append(f"{prefix}    Format: {defn['format']}")
        if "examples" in defn:
            lines.append(f"{prefix}    Examples: {defn['examples']}")
        if "$ref" in defn:
            lines.append(f"{prefix}    Reference: {defn['$ref']}")
        # Handle nested properties
        if "properties" in defn:
            lines.append(f"{prefix}    Nested properties:")
            lines.append(_format_properties(defn["properties"], indent + 2))
        if "required" in defn:
            lines.append(f"{prefix}    Required: {', '.join(defn['required'])}")
        if "items" in defn:
            items = defn["items"]
            if isinstance(items, dict):
                if "$ref" in items:
                    lines.append(f"{prefix}    Items: {items['$ref']}")
                elif "type" in items:
                    lines.append(f"{prefix}    Items type: {items['type']}")
    return "\n".join(lines)

# Markdown Header-Aware Chunking
def _chunk_markdown(doc: Document) -> list[Document]:

    source = doc.metadata.get("source", "unknown")

    # Define headers to split on
    headers_to_split_on = [
        ("#", "h1"),
        ("##", "h2"),
        ("###", "h3"),
    ]

    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False,
    )

    try:
        md_chunks = md_splitter.split_text(doc.page_content)
    except Exception as e:
        logger.warning("Markdown splitter failed for %s: %s — falling back", source, e)
        return _chunk_text_fallback(doc)

    # Secondary splitter for oversized chunks
    secondary_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1500,
        chunk_overlap=200,
        length_function=len,
        separators=["\n\n", "\n", " ", ""],
    )

    chunks: list[Document] = []
    for md_chunk in md_chunks:
        # Build header context string from metadata
        header_parts = []
        for key in ["h1", "h2", "h3"]:
            if key in md_chunk.metadata:
                header_parts.append(md_chunk.metadata[key])
        header_context = " > ".join(header_parts)

        # Prepend header context to chunk content if headers were found
        content = md_chunk.page_content
        if header_context:
            content = f"[Section: {header_context}]\n\n{content}"

        if len(content) > 1500:
            # Split further
            sub_doc = Document(page_content=content, metadata={})
            sub_chunks = secondary_splitter.split_documents([sub_doc])
            for idx, sc in enumerate(sub_chunks):
                sc.metadata = {
                    "source": source,
                    "file_type": doc.metadata.get("file_type", ".md"),
                    "chunk_type": "markdown_section",
                    "section": header_context,
                    "sub_chunk": idx,
                }
                chunks.append(sc)
        else:
            chunks.append(Document(
                page_content=content,
                metadata={
                    "source": source,
                    "file_type": doc.metadata.get("file_type", ".md"),
                    "chunk_type": "markdown_section",
                    "section": header_context,
                },
            ))

    logger.info("Markdown '%s' → %d header-aware chunks", source, len(chunks))
    return chunks

# Fallback: Standard Text Splitter
def _chunk_text_fallback(doc: Document) -> list[Document]:

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = splitter.split_documents([doc])
    for idx, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = idx
    return chunks

# Main Chunking Dispatcher
def chunk_documents(documents: list[Document]) -> list[Document]:

    all_chunks: list[Document] = []

    for doc in documents:
        file_type = doc.metadata.get("file_type", "")
        source = doc.metadata.get("source", "unknown")

        if file_type == ".schema":
            chunks = _chunk_json_schema(doc)
        elif file_type == ".md":
            chunks = _chunk_markdown(doc)
        else:
            logger.warning("Unknown file type '%s' for %s — using fallback", file_type, source)
            chunks = _chunk_text_fallback(doc)

        # Add chunk_index to all chunks
        for idx, chunk in enumerate(chunks):
            chunk.metadata["chunk_index"] = idx

        all_chunks.extend(chunks)

    logger.info(
        "Chunking complete: %d documents -> %d structure-aware chunks",
        len(documents),
        len(all_chunks),
    )
    return all_chunks
