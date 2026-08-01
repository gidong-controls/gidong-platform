from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_digest(value: Any) -> str:
    """Digest a protocol object independently of whitespace and key order."""
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def json_line(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
