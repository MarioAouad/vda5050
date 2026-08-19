"""
Direct tests of the two deterministic tools (no HTTP layer, no FastAPI
TestClient needed — these call the same functions the endpoints call).
Run from services/agent-system-b/: pytest
"""
import json
import sys
from pathlib import Path

import jsonschema
import pytest

_APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_APP_DIR))

from app.data import ERROR_LEVELS, ERROR_TYPES, SCHEMA_FILES

DATA_DIR = _APP_DIR.parent.parent / "data"
SCHEMAS_DIR = DATA_DIR / "raw_docs" / "json_schemas"


def _validate(schema_name: str, payload: dict) -> list[str]:
    schema = json.loads((SCHEMAS_DIR / SCHEMA_FILES[schema_name]).read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    return [f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in validator.iter_errors(payload)]


def test_schema_files_exist_and_parse():
    for name, filename in SCHEMA_FILES.items():
        path = SCHEMAS_DIR / filename
        assert path.exists(), f"missing schema file for {name}: {path}"
        json.loads(path.read_text(encoding="utf-8"))  # must be valid JSON (explicit encoding — see app/tools.py comment)


def test_valid_order_passes():
    # Minimal order payload built from order.schema's own required fields.
    schema = json.loads((SCHEMAS_DIR / "order.schema").read_text(encoding="utf-8"))
    required = schema["required"]
    payload = {
        "headerId": 1,
        "timestamp": "2026-08-13T12:00:00.000Z",
        "version": "2.0.0",
        "manufacturer": "RobotCorp",
        "serialNumber": "ABC123",
        "orderId": "order-1",
        "orderUpdateId": 0,
        "nodes": [],
        "edges": [],
    }
    missing = set(required) - set(payload)
    assert not missing, f"test payload is missing required fields the schema actually needs: {missing}"
    errors = _validate("order", payload)
    assert errors == [], f"expected a valid order to pass, got: {errors}"


def test_invalid_order_missing_required_field_fails():
    payload = {"headerId": 1}  # missing almost everything order.schema requires
    errors = _validate("order", payload)
    assert len(errors) > 0


def test_wrong_type_fails():
    schema = json.loads((SCHEMAS_DIR / "order.schema").read_text(encoding="utf-8"))
    required = schema["required"]
    payload = {
        "headerId": "not-a-number",  # should be an integer per the schema
        "timestamp": "2026-08-13T12:00:00.000Z",
        "version": "2.0.0",
        "manufacturer": "RobotCorp",
        "serialNumber": "ABC123",
        "orderId": "order-1",
        "orderUpdateId": 0,
        "nodes": [],
        "edges": [],
    }
    assert not (set(required) - set(payload))
    errors = _validate("order", payload)
    assert any("headerId" in e for e in errors)


def test_all_error_types_have_valid_levels():
    for error_type, entry in ERROR_TYPES.items():
        assert entry["level"] in ERROR_LEVELS, f"{error_type} references unknown level {entry['level']}"


def test_error_lookup_known_type():
    assert "NODE_UNREACHABLE" in ERROR_TYPES
    assert ERROR_TYPES["NODE_UNREACHABLE"]["level"] == "CRITICAL"


def test_error_lookup_case_insensitive_matching_key():
    # main.py uppercases the input before lookup — verify the source data
    # itself is already uppercase so that actually works.
    assert all(k == k.upper() for k in ERROR_TYPES)


def test_error_type_count_matches_spec_table():
    # Spec section 6.6.5.4 defines exactly 22 predefined error types.
    assert len(ERROR_TYPES) == 22
