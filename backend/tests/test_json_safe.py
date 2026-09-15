from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.services.json_safe import json_safe


def test_json_safe_handles_nested_types():
    payload = {
        "when": datetime(2026, 1, 1, 12, 0, 0),
        "score": Decimal("8.5"),
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "rows": [{"volume": 100}],
    }
    out = json_safe(payload)
    assert out["when"] == "2026-01-01T12:00:00"
    assert out["score"] == 8.5
    assert out["id"] == "00000000-0000-0000-0000-000000000001"
