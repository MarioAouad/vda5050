"""
The two deterministic tools for Agent System B — schema validation and
error-code lookup. Plain Python functions, callable two ways:

  1. Directly, by the raw FastAPI endpoints in app/main.py (POST
     /validate-payload, POST /lookup-error) — kept available for callers
     that want to skip the LLM routing layer entirely.
  2. As ADK FunctionTools, auto-wrapped by google-adk from each function's
     signature, type hints, and docstring (see app/agent.py). This is the
     path agent-system-a actually calls today, via POST /agent/ask.

Either way, this is the ONE place the actual logic lives now — main.py and
agent.py both call into this module rather than each having their own copy,
so there's a single source of truth for what counts as "valid" or "found".

ADK auto-generates each tool's schema from the type hints + docstring below
(Google-style Args/Returns), which is why the docstrings here are written
for a model to read, not just a human.
"""
import json
import os
from pathlib import Path

import jsonschema

from app.data import ERROR_LEVELS, ERROR_TYPES, SCHEMA_FILES

DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent.parent / "data")))
SCHEMAS_DIR = DATA_DIR / "raw_docs" / "json_schemas"


def validate_payload_tool(schema_name: str, payload: dict) -> dict:
    """Validate a JSON payload against the real VDA 5050 JSON Schema for a topic.

    Args:
        schema_name: Which VDA 5050 schema to validate against. Must be one
            of: order, state, instantActions, connection, visualization,
            factsheet, zoneSet, responses.
        payload: The JSON payload to check, as a dict.

    Returns:
        A dict with two keys: 'valid' (bool) and 'errors' (a list of
        field-level error strings, empty if the payload is valid).
    """
    if schema_name not in SCHEMA_FILES:
        return {
            "valid": False,
            "errors": [f"Unknown schema_name '{schema_name}'. Valid options: {sorted(SCHEMA_FILES)}"],
        }

    schema_path = SCHEMAS_DIR / SCHEMA_FILES[schema_name]
    if not schema_path.exists():
        return {"valid": False, "errors": [f"Schema file not found on disk: {schema_path}"]}

    # encoding="utf-8" is NOT optional here: several of the shipped .schema
    # files (factsheet.schema in particular) contain UTF-8 curly quotes
    # (“ ”). read_text() without an explicit encoding uses the platform's
    # default locale encoding, which on Windows is cp1252, not UTF-8 — that
    # mismatch is exactly what threw UnicodeDecodeError: 'charmap' codec
    # can't decode byte 0x9d in your pytest run. Linux/Mac containers
    # default to UTF-8 already, which is why this never showed up in Docker.
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    error_messages = [
        f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors
    ]

    return {"valid": len(error_messages) == 0, "errors": error_messages}


def lookup_error_tool(error_type: str) -> dict:
    """Look up the VDA 5050 standard's defined meaning, severity level, and
    handling guidance for a given errorType.

    Args:
        error_type: The errorType to look up, e.g. NODE_UNREACHABLE,
            LOCALIZATION_ERROR, VALIDATION_FAILURE. Case-insensitive.

    Returns:
        A dict with 'found' (bool). If found, also includes 'error_type',
        'level', 'level_meaning', 'robot_behavior', 'description',
        'typical_reference', and 'report_duration'. If not found, that
        means the errorType is genuinely not part of the standard — this
        is the correct, final answer, not a reason to retry with different
        spelling or capitalization.
    """
    key = error_type.strip().upper()
    entry = ERROR_TYPES.get(key)
    if entry is None:
        return {"found": False, "error_type": error_type}

    level_info = ERROR_LEVELS[entry["level"]]
    return {
        "found": True,
        "error_type": key,
        "level": entry["level"],
        "level_meaning": level_info["meaning"],
        "robot_behavior": level_info["robot_behavior"],
        "description": entry["description"],
        "typical_reference": entry["reference"],
        "report_duration": entry["report_duration"],
    }
