"""Bounded runner-capability diagnostics shared by Host and Agent.

The diagnostic describes a simulator preflight limitation.  It is deliberately
not a PLC fault receipt and carries no source, session, or mutation authority.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

_DIAGNOSTIC_KEYS = frozenset({"schemaVersion", "category", "issues"})
_AUTHORED_DIAGNOSTIC_KEYS = frozenset(
    {"schemaVersion", "category", "reasonCode", "requiredDesignSection"}
)
_ISSUE_KEYS = frozenset({"code", "collection", "itemId", "field", "signalIds"})
_COLLECTIONS = frozenset(
    {
        "controlNetworks",
        "timers",
        "alarms",
        "operatingModes",
        "sequences",
        "recoveryRules",
        "safetyRules",
    }
)
_MAX_PREFLIGHT_DIAGNOSTIC_BYTES = 100_000


def _invalid() -> ValueError:
    return ValueError("native simulation preflight diagnostic is invalid")


def _bounded_identifier(value: Any) -> bool:
    """Apply the generic bounded identifier rule without a naming policy."""

    return (
        isinstance(value, str)
        and 1 <= len(value) <= 80
        and not any(unicodedata.category(character) == "Cc" for character in value)
    )


def validate_native_preflight_diagnostic(value: Any) -> dict[str, Any]:
    """Validate and return a detached JSON copy of one preflight diagnostic."""

    if not isinstance(value, dict):
        raise _invalid()

    authored = value.get("schemaVersion") == (
        "controlstudio-native-simulation-preflight-diagnostic/v2"
    )
    if authored:
        if (
            set(value) != _AUTHORED_DIAGNOSTIC_KEYS
            or value.get("category") != "scenario_contract"
            or value.get("reasonCode")
            not in {
                "AUTHORED_SCENARIO_REQUIRED",
                "AUTHORED_SCENARIO_PROFILE_INVALID",
            }
            or value.get("requiredDesignSection") != "simulation_scenario"
        ):
            raise _invalid()
    elif (
        set(value) != _DIAGNOSTIC_KEYS
        or value.get("schemaVersion")
        != "controlstudio-native-simulation-preflight-diagnostic/v1"
        or value.get("category") != "runner_capability"
    ):
        raise _invalid()

    if not authored:
        issues = value.get("issues")
        if not isinstance(issues, list) or not 1 <= len(issues) <= 256:
            raise _invalid()
        for issue in issues:
            if not isinstance(issue, dict) or set(issue) != _ISSUE_KEYS:
                raise _invalid()
            if (
                issue.get("code") != "NON_STIMULUS_CONDITION"
                or not isinstance(issue.get("collection"), str)
                or issue.get("collection") not in _COLLECTIONS
                or not _bounded_identifier(issue.get("itemId"))
                or not _bounded_identifier(issue.get("field"))
            ):
                raise _invalid()
            signal_ids = issue.get("signalIds")
            if (
                not isinstance(signal_ids, list)
                or not 1 <= len(signal_ids) <= 64
                or any(not _bounded_identifier(signal_id) for signal_id in signal_ids)
                or len(set(signal_ids)) != len(signal_ids)
            ):
                raise _invalid()

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        projected = json.loads(encoded)
        encoded_bytes = len(encoded.encode("utf-8"))
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise _invalid() from exc
    if (
        not isinstance(projected, dict)
        or encoded_bytes > _MAX_PREFLIGHT_DIAGNOSTIC_BYTES
    ):
        raise _invalid()
    return projected


__all__ = ["validate_native_preflight_diagnostic"]
