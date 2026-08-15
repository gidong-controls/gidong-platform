"""Host-authored, bounded receipts for read-only PLC structural analysis.

The MCP result remains useful to the private Agent for explanation, but it is
not authoritative merely because it crossed the tool boundary.  This module
reduces structural-analysis results to small canonical facts that the
Host can persist, show to the model, and independently compare with the final
engineering-analysis artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .mcp_client import MCPToolError

STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA = "controlstudio-structural-analysis-evidence/v1"
STRUCTURAL_ANALYSIS_TOOL_COVERAGE: dict[str, str] = {
    "project.structure_index": "project_structure",
    "workspace.structure_index": "project_structure",
    "project.search": "programs",
    "program.logic_graph": "logic_graph",
    "program.state_model": "state_model",
    "device.references": "cross_references",
    "device.causal_impact": "causal_paths",
    "project.behavior_report": "programs",
}


def structural_receipt_tool_name(tool_name: str) -> str:
    """Return the durable structural receipt tool for an Agent-visible tool."""

    return (
        "project.structure_index"
        if tool_name == "workspace.structure_index"
        else tool_name
    )


AMBIGUOUS_STRUCTURAL_SCOPE_LIMITATION = (
    "Coverage counts are omitted because selected structural receipts overlap or "
    "have an ambiguous scope."
)

_FACT_COUNT_LIMIT = 200
_MODEL_CONTEXT_ITEM_LIMIT = 32
_PAYLOAD_BINDING_LIMIT = 200
_LOCATION_FACT_BYTE_LIMIT = 80_000
_RELATION_FACT_BYTE_LIMIT = 120_000
_STRUCTURE_FACT_BYTE_LIMIT = 120_000
_PROJECT_STRUCTURE_RAW_BYTE_LIMIT = 32 * 1024 * 1024
_DEVICE_TOKEN = re.compile(r"(?<![A-Z0-9.])([A-Z][A-Z0-9]*[0-9][A-Z0-9.]*)")
_EXACT_DEVICE = re.compile(r"^[A-Z][A-Z0-9]*[0-9][A-Z0-9.]*$")
_DIRECT_DATA_REGISTER = re.compile(r"^D(?P<index>[0-9]+)$")
_DATA_LITERAL = re.compile(r"^(?:[+-]?[0-9]+|H[0-9A-F]+)$", re.IGNORECASE)
_COMPARISON_RELATIONS = {
    "=": "equal",
    "<>": "not_equal",
    ">": "greater_than",
    "<": "less_than",
    ">=": "greater_than_or_equal",
    "<=": "less_than_or_equal",
}
_COMPARISON_DEVICE = re.compile(r"^[PMKFTCLD][0-9]+$")
_COMPARISON_LITERAL = re.compile(r"^[+-]?[0-9]+$")
_STATEFUL_ACTIONS = frozenset({"SET", "RST", "TON", "TOF", "CTR", "CTU", "CTD", "TRTG"})
_DATA_TRANSFER_ACTIONS = frozenset({"MOV", "DMOV"})
_DATA_TRANSFER_WIDTH_WORDS = {"MOV": 1, "DMOV": 2}
_IN_PLACE_MUTATION_BASE_WIDTHS = {
    "INC": 1,
    "DINC": 2,
    "DEC": 1,
    "DDEC": 2,
    "INCU": 1,
    "DINCU": 2,
    "DECU": 1,
    "DDECU": 2,
}
_IN_PLACE_MUTATION_WIDTH_WORDS = {
    f"{mnemonic}{suffix}": width_words
    for mnemonic, width_words in _IN_PLACE_MUTATION_BASE_WIDTHS.items()
    for suffix in ("", "P")
}
_IN_PLACE_MUTATION_ACTIONS = frozenset(_IN_PLACE_MUTATION_WIDTH_WORDS)
_ACCESS_KINDS = frozenset(
    {"read", "write", "state_write", "condition", "action", "argument", "unknown"}
)
_RELATION_KINDS = frozenset(
    {"read", "write", "state_write", "condition", "causal_path", "state_transition"}
)
_SEMANTIC_STATUSES = frozenset({"observed", "structured", "partial"})
_COVERAGE_STATUSES = frozenset({"complete", "partial", "not_observed", "not_applicable"})
_RECEIPT_BOUND_LIMITATION = (
    "Host structural evidence receipt retained a bounded representative fact index; "
    "omitted facts cannot be selected in this artifact."
)
_CACHE_REUSE_LIMITATION = (
    "One or more program payloads were reused from a prior stable read for this bound session."
)
_SEQUENTIAL_CAPTURE_LIMITATION = (
    "Project-wide program payloads were captured sequentially rather than atomically."
)
_PAYLOAD_BINDING_BOUND_LIMITATION = (
    "The durable receipt retains only a bounded subset of program payload bindings."
)
_UNRESOLVED_WRITE_TARGET_LIMITATION = (
    "One or more OUT/SET/RST actions do not expose one exact device target."
)
_PROJECT_STRUCTURE_STATIC_LIMITATION = (
    "The project structure index is a static bounded XML observation; runtime task "
    "order, implicit vendor calls, and external behavior are not proven."
)
_PROJECT_STRUCTURE_FACT_BOUND_LIMITATION = (
    "Host project-structure evidence retained a bounded representative fact index; "
    "omitted facts cannot be selected in this artifact."
)
_PROJECT_STRUCTURE_NESTED_BOUND_LIMITATION = (
    "One or more project-structure facts retain only a bounded subset of nested "
    "code-unit identifiers."
)
_PROJECT_STRUCTURE_SECTION_NAMES = (
    "configurations",
    "tasks",
    "code_units",
    "symbols",
    "devices",
    "call_edges",
    "parameters",
    "modules",
    "communications",
    "opaque_containers",
)
_PROJECT_STRUCTURE_FACT_SECTIONS = _PROJECT_STRUCTURE_SECTION_NAMES[:-1]
_PROJECT_STRUCTURE_FACT_KINDS = frozenset(
    {
        "configuration",
        "task",
        "code_unit",
        "symbol",
        "device",
        "call_edge",
        "parameter",
        "module",
        "communication",
    }
)
_PROJECT_STRUCTURE_FACT_STATUSES = frozenset({"observed", "partial"})
_PROJECT_SEARCH_STRUCTURE_FIELDS = {
    "code_units": {"name", "instance_name", "comment", "task_assignment.task_name"},
    "symbols": {"symbol", "comment", "data_type", "device.address"},
    "devices": {"address"},
}


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MCPToolError(f"{label} is not a structured object")
    return value


def _items(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise MCPToolError(f"{label} is not a list")
    return value


def _text(value: Any, label: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise MCPToolError(f"{label} is not valid text")
    return value.strip()


def _integer(value: Any, label: str, *, maximum: int = 100_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise MCPToolError(f"{label} is not a valid count or offset")
    return value


def _optional_integer(value: Any, label: str, *, maximum: int) -> int | None:
    if value is None:
        return None
    return _integer(value, label, maximum=maximum)


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise MCPToolError(f"{label} is not boolean")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    result = _items(value, label)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise MCPToolError(f"{label} contains invalid text")
    return [item.strip() for item in result]


def _device(value: Any, label: str) -> str:
    normalized = _text(value, label, maximum=200).upper()
    if _EXACT_DEVICE.fullmatch(normalized) is None:
        raise MCPToolError(f"{label} is not one exact device")
    return normalized


def _first_device(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DEVICE_TOKEN.search(value.upper())
    return match.group(1) if match is not None else None


def _exact_device_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if _EXACT_DEVICE.fullmatch(normalized) is not None else None


def _direct_data_register_span(value: str, width_words: int) -> list[str] | None:
    match = _DIRECT_DATA_REGISTER.fullmatch(value.strip().upper())
    if match is None:
        return None
    digits = match.group("index")
    start = int(digits, 10)
    return [f"D{index:0{len(digits)}d}" for index in range(start, start + width_words)]


def _application_data_transfer_targets(operand: str) -> list[str] | None:
    parts = [part.strip().upper() for part in re.split(r"[,\s]+", operand) if part.strip()]
    if len(parts) != 3:
        return None
    width_words = _DATA_TRANSFER_WIDTH_WORDS.get(parts[0])
    if width_words is None:
        return None
    source_devices = _direct_data_register_span(parts[1], width_words)
    if _DATA_LITERAL.fullmatch(parts[1]) is None and source_devices is None:
        return None
    return _direct_data_register_span(parts[2], width_words)


def _application_data_write_targets(operand: str) -> list[str] | None:
    transfer_targets = _application_data_transfer_targets(operand)
    if transfer_targets is not None:
        return transfer_targets
    mutation = _application_in_place_mutation(operand)
    return list(mutation["writeDevices"]) if mutation is not None else None


def _application_in_place_mutation(operand: str) -> dict[str, Any] | None:
    """Return the bounded direct-D semantics independently verified by the Host."""

    parts = [part.strip().upper() for part in re.split(r"[,\s]+", operand) if part.strip()]
    width_words = (
        _IN_PLACE_MUTATION_WIDTH_WORDS.get(parts[0]) if len(parts) == 2 else None
    )
    if width_words is None:
        return None
    target_devices = _direct_data_register_span(parts[1], width_words)
    if target_devices is None:
        return None
    base = parts[0].removesuffix("P")
    return {
        "mnemonic": parts[0],
        "operation": (
            "decrement_in_place" if "DEC" in base else "increment_in_place"
        ),
        "execution": "rising_edge" if parts[0].endswith("P") else "scan",
        "dataType": (
            f"{'unsigned' if base.endswith('U') else 'signed'}_{width_words * 16}_bit"
        ),
        "target": parts[1],
        "readDevices": target_devices,
        "writeDevices": target_devices,
        "widthWords": width_words,
    }


def _action_target(action: str, operand: Any) -> str | None:
    normalized_action = action.upper()
    if normalized_action in {"OUTPUT", "SET", "RESET", "OUT", "RST"}:
        return _exact_device_or_none(operand)
    if normalized_action != "APPLICATION" or not isinstance(operand, str):
        return None
    data_write_targets = _application_data_write_targets(operand)
    if data_write_targets is not None:
        return data_write_targets[0]
    parts = [part.strip().upper() for part in re.split(r"[,\s]+", operand) if part.strip()]
    if len(parts) < 2:
        return None
    expected_prefix = {
        "TON": "T",
        "TOF": "T",
        "TRTG": "T",
        "CTR": "C",
        "CTU": "C",
        "CTD": "C",
    }.get(parts[0])
    target = parts[1]
    if (
        expected_prefix is None
        or _EXACT_DEVICE.fullmatch(target) is None
        or not target.startswith(expected_prefix)
    ):
        return None
    return target


def _semantic(value: Any, label: str) -> str:
    if value == "structural" or value == "local-row":
        return "structured"
    if value in _SEMANTIC_STATUSES:
        return str(value)
    raise MCPToolError(f"{label} has an unsupported semantic status")


def _stable_union(*groups: Iterable[str]) -> list[str]:
    return sorted({item.strip() for group in groups for item in group if item.strip()})


def _fingerprinted(fact: dict[str, Any]) -> dict[str, Any]:
    return {"fingerprint": canonical_digest(fact), **fact}


def _source_location(
    source_value: Any,
    *,
    access: str,
    semantic_status: str,
    device: str | None = None,
    network_id: str | None = None,
    network_index: int | None = None,
    rung_id: str | None = None,
    camel: bool = False,
) -> dict[str, Any]:
    source = _mapping(source_value, "structural source span")
    row_key = "visualRow" if camel else "visual_row"
    token_key = "tokenOffset" if camel else "token_offset"
    program = _text(source.get("program"), "structural source program")
    visual_row = _optional_integer(
        source.get(row_key),
        "structural source visual row",
        maximum=1_000_000,
    )
    token_offset = _optional_integer(
        source.get(token_key),
        "structural source token offset",
        maximum=100_000_000,
    )
    if visual_row is None or token_offset is None:
        raise MCPToolError("structural source span is missing its exact row or token offset")
    if access not in _ACCESS_KINDS:
        raise MCPToolError("structural source access is invalid")
    if semantic_status not in _SEMANTIC_STATUSES:
        raise MCPToolError("structural source semantic status is invalid")
    fact: dict[str, Any] = {
        "program": program,
        "access": access,
        "semanticStatus": semantic_status,
    }
    if network_id is not None:
        fact["networkId"] = _text(network_id, "network id")
    if network_index is not None:
        fact["networkIndex"] = _integer(
            network_index,
            "network index",
            maximum=1_000_000,
        )
    if rung_id is not None:
        fact["rungId"] = _text(rung_id, "rung id")
    if visual_row is not None:
        fact["visualRow"] = visual_row
    if token_offset is not None:
        fact["tokenOffset"] = token_offset
    if device is not None:
        fact["device"] = _device(device, "structural source device")
    return _fingerprinted(fact)


def _action_access(action: str, *, modeled_application: bool = False) -> str:
    normalized = action.upper()
    if normalized in {
        "OUT",
        "OUTPUT",
        *_DATA_TRANSFER_ACTIONS,
        *_IN_PLACE_MUTATION_ACTIONS,
    }:
        return "write"
    if (
        normalized in _STATEFUL_ACTIONS
        or normalized in {"RESET"}
        or (normalized == "APPLICATION" and modeled_application)
    ):
        return "state_write"
    return "action"


def _fact_without_fingerprint(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): item for key, item in value.items() if key != "fingerprint"}


def _comparison_expression(expression: str) -> dict[str, Any] | None:
    """Independently parse one bounded signed XGK/XGB comparison expression."""

    fields = expression.split(",")
    if len(fields) != 3:
        return None
    instruction, lhs_text, rhs_text = (field.strip() for field in fields)
    double_word = instruction.startswith("D")
    relation = _COMPARISON_RELATIONS.get(
        instruction[1:] if double_word else instruction
    )
    if relation is None or not lhs_text or not rhs_text:
        return None
    width_words = 2 if double_word else 1
    data_type = "signed_32_bit" if double_word else "signed_16_bit"
    minimum, maximum = (
        (-2_147_483_648, 2_147_483_647)
        if width_words == 2
        else (-32_768, 32_767)
    )

    def operand(text: str) -> dict[str, Any] | None:
        if _COMPARISON_DEVICE.fullmatch(text) is not None:
            return {"kind": "device", "value": text}
        if _COMPARISON_LITERAL.fullmatch(text) is None:
            return None
        value = int(text, 10)
        if not minimum <= value <= maximum:
            return None
        return {"kind": "literal", "value": value}

    lhs = operand(lhs_text)
    rhs = operand(rhs_text)
    if lhs is None or rhs is None:
        return None
    devices: list[str] = []
    for item in (lhs, rhs):
        if item["kind"] == "device" and item["value"] not in devices:
            devices.append(item["value"])
    return {
        "instruction": instruction,
        "relation": relation,
        "data_type": data_type,
        "width_words": width_words,
        "lhs": lhs,
        "rhs": rhs,
        "devices": devices,
    }


def _validated_comparison(
    condition: Mapping[str, Any],
    *,
    semantic_status: str,
) -> tuple[str, dict[str, Any]] | None:
    """Validate one raw COMPARE condition against the Host-local grammar."""

    expression = _text(
        condition.get("expression"), "comparison expression", maximum=4_000
    )
    parsed = _comparison_expression(expression)
    if parsed is None:
        if "comparison" in condition or "devices" in condition:
            raise MCPToolError("unsupported comparison exposes structured metadata")
        if semantic_status != "partial":
            raise MCPToolError("unsupported comparison hides partial semantics")
        return None
    metadata = _mapping(condition.get("comparison"), "comparison metadata")
    if dict(metadata) != parsed or "devices" in condition:
        raise MCPToolError("comparison metadata disagrees with its raw expression")
    return expression, parsed


def _condition_locations(
    condition_value: Any,
    *,
    semantic_status: str,
    network_id: str | None,
    network_index: int | None,
    rung_id: str | None,
) -> list[dict[str, Any]]:
    condition = _mapping(condition_value, "logic condition")
    operator = _text(condition.get("operator"), "logic condition operator", maximum=50)
    if operator == "DEVICE":
        return [
            _source_location(
                condition.get("source"),
                access="condition",
                semantic_status=semantic_status,
                device=_device(condition.get("device"), "condition device"),
                network_id=network_id,
                network_index=network_index,
                rung_id=rung_id,
            )
        ]
    if operator == "COMPARE":
        validated = _validated_comparison(
            condition, semantic_status=semantic_status
        )
        if validated is None:
            return []
        _, parsed = validated
        return [
            _source_location(
                condition.get("source"),
                access="condition",
                semantic_status=semantic_status,
                device=device,
                network_id=network_id,
                network_index=network_index,
                rung_id=rung_id,
            )
            for device in parsed["devices"]
        ]
    if operator == "NOT":
        return _condition_locations(
            condition.get("term"),
            semantic_status=semantic_status,
            network_id=network_id,
            network_index=network_index,
            rung_id=rung_id,
        )
    if operator in {"AND", "OR"}:
        return [
            location
            for term in _items(condition.get("terms"), "logic condition terms")
            for location in _condition_locations(
                term,
                semantic_status=semantic_status,
                network_id=network_id,
                network_index=network_index,
                rung_id=rung_id,
            )
        ]
    if operator in {"TRUE", "UNKNOWN"}:
        return []
    raise MCPToolError("logic condition operator is not supported by the receipt")


def _relation_fact(
    *,
    relation_kind: str,
    source: str,
    target: str,
    device_chain: list[str],
    direct: bool,
    semantic_status: str,
    locations: list[dict[str, Any]],
) -> dict[str, Any]:
    if relation_kind not in _RELATION_KINDS:
        raise MCPToolError("structural relation kind is invalid")
    normalized_source = _text(source, "structural relation source")
    normalized_target = _text(target, "structural relation target")
    normalized_chain = [_device(item, "causal path device") for item in device_chain]
    if relation_kind == "causal_path":
        if (
            len(normalized_chain) < 2
            or normalized_chain[0] != normalized_source
            or normalized_chain[-1] != normalized_target
            or direct != (len(normalized_chain) == 2)
        ):
            raise MCPToolError("causal path relation is not a canonical source-to-target chain")
    if semantic_status not in {"structured", "partial"}:
        raise MCPToolError("structural relation semantic status is invalid")
    fact = {
        "relationKind": relation_kind,
        "source": normalized_source,
        "target": normalized_target,
        "deviceChain": normalized_chain,
        "direct": _boolean(direct, "structural relation direct flag"),
        "semanticStatus": semantic_status,
        "locations": locations,
    }
    return _fingerprinted(fact)


def _dedupe_facts(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for value in values:
        fingerprint = _text(value.get("fingerprint"), "fact fingerprint", maximum=64)
        previous = indexed.get(fingerprint)
        if previous is not None and previous != value:
            raise MCPToolError("structural fact fingerprint collision")
        indexed[fingerprint] = value
    return sorted(
        indexed.values(),
        key=lambda item: json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


def _dedupe_structure_facts(
    values: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    indexed: dict[str, dict[str, Any]] = {}
    for value in values:
        fingerprint = _text(
            value.get("fingerprint"),
            "project structure fact fingerprint",
            maximum=64,
        )
        previous = indexed.get(fingerprint)
        if previous is not None:
            if previous != value:
                raise MCPToolError("project structure fact fingerprint collision")
            continue
        indexed[fingerprint] = value
        result.append(value)
    return result


def _bounded_facts(
    values: list[dict[str, Any]],
    *,
    byte_limit: int,
    allowed_location_fingerprints: set[str] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    used = 2
    for item in values:
        if len(result) >= _FACT_COUNT_LIMIT:
            break
        if allowed_location_fingerprints is not None:
            nested = _items(item.get("locations"), "relation locations")
            if any(
                not isinstance(location, Mapping)
                or location.get("fingerprint") not in allowed_location_fingerprints
                for location in nested
            ):
                continue
        size = len(
            json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ) + 1
        if used + size > byte_limit:
            continue
        result.append(item)
        used += size
    return result


def _bounded_structure_facts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    used = 2
    for item in values:
        if len(result) >= _FACT_COUNT_LIMIT:
            break
        size = len(
            json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ) + 1
        if used + size > _STRUCTURE_FACT_BYTE_LIMIT:
            continue
        result.append(item)
        used += size
    return result


def _coverage(
    *,
    area: str,
    status: str,
    observed_count: int,
    total_count: int | None,
    truncated: bool,
    limitations: Iterable[str],
) -> dict[str, Any]:
    if area not in set(STRUCTURAL_ANALYSIS_TOOL_COVERAGE.values()):
        raise MCPToolError("structural receipt coverage area is invalid")
    if status not in _COVERAGE_STATUSES:
        raise MCPToolError("structural receipt coverage status is invalid")
    fact: dict[str, Any] = {
        "area": area,
        "status": status,
        "observedCount": _integer(observed_count, "coverage observed count"),
        "truncated": _boolean(truncated, "coverage truncated flag"),
        "limitations": _stable_union(limitations),
    }
    if total_count is not None:
        fact["totalCount"] = _integer(total_count, "coverage total count")
        if observed_count > total_count:
            raise MCPToolError("coverage observed count exceeds its total")
    if status == "complete" and truncated:
        raise MCPToolError("complete structural coverage cannot be truncated")
    if status in {"partial", "not_observed"} and not fact["limitations"]:
        raise MCPToolError("incomplete structural coverage requires a limitation")
    return fact


def _contract(
    raw_name: str,
    coverage: dict[str, Any],
    locations: Iterable[dict[str, Any]],
    relations: Iterable[dict[str, Any]],
    *,
    scope: dict[str, Any],
    structure_facts: Iterable[dict[str, Any]] | None = None,
    project_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    all_locations = _dedupe_facts(locations)
    all_relations = _dedupe_facts(relations)
    if scope.get("payloadBindingsTruncated") is not True:
        bound_program_names = {
            str(item["program"])
            for item in _items(scope.get("payloadBindings"), "scope payload bindings")
            if isinstance(item, Mapping)
        }
        if any(
            str(location["program"]) not in bound_program_names
            for location in all_locations
        ):
            raise MCPToolError("structural facts refer to a program outside the payload scope")
    returned_locations = _bounded_facts(
        all_locations,
        byte_limit=_LOCATION_FACT_BYTE_LIMIT,
    )
    location_fingerprints = {
        str(item["fingerprint"]) for item in returned_locations
    }
    returned_relations = _bounded_facts(
        all_relations,
        byte_limit=_RELATION_FACT_BYTE_LIMIT,
        allowed_location_fingerprints=location_fingerprints,
    )
    location_truncated = len(returned_locations) != len(all_locations)
    relation_truncated = len(returned_relations) != len(all_relations)
    all_structure_facts = (
        _dedupe_structure_facts(structure_facts)
        if structure_facts is not None
        else []
    )
    returned_structure_facts = _bounded_structure_facts(all_structure_facts)
    structure_truncated = len(returned_structure_facts) != len(all_structure_facts)
    receipt_index_truncated = (
        location_truncated or relation_truncated or structure_truncated
    )
    contract: dict[str, Any] = {
        "schema": STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA,
        "toolName": raw_name,
        "scope": scope,
        "coverage": coverage,
        "receiptIndexTruncated": receipt_index_truncated,
        "receiptIndexLimitations": (
            [_RECEIPT_BOUND_LIMITATION] if receipt_index_truncated else []
        ),
        "locationFactCount": len(all_locations),
        "locationFactReturnedCount": len(returned_locations),
        "locationFactsTruncated": location_truncated,
        "locationFactsDigest": canonical_digest(all_locations),
        "locationFacts": returned_locations,
        "relationFactCount": len(all_relations),
        "relationFactReturnedCount": len(returned_relations),
        "relationFactsTruncated": relation_truncated,
        "relationFactsDigest": canonical_digest(all_relations),
        "relationFacts": returned_relations,
    }
    if structure_facts is not None:
        if project_snapshot is None:
            raise MCPToolError("project structure receipt has no snapshot binding")
        contract.update(
            {
                "projectSnapshot": project_snapshot,
                "structureFactCount": len(all_structure_facts),
                "structureFactReturnedCount": len(returned_structure_facts),
                "structureFactsTruncated": structure_truncated,
                "structureFactsDigest": canonical_digest(all_structure_facts),
                "structureFacts": returned_structure_facts,
            }
        )
        if structure_truncated:
            contract["receiptIndexLimitations"] = _stable_union(
                contract["receiptIndexLimitations"],
                [_PROJECT_STRUCTURE_FACT_BOUND_LIMITATION],
            )
    contract["receiptDigest"] = canonical_digest(contract)
    validate_structural_evidence_contract(contract, expected_tool=raw_name)
    return contract


def _validated_result(result: Any, raw_name: str) -> Mapping[str, Any]:
    value = _mapping(result, f"{raw_name} result")
    if (
        value.get("ok") is not True
        or value.get("verified") is not True
        or value.get("project_unchanged") is not True
        or value.get("operation") != raw_name
    ):
        raise MCPToolError(f"{raw_name} result did not retain its verified read-only envelope")
    project_hash_before = _text(
        value.get("project_hash_before"),
        f"{raw_name} pre-read project hash",
        maximum=64,
    ).casefold()
    project_hash_after = _text(
        value.get("project_hash_after"),
        f"{raw_name} post-read project hash",
        maximum=64,
    ).casefold()
    if (
        re.fullmatch(r"[0-9a-f]{64}", project_hash_before) is None
        or project_hash_before != project_hash_after
    ):
        raise MCPToolError(f"{raw_name} project hash changed during analysis")
    return value


def _payload_scope(
    envelope: Mapping[str, Any],
    raw_name: str,
    *,
    query: dict[str, Any],
    coverage_unit_keys: list[str],
    counts_additive: bool,
) -> tuple[dict[str, Any], list[str]]:
    payload_values = envelope.get("payloads")
    if payload_values is None:
        program = _mapping(envelope.get("program"), f"{raw_name} program binding")
        payload_values = [
            {
                "program_id": program.get("program_id"),
                "name": program.get("name"),
                "payload_sha256": envelope.get("payload_sha256"),
                "stable_repeat": envelope.get("stable_repeat"),
                "cache_hit": envelope.get("cache_hit"),
            }
        ]
    payloads = _items(payload_values, f"{raw_name} payload bindings")
    bindings: list[dict[str, Any]] = []
    for value in payloads:
        payload = _mapping(value, f"{raw_name} payload binding")
        digest = _text(
            payload.get("payload_sha256"),
            f"{raw_name} payload digest",
            maximum=64,
        ).casefold()
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise MCPToolError(f"{raw_name} payload digest is invalid")
        stable_repeat = _boolean(
            payload.get("stable_repeat"),
            f"{raw_name} stable-repeat flag",
        )
        if not stable_repeat:
            raise MCPToolError(f"{raw_name} payload was not a stable repeated read")
        bindings.append(
            {
                "programId": _text(
                    payload.get("program_id"),
                    f"{raw_name} payload program id",
                ),
                "program": _text(
                    payload.get("name"),
                    f"{raw_name} payload program name",
                ),
                "payloadSha256": digest,
                "stableRepeat": True,
                "cacheHit": _boolean(
                    payload.get("cache_hit"),
                    f"{raw_name} payload cache flag",
                ),
            }
        )
    bindings.sort(
        key=lambda item: (
            str(item["programId"]),
            str(item["program"]),
            str(item["payloadSha256"]),
        )
    )
    if len({str(item["programId"]) for item in bindings}) != len(bindings):
        raise MCPToolError(f"{raw_name} payload program bindings are not unique")
    cache_reused = any(item["cacheHit"] is True for item in bindings)
    sequential_capture = len(bindings) > 1
    payload_bindings_truncated = len(bindings) > _PAYLOAD_BINDING_LIMIT
    payload_set_digest = canonical_digest(
        [
            {
                "programId": item["programId"],
                "program": item["program"],
                "payloadSha256": item["payloadSha256"],
            }
            for item in bindings
        ]
    )
    logical_material = {
        "toolName": raw_name,
        "query": query,
        "coverageUnitKeys": sorted(coverage_unit_keys),
    }
    scope_material = {
        **logical_material,
        "payloadSetDigest": payload_set_digest,
        "cacheReused": cache_reused,
        "sequentialCapture": sequential_capture,
        "payloadBindingsTruncated": payload_bindings_truncated,
    }
    scope = {
        "scopeKey": canonical_digest(scope_material),
        "logicalScopeKey": canonical_digest(logical_material),
        "query": query,
        "coverageUnitKeys": sorted(coverage_unit_keys),
        "countsAdditive": counts_additive,
        "payloadCount": len(bindings),
        "payloadSetDigest": payload_set_digest,
        "payloadBindings": bindings[:_PAYLOAD_BINDING_LIMIT],
        "payloadBindingsTruncated": payload_bindings_truncated,
        "cacheReused": cache_reused,
        "sequentialCapture": sequential_capture,
    }
    limitations: list[str] = []
    if cache_reused:
        limitations.append(_CACHE_REUSE_LIMITATION)
    if sequential_capture:
        limitations.append(_SEQUENTIAL_CAPTURE_LIMITATION)
    if len(bindings) > _PAYLOAD_BINDING_LIMIT:
        limitations.append(_PAYLOAD_BINDING_BOUND_LIMITATION)
    return scope, limitations


def _with_capture_limitations(
    coverage: dict[str, Any],
    limitations: list[str],
) -> dict[str, Any]:
    if not limitations:
        return coverage
    return {
        **coverage,
        "status": (
            coverage["status"]
            if coverage["status"] == "not_observed"
            else "partial"
        ),
        "limitations": _stable_union(coverage["limitations"], limitations),
    }


def _logic_graph_contract(result: Any) -> dict[str, Any]:
    envelope = _validated_result(result, "program.logic_graph")
    program_binding = _mapping(envelope.get("program"), "logic graph program binding")
    program_id = _text(program_binding.get("program_id"), "logic graph program id")
    scope, capture_limitations = _payload_scope(
        envelope,
        "program.logic_graph",
        query={"programId": program_id},
        coverage_unit_keys=[f"program:{program_id}"],
        counts_additive=True,
    )
    graph = _mapping(envelope.get("graph"), "logic graph")
    if graph.get("schema") != "xg5000.ladder.graph/1.0":
        raise MCPToolError("logic graph schema is invalid")
    program = _text(graph.get("program"), "logic graph program")
    if program != _text(program_binding.get("name"), "logic graph bound program name"):
        raise MCPToolError("logic graph program disagrees with its payload binding")
    graph_status = _semantic(graph.get("semantic_status"), "logic graph")
    networks = _items(graph.get("networks"), "logic graph networks")
    if _integer(graph.get("network_count"), "logic graph network count") != len(networks):
        raise MCPToolError("logic graph network count is inconsistent")
    locations: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    partial_network_seen = False
    unresolved_write_seen = False
    for network_value in networks:
        network = _mapping(network_value, "logic graph network")
        network_id = _text(network.get("network_id"), "logic graph network id")
        network_index = _integer(
            network.get("network_index"),
            "logic graph network index",
            maximum=1_000_000,
        )
        network_status = _semantic(network.get("semantic_status"), "logic graph network")
        partial_network_seen = partial_network_seen or network_status == "partial"
        nodes = _items(network.get("nodes"), "logic graph nodes")
        unparsed = _items(network.get("unparsed_tokens"), "logic graph unparsed tokens")
        has_unparsed = False
        for item_value in unparsed:
            item = _mapping(item_value, "logic graph unparsed token row")
            tokens = _items(item.get("tokens"), "logic graph unparsed row tokens")
            if any(not isinstance(token, str) for token in tokens):
                raise MCPToolError("logic graph unparsed tokens are invalid")
            has_unparsed = has_unparsed or any(token != "" for token in tokens)
        has_unsupported_node = False
        for node_value in nodes:
            node = _mapping(node_value, "logic graph node")
            kind = _text(node.get("kind"), "logic graph node kind", maximum=100)
            value = node.get("value")
            if not isinstance(value, str) or len(value) > 4_000:
                raise MCPToolError("logic graph node value is invalid")
            node_unresolved_write = (
                kind in {"OUTPUT", "SET", "RESET"}
                and _action_target(kind, value) is None
            )
            unresolved_write_seen = unresolved_write_seen or node_unresolved_write
            has_unsupported_node = has_unsupported_node or kind == "UNKNOWN" or (
                kind == "APPLICATION" and _action_target(kind, value) is None
            ) or (
                kind == "COMPARE" and _comparison_expression(value) is None
            ) or node_unresolved_write
        partial_rung_in_network = False
        for rung_value in _items(network.get("rungs"), "logic graph rungs"):
            rung = _mapping(rung_value, "logic graph rung")
            rung_id = _text(rung.get("rung_id"), "logic graph rung id")
            rung_status = _semantic(rung.get("semantic_status"), "logic graph rung")
            partial_rung_in_network = partial_rung_in_network or rung_status == "partial"
            condition_locations = _condition_locations(
                rung.get("condition"),
                semantic_status=rung_status,
                network_id=network_id,
                network_index=network_index,
                rung_id=rung_id,
            )
            if rung_status == "partial" and network_status != "partial":
                raise MCPToolError("logic graph network hides a partial rung")
            if any(location["program"] != program for location in condition_locations):
                raise MCPToolError("logic graph condition source disagrees with its program")
            locations.extend(condition_locations)
            for output_value in _items(rung.get("outputs"), "logic graph rung outputs"):
                output = _mapping(output_value, "logic graph output")
                action = _text(output.get("action"), "logic graph action", maximum=100).upper()
                operand = _text(output.get("operand"), "logic graph operand", maximum=4_000)
                target = _action_target(action, operand)
                unresolved_write = action in {"OUTPUT", "SET", "RESET"} and target is None
                unresolved_write_seen = unresolved_write_seen or unresolved_write
                if unresolved_write and rung_status != "partial":
                    raise MCPToolError(
                        "logic graph rung hides an unresolved write target"
                    )
                action_location = _source_location(
                    output.get("source"),
                    access=(
                        "write"
                        if action == "APPLICATION"
                        and _application_data_write_targets(operand) is not None
                        else _action_access(
                            action,
                            modeled_application=(
                                action == "APPLICATION" and target is not None
                            ),
                        )
                    ),
                    semantic_status=rung_status,
                    device=target,
                    network_id=network_id,
                    network_index=network_index,
                    rung_id=rung_id,
                )
                if action_location["program"] != program:
                    raise MCPToolError("logic graph output source disagrees with its program")
                locations.append(action_location)
                if target is not None:
                    for condition_location in condition_locations:
                        source_device = condition_location.get("device")
                        if isinstance(source_device, str):
                            relations.append(
                                _relation_fact(
                                    relation_kind="condition",
                                    source=source_device,
                                    target=target,
                                    device_chain=[],
                                    direct=True,
                                    semantic_status=rung_status,
                                    locations=[condition_location, action_location],
                                )
                            )
        expected_network_partial = any(
            (has_unparsed, has_unsupported_node, partial_rung_in_network)
        )
        if (network_status == "partial") != expected_network_partial:
            raise MCPToolError("logic graph network semantic status is inconsistent")
        if network_status == "partial" and graph_status != "partial":
            raise MCPToolError("logic graph semantic status contradicts a partial network")
    if (graph_status == "partial") != partial_network_seen:
        raise MCPToolError("logic graph root semantic status is inconsistent")
    partial = graph_status == "partial"
    logic_limitations = (
        ["Logic graph contains partial or unsupported structural semantics."]
        if partial
        else []
    )
    if unresolved_write_seen:
        logic_limitations.append(_UNRESOLVED_WRITE_TARGET_LIMITATION)
    coverage = _with_capture_limitations(_coverage(
        area="logic_graph",
        status="partial" if partial else "complete",
        observed_count=len(networks),
        total_count=len(networks),
        truncated=False,
        limitations=logic_limitations,
    ), capture_limitations)
    return _contract(
        "program.logic_graph",
        coverage,
        locations,
        relations,
        scope=scope,
    )


def _state_model_contract(result: Any) -> dict[str, Any]:
    envelope = _validated_result(result, "program.state_model")
    program_binding = _mapping(envelope.get("program"), "state model program binding")
    program_id = _text(program_binding.get("program_id"), "state model program id")
    scope, capture_limitations = _payload_scope(
        envelope,
        "program.state_model",
        query={"programId": program_id},
        coverage_unit_keys=[f"program:{program_id}"],
        counts_additive=True,
    )
    model = _mapping(envelope.get("state_model"), "program state model")
    if model.get("schema") != "xg5000.program.state-model/1.0":
        raise MCPToolError("program state model schema is invalid")
    program = _text(model.get("program"), "state model program")
    if program != _text(program_binding.get("name"), "state model bound program name"):
        raise MCPToolError("state model program disagrees with its payload binding")
    model_status = _semantic(model.get("semantic_status"), "state model")
    actions = _items(model.get("actions"), "state model actions")
    if _integer(model.get("action_count"), "state model action count") != len(actions):
        raise MCPToolError("state model action count is inconsistent")
    locations: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    partial_action_seen = False
    unresolved_write_seen = False
    for action_value in actions:
        action = _mapping(action_value, "state model action")
        action_name = _text(action.get("action"), "state model action name", maximum=100).upper()
        action_status = _semantic(action.get("semantic_status"), "state model action")
        partial_action_seen = partial_action_seen or action_status == "partial"
        record_kind = _text(
            action.get("record_kind"),
            "state model record kind",
            maximum=100,
        )
        target_value = action.get("target")
        if record_kind in {"OUTPUT", "SET", "RESET"}:
            raw_value = action.get("raw_value")
            if not isinstance(raw_value, str):
                raise MCPToolError("state model write action has no raw operand")
            target = _exact_device_or_none(raw_value)
            returned_target = _exact_device_or_none(target_value)
            if target is not None and returned_target != target:
                raise MCPToolError(
                    "state model exact write target disagrees with its raw operand"
                )
        elif record_kind == "APPLICATION":
            raw_value = action.get("raw_value")
            if not isinstance(raw_value, str):
                raise MCPToolError("state model application has no raw operand")
            target = _action_target(record_kind, raw_value)
            if _exact_device_or_none(target_value) != target:
                raise MCPToolError(
                    "state model application target disagrees with its raw operand"
                )
        else:
            target = _exact_device_or_none(target_value)
        unresolved_write = record_kind in {"OUTPUT", "SET", "RESET"} and target is None
        unresolved_write_seen = unresolved_write_seen or unresolved_write
        if unresolved_write and action_status != "partial":
            raise MCPToolError("state model action hides an unresolved write target")
        rung_id_value = action.get("rung_id")
        rung_id = (
            _text(rung_id_value, "state model rung id")
            if rung_id_value is not None
            else None
        )
        action_location = _source_location(
            action.get("source"),
            access=_action_access(action_name),
            semantic_status=action_status,
            device=target,
            rung_id=rung_id,
        )
        if action_location["program"] != program:
            raise MCPToolError("state model action source disagrees with its program")
        locations.append(action_location)
        condition_locations = _condition_locations(
            action.get("condition"),
            semantic_status=action_status,
            network_id=None,
            network_index=None,
            rung_id=rung_id,
        )
        if any(location["program"] != program for location in condition_locations):
            raise MCPToolError("state model condition source disagrees with its program")
        locations.extend(condition_locations)
        if target is not None:
            for condition_location in condition_locations:
                source_device = condition_location.get("device")
                if isinstance(source_device, str):
                    relations.append(
                        _relation_fact(
                            relation_kind="condition",
                            source=source_device,
                            target=target,
                            device_chain=[],
                            direct=True,
                            semantic_status=action_status,
                            locations=[condition_location, action_location],
                        )
                    )
    if (model_status == "partial") != partial_action_seen:
        raise MCPToolError("state model root semantic status is inconsistent")
    partial = model_status == "partial"
    state_limitations = (
        ["State model contains partial or unsupported action semantics."]
        if partial
        else []
    )
    if unresolved_write_seen:
        state_limitations.append(_UNRESOLVED_WRITE_TARGET_LIMITATION)
    coverage = _with_capture_limitations(_coverage(
        area="state_model",
        status="partial" if partial else "complete",
        observed_count=len(actions),
        total_count=len(actions),
        truncated=False,
        limitations=state_limitations,
    ), capture_limitations)
    return _contract(
        "program.state_model",
        coverage,
        locations,
        relations,
        scope=scope,
    )


def _references_contract(result: Any) -> dict[str, Any]:
    envelope = _validated_result(result, "device.references")
    index = _mapping(envelope.get("references"), "device reference index")
    if index.get("schema") != "xg5000.device.references/1.0":
        raise MCPToolError("device reference schema is invalid")
    devices = _items(index.get("devices"), "device reference page")
    returned_count = _integer(index.get("device_count"), "returned device count")
    total_count = _integer(index.get("total_device_count"), "total device count")
    pagination = _mapping(index.get("pagination"), "device reference pagination")
    offset = _integer(pagination.get("offset"), "device reference offset")
    page_returned = _integer(pagination.get("returned"), "device reference page count")
    page_total = _integer(pagination.get("total"), "device reference page total")
    page_limit = _integer(pagination.get("limit"), "device reference page limit")
    if not (returned_count == page_returned == len(devices) and total_count == page_total):
        raise MCPToolError("device reference pagination counts are inconsistent")
    next_offset = pagination.get("next_offset")
    if next_offset is not None:
        _integer(next_offset, "device reference next offset")
    page_complete = (
        offset == 0
        and returned_count == total_count
        and next_offset is None
    )
    locations: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    global_reference_count = _integer(
        index.get("reference_count"),
        "reference index count",
    )
    unresolved_write_action_count = _integer(
        index.get("unresolved_write_action_count"),
        "unresolved write action count",
    )
    returned_reference_count = 0
    detail_truncated = _boolean(index.get("details_truncated"), "reference detail truncation")
    computed_detail_truncated = False
    partial_relation_seen = False
    unresolved_write_seen = unresolved_write_action_count > 0
    filter_value = index.get("filter")
    filter_device = (
        _device(filter_value, "device reference filter")
        if filter_value is not None
        else None
    )
    if filter_device is not None and total_count > 1:
        raise MCPToolError("exact device reference filter returned multiple devices")
    for device_value in devices:
        item = _mapping(device_value, "device reference item")
        address = _device(item.get("device"), "referenced device")
        if filter_device is not None and address != filter_device:
            raise MCPToolError("device reference item disagrees with its exact filter")
        references = _items(item.get("references"), "device references")
        reference_count = _integer(item.get("reference_count"), "device reference count")
        references_returned = _integer(
            item.get("references_returned_count"),
            "returned device reference count",
        )
        references_truncated = _boolean(
            item.get("references_truncated"),
            "device reference detail truncation",
        )
        if (
            reference_count < references_returned
            or references_returned != len(references)
            or references_truncated != (reference_count > references_returned)
        ):
            raise MCPToolError("device reference detail counts are inconsistent")
        returned_reference_count += reference_count
        for reference_value in references:
            reference = _mapping(reference_value, "device reference")
            access = _text(reference.get("access"), "device reference access", maximum=50)
            if access not in {"read", "write", "state_write", "argument"}:
                raise MCPToolError("device reference access is invalid")
            location = _source_location(
                reference.get("source"),
                access=access,
                semantic_status="observed",
                device=address,
            )
            locations.append(location)
            if access in {"read", "write", "state_write"}:
                program = str(location["program"])
                relations.append(
                    _relation_fact(
                        relation_kind=access,
                        source=address if access == "read" else program,
                        target=program if access == "read" else address,
                        device_chain=[],
                        direct=True,
                        semantic_status="structured",
                        locations=[location],
                    )
                )
        affected = _items(
            item.get("affected_outputs_and_states"),
            "affected outputs and states",
        )
        affected_count = _integer(
            item.get("affected_outputs_and_states_count"),
            "affected output count",
        )
        affected_returned = _integer(
            item.get("affected_outputs_and_states_returned_count"),
            "returned affected output count",
        )
        affected_truncated = _boolean(
            item.get("affected_outputs_and_states_truncated"),
            "affected output truncation",
        )
        if (
            affected_count < affected_returned
            or affected_returned != len(affected)
            or affected_truncated != (affected_count > affected_returned)
        ):
            raise MCPToolError("affected output detail counts are inconsistent")
        nested_truncated = False
        for affected_value in affected:
            affected_item = _mapping(affected_value, "affected output relation")
            outputs = _items(
                affected_item.get("outputs_and_states"),
                "affected relation outputs",
            )
            outputs_count = _integer(
                affected_item.get("outputs_and_states_count"),
                "affected relation output count",
            )
            outputs_truncated = _boolean(
                affected_item.get("outputs_and_states_truncated"),
                "affected relation output truncation",
            )
            if outputs_count < len(outputs) or outputs_truncated != (
                outputs_count > len(outputs)
            ):
                raise MCPToolError("affected relation output counts are inconsistent")
            nested_truncated = nested_truncated or outputs_truncated
            affected_status = _semantic(
                affected_item.get("semantic_status"),
                "affected relation",
            )
            partial_relation_seen = partial_relation_seen or affected_status == "partial"
            rung_id = _text(affected_item.get("rung_id"), "affected relation rung")
            for output_value in outputs:
                output = _mapping(output_value, "affected output")
                action = _text(output.get("action"), "affected output action", maximum=100)
                target = _action_target(action, output.get("operand"))
                unresolved_write = action.upper() in {
                    "OUTPUT",
                    "SET",
                    "RESET",
                } and target is None
                unresolved_write_seen = unresolved_write_seen or unresolved_write
                if unresolved_write and affected_status != "partial":
                    raise MCPToolError(
                        "device reference impact hides an unresolved write target"
                    )
                output_location = _source_location(
                    output.get("source"),
                    access=(
                        "write"
                        if action.upper() == "APPLICATION"
                        and isinstance(output.get("operand"), str)
                        and _application_data_write_targets(output["operand"]) is not None
                        else _action_access(
                            action,
                            modeled_application=(
                                action.upper() == "APPLICATION" and target is not None
                            ),
                        )
                    ),
                    semantic_status=affected_status,
                    device=target,
                    rung_id=rung_id,
                )
                locations.append(output_location)
                if target is not None:
                    relations.append(
                        _relation_fact(
                            relation_kind="condition",
                            source=address,
                            target=target,
                            device_chain=[],
                            direct=True,
                            semantic_status=affected_status,
                            locations=[output_location],
                        )
                    )
        affected_details_truncated = _boolean(
            item.get("affected_details_truncated"),
            "nested affected detail truncation",
        )
        if nested_truncated and not affected_details_truncated:
            raise MCPToolError("nested affected detail truncation is inconsistent")
        if not affected_truncated and affected_details_truncated != nested_truncated:
            raise MCPToolError("complete affected detail truncation is inconsistent")
        computed_detail_truncated = computed_detail_truncated or any(
            (references_truncated, affected_truncated, affected_details_truncated)
        )
    if global_reference_count < returned_reference_count:
        raise MCPToolError("device reference global count is smaller than its returned page")
    if page_complete and global_reference_count != returned_reference_count:
        raise MCPToolError("complete device reference page hides reference details")
    if computed_detail_truncated and not detail_truncated:
        raise MCPToolError("device reference top-level detail truncation is inconsistent")
    if page_complete and detail_truncated != computed_detail_truncated:
        raise MCPToolError("complete device page has inconsistent detail truncation")
    truncated = not page_complete or detail_truncated
    limitations: list[str] = []
    if truncated:
        limitations.append("Device reference page or nested source details are truncated.")
    if partial_relation_seen:
        limitations.append(
            "One or more affected output/state relations have partial structural semantics."
        )
    if unresolved_write_seen:
        limitations.append(_UNRESOLVED_WRITE_TARGET_LIMITATION)
    unit_keys = (
        [f"device:{filter_device}"]
        if filter_device is not None
        else [f"device-page:{offset}:{offset + returned_count}"]
    )
    scope, capture_limitations = _payload_scope(
        envelope,
        "device.references",
        query={
            "device": filter_device,
            "offset": offset,
            "limit": page_limit,
        },
        coverage_unit_keys=unit_keys,
        counts_additive=filter_device is not None,
    )
    if _integer(envelope.get("program_count"), "reference program count") != scope[
        "payloadCount"
    ]:
        raise MCPToolError("device reference payload count is inconsistent")
    coverage = _with_capture_limitations(_coverage(
        area="cross_references",
        status=(
            "partial"
            if truncated or partial_relation_seen or unresolved_write_seen
            else "complete"
        ),
        observed_count=returned_count,
        total_count=total_count,
        truncated=truncated,
        limitations=limitations,
    ), capture_limitations)
    return _contract(
        "device.references",
        coverage,
        locations,
        relations,
        scope=scope,
    )


def _causal_edge_locations(edge_value: Any) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
    edge = _mapping(edge_value, "causal dependency edge")
    dependency_kind = edge.get("dependency_kind", "control_condition")
    if dependency_kind not in {"control_condition", "data_transfer"}:
        raise MCPToolError("causal dependency kind is invalid")
    source = _device(edge.get("source_device"), "causal edge source")
    target = _device(edge.get("target_device"), "causal edge target")
    program = _text(edge.get("program"), "causal edge program")
    network_index = _integer(
        edge.get("network_index"),
        "causal edge network index",
        maximum=1_000_000,
    )
    rung_id = _text(edge.get("rung_id"), "causal edge rung id")
    status = _semantic(edge.get("semantic_status"), "causal edge")
    action = _text(edge.get("action"), "causal edge action", maximum=100)
    condition_location = _source_location(
        edge.get("condition_source"),
        access="read" if dependency_kind == "data_transfer" else "condition",
        semantic_status=status,
        device=source,
        network_index=network_index,
        rung_id=rung_id,
    )
    target_location = _source_location(
        edge.get("target_source"),
        access=_action_access(action),
        semantic_status=status,
        device=target,
        network_index=network_index,
        rung_id=rung_id,
    )
    if condition_location["program"] != program or target_location["program"] != program:
        raise MCPToolError("causal edge source spans disagree with their program")
    return edge, [condition_location, target_location]


def _causal_contract(result: Any) -> dict[str, Any]:
    envelope = _validated_result(result, "device.causal_impact")
    analysis = _mapping(envelope.get("analysis"), "device causal impact")
    if analysis.get("schema") != "xg5000.device.causal-impact/1.0":
        raise MCPToolError("device causal impact schema is invalid")
    start_device = _device(analysis.get("device"), "causal analysis device")
    observed = _boolean(analysis.get("device_observed"), "causal device observation")
    direction = _text(analysis.get("direction"), "causal direction", maximum=20)
    if direction not in {"downstream", "upstream", "both"}:
        raise MCPToolError("causal direction is invalid")
    source_coverage = _mapping(analysis.get("coverage"), "causal analysis coverage")
    complete_semantics = _boolean(source_coverage.get("complete"), "causal coverage completeness")
    analysis_status = _semantic(analysis.get("semantic_status"), "causal analysis")
    if complete_semantics != (analysis_status == "structured"):
        raise MCPToolError("causal semantic status contradicts its coverage")
    expected_semantic_complete = not any(
        _integer(source_coverage.get(key), f"causal coverage {key}")
        for key in (
            "partial_rung_count",
            "unknown_condition_count",
            "unsupported_application_count",
        )
    )
    if complete_semantics != expected_semantic_complete:
        raise MCPToolError("causal coverage completeness is inconsistent")
    locations: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    path_count = 0
    traversal_truncated = False
    observed_max_depth: int | None = None
    observed_max_paths: int | None = None
    requested_directions = (
        ("downstream", "upstream") if direction == "both" else (direction,)
    )
    for direction_name in requested_directions:
        trace = _mapping(analysis.get(direction_name), f"causal {direction_name} trace")
        if trace.get("direction") != direction_name or trace.get("start_device") != start_device:
            raise MCPToolError("causal trace identity is inconsistent")
        paths = _items(trace.get("paths"), "causal paths")
        trace_path_count = _integer(trace.get("path_count"), "causal path count")
        max_paths = _integer(trace.get("max_paths"), "causal maximum path count")
        max_depth = _integer(
            trace.get("max_depth"),
            "causal maximum path depth",
            maximum=16,
        )
        if not 1 <= max_depth <= 16:
            raise MCPToolError("causal maximum path depth is invalid")
        if not 1 <= max_paths <= 500:
            raise MCPToolError("causal maximum path count is invalid")
        if observed_max_depth is not None and observed_max_depth != max_depth:
            raise MCPToolError("causal directions disagree on maximum depth")
        if observed_max_paths is not None and observed_max_paths != max_paths:
            raise MCPToolError("causal directions disagree on maximum path count")
        observed_max_depth = max_depth
        observed_max_paths = max_paths
        direct_links = _items(trace.get("direct_links"), "causal direct links")
        direct_link_count = _integer(trace.get("direct_link_count"), "causal direct-link count")
        direct_links_truncated = _boolean(
            trace.get("direct_links_truncated"),
            "causal direct-link truncation",
        )
        cycles = _items(trace.get("cycles"), "causal cycles")
        cycle_count = _integer(trace.get("cycle_count"), "causal cycle count")
        cycles_truncated = _boolean(trace.get("cycles_truncated"), "causal cycle truncation")
        if (
            trace_path_count != len(paths)
            or trace_path_count > max_paths
            or len(direct_links) != min(direct_link_count, max_paths)
            or direct_links_truncated != (direct_link_count > max_paths)
            or len(cycles) != min(cycle_count, max_paths)
            or cycles_truncated != (cycle_count > max_paths)
        ):
            raise MCPToolError("causal trace bounded counts are inconsistent")
        trace_truncated = _boolean(trace.get("truncated"), "causal trace truncation")
        if (direct_links_truncated or cycles_truncated) and not trace_truncated:
            raise MCPToolError("causal trace hides bounded direct-link or cycle details")
        traversal_truncated = traversal_truncated or trace_truncated
        path_count += trace_path_count
        for path_value in paths:
            path = _mapping(path_value, "causal path")
            chain = [_device(item, "causal path device") for item in _items(path.get("device_chain"), "causal device chain")]
            edges = _items(path.get("edges"), "causal path edges")
            depth = _integer(path.get("depth"), "causal path depth", maximum=16)
            if len(chain) != len(edges) + 1 or depth != len(edges) or len(chain) < 2:
                raise MCPToolError("causal path depth or chain is inconsistent")
            nested_locations: list[dict[str, Any]] = []
            path_partial = False
            for index, edge_value in enumerate(edges):
                edge, edge_locations = _causal_edge_locations(edge_value)
                if edge.get("source_device") != chain[index] or edge.get("target_device") != chain[index + 1]:
                    raise MCPToolError("causal path edge does not match its canonical chain")
                path_partial = path_partial or edge.get("semantic_status") == "partial"
                nested_locations.extend(edge_locations)
            locations.extend(nested_locations)
            relations.append(
                _relation_fact(
                    relation_kind="causal_path",
                    source=chain[0],
                    target=chain[-1],
                    device_chain=chain,
                    direct=len(chain) == 2,
                    semantic_status="partial" if path_partial else "structured",
                    locations=nested_locations,
                )
            )
    raw_limitations = _string_list(analysis.get("limitations"), "causal limitations")
    limitations = list(raw_limitations)
    if not observed:
        limitations.append("Requested device was not observed in the analyzed LD programs.")
    if traversal_truncated:
        limitations.append("Causal traversal or its direct-link/cycle details are truncated.")
    status = (
        "not_observed"
        if not observed
        else "complete"
        if complete_semantics and not traversal_truncated
        else "partial"
    )
    truncated = traversal_truncated
    assert observed_max_depth is not None
    assert observed_max_paths is not None
    scope, capture_limitations = _payload_scope(
        envelope,
        "device.causal_impact",
        query={
            "device": start_device,
            "direction": direction,
            "maxDepth": observed_max_depth,
            "maxPaths": observed_max_paths,
        },
        coverage_unit_keys=[f"causal-query:{start_device}:{direction}"],
        counts_additive=False,
    )
    if (
        _integer(envelope.get("program_count"), "causal program count")
        != scope["payloadCount"]
        or _integer(source_coverage.get("program_count"), "causal coverage program count")
        != scope["payloadCount"]
    ):
        raise MCPToolError("causal payload count is inconsistent")
    coverage = _with_capture_limitations(_coverage(
        area="causal_paths",
        status=status,
        observed_count=path_count,
        total_count=None if traversal_truncated else path_count,
        truncated=truncated,
        limitations=limitations if status != "complete" else raw_limitations,
    ), capture_limitations)
    return _contract(
        "device.causal_impact",
        coverage,
        locations,
        relations,
        scope=scope,
    )


def _report_collection(report: Mapping[str, Any], key: str) -> tuple[list[Any], bool]:
    collection = _mapping(report.get(key), f"behavior report {key}")
    items = _items(collection.get("items"), f"behavior report {key} items")
    count = _integer(collection.get("count"), f"behavior report {key} count")
    returned = _integer(
        collection.get("returnedCount"),
        f"behavior report {key} returned count",
    )
    truncated = _boolean(collection.get("truncated"), f"behavior report {key} truncation")
    if count < returned or returned != len(items) or truncated != (count > returned):
        raise MCPToolError(f"behavior report {key} bounded counts are inconsistent")
    return items, truncated


def _behavior_contract(result: Any) -> dict[str, Any]:
    envelope = _validated_result(result, "project.behavior_report")
    report = _mapping(envelope.get("report"), "project behavior report")
    if report.get("schema") != "xg5000.project.behavior-report/1.0":
        raise MCPToolError("project behavior report schema is invalid")
    programs = _items(report.get("programs"), "behavior report programs")
    program_total = _integer(report.get("programs_total_count"), "behavior program total")
    program_returned = _integer(
        report.get("programs_returned_count"),
        "behavior program returned count",
    )
    programs_truncated = _boolean(
        report.get("programs_truncated"),
        "behavior program truncation",
    )
    if (
        program_total < program_returned
        or program_returned != len(programs)
        or programs_truncated != (program_total > program_returned)
    ):
        raise MCPToolError("behavior program counts are inconsistent")
    if _integer(report.get("program_count"), "behavior report program count") != program_total:
        raise MCPToolError("behavior report program total is inconsistent")
    program_detail_truncated = False
    for program_value in programs:
        program = _mapping(program_value, "behavior program summary")
        action_types = _mapping(program.get("action_types"), "program action types")
        action_type_count = _integer(program.get("action_type_count"), "program action-type count")
        action_types_truncated = _boolean(
            program.get("action_types_truncated"),
            "program action-type truncation",
        )
        stateful_targets = _items(program.get("stateful_targets"), "program stateful targets")
        stateful_count = _integer(
            program.get("stateful_target_count"),
            "program stateful-target count",
        )
        stateful_truncated = _boolean(
            program.get("stateful_targets_truncated"),
            "program stateful-target truncation",
        )
        if (
            action_type_count < len(action_types)
            or stateful_count < len(stateful_targets)
            or action_types_truncated != (action_type_count > len(action_types))
            or stateful_truncated != (stateful_count > len(stateful_targets))
        ):
            raise MCPToolError("behavior program detail counts are inconsistent")
        program_detail_truncated = program_detail_truncated or action_types_truncated or stateful_truncated
    locations: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    collection_truncated = False
    nested_truncated = False
    collection_names = (
        "writeTargets",
        "physicalWriteTargets",
        "sequenceTargets",
        "timerCounterTargets",
        "multipleWriterTargets",
        "applicationInventory",
    )
    for collection_name in collection_names:
        collection_items, current_truncated = _report_collection(report, collection_name)
        collection_truncated = collection_truncated or current_truncated
        for item_value in collection_items:
            item = _mapping(item_value, f"behavior report {collection_name} item")
            spans = _items(item.get("sourceSpans"), "behavior source spans")
            span_count = _integer(item.get("sourceSpanCount"), "behavior source-span count")
            spans_truncated = _boolean(
                item.get("sourceSpansTruncated"),
                "behavior source-span truncation",
            )
            item_truncated = _boolean(item.get("truncated"), "behavior item truncation")
            if (
                span_count < len(spans)
                or spans_truncated != (span_count > len(spans))
                or spans_truncated
                and not item_truncated
            ):
                raise MCPToolError("behavior source-span bounded counts are inconsistent")
            nested_truncated = nested_truncated or item_truncated
            target_value = item.get("target")
            target = _device(target_value, "behavior write target") if target_value is not None else None
            for span_value in spans:
                span = _mapping(span_value, "behavior source span")
                action = _text(span.get("action"), "behavior source action", maximum=100)
                status = _semantic(span.get("semanticStatus"), "behavior source span")
                location = _source_location(
                    span,
                    access=_action_access(action),
                    semantic_status=status,
                    device=target,
                    rung_id=(
                        _text(span.get("rungId"), "behavior source rung")
                        if span.get("rungId") is not None
                        else None
                    ),
                    camel=True,
                )
                locations.append(location)
                if target is not None:
                    relations.append(
                        _relation_fact(
                            relation_kind=(
                                "state_write"
                                if location["access"] == "state_write"
                                else "write"
                            ),
                            source=str(location["program"]),
                            target=target,
                            device_chain=[],
                            direct=True,
                            semantic_status=status,
                            locations=[location],
                        )
                    )
    most_referenced = _items(
        report.get("most_referenced_devices"),
        "behavior most-referenced devices",
    )
    ranking_total = _integer(
        report.get("most_referenced_devices_total_count"),
        "behavior ranking total",
    )
    ranking_returned = _integer(
        report.get("most_referenced_devices_returned_count"),
        "behavior ranking returned count",
    )
    ranking_limit = _integer(
        report.get("most_referenced_devices_limit"),
        "behavior ranking limit",
    )
    ranking_truncated = _boolean(
        report.get("most_referenced_devices_truncated"),
        "behavior ranking truncation",
    )
    if (
        ranking_returned != len(most_referenced)
        or ranking_returned != min(ranking_total, ranking_limit)
        or ranking_truncated != (ranking_total > ranking_limit)
    ):
        raise MCPToolError("behavior ranking bounded counts are inconsistent")
    source_coverage = _mapping(report.get("coverage"), "behavior coverage")
    source_complete = _boolean(source_coverage.get("complete"), "behavior coverage completeness")
    source_truncated = _boolean(source_coverage.get("truncated"), "behavior coverage truncation")
    if (
        _integer(source_coverage.get("programCount"), "behavior coverage program count")
        != program_total
        or _integer(source_coverage.get("visualRowCount"), "behavior coverage row count")
        != _integer(report.get("visual_row_count"), "behavior report row count")
        or _integer(source_coverage.get("deviceCount"), "behavior coverage device count")
        != _integer(report.get("device_count"), "behavior report device count")
    ):
        raise MCPToolError("behavior report top-level coverage counts are inconsistent")
    computed_truncated = any(
        (
            programs_truncated,
            program_detail_truncated,
            ranking_truncated,
            collection_truncated,
            nested_truncated,
        )
    )
    if source_truncated != computed_truncated or source_complete and source_truncated:
        raise MCPToolError("behavior coverage truncation or completeness is inconsistent")
    semantic_incomplete = any(
        _integer(source_coverage.get(key), f"behavior coverage {key}")
        for key in (
            "partialProgramCount",
            "partialNetworkCount",
            "partialRungCount",
            "partialActionCount",
            "unmodeledApplicationCount",
            "unresolvedWriteActionCount",
        )
    ) or program_total == 0
    if source_complete != (not semantic_incomplete and not source_truncated):
        raise MCPToolError("behavior coverage completeness is inconsistent")
    report_status = _semantic(report.get("semantic_status"), "behavior report")
    if source_complete != (report_status == "structured"):
        raise MCPToolError("behavior semantic status contradicts its coverage")
    limitations = _string_list(report.get("limitations"), "behavior limitations")
    if ranking_truncated:
        limitations.append("Most-referenced-device ranking is limited to a leading subset.")
    truncated = source_truncated or ranking_truncated
    status = "complete" if source_complete and not truncated else "partial"
    scope, capture_limitations = _payload_scope(
        envelope,
        "project.behavior_report",
        query={},
        coverage_unit_keys=["project-behavior"],
        counts_additive=False,
    )
    if scope["payloadCount"] != program_total:
        raise MCPToolError("behavior report payload count is inconsistent")
    coverage = _with_capture_limitations(_coverage(
        area="programs",
        status=status,
        observed_count=program_returned,
        total_count=program_total,
        truncated=truncated,
        limitations=limitations,
    ), capture_limitations)
    return _contract(
        "project.behavior_report",
        coverage,
        locations,
        relations,
        scope=scope,
    )


def _raw_text(
    value: Any,
    label: str,
    *,
    maximum: int = 4_096,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise MCPToolError(f"{label} is not bounded text")
    if not allow_empty and not value.strip():
        raise MCPToolError(f"{label} is blank")
    return value


def _raw_optional_text(value: Any, label: str, *, maximum: int = 4_096) -> str | None:
    if value is None:
        return None
    return _raw_text(value, label, maximum=maximum)


def _raw_integer(
    value: Any,
    label: str,
    *,
    minimum: int = -(2**31),
    maximum: int = 100_000_000,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise MCPToolError(f"{label} is not a bounded integer")
    return value


def _raw_optional_integer(
    value: Any,
    label: str,
    *,
    minimum: int = -(2**31),
    maximum: int = 100_000_000,
) -> int | None:
    if value is None:
        return None
    return _raw_integer(value, label, minimum=minimum, maximum=maximum)


def _raw_sha256(value: Any, label: str) -> str:
    digest = _raw_text(value, label, maximum=64, allow_empty=False).casefold()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise MCPToolError(f"{label} is not SHA-256")
    return digest


def _exact_raw_mapping(
    value: Any,
    label: str,
    fields: set[str] | frozenset[str],
) -> Mapping[str, Any]:
    result = _mapping(value, label)
    if set(result) != set(fields):
        raise MCPToolError(f"{label} fields are invalid")
    return result


def _structure_attribute(name: str, value: Any) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        normalized: str | int | bool = value
    elif isinstance(value, int):
        normalized = _raw_integer(value, f"project structure {name}")
    elif isinstance(value, str):
        normalized = _raw_text(value, f"project structure {name}", maximum=4_096)
    else:
        raise MCPToolError(f"project structure {name} is not a scalar attribute")
    return {"name": name, "value": normalized}


def _structure_fact(
    *,
    kind: str,
    entity_id: Any,
    name: Any = None,
    parent_id: Any = None,
    order: Any = None,
    status: str,
    attributes: Mapping[str, Any],
) -> dict[str, Any]:
    if kind not in _PROJECT_STRUCTURE_FACT_KINDS:
        raise MCPToolError("project structure fact kind is invalid")
    canonical: dict[str, Any] = {
        "kind": kind,
        "entityId": _raw_text(
            entity_id,
            "project structure entity id",
            maximum=500,
            allow_empty=False,
        ),
        "status": status,
        "attributes": [],
    }
    if status not in _PROJECT_STRUCTURE_FACT_STATUSES:
        raise MCPToolError("project structure fact status is invalid")
    if name is not None:
        normalized_name = _raw_text(name, "project structure fact name")
        if normalized_name:
            canonical["name"] = normalized_name
    if parent_id is not None:
        normalized_parent_id = _raw_text(
            parent_id,
            "project structure parent id",
            maximum=500,
            allow_empty=False,
        )
        canonical["parentId"] = normalized_parent_id
    if order is not None:
        canonical["order"] = _raw_integer(
            order,
            "project structure fact order",
            minimum=0,
        )
    normalized_attributes = [
        item
        for key, value in sorted(attributes.items())
        if (item := _structure_attribute(key, value)) is not None
    ]
    if len({str(item["name"]) for item in normalized_attributes}) != len(
        normalized_attributes
    ):
        raise MCPToolError("project structure fact attributes are not unique")
    canonical["attributes"] = normalized_attributes
    return _fingerprinted(canonical)


def _round_robin_structure_facts(
    facts_by_section: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    maximum = max((len(items) for items in facts_by_section.values()), default=0)
    for index in range(maximum):
        for section in _PROJECT_STRUCTURE_FACT_SECTIONS:
            values = facts_by_section.get(section, [])
            if index < len(values):
                result.append(values[index])
    return result


def _raw_string_list(value: Any, label: str, *, maximum: int = 4_096) -> list[str]:
    values = _items(value, label)
    if len(values) > 2_000:
        raise MCPToolError(f"{label} exceeds its item bound")
    return [
        _raw_text(item, f"{label} item", maximum=maximum, allow_empty=False)
        for item in values
    ]


def _validate_vendor_uuid(value: Any, label: str) -> str | None:
    candidate = _raw_optional_text(value, label, maximum=36)
    if candidate is not None and re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        candidate,
        re.IGNORECASE,
    ) is None:
        raise MCPToolError(f"{label} is not a canonical vendor UUID")
    return candidate


def _validate_source_path(value: Any, label: str) -> str:
    path = _raw_text(value, label, maximum=32_768, allow_empty=False)
    if not path.startswith("/Project[1]") or any(character in path for character in "\r\n"):
        raise MCPToolError(f"{label} is not a bounded project element path")
    return path


def _validate_structure_item(
    section: str,
    value: Any,
) -> dict[str, Any] | None:
    item = _mapping(value, f"project structure {section} item")
    if section == "configurations":
        item = _exact_raw_mapping(
            item,
            "project structure configuration",
            {
                "configuration_id",
                "order",
                "name",
                "vendor_guid",
                "kind_code",
                "type_code",
                "version",
                "comment",
                "source_path",
                "task_count",
            },
        )
        _validate_vendor_uuid(item["vendor_guid"], "configuration vendor GUID")
        _validate_source_path(item["source_path"], "configuration source path")
        _raw_text(item["comment"], "configuration comment")
        return _structure_fact(
            kind="configuration",
            entity_id=item["configuration_id"],
            name=item["name"],
            order=_raw_integer(item["order"], "configuration order", minimum=0),
            status="observed",
            attributes={
                "kindCode": _raw_optional_integer(item["kind_code"], "configuration kind"),
                "typeCode": _raw_optional_integer(item["type_code"], "configuration type"),
                "version": _raw_text(item["version"], "configuration version"),
                "taskCount": _raw_integer(item["task_count"], "configuration task count", minimum=0),
            },
        )
    if section == "tasks":
        item = _exact_raw_mapping(
            item,
            "project structure task",
            {
                "task_id",
                "configuration_id",
                "order",
                "name",
                "kind_code",
                "type_code",
                "priority",
                "task_index",
                "trigger",
                "order_basis",
                "source_path",
                "assigned_code_unit_count",
            },
        )
        trigger = _exact_raw_mapping(
            item["trigger"],
            "project structure task trigger",
            {
                "device_type_code",
                "device",
                "bit_condition",
                "word_condition",
                "word_value",
            },
        )
        _validate_source_path(item["source_path"], "task source path")
        return _structure_fact(
            kind="task",
            entity_id=item["task_id"],
            parent_id=item["configuration_id"],
            name=item["name"],
            order=_raw_integer(item["order"], "task order", minimum=0),
            status="partial",
            attributes={
                "kindCode": _raw_optional_integer(item["kind_code"], "task kind"),
                "typeCode": _raw_optional_integer(item["type_code"], "task type"),
                "priority": _raw_optional_integer(item["priority"], "task priority"),
                "taskIndex": _raw_optional_integer(item["task_index"], "task index"),
                "triggerDeviceTypeCode": _raw_optional_integer(trigger["device_type_code"], "task trigger device type"),
                "triggerDevice": _raw_text(trigger["device"], "task trigger device"),
                "triggerBitCondition": _raw_optional_integer(trigger["bit_condition"], "task trigger bit condition"),
                "triggerWordCondition": _raw_optional_integer(trigger["word_condition"], "task trigger word condition"),
                "triggerWordValue": _raw_optional_integer(trigger["word_value"], "task trigger word value"),
                "orderBasis": _raw_text(item["order_basis"], "task order basis", allow_empty=False),
                "assignedCodeUnitCount": _raw_integer(item["assigned_code_unit_count"], "task assigned code-unit count", minimum=0),
            },
        )
    if section == "code_units":
        item = _exact_raw_mapping(
            item,
            "project structure code unit",
            {
                "code_unit_id",
                "configuration_id",
                "order",
                "task_program_order",
                "order_basis",
                "name",
                "code_unit_type",
                "kind_code",
                "language",
                "language_status",
                "routine_tags",
                "vendor_object_id",
                "version",
                "instance_name",
                "comment",
                "task_assignment",
                "source_path",
                "body",
            },
        )
        assignment = _exact_raw_mapping(
            item["task_assignment"],
            "project structure task assignment",
            {"task_name", "task_id", "status"},
        )
        body = _exact_raw_mapping(
            item["body"],
            "project structure code body",
            {
                "status",
                "container_count",
                "decoded_container_count",
                "container_payload_digests",
                "marker_string_count",
                "application_token_count",
                "device_reference_count",
                "raw_body_included",
            },
        )
        tags = _raw_string_list(item["routine_tags"], "code-unit routine tags", maximum=256)
        digests = _raw_string_list(
            body["container_payload_digests"],
            "code-unit container payload digests",
            maximum=64,
        )
        if any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in digests):
            raise MCPToolError("code-unit container payload digest is invalid")
        if body["raw_body_included"] is not False:
            raise MCPToolError("project structure unexpectedly exposed a raw code body")
        _validate_vendor_uuid(item["vendor_object_id"], "code-unit vendor object id")
        _validate_source_path(item["source_path"], "code-unit source path")
        _raw_text(item["comment"], "code-unit comment")
        assignment_status = _raw_text(
            assignment["status"],
            "code-unit task assignment status",
            maximum=50,
            allow_empty=False,
        )
        if assignment_status not in {"observed", "unresolved"}:
            raise MCPToolError("code-unit task assignment status is invalid")
        if assignment["task_id"] is not None:
            _raw_text(
                assignment["task_id"],
                "code-unit task id",
                maximum=500,
                allow_empty=False,
            )
        body_status = _raw_text(
            body["status"],
            "code-unit body status",
            maximum=100,
            allow_empty=False,
        )
        language_status = _raw_text(
            item["language_status"],
            "code-unit language status",
            maximum=100,
            allow_empty=False,
        )
        if language_status not in {"observed_routine_tag", "partial"} or body_status not in {
            "partial_binary_tokens_observed",
            "empty",
            "unobserved",
        }:
            raise MCPToolError("code-unit language or body status is invalid")
        return _structure_fact(
            kind="code_unit",
            entity_id=item["code_unit_id"],
            parent_id=assignment["task_id"] or item["configuration_id"],
            name=item["name"],
            order=_raw_integer(item["order"], "code-unit order", minimum=0),
            status=(
                "observed"
                if language_status == "observed_routine_tag"
                and body_status in {"empty", "unobserved"}
                and assignment_status == "observed"
                else "partial"
            ),
            attributes={
                "configurationId": _raw_text(item["configuration_id"], "code-unit configuration id", maximum=500, allow_empty=False),
                "taskId": _raw_optional_text(assignment["task_id"], "code-unit task id", maximum=500),
                "taskName": _raw_text(assignment["task_name"], "code-unit task name"),
                "taskAssignmentStatus": assignment_status,
                "taskProgramOrder": _raw_optional_integer(item["task_program_order"], "code-unit task program order", minimum=0),
                "orderBasis": _raw_text(item["order_basis"], "code-unit order basis", allow_empty=False),
                "codeUnitType": _raw_text(item["code_unit_type"], "code-unit type", maximum=256, allow_empty=False),
                "kindCode": _raw_optional_integer(item["kind_code"], "code-unit kind"),
                "language": _raw_text(item["language"], "code-unit language", maximum=100, allow_empty=False),
                "languageStatus": language_status,
                "routineTags": ", ".join(tags),
                "version": _raw_text(item["version"], "code-unit version"),
                "instanceName": _raw_text(item["instance_name"], "code-unit instance name"),
                "bodyStatus": body_status,
                "containerCount": _raw_integer(body["container_count"], "code-unit container count", minimum=0),
                "decodedContainerCount": _raw_integer(body["decoded_container_count"], "code-unit decoded container count", minimum=0),
                "markerStringCount": _raw_integer(body["marker_string_count"], "code-unit marker count", minimum=0),
                "applicationTokenCount": _raw_integer(body["application_token_count"], "code-unit application count", minimum=0),
                "deviceReferenceCount": _raw_integer(body["device_reference_count"], "code-unit device-reference count", minimum=0),
            },
        )
    if section == "symbols":
        item = _exact_raw_mapping(
            item,
            "project structure symbol",
            {
                "symbol_id",
                "order",
                "record_version",
                "symbol",
                "device",
                "data_type",
                "comment",
                "array_descriptor",
                "source",
            },
        )
        device = _exact_raw_mapping(
            item["device"],
            "project structure symbol device",
            {"prefix", "encoded_index", "address", "address_status"},
        )
        source = _exact_raw_mapping(
            item["source"],
            "project structure symbol source",
            {"path", "record_byte_offset", "container_sha256"},
        )
        _validate_source_path(source["path"], "symbol source path")
        _raw_sha256(source["container_sha256"], "symbol source container digest")
        _raw_text(item["comment"], "symbol comment")
        if device["address_status"] not in {
            "corroborated_by_program_token",
            "decoded_sv5_record",
        }:
            raise MCPToolError("symbol device address status is invalid")
        return _structure_fact(
            kind="symbol",
            entity_id=item["symbol_id"],
            name=item["symbol"],
            order=_raw_integer(item["order"], "symbol order", minimum=0),
            status="partial",
            attributes={
                "recordVersion": _raw_text(item["record_version"], "symbol record version", maximum=128, allow_empty=False),
                "devicePrefix": _raw_text(device["prefix"], "symbol device prefix", maximum=16, allow_empty=False),
                "deviceEncodedIndex": _raw_integer(device["encoded_index"], "symbol encoded device index", minimum=0),
                "deviceAddress": _raw_text(device["address"], "symbol device address", maximum=200, allow_empty=False),
                "addressStatus": _raw_text(device["address_status"], "symbol address status", maximum=100, allow_empty=False),
                "dataType": _raw_text(item["data_type"], "symbol data type", maximum=128),
                "arrayDescriptor": _raw_text(item["array_descriptor"], "symbol array descriptor", maximum=128),
            },
        )
    if section == "devices":
        item = _exact_raw_mapping(
            item,
            "project structure device",
            {
                "device_id",
                "address",
                "symbol_record_count",
                "symbol_ids",
                "symbol_ids_truncated",
                "code_reference_count",
                "referenced_by_code_unit_count",
                "referenced_by_code_unit_ids",
                "referenced_by_code_unit_ids_truncated",
                "status",
            },
        )
        symbol_ids = _raw_string_list(item["symbol_ids"], "device symbol ids", maximum=500)
        unit_ids = _raw_string_list(
            item["referenced_by_code_unit_ids"],
            "device code-unit ids",
            maximum=500,
        )
        symbol_count = _raw_integer(item["symbol_record_count"], "device symbol count", minimum=0)
        unit_count = _raw_integer(item["referenced_by_code_unit_count"], "device code-unit count", minimum=0)
        symbol_truncated = _boolean(item["symbol_ids_truncated"], "device symbol truncation")
        unit_truncated = _boolean(item["referenced_by_code_unit_ids_truncated"], "device code-unit truncation")
        if (
            symbol_truncated != (symbol_count > len(symbol_ids))
            or unit_truncated != (unit_count > len(unit_ids))
            or len(symbol_ids) != min(symbol_count, 32)
            or len(unit_ids) != min(unit_count, 64)
        ):
            raise MCPToolError("device structure bounded identifiers are inconsistent")
        if item["status"] not in {
            "symbol_and_code_observed",
            "symbol_observed",
            "code_token_observed",
        }:
            raise MCPToolError("project structure device status is invalid")
        return _structure_fact(
            kind="device",
            entity_id=item["device_id"],
            name=item["address"],
            status="partial",
            attributes={
                "address": _raw_text(item["address"], "device address", maximum=200, allow_empty=False),
                "status": _raw_text(item["status"], "device status", maximum=100, allow_empty=False),
                "symbolRecordCount": symbol_count,
                "symbolIds": ", ".join(symbol_ids),
                "symbolIdsTruncated": symbol_truncated,
                "codeReferenceCount": _raw_integer(item["code_reference_count"], "device reference count", minimum=0),
                "referencedByCodeUnitCount": unit_count,
                "referencedByCodeUnitIds": ", ".join(unit_ids[:32]),
                "referencedByCodeUnitIdsTruncated": unit_truncated,
                "projectedReferencedByCodeUnitIdsTruncated": unit_count > 32,
            },
        )
    if section == "call_edges":
        item = _exact_raw_mapping(
            item,
            "project structure call edge",
            {
                "edge_id",
                "source_code_unit_id",
                "edge_type",
                "opcode",
                "source",
                "status",
                "target",
            },
        )
        source = _exact_raw_mapping(
            item["source"],
            "project structure call source",
            {"path", "marker_byte_offset", "container_sha256"},
        )
        target = _exact_raw_mapping(
            item["target"],
            "project structure call target",
            {"name", "code_unit_id", "resolution"},
        )
        _validate_source_path(source["path"], "call source path")
        _raw_sha256(source["container_sha256"], "call source container digest")
        if target["code_unit_id"] is not None:
            _raw_text(
                target["code_unit_id"],
                "call target code-unit id",
                maximum=500,
                allow_empty=False,
            )
        if (
            target["resolution"]
            not in {"resolved_code_unit", "ambiguous", "unresolved"}
            or item["edge_type"]
            not in {"call", "function_call", "function_block_call"}
            or item["opcode"]
            not in {"CALL", "CALLP", "ECALL", "FCALL", "SCALL", "FBCALL", "CALL_FB"}
            or item["status"] != "observed_instruction_token"
        ):
            raise MCPToolError("project structure call edge enum is invalid")
        return _structure_fact(
            kind="call_edge",
            entity_id=item["edge_id"],
            parent_id=item["source_code_unit_id"],
            name=target["name"],
            status="partial",
            attributes={
                "sourceCodeUnitId": _raw_text(item["source_code_unit_id"], "call source code-unit id", maximum=500, allow_empty=False),
                "targetCodeUnitId": _raw_optional_text(target["code_unit_id"], "call target code-unit id", maximum=500),
                "targetResolution": _raw_text(target["resolution"], "call target resolution", maximum=100, allow_empty=False),
                "edgeType": _raw_text(item["edge_type"], "call edge type", maximum=100, allow_empty=False),
                "opcode": _raw_text(item["opcode"], "call opcode", maximum=50, allow_empty=False),
                "tokenStatus": _raw_text(item["status"], "call token status", maximum=100, allow_empty=False),
                "markerByteOffset": _raw_integer(source["marker_byte_offset"], "call marker byte offset", minimum=0),
            },
        )
    if section == "parameters":
        item = _exact_raw_mapping(
            item,
            "project structure parameter",
            {
                "parameter_id",
                "configuration_id",
                "order",
                "parameter_type",
                "child_types",
                "source_path",
                "raw_parameter_payload_included",
            },
        )
        child_types = _raw_string_list(item["child_types"], "parameter child types", maximum=256)
        if item["raw_parameter_payload_included"] is not False:
            raise MCPToolError("project structure unexpectedly exposed a raw parameter payload")
        _validate_source_path(item["source_path"], "parameter source path")
        return _structure_fact(
            kind="parameter",
            entity_id=item["parameter_id"],
            parent_id=item["configuration_id"],
            name=item["parameter_type"],
            order=_raw_integer(item["order"], "parameter order", minimum=0),
            status="observed",
            attributes={"childTypes": ", ".join(child_types)},
        )
    if section == "modules":
        item = _exact_raw_mapping(
            item,
            "project structure module",
            {
                "module_id",
                "configuration_id",
                "parameter_id",
                "order",
                "base",
                "slot",
                "vendor_id",
                "subtype",
                "name",
                "comment",
                "details",
                "source_path",
            },
        )
        _validate_source_path(item["source_path"], "module source path")
        _raw_text(item["comment"], "module comment")
        return _structure_fact(
            kind="module",
            entity_id=item["module_id"],
            parent_id=item["parameter_id"],
            name=item["name"],
            order=_raw_integer(item["order"], "module order", minimum=0),
            status="observed",
            attributes={
                "configurationId": _raw_text(item["configuration_id"], "module configuration id", maximum=500, allow_empty=False),
                "base": _raw_optional_integer(item["base"], "module base"),
                "slot": _raw_optional_integer(item["slot"], "module slot"),
                "vendorId": _raw_optional_integer(item["vendor_id"], "module vendor id"),
                "subtype": _raw_optional_integer(item["subtype"], "module subtype"),
                "details": _raw_text(item["details"], "module details"),
            },
        )
    if section == "communications":
        return _validate_communication_structure_item(item)
    if section == "opaque_containers":
        _validate_opaque_structure_item(item)
        return None
    raise MCPToolError("project structure section is unsupported")


def _validate_communication_structure_item(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    kind = _raw_text(
        value.get("kind"),
        "communication kind",
        maximum=100,
        allow_empty=False,
    )
    common = {"communication_id", "kind", "source_path"}
    fields_by_kind: dict[str, set[str]] = {
        "network": common | {"name", "network_type", "type"},
        "network_module": common
        | {
            "parent_communication_id",
            "Base",
            "Slot",
            "ChannelType",
            "ConfigType",
            "Id",
            "OptionType",
            "Type",
            "alias",
            "name",
            "config_name",
            "description",
        },
        "cnet_configuration": common | {"Base", "Slot", "StationNo", "SubType", "Type"},
        "cnet_port": common
        | {
            "parent_communication_id",
            "order",
            "DriverType",
            "Mode",
            "Bps",
            "DataBit",
            "Parity",
            "StopBit",
            "StationNo",
            "StationNoB",
            "RequestDelayTime",
            "RxTimeOut",
            "CharTimeOut",
            "InterCharTimeOut",
            "TerminatingResister",
            "io_bindings",
            "initialization_command_count",
            "initialization_commands_included",
        },
        "xgcomm_settings": common
        | {
            "connection_blob_present",
            "connection_blob_encoded_char_count",
            "connection_blob_included",
        },
    }
    expected = fields_by_kind.get(kind)
    if expected is None:
        raise MCPToolError("communication structure kind is unsupported")
    item = _exact_raw_mapping(value, "project structure communication", expected)
    _validate_source_path(item["source_path"], "communication source path")
    parent_id = item.get("parent_communication_id")
    attributes: dict[str, Any] = {"communicationKind": kind}
    label = ""
    order = item.get("order")
    if kind == "network":
        label = _raw_text(item["name"], "communication network name")
        attributes.update(
            {
                "networkType": _raw_text(item["network_type"], "communication network type", maximum=256),
                "type": _raw_text(item["type"], "communication network item type", maximum=256),
            }
        )
    elif kind == "network_module":
        label = _raw_text(item["name"], "communication module name") or _raw_text(
            item["alias"],
            "communication module alias",
        )
        for source_name, target_name in (
            ("Base", "base"),
            ("Slot", "slot"),
            ("ChannelType", "channelType"),
            ("ConfigType", "configType"),
            ("Id", "vendorId"),
            ("OptionType", "optionType"),
            ("Type", "typeCode"),
        ):
            attributes[target_name] = _raw_optional_integer(
                item[source_name],
                f"communication module {source_name}",
            )
        attributes.update(
            {
                "alias": _raw_text(item["alias"], "communication module alias"),
                "configName": _raw_text(item["config_name"], "communication module config name"),
            }
        )
        _raw_text(item["description"], "communication module description")
    elif kind == "cnet_configuration":
        label = "CNET configuration"
        for source_name, target_name in (
            ("Base", "base"),
            ("Slot", "slot"),
            ("StationNo", "stationNumber"),
            ("SubType", "subtype"),
            ("Type", "typeCode"),
        ):
            attributes[target_name] = _raw_optional_integer(
                item[source_name],
                f"CNET configuration {source_name}",
            )
    elif kind == "cnet_port":
        label = "CNET port"
        for source_name, target_name in (
            ("DriverType", "driverType"),
            ("Mode", "mode"),
            ("Bps", "bps"),
            ("DataBit", "dataBits"),
            ("Parity", "parity"),
            ("StopBit", "stopBits"),
            ("StationNo", "stationNumber"),
            ("StationNoB", "stationNumberB"),
            ("RequestDelayTime", "requestDelayTime"),
            ("RxTimeOut", "receiveTimeout"),
            ("CharTimeOut", "characterTimeout"),
            ("InterCharTimeOut", "interCharacterTimeout"),
            ("TerminatingResister", "terminatingResistor"),
        ):
            attributes[target_name] = _raw_optional_integer(
                item[source_name],
                f"CNET port {source_name}",
            )
        bindings = _items(item["io_bindings"], "CNET I/O bindings")
        if len(bindings) > 4:
            raise MCPToolError("CNET I/O bindings exceed the fixed DI/DO/AI/AO bound")
        rendered_bindings: list[str] = []
        for binding_value in bindings:
            binding = _exact_raw_mapping(
                binding_value,
                "CNET I/O binding",
                {"kind", "address", "size", "device_type_code", "data_type_code"},
            )
            binding_kind = _raw_text(
                binding["kind"],
                "CNET I/O binding kind",
                maximum=2,
                allow_empty=False,
            )
            if binding_kind not in {"DI", "DO", "AI", "AO"}:
                raise MCPToolError("CNET I/O binding kind is invalid")
            address = _raw_text(binding["address"], "CNET I/O address", maximum=256)
            size = _raw_optional_integer(binding["size"], "CNET I/O size", minimum=0)
            _raw_optional_integer(binding["device_type_code"], "CNET I/O device type")
            _raw_optional_integer(binding["data_type_code"], "CNET I/O data type")
            rendered_bindings.append(f"{binding_kind}:{address}:size={size}")
        initialization_count = _raw_integer(
            item["initialization_command_count"],
            "CNET initialization command count",
            minimum=0,
        )
        if item["initialization_commands_included"] is not False:
            raise MCPToolError("project structure unexpectedly exposed CNET commands")
        attributes.update(
            {
                "ioBindings": "; ".join(rendered_bindings),
                "initializationCommandCount": initialization_count,
                "initializationCommandsIncluded": False,
            }
        )
    else:
        label = "XGComm settings"
        blob_present = _boolean(
            item["connection_blob_present"],
            "XGComm connection blob presence",
        )
        blob_size = _raw_integer(
            item["connection_blob_encoded_char_count"],
            "XGComm connection blob size",
            minimum=0,
        )
        if item["connection_blob_included"] is not False or blob_present != (blob_size > 0):
            raise MCPToolError("XGComm redacted connection blob metadata is inconsistent")
        attributes.update(
            {
                "connectionBlobPresent": blob_present,
                "connectionBlobEncodedCharacterCount": blob_size,
                "connectionBlobIncluded": False,
            }
        )
    return _structure_fact(
        kind="communication",
        entity_id=item["communication_id"],
        parent_id=parent_id,
        name=label,
        order=(
            _raw_integer(order, "communication order", minimum=0)
            if order is not None
            else None
        ),
        status="partial",
        attributes=attributes,
    )


def _validate_opaque_structure_item(value: Mapping[str, Any]) -> None:
    required = {
        "container_id",
        "source_path",
        "container_type",
        "field",
        "encoding",
        "compressed_flag",
        "encoded_char_count",
        "raw_payload_included",
        "status",
    }
    optional = {
        "encoded_byte_count",
        "payload_sha256",
        "compression",
        "decoded_byte_count",
        "decoded_sha256",
        "declared_record_count",
        "record_count",
        "marker_string_count",
        "unparsed_trailing_byte_count",
    }
    if not required.issubset(value) or set(value) - required - optional:
        raise MCPToolError("opaque project container fields are invalid")
    _raw_text(value["container_id"], "opaque container id", maximum=500, allow_empty=False)
    _validate_source_path(value["source_path"], "opaque container source path")
    for key in ("container_type", "field", "encoding", "status"):
        _raw_text(value[key], f"opaque container {key}", maximum=256, allow_empty=False)
    _raw_optional_text(value["compressed_flag"], "opaque compressed flag", maximum=100)
    _raw_integer(value["encoded_char_count"], "opaque encoded character count", minimum=0)
    if value["raw_payload_included"] is not False:
        raise MCPToolError("project structure unexpectedly exposed an opaque payload")
    for key in (
        "encoded_byte_count",
        "decoded_byte_count",
        "declared_record_count",
        "record_count",
        "marker_string_count",
        "unparsed_trailing_byte_count",
    ):
        if key in value:
            _raw_optional_integer(value[key], f"opaque container {key}", minimum=0)
    for key in ("payload_sha256", "decoded_sha256"):
        if key in value:
            _raw_sha256(value[key], f"opaque container {key}")
    if "compression" in value:
        _raw_text(value["compression"], "opaque container compression", maximum=100, allow_empty=False)


def _structure_index_contract(
    result: Any,
    *,
    expected_session_identity: Mapping[str, Any] | None,
    allow_workspace_static_identity: bool = False,
) -> dict[str, Any]:
    index = _exact_raw_mapping(
        result,
        "project structure index",
        {
            "schema",
            "status",
            "read_only",
            "project_binding",
            "project",
            "sections",
            "coverage",
            "limitations",
            "index_digest",
        },
    )
    if (
        index["schema"] != "xg5000.project.structure-index/1.0"
        or index["read_only"] is not True
    ):
        raise MCPToolError("project structure index schema or read-only flag is invalid")
    supplied_index_digest = _raw_sha256(index["index_digest"], "project structure index digest")
    unsigned_index = dict(index)
    unsigned_index.pop("index_digest")
    try:
        encoded_index = json.dumps(
            unsigned_index,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MCPToolError("project structure index is not canonical JSON") from exc
    if len(encoded_index) > _PROJECT_STRUCTURE_RAW_BYTE_LIMIT or supplied_index_digest != hashlib.sha256(
        encoded_index
    ).hexdigest():
        raise MCPToolError("project structure index digest is invalid")

    binding = _exact_raw_mapping(
        index["project_binding"],
        "project structure binding",
        {
            "session_id",
            "pid",
            "hwnd",
            "project_path",
            "project_sha256",
            "project_digest",
            "source_receipt",
        },
    )
    _raw_text(binding["session_id"], "project structure session id", maximum=500, allow_empty=False)
    expected_workspace_static = (
        allow_workspace_static_identity
        and isinstance(expected_session_identity, Mapping)
        and set(expected_session_identity)
        == {"session_id", "pid", "hwnd", "project_path", "project_hash"}
        and isinstance(expected_session_identity.get("session_id"), str)
        and expected_session_identity["session_id"].startswith("workspace:")
        and expected_session_identity.get("pid") == 0
        and expected_session_identity.get("hwnd") == 0
    )
    identity_minimum = 0 if expected_workspace_static else 1
    _raw_integer(
        binding["pid"], "project structure process id", minimum=identity_minimum
    )
    _raw_integer(
        binding["hwnd"], "project structure window handle", minimum=identity_minimum
    )
    project_path = _raw_text(
        binding["project_path"],
        "project structure path",
        maximum=32_768,
        allow_empty=False,
    )
    if not project_path.casefold().endswith(".xgwx"):
        raise MCPToolError("project structure path is not an XGWX working copy")
    project_sha256 = _raw_sha256(
        binding["project_sha256"],
        "project structure project digest",
    )
    if not isinstance(expected_session_identity, Mapping) or set(
        expected_session_identity
    ) != set(("session_id", "pid", "hwnd", "project_path", "project_hash")):
        raise MCPToolError("project structure index has no Host-bound session identity")
    expected_project_sha256 = _raw_sha256(
        expected_session_identity.get("project_hash"),
        "Host-bound project digest",
    )
    try:
        observed_path = os.path.normcase(os.path.abspath(project_path))
        expected_path = os.path.normcase(
            os.path.abspath(
                _raw_text(
                    expected_session_identity.get("project_path"),
                    "Host-bound project path",
                    maximum=32_768,
                    allow_empty=False,
                )
            )
        )
    except (OSError, TypeError, ValueError) as exc:
        raise MCPToolError("project structure path identity is invalid") from exc
    if (
        str(binding["session_id"]) != str(expected_session_identity.get("session_id"))
        or binding["pid"] != expected_session_identity.get("pid")
        or binding["hwnd"] != expected_session_identity.get("hwnd")
        or observed_path != expected_path
        or project_sha256 != expected_project_sha256
    ):
        raise MCPToolError("project structure index is not bound to the active Host session")
    digest_binding = _exact_raw_mapping(
        binding["project_digest"],
        "project structure digest binding",
        {"algorithm", "value", "scope"},
    )
    if (
        digest_binding["algorithm"] != "sha256"
        or digest_binding["scope"] != "exact_bound_xgwx_file"
        or _raw_sha256(digest_binding["value"], "project structure bound digest")
        != project_sha256
    ):
        raise MCPToolError("project structure digest binding is inconsistent")
    source = _exact_raw_mapping(
        binding["source_receipt"],
        "project structure XML source receipt",
        {
            "kind",
            "observed",
            "project_sha256",
            "project_size",
            "xml_sha256",
            "xml_size",
            "gzip_offset",
            "gzip_member_size",
            "trailing_size",
        },
    )
    source_project_sha = _raw_sha256(
        source["project_sha256"],
        "project structure XML source project digest",
    )
    xml_sha256 = _raw_sha256(
        source["xml_sha256"],
        "project structure XML digest",
    )
    project_size = _raw_integer(
        source["project_size"],
        "project structure file size",
        minimum=1,
        maximum=256 * 1024 * 1024,
    )
    xml_size = _raw_integer(
        source["xml_size"],
        "project structure XML size",
        minimum=1,
        maximum=32 * 1024 * 1024,
    )
    gzip_offset = _raw_integer(
        source["gzip_offset"],
        "project structure gzip offset",
        minimum=0,
        maximum=4_095,
    )
    gzip_member_size = _raw_integer(
        source["gzip_member_size"],
        "project structure gzip member size",
        minimum=19,
        maximum=256 * 1024 * 1024,
    )
    trailing_size = _raw_integer(
        source["trailing_size"],
        "project structure trailing size",
        minimum=0,
        maximum=256 * 1024 * 1024,
    )
    if (
        source["kind"] != "xgwx_project_xml"
        or source["observed"] is not True
        or source_project_sha != project_sha256
        or gzip_offset + gzip_member_size + trailing_size != project_size
    ):
        raise MCPToolError("project structure XML source binding is inconsistent")

    project = _exact_raw_mapping(
        index["project"],
        "project structure project",
        {
            "name",
            "vendor_guid",
            "version",
            "file_version",
            "comment",
            "configuration_count",
        },
    )
    project_name = _raw_text(project["name"], "project structure project name")
    _validate_vendor_uuid(project["vendor_guid"], "project vendor GUID")
    for key in ("version", "file_version", "comment"):
        _raw_text(project[key], f"project structure {key}")
    configuration_count = _raw_integer(
        project["configuration_count"],
        "project structure configuration count",
        minimum=0,
    )

    top_coverage = _exact_raw_mapping(
        index["coverage"],
        "project structure coverage",
        {
            "offset",
            "limit",
            "maximum_limit",
            "output_truncated",
            "code_body_semantics",
            "task_order_basis",
            "raw_xml_included",
            "raw_binary_included",
            "secret_values_included",
            "secret_attribute_count_redacted",
        },
    )
    offset = _raw_integer(top_coverage["offset"], "project structure offset", minimum=0)
    limit = _raw_integer(
        top_coverage["limit"],
        "project structure limit",
        minimum=1,
        maximum=2_000,
    )
    if _raw_integer(
        top_coverage["maximum_limit"],
        "project structure maximum limit",
        minimum=1,
        maximum=2_000,
    ) != 2_000:
        raise MCPToolError("project structure maximum limit changed unexpectedly")
    code_body_semantics = _raw_text(
        top_coverage["code_body_semantics"],
        "project structure code-body semantics",
        maximum=50,
        allow_empty=False,
    )
    if code_body_semantics not in {"partial", "not_present"}:
        raise MCPToolError("project structure code-body semantics is invalid")
    if top_coverage["task_order_basis"] != "configuration_xml_document_order":
        raise MCPToolError("project structure task order basis is invalid")
    if any(
        top_coverage[key] is not False
        for key in ("raw_xml_included", "raw_binary_included", "secret_values_included")
    ):
        raise MCPToolError("project structure unexpectedly exposed raw or secret content")
    secret_count = _raw_integer(
        top_coverage["secret_attribute_count_redacted"],
        "project structure redacted secret count",
        minimum=0,
    )

    sections = _exact_raw_mapping(
        index["sections"],
        "project structure sections",
        set(_PROJECT_STRUCTURE_SECTION_NAMES),
    )
    facts_by_section: dict[str, list[dict[str, Any]]] = {
        section: [] for section in _PROJECT_STRUCTURE_FACT_SECTIONS
    }
    total_count = 0
    observed_count = 0
    source_truncated = False
    for section_name in _PROJECT_STRUCTURE_SECTION_NAMES:
        section = _exact_raw_mapping(
            sections[section_name],
            f"project structure {section_name} section",
            {"coverage", "items"},
        )
        section_coverage = _exact_raw_mapping(
            section["coverage"],
            f"project structure {section_name} coverage",
            {"count", "total", "returned", "offset", "limit", "truncated", "next_offset"},
        )
        items = _items(section["items"], f"project structure {section_name} items")
        if len(items) > limit:
            raise MCPToolError(f"project structure {section_name} exceeds its page limit")
        count = _raw_integer(
            section_coverage["count"],
            f"project structure {section_name} count",
            minimum=0,
        )
        total = _raw_integer(
            section_coverage["total"],
            f"project structure {section_name} total",
            minimum=0,
        )
        returned = _raw_integer(
            section_coverage["returned"],
            f"project structure {section_name} returned count",
            minimum=0,
        )
        section_offset = _raw_integer(
            section_coverage["offset"],
            f"project structure {section_name} offset",
            minimum=0,
        )
        section_limit = _raw_integer(
            section_coverage["limit"],
            f"project structure {section_name} limit",
            minimum=1,
            maximum=2_000,
        )
        truncated = _boolean(
            section_coverage["truncated"],
            f"project structure {section_name} truncation",
        )
        expected_returned = min(limit, max(total - offset, 0))
        expected_next = offset + returned if offset + returned < total else None
        next_offset = _raw_optional_integer(
            section_coverage["next_offset"],
            f"project structure {section_name} next offset",
            minimum=0,
        )
        if (
            count != total
            or returned != len(items)
            or returned != expected_returned
            or section_offset != offset
            or section_limit != limit
            or truncated != (offset > 0 or offset + returned < total)
            or next_offset != expected_next
        ):
            raise MCPToolError(f"project structure {section_name} page coverage is inconsistent")
        total_count += total
        observed_count += returned
        source_truncated = source_truncated or truncated
        for item in items:
            fact = _validate_structure_item(section_name, item)
            if fact is not None:
                facts_by_section[section_name].append(fact)
    if configuration_count != _mapping(
        sections["configurations"],
        "project structure configuration section",
    )["coverage"]["total"]:
        raise MCPToolError("project structure configuration count is inconsistent")
    if top_coverage["output_truncated"] is not source_truncated:
        raise MCPToolError("project structure top-level truncation is inconsistent")

    limitation_values = _items(index["limitations"], "project structure limitations")
    if len(limitation_values) > 500:
        raise MCPToolError("project structure limitations exceed their bound")
    raw_limitations: list[str] = []
    canonical_limitation_order: list[tuple[str, str, str]] = []
    for limitation_value in limitation_values:
        limitation = _exact_raw_mapping(
            limitation_value,
            "project structure limitation",
            {"code", "scope", "count", "detail"},
        )
        code = _raw_text(limitation["code"], "project structure limitation code", maximum=200, allow_empty=False)
        scope_name = _raw_text(limitation["scope"], "project structure limitation scope", maximum=500, allow_empty=False)
        detail = _raw_text(limitation["detail"], "project structure limitation detail", maximum=1_000, allow_empty=False)
        count = _raw_integer(limitation["count"], "project structure limitation count", minimum=1)
        canonical_limitation_order.append((code, scope_name, detail))
        raw_limitations.append(f"{code} ({scope_name}, count={count}): {detail}")
    if canonical_limitation_order != sorted(set(canonical_limitation_order)):
        raise MCPToolError("project structure limitations are not canonical")
    source_status = _raw_text(
        index["status"],
        "project structure status",
        maximum=20,
        allow_empty=False,
    )
    if source_status not in {"complete", "partial"} or (
        source_status == "complete" and (limitation_values or source_truncated)
    ) or (source_status == "partial" and not limitation_values and not source_truncated):
        raise MCPToolError("project structure status contradicts its limitations")
    if secret_count and not any(
        limitation.get("code") == "secret_attributes_redacted"
        for limitation in limitation_values
        if isinstance(limitation, Mapping)
    ):
        raise MCPToolError("project structure secret redaction is not disclosed")

    facts = _round_robin_structure_facts(facts_by_section)
    deduped_facts = _dedupe_structure_facts(facts)
    fact_index_truncated = len(_bounded_structure_facts(deduped_facts)) != len(
        deduped_facts
    )
    nested_projection_truncated = any(
        attribute.get("name") == "projectedReferencedByCodeUnitIdsTruncated"
        and attribute.get("value") is True
        for fact in deduped_facts
        for attribute in fact["attributes"]
    )
    limitations = _stable_union(
        raw_limitations,
        [_PROJECT_STRUCTURE_STATIC_LIMITATION],
        ([_PROJECT_STRUCTURE_FACT_BOUND_LIMITATION] if fact_index_truncated else []),
        (
            [_PROJECT_STRUCTURE_NESTED_BOUND_LIMITATION]
            if nested_projection_truncated
            else []
        ),
    )
    scope, capture_limitations = _payload_scope(
        {
            "payloads": [
                {
                    "program_id": f"xg5000:project:{project_sha256}",
                    "name": project_name or "XG5000 project",
                    "payload_sha256": project_sha256,
                    "stable_repeat": True,
                    "cache_hit": False,
                }
            ]
        },
        "project.structure_index",
        query={"offset": offset, "limit": limit},
        coverage_unit_keys=[f"structure:{name}" for name in _PROJECT_STRUCTURE_SECTION_NAMES],
        counts_additive=False,
    )
    coverage = _with_capture_limitations(
        _coverage(
            area="project_structure",
            status="partial",
            observed_count=observed_count,
            total_count=total_count,
            truncated=(
                source_truncated
                or fact_index_truncated
                or nested_projection_truncated
            ),
            limitations=limitations,
        ),
        capture_limitations,
    )
    project_snapshot = {
        "projectSha256": project_sha256,
        "indexDigest": supplied_index_digest,
        "sourceKind": "xgwx_project_xml",
        "sourceObserved": True,
        "projectSize": project_size,
        "xmlSha256": xml_sha256,
        "xmlSize": xml_size,
        "gzipOffset": gzip_offset,
        "gzipMemberSize": gzip_member_size,
        "trailingSize": trailing_size,
        "rawXmlIncluded": False,
        "rawBinaryIncluded": False,
        "secretValuesIncluded": False,
        "secretAttributeCountRedacted": secret_count,
    }
    return _contract(
        "project.structure_index",
        coverage,
        [],
        [],
        scope=scope,
        structure_facts=facts,
        project_snapshot=project_snapshot,
    )


def _project_search_contract(
    result: Any,
    *,
    model_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    envelope = _validated_result(result, "project.search")
    search = _mapping(envelope.get("search"), "project search receipt")
    if search.get("schema") != "xg5000.project-search/1.0":
        raise MCPToolError("project search schema is invalid")
    supplied_digest = _raw_sha256(search.get("search_digest"), "project search digest")
    unsigned = {key: value for key, value in search.items() if key != "search_digest"}
    if supplied_digest != canonical_digest(unsigned):
        raise MCPToolError("project search digest is invalid")
    query = _raw_text(search.get("query"), "project search query", maximum=256, allow_empty=False)
    if search.get("case_sensitive") is not False:
        raise MCPToolError("project search must use literal case-insensitive matching")
    project_sha256 = _raw_sha256(
        search.get("project_sha256"),
        "project search project digest",
    )
    if project_sha256 != str(envelope.get("project_hash_before") or "").casefold():
        raise MCPToolError("project search project binding is invalid")
    program_filter = search.get("program_id")
    if program_filter is not None:
        program_filter = _raw_text(
            program_filter,
            "project search program filter",
            maximum=500,
            allow_empty=False,
        )
    payloads = _items(search.get("payloads"), "project search payload bindings")
    scope, capture_limitations = _payload_scope(
        {**envelope, "payloads": payloads},
        "project.search",
        query={
            "literal": query,
            "programId": program_filter,
            "searchDigest": supplied_digest,
        },
        coverage_unit_keys=[],
        counts_additive=True,
    )
    program_count = _integer(search.get("program_count"), "project search program count")
    if program_count != len(payloads):
        raise MCPToolError("project search program count disagrees with its payload scope")
    bound_programs = {
        (str(item["programId"]), str(item["program"]))
        for item in scope["payloadBindings"]
    }

    matches = _items(search.get("matches"), "project search matches")
    returned_count = _integer(search.get("match_count"), "project search returned count")
    total_count = _integer(search.get("total_match_count"), "project search total count")
    pagination = _mapping(search.get("pagination"), "project search pagination")
    offset = _integer(pagination.get("offset"), "project search offset")
    page_limit = _integer(pagination.get("limit"), "project search limit", maximum=100)
    page_returned = _integer(pagination.get("returned"), "project search page count")
    page_total = _integer(pagination.get("total"), "project search page total")
    next_offset = pagination.get("next_offset")
    if next_offset is not None:
        next_offset = _integer(next_offset, "project search next offset")
    if not (
        page_limit >= 1
        and returned_count == page_returned == len(matches)
        and total_count == page_total
        and offset + returned_count <= total_count
        and next_offset == (offset + returned_count if offset + returned_count < total_count else None)
    ):
        raise MCPToolError("project search pagination is inconsistent")

    locations: list[dict[str, Any]] = []
    match_ids: list[str] = []
    for raw_match in matches:
        match = _mapping(raw_match, "project search match")
        match_id = _raw_sha256(match.get("match_id"), "project search match id")
        unsigned_match = {key: value for key, value in match.items() if key != "match_id"}
        if match_id != canonical_digest(unsigned_match):
            raise MCPToolError("project search match identity is invalid")
        match_ids.append(match_id)
        kind = match.get("kind")
        matched_field = _raw_text(
            match.get("matched_field"),
            "project search matched field",
            maximum=100,
            allow_empty=False,
        )
        matched_text = _raw_text(
            match.get("matched_text"),
            "project search matched text",
            maximum=4_096,
            allow_empty=False,
        )
        if query.casefold() not in matched_text.casefold():
            raise MCPToolError("project search match does not contain its literal query")
        if kind == "program_name":
            if set(match) != {
                "match_id",
                "kind",
                "matched_field",
                "matched_text",
                "program_id",
                "program_name",
            }:
                raise MCPToolError("project search program-name match fields are invalid")
            program_binding = (
                _raw_text(
                    match.get("program_id"),
                    "matched program id",
                    maximum=500,
                    allow_empty=False,
                ),
                _raw_text(
                    match.get("program_name"),
                    "matched program name",
                    allow_empty=False,
                ),
            )
            if (
                matched_field != "name"
                or matched_text != program_binding[1]
                or program_binding not in bound_programs
            ):
                raise MCPToolError("project search program-name match is inconsistent")
            if model_candidates is not None:
                model_candidates.append(
                    {
                        "kind": "program_name",
                        "matchId": match_id,
                        "programId": program_binding[0],
                        "programName": program_binding[1],
                    }
                )
            continue
        if kind == "ladder_record":
            if set(match) != {
                "match_id",
                "kind",
                "matched_field",
                "matched_text",
                "program_id",
                "program_name",
                "record_kind",
                "access",
                "visual_row",
                "column",
                "token_offset",
                "value_token_offset",
            }:
                raise MCPToolError("project search ladder match fields are invalid")
            program_id = _raw_text(
                match.get("program_id"),
                "matched ladder program id",
                maximum=500,
                allow_empty=False,
            )
            program_name = _raw_text(
                match.get("program_name"),
                "matched ladder program name",
                allow_empty=False,
            )
            if (program_id, program_name) not in bound_programs:
                raise MCPToolError("project search ladder match is outside its payload scope")
            if matched_field != "value":
                raise MCPToolError("project search ladder matched field is invalid")
            access = _raw_text(
                match.get("access"),
                "project search ladder access",
                maximum=50,
                allow_empty=False,
            )
            if access not in _ACCESS_KINDS:
                raise MCPToolError("project search ladder access is invalid")
            _raw_text(
                match.get("record_kind"),
                "project search record kind",
                maximum=100,
                allow_empty=False,
            )
            visual_row = _raw_integer(
                match.get("visual_row"),
                "project search visual row",
                minimum=0,
            )
            _raw_optional_integer(
                match.get("column"),
                "project search column",
                minimum=0,
            )
            _raw_integer(
                match.get("token_offset"),
                "project search record token offset",
                minimum=0,
            )
            value_token_offset = _raw_integer(
                match.get("value_token_offset"),
                "project search value token offset",
                minimum=0,
            )
            location = _source_location(
                {
                    "program": program_name,
                    "visual_row": visual_row,
                    "token_offset": value_token_offset,
                },
                access=access,
                semantic_status="observed",
                device=_exact_device_or_none(match.get("matched_text")),
            )
            locations.append(location)
            if model_candidates is not None:
                model_candidates.append(
                    {
                        "kind": "ladder_record",
                        "matchId": match_id,
                        "programId": program_id,
                        "programName": program_name,
                        "recordKind": str(match["record_kind"]),
                        "visualRow": location["visualRow"],
                        "tokenOffset": location["tokenOffset"],
                        "access": location["access"],
                        **(
                            {"column": int(match["column"])}
                            if match["column"] is not None
                            else {}
                        ),
                        **({"device": location["device"]} if "device" in location else {}),
                    }
                )
            continue
        if kind == "structure_item":
            if set(match) != {
                "match_id",
                "kind",
                "section",
                "entity_id",
                "matched_field",
                "matched_text",
                "item",
            }:
                raise MCPToolError("project search structure match fields are invalid")
            section = _raw_text(
                match.get("section"),
                "project search structure section",
                maximum=100,
                allow_empty=False,
            )
            if section not in {"code_units", "symbols", "devices"}:
                raise MCPToolError("project search structure section is unsupported")
            item = _mapping(match.get("item"), "project search structure item")
            if matched_field not in _PROJECT_SEARCH_STRUCTURE_FIELDS[section]:
                raise MCPToolError("project search structure matched field is invalid")
            observed: Any = item
            for field_part in matched_field.split("."):
                if not isinstance(observed, Mapping):
                    raise MCPToolError("project search structure matched field is invalid")
                observed = observed.get(field_part)
            if not isinstance(observed, str) or observed.strip() != matched_text:
                raise MCPToolError("project search structure matched text is inconsistent")
            id_field = {
                "code_units": "code_unit_id",
                "symbols": "symbol_id",
                "devices": "device_id",
            }[section]
            if item.get(id_field) != match.get("entity_id"):
                raise MCPToolError("project search structure identity is inconsistent")
            fact = _validate_structure_item(section, item)
            if fact is None:
                raise MCPToolError("project search structure item has no public fact")
            if model_candidates is not None:
                model_candidates.append(
                    {
                        "kind": "structure_item",
                        "matchId": match_id,
                        "section": section,
                        "matchedField": matched_field,
                        "structureLocator": _fact_without_fingerprint(fact),
                    }
                )
            continue
        raise MCPToolError("project search match kind is unsupported")
    if len(match_ids) != len(set(match_ids)):
        raise MCPToolError("project search match identities are not unique")
    scope["coverageUnitKeys"] = sorted(match_ids)
    scope["scopeKey"] = canonical_digest(
        {
            "toolName": "project.search",
            "query": scope["query"],
            "coverageUnitKeys": scope["coverageUnitKeys"],
            "payloadSetDigest": scope["payloadSetDigest"],
            "cacheReused": scope["cacheReused"],
            "sequentialCapture": scope["sequentialCapture"],
            "payloadBindingsTruncated": scope["payloadBindingsTruncated"],
        }
    )
    scope["logicalScopeKey"] = canonical_digest(
        {
            "toolName": "project.search",
            "query": scope["query"],
            "coverageUnitKeys": scope["coverageUnitKeys"],
        }
    )

    limitations = _string_list(search.get("limitations"), "project search limitations")
    status = _raw_text(
        search.get("status"),
        "project search status",
        maximum=20,
        allow_empty=False,
    )
    if status not in {"complete", "partial"} or (status == "complete") != (not limitations):
        raise MCPToolError("project search status contradicts its limitations")
    structure_digest = search.get("structure_index_digest")
    if structure_digest is not None:
        _raw_sha256(structure_digest, "project search structure index digest")
    coverage = _with_capture_limitations(
        _coverage(
            area="programs",
            status="complete" if status == "complete" else "partial",
            observed_count=returned_count,
            total_count=total_count,
            truncated=next_offset is not None,
            limitations=limitations,
        ),
        capture_limitations,
    )
    return _contract(
        "project.search",
        coverage,
        locations,
        [],
        scope=scope,
    )


def project_search_context(result: Any) -> dict[str, Any] | None:
    """Return bounded, receipt-bound search candidates for inference only."""

    candidates: list[dict[str, Any]] = []
    receipt = _project_search_contract(result, model_candidates=candidates)
    if not candidates:
        return None
    candidates_by_kind = {
        kind: [item for item in candidates if item["kind"] == kind]
        for kind in ("program_name", "structure_item", "ladder_record")
    }
    bounded_candidates = [
        candidates_by_kind[kind][index]
        for index in range(max(map(len, candidates_by_kind.values())))
        for kind in candidates_by_kind
        if index < len(candidates_by_kind[kind])
    ][:_MODEL_CONTEXT_ITEM_LIMIT]
    return {
        "searchDigest": receipt["scope"]["query"]["searchDigest"],
        "coverageUnitKeys": receipt["scope"]["coverageUnitKeys"],
        "candidates": bounded_candidates,
        "candidateCount": len(candidates),
        "truncated": len(candidates) > _MODEL_CONTEXT_ITEM_LIMIT,
    }


def build_structural_evidence_contract(
    raw_name: str,
    result: Any,
    *,
    expected_session_identity: Mapping[str, Any] | None = None,
    allow_workspace_static_identity: bool = False,
) -> dict[str, Any] | None:
    """Build a common bounded receipt for the structural analysis tools."""

    if raw_name == "project.structure_index":
        return _structure_index_contract(
            result,
            expected_session_identity=expected_session_identity,
            allow_workspace_static_identity=allow_workspace_static_identity,
        )
    builders = {
        "project.search": _project_search_contract,
        "program.logic_graph": _logic_graph_contract,
        "program.state_model": _state_model_contract,
        "device.references": _references_contract,
        "device.causal_impact": _causal_contract,
        "project.behavior_report": _behavior_contract,
    }
    builder = builders.get(raw_name)
    return builder(result) if builder is not None else None


def state_model_context(result: Any) -> dict[str, Any] | None:
    """Project static meaning after the same raw result produced a valid receipt."""

    raw_name = "program.state_model"
    envelope = _validated_result(result, raw_name)
    model = _mapping(envelope.get("state_model"), "state model")
    projected: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []

    def collect_comparisons(
        condition_value: Any,
        *,
        semantic_status: str,
        enclosing: dict[str, Any],
    ) -> None:
        condition = _mapping(condition_value, "state model condition")
        operator = _text(
            condition.get("operator"),
            "state model condition operator",
            maximum=50,
        )
        if operator == "COMPARE":
            validated = _validated_comparison(
                condition, semantic_status=semantic_status
            )
            if validated is None:
                return
            expression, parsed = validated
            if semantic_status == "structured":
                source = _source_location(
                    condition.get("source"),
                    access="condition",
                    semantic_status=semantic_status,
                    rung_id=enclosing["rungId"],
                )
                comparisons.append(
                    {
                        "expression": expression,
                        **parsed,
                        "source": {
                            "program": source["program"],
                            "visualRow": source["visualRow"],
                            "tokenOffset": source["tokenOffset"],
                        },
                        "action": enclosing["action"],
                        "target": enclosing["target"],
                        "rungId": enclosing["rungId"],
                    }
                )
            return
        if operator == "NOT":
            collect_comparisons(
                condition.get("term"),
                semantic_status=semantic_status,
                enclosing=enclosing,
            )
            return
        if operator in {"AND", "OR"}:
            for term in _items(condition.get("terms"), "state model condition terms"):
                collect_comparisons(
                    term,
                    semantic_status=semantic_status,
                    enclosing=enclosing,
                )
            return
        if operator not in {"DEVICE", "TRUE", "UNKNOWN"}:
            raise MCPToolError("state model condition operator is not supported")

    for action_value in _items(model.get("actions"), "state model actions"):
        action = _mapping(action_value, "state model action")
        action_status = _semantic(
            action.get("semantic_status"), "state model action"
        )
        action_name = _text(
            action.get("action"), "state model action name", maximum=100
        ).upper()
        target = _exact_device_or_none(action.get("target"))
        rung_id_value = action.get("rung_id")
        rung_id = (
            _text(rung_id_value, "state model rung id")
            if rung_id_value is not None
            else None
        )
        collect_comparisons(
            action.get("condition"),
            semantic_status=action_status,
            enclosing={"action": action_name, "target": target, "rungId": rung_id},
        )
        if action.get("record_kind") != "APPLICATION":
            continue
        raw_value = _text(
            action.get("raw_value"),
            "state model application operand",
            maximum=4_000,
        )
        semantics = _application_in_place_mutation(raw_value)
        if semantics is None:
            continue
        expected_metadata = {
            "mnemonic": semantics["mnemonic"],
            "operation": semantics["operation"],
            "execution": semantics["execution"],
            "data_type": semantics["dataType"],
            "target": semantics["target"],
            "read_devices": semantics["readDevices"],
            "write_devices": semantics["writeDevices"],
            "width_words": semantics["widthWords"],
        }
        metadata = _mapping(
            action.get("in_place_mutation"),
            "state model in-place mutation",
        )
        if dict(metadata) != expected_metadata:
            raise MCPToolError("state model in-place mutation semantics are inconsistent")
        projected.append(semantics)

    if not projected and not comparisons:
        return None
    context: dict[str, Any] = {}
    if projected:
        context.update(
            {
                "inPlaceMutations": projected[:_MODEL_CONTEXT_ITEM_LIMIT],
                "totalCount": len(projected),
                "truncated": len(projected) > _MODEL_CONTEXT_ITEM_LIMIT,
            }
        )
    if comparisons:
        context.update(
            {
                "comparisons": comparisons[:_MODEL_CONTEXT_ITEM_LIMIT],
                "comparisonCount": len(comparisons),
                "comparisonsTruncated": len(comparisons) > _MODEL_CONTEXT_ITEM_LIMIT,
            }
        )
    return context


def _validate_location_fact(value: Any) -> dict[str, Any]:
    fact = _mapping(value, "structural location fact")
    allowed = {
        "fingerprint",
        "program",
        "networkId",
        "networkIndex",
        "rungId",
        "visualRow",
        "tokenOffset",
        "device",
        "access",
        "semanticStatus",
    }
    if set(fact) - allowed:
        raise ValueError("structural location fact has unknown fields")
    canonical: dict[str, Any] = {
        "program": _text(fact.get("program"), "location program"),
        "access": _text(fact.get("access"), "location access", maximum=50),
        "semanticStatus": _text(
            fact.get("semanticStatus"),
            "location semantic status",
            maximum=50,
        ),
    }
    if canonical["access"] not in _ACCESS_KINDS or canonical["semanticStatus"] not in _SEMANTIC_STATUSES:
        raise ValueError("structural location fact enum is invalid")
    for key, maximum in (
        ("networkIndex", 1_000_000),
        ("visualRow", 1_000_000),
        ("tokenOffset", 100_000_000),
    ):
        if key in fact:
            canonical[key] = _integer(fact[key], f"location {key}", maximum=maximum)
    for key, maximum in (("networkId", 500), ("rungId", 500)):
        if key in fact:
            canonical[key] = _text(fact[key], f"location {key}", maximum=maximum)
    if "device" in fact:
        canonical["device"] = _device(fact["device"], "location device")
    fingerprint = _text(fact.get("fingerprint"), "location fingerprint", maximum=64)
    if fingerprint != canonical_digest(canonical):
        raise ValueError("structural location fingerprint is invalid")
    return {"fingerprint": fingerprint, **canonical}


def _validate_relation_fact(value: Any) -> dict[str, Any]:
    fact = _mapping(value, "structural relation fact")
    allowed = {
        "fingerprint",
        "relationKind",
        "source",
        "target",
        "deviceChain",
        "direct",
        "semanticStatus",
        "locations",
    }
    if set(fact) != allowed:
        raise ValueError("structural relation fact fields are invalid")
    locations = [_validate_location_fact(item) for item in _items(fact["locations"], "relation locations")]
    canonical = {
        "relationKind": _text(fact["relationKind"], "relation kind", maximum=50),
        "source": _text(fact["source"], "relation source"),
        "target": _text(fact["target"], "relation target"),
        "deviceChain": [
            _device(item, "relation device chain")
            for item in _items(fact["deviceChain"], "relation device chain")
        ],
        "direct": _boolean(fact["direct"], "relation direct flag"),
        "semanticStatus": _text(
            fact["semanticStatus"],
            "relation semantic status",
            maximum=50,
        ),
        "locations": locations,
    }
    if canonical["relationKind"] not in _RELATION_KINDS or canonical["semanticStatus"] not in {"structured", "partial"}:
        raise ValueError("structural relation fact enum is invalid")
    if canonical["relationKind"] == "causal_path" and (
        len(canonical["deviceChain"]) < 2
        or canonical["deviceChain"][0] != canonical["source"]
        or canonical["deviceChain"][-1] != canonical["target"]
        or canonical["direct"] != (len(canonical["deviceChain"]) == 2)
    ):
        raise ValueError("structural causal relation is not canonical")
    fingerprint = _text(fact["fingerprint"], "relation fingerprint", maximum=64)
    if fingerprint != canonical_digest(canonical):
        raise ValueError("structural relation fingerprint is invalid")
    return {"fingerprint": fingerprint, **canonical}


def _validate_structure_fact(value: Any) -> dict[str, Any]:
    fact = _mapping(value, "project structure fact")
    allowed = {
        "fingerprint",
        "kind",
        "entityId",
        "name",
        "parentId",
        "order",
        "status",
        "attributes",
    }
    required = {"fingerprint", "kind", "entityId", "status", "attributes"}
    if not required.issubset(fact) or set(fact) - allowed:
        raise ValueError("project structure fact fields are invalid")
    kind = _text(fact["kind"], "project structure fact kind", maximum=100)
    status = _text(fact["status"], "project structure fact status", maximum=50)
    if (
        kind not in _PROJECT_STRUCTURE_FACT_KINDS
        or status not in _PROJECT_STRUCTURE_FACT_STATUSES
    ):
        raise ValueError("project structure fact enum is invalid")
    canonical: dict[str, Any] = {
        "kind": kind,
        "entityId": _text(
            fact["entityId"],
            "project structure fact entity id",
            maximum=500,
        ),
        "status": status,
        "attributes": [],
    }
    if "name" in fact:
        canonical["name"] = _text(
            fact["name"],
            "project structure fact name",
            maximum=4_096,
        )
    if "parentId" in fact:
        canonical["parentId"] = _text(
            fact["parentId"],
            "project structure fact parent id",
            maximum=500,
        )
    if "order" in fact:
        canonical["order"] = _integer(
            fact["order"],
            "project structure fact order",
        )
    attributes: list[dict[str, Any]] = []
    for attribute_value in _items(
        fact["attributes"],
        "project structure fact attributes",
    ):
        attribute = _mapping(attribute_value, "project structure fact attribute")
        if set(attribute) != {"name", "value"}:
            raise ValueError("project structure fact attribute fields are invalid")
        attribute_name = _text(
            attribute["name"],
            "project structure fact attribute name",
            maximum=100,
        )
        attribute_data = attribute["value"]
        if isinstance(attribute_data, bool):
            normalized_data: str | int | bool = attribute_data
        elif isinstance(attribute_data, int) and not isinstance(attribute_data, bool):
            if not -(2**31) <= attribute_data <= 100_000_000:
                raise ValueError("project structure fact integer attribute is out of range")
            normalized_data = attribute_data
        elif isinstance(attribute_data, str) and attribute_data and len(attribute_data) <= 4_096:
            normalized_data = attribute_data
        else:
            raise ValueError("project structure fact attribute value is invalid")
        attributes.append({"name": attribute_name, "value": normalized_data})
    if attributes != sorted(attributes, key=lambda item: str(item["name"])) or len(
        {str(item["name"]) for item in attributes}
    ) != len(attributes):
        raise ValueError("project structure fact attributes are not canonical")
    canonical["attributes"] = attributes
    fingerprint = _text(
        fact["fingerprint"],
        "project structure fact fingerprint",
        maximum=64,
    )
    if fingerprint != canonical_digest(canonical):
        raise ValueError("project structure fact fingerprint is invalid")
    return {"fingerprint": fingerprint, **canonical}


def _validate_project_snapshot(value: Any) -> dict[str, Any]:
    snapshot = dict(
        _exact_raw_mapping(
            value,
            "project structure snapshot",
            {
                "projectSha256",
                "indexDigest",
                "sourceKind",
                "sourceObserved",
                "projectSize",
                "xmlSha256",
                "xmlSize",
                "gzipOffset",
                "gzipMemberSize",
                "trailingSize",
                "rawXmlIncluded",
                "rawBinaryIncluded",
                "secretValuesIncluded",
                "secretAttributeCountRedacted",
            },
        )
    )
    snapshot["projectSha256"] = _raw_sha256(
        snapshot["projectSha256"],
        "project structure snapshot project digest",
    )
    snapshot["indexDigest"] = _raw_sha256(
        snapshot["indexDigest"],
        "project structure snapshot index digest",
    )
    snapshot["xmlSha256"] = _raw_sha256(
        snapshot["xmlSha256"],
        "project structure snapshot XML digest",
    )
    if snapshot["sourceKind"] != "xgwx_project_xml" or snapshot["sourceObserved"] is not True:
        raise ValueError("project structure snapshot source is invalid")
    for key, maximum in (
        ("projectSize", 256 * 1024 * 1024),
        ("xmlSize", 32 * 1024 * 1024),
        ("gzipOffset", 4_095),
        ("gzipMemberSize", 256 * 1024 * 1024),
        ("trailingSize", 256 * 1024 * 1024),
        ("secretAttributeCountRedacted", 100_000_000),
    ):
        snapshot[key] = _integer(snapshot[key], f"project structure snapshot {key}", maximum=maximum)
    if snapshot["projectSize"] <= 0 or snapshot["xmlSize"] <= 0 or snapshot["gzipMemberSize"] <= 18:
        raise ValueError("project structure snapshot sizes are invalid")
    if (
        snapshot["gzipOffset"]
        + snapshot["gzipMemberSize"]
        + snapshot["trailingSize"]
        != snapshot["projectSize"]
    ):
        raise ValueError("project structure snapshot container sizes are inconsistent")
    for key in ("rawXmlIncluded", "rawBinaryIncluded", "secretValuesIncluded"):
        if snapshot[key] is not False:
            raise ValueError("project structure snapshot exposes forbidden content")
    return snapshot


def validate_structural_evidence_contract(
    value: Any,
    *,
    expected_tool: str | None = None,
) -> dict[str, Any]:
    """Validate a durable receipt without trusting its stored derived fields."""

    contract = dict(_mapping(value, "structural evidence receipt"))
    required = {
        "schema",
        "toolName",
        "scope",
        "coverage",
        "receiptIndexTruncated",
        "receiptIndexLimitations",
        "locationFactCount",
        "locationFactReturnedCount",
        "locationFactsTruncated",
        "locationFactsDigest",
        "locationFacts",
        "relationFactCount",
        "relationFactReturnedCount",
        "relationFactsTruncated",
        "relationFactsDigest",
        "relationFacts",
        "receiptDigest",
    }
    if contract.get("schema") != STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA:
        raise ValueError("structural evidence receipt schema is invalid")
    tool_name = _text(contract.get("toolName"), "structural receipt tool")
    if tool_name not in STRUCTURAL_ANALYSIS_TOOL_COVERAGE or (
        expected_tool is not None and tool_name != expected_tool
    ):
        raise ValueError("structural evidence receipt tool is invalid")
    if tool_name == "project.structure_index":
        required.update(
            {
                "projectSnapshot",
                "structureFactCount",
                "structureFactReturnedCount",
                "structureFactsTruncated",
                "structureFactsDigest",
                "structureFacts",
            }
        )
    if set(contract) != required:
        raise ValueError("structural evidence receipt fields are invalid")
    supplied_digest = _text(contract.get("receiptDigest"), "receipt digest", maximum=64)
    unsigned = dict(contract)
    unsigned.pop("receiptDigest")
    if supplied_digest != canonical_digest(unsigned):
        raise ValueError("structural evidence receipt digest is invalid")
    scope_value = dict(_mapping(contract["scope"], "structural receipt scope"))
    scope_required = {
        "scopeKey",
        "logicalScopeKey",
        "query",
        "coverageUnitKeys",
        "countsAdditive",
        "payloadCount",
        "payloadSetDigest",
        "payloadBindings",
        "payloadBindingsTruncated",
        "cacheReused",
        "sequentialCapture",
    }
    if set(scope_value) != scope_required:
        raise ValueError("structural receipt scope fields are invalid")
    query = dict(_mapping(scope_value["query"], "structural receipt query"))
    unit_keys = sorted(
        _string_list(scope_value["coverageUnitKeys"], "coverage unit keys")
    )
    if len(unit_keys) != len(set(unit_keys)):
        raise ValueError("structural receipt coverage unit keys are not unique")
    payload_set_digest = _text(
        scope_value["payloadSetDigest"],
        "payload-set digest",
        maximum=64,
    ).casefold()
    if re.fullmatch(r"[0-9a-f]{64}", payload_set_digest) is None:
        raise ValueError("structural receipt payload-set digest is invalid")
    payload_bindings = _items(
        scope_value["payloadBindings"],
        "structural receipt payload bindings",
    )
    canonical_bindings: list[dict[str, Any]] = []
    for binding_value in payload_bindings:
        binding = _mapping(binding_value, "structural receipt payload binding")
        if set(binding) != {
            "programId",
            "program",
            "payloadSha256",
            "stableRepeat",
            "cacheHit",
        }:
            raise ValueError("structural receipt payload binding fields are invalid")
        payload_digest = _text(
            binding["payloadSha256"],
            "structural receipt payload digest",
            maximum=64,
        ).casefold()
        if re.fullmatch(r"[0-9a-f]{64}", payload_digest) is None:
            raise ValueError("structural receipt payload digest is invalid")
        canonical_bindings.append(
            {
                "programId": _text(binding["programId"], "payload program id"),
                "program": _text(binding["program"], "payload program name"),
                "payloadSha256": payload_digest,
                "stableRepeat": _boolean(
                    binding["stableRepeat"],
                    "payload stable-repeat flag",
                ),
                "cacheHit": _boolean(binding["cacheHit"], "payload cache flag"),
            }
        )
    sorted_bindings = sorted(
        canonical_bindings,
        key=lambda item: (
            str(item["programId"]),
            str(item["program"]),
            str(item["payloadSha256"]),
        ),
    )
    if canonical_bindings != sorted_bindings or len(
        {str(item["programId"]) for item in canonical_bindings}
    ) != len(canonical_bindings):
        raise ValueError("structural receipt payload bindings are not canonical")
    if any(binding["stableRepeat"] is not True for binding in canonical_bindings):
        raise ValueError("structural receipt contains an unstable payload binding")
    payload_count = _integer(scope_value["payloadCount"], "scope payload count")
    payload_bindings_truncated = _boolean(
        scope_value["payloadBindingsTruncated"],
        "payload binding truncation",
    )
    if (
        payload_bindings_truncated != (payload_count > len(canonical_bindings))
        or len(canonical_bindings) != min(payload_count, _PAYLOAD_BINDING_LIMIT)
    ):
        raise ValueError("structural receipt payload binding counts are inconsistent")
    identity_bindings = [
        {
            "programId": item["programId"],
            "program": item["program"],
            "payloadSha256": item["payloadSha256"],
        }
        for item in canonical_bindings
    ]
    if not payload_bindings_truncated and payload_set_digest != canonical_digest(
        identity_bindings
    ):
        raise ValueError("structural receipt payload-set digest is invalid")
    cache_reused = _boolean(scope_value["cacheReused"], "scope cache flag")
    sequential_capture = _boolean(
        scope_value["sequentialCapture"],
        "scope sequential-capture flag",
    )
    logical_material = {
        "toolName": tool_name,
        "query": query,
        "coverageUnitKeys": unit_keys,
    }
    scope_material = {
        **logical_material,
        "payloadSetDigest": payload_set_digest,
        "cacheReused": cache_reused,
        "sequentialCapture": sequential_capture,
        "payloadBindingsTruncated": payload_bindings_truncated,
    }
    if (
        scope_value["logicalScopeKey"] != canonical_digest(logical_material)
        or scope_value["scopeKey"] != canonical_digest(scope_material)
    ):
        raise ValueError("structural receipt scope digest is invalid")
    scope = {
        "scopeKey": str(scope_value["scopeKey"]),
        "logicalScopeKey": str(scope_value["logicalScopeKey"]),
        "query": query,
        "coverageUnitKeys": unit_keys,
        "countsAdditive": _boolean(
            scope_value["countsAdditive"],
            "scope count-additivity",
        ),
        "payloadCount": payload_count,
        "payloadSetDigest": payload_set_digest,
        "payloadBindings": canonical_bindings,
        "payloadBindingsTruncated": payload_bindings_truncated,
        "cacheReused": cache_reused,
        "sequentialCapture": sequential_capture,
    }
    if scope["sequentialCapture"] != (payload_count > 1):
        raise ValueError("structural receipt sequential-capture flag is inconsistent")
    retained_cache_reused = any(
        binding["cacheHit"] is True for binding in canonical_bindings
    )
    if retained_cache_reused and scope["cacheReused"] is not True:
        raise ValueError("structural receipt hides a retained cached payload")
    if not payload_bindings_truncated and scope["cacheReused"] != retained_cache_reused:
        raise ValueError("structural receipt cache-reuse flag is inconsistent")
    if scope != scope_value:
        raise ValueError("structural receipt scope is not canonical")
    locations = [_validate_location_fact(item) for item in _items(contract["locationFacts"], "location facts")]
    relations = [_validate_relation_fact(item) for item in _items(contract["relationFacts"], "relation facts")]
    location_count = _integer(contract["locationFactCount"], "location fact count")
    relation_count = _integer(contract["relationFactCount"], "relation fact count")
    if (
        _integer(contract["locationFactReturnedCount"], "returned location fact count") != len(locations)
        or _boolean(contract["locationFactsTruncated"], "location fact truncation")
        != (location_count > len(locations))
        or _integer(contract["relationFactReturnedCount"], "returned relation fact count") != len(relations)
        or _boolean(contract["relationFactsTruncated"], "relation fact truncation")
        != (relation_count > len(relations))
    ):
        raise ValueError("structural evidence receipt fact counts are inconsistent")
    if len({str(item["fingerprint"]) for item in locations}) != len(locations) or len(
        {str(item["fingerprint"]) for item in relations}
    ) != len(relations):
        raise ValueError("structural evidence receipt contains duplicate facts")
    location_digest = _text(
        contract["locationFactsDigest"],
        "location fact-set digest",
        maximum=64,
    ).casefold()
    relation_digest = _text(
        contract["relationFactsDigest"],
        "relation fact-set digest",
        maximum=64,
    ).casefold()
    if (
        re.fullmatch(r"[0-9a-f]{64}", location_digest) is None
        or re.fullmatch(r"[0-9a-f]{64}", relation_digest) is None
    ):
        raise ValueError("structural evidence fact-set digest is invalid")
    location_truncated = bool(contract["locationFactsTruncated"])
    relation_truncated = bool(contract["relationFactsTruncated"])
    if not location_truncated and location_digest != canonical_digest(locations):
        raise ValueError("complete structural location fact digest is invalid")
    if not relation_truncated and relation_digest != canonical_digest(relations):
        raise ValueError("complete structural relation fact digest is invalid")
    structure_facts: list[dict[str, Any]] = []
    structure_truncated = False
    project_snapshot: dict[str, Any] | None = None
    if tool_name == "project.structure_index":
        if locations or relations or location_count or relation_count:
            raise ValueError("project structure receipt contains unrelated location facts")
        project_snapshot = _validate_project_snapshot(contract["projectSnapshot"])
        structure_facts = [
            _validate_structure_fact(item)
            for item in _items(contract["structureFacts"], "project structure facts")
        ]
        structure_count = _integer(
            contract["structureFactCount"],
            "project structure fact count",
        )
        returned_structure_count = _integer(
            contract["structureFactReturnedCount"],
            "returned project structure fact count",
        )
        structure_truncated = _boolean(
            contract["structureFactsTruncated"],
            "project structure fact truncation",
        )
        if (
            returned_structure_count != len(structure_facts)
            or len(structure_facts) > _FACT_COUNT_LIMIT
            or structure_truncated != (structure_count > len(structure_facts))
            or len({str(item["fingerprint"]) for item in structure_facts})
            != len(structure_facts)
        ):
            raise ValueError("project structure receipt fact counts are inconsistent")
        structure_digest = _text(
            contract["structureFactsDigest"],
            "project structure fact-set digest",
            maximum=64,
        ).casefold()
        if re.fullmatch(r"[0-9a-f]{64}", structure_digest) is None or (
            not structure_truncated
            and structure_digest != canonical_digest(structure_facts)
        ):
            raise ValueError("project structure fact-set digest is invalid")
        expected_units = sorted(
            f"structure:{name}" for name in _PROJECT_STRUCTURE_SECTION_NAMES
        )
        if (
            scope["query"]
            != {
                "offset": _integer(scope["query"].get("offset"), "project structure scope offset"),
                "limit": _integer(scope["query"].get("limit"), "project structure scope limit", maximum=2_000),
            }
            or scope["query"]["limit"] < 1
            or scope["coverageUnitKeys"] != expected_units
            or scope["countsAdditive"] is not False
            or scope["payloadCount"] != 1
            or scope["payloadBindingsTruncated"] is not False
            or scope["cacheReused"] is not False
            or scope["sequentialCapture"] is not False
            or len(scope["payloadBindings"]) != 1
            or scope["payloadBindings"][0]["payloadSha256"]
            != project_snapshot["projectSha256"]
            or scope["payloadBindings"][0]["programId"]
            != f"xg5000:project:{project_snapshot['projectSha256']}"
        ):
            raise ValueError("project structure scope does not bind its snapshot")
    receipt_index_truncated = _boolean(
        contract["receiptIndexTruncated"],
        "receipt index truncation",
    )
    receipt_index_limitations = _string_list(
        contract["receiptIndexLimitations"],
        "receipt index limitations",
    )
    expected_index_limitations = (
        _stable_union(
            [_RECEIPT_BOUND_LIMITATION],
            (
                [_PROJECT_STRUCTURE_FACT_BOUND_LIMITATION]
                if structure_truncated
                else []
            ),
        )
        if location_truncated or relation_truncated or structure_truncated
        else []
    )
    if receipt_index_truncated != (
        location_truncated or relation_truncated or structure_truncated
    ) or receipt_index_limitations != expected_index_limitations:
        raise ValueError("structural receipt index truncation is inconsistent")
    location_fingerprints = {str(item["fingerprint"]) for item in locations}
    if any(
        str(location["fingerprint"]) not in location_fingerprints
        for relation in relations
        for location in relation["locations"]
    ):
        raise ValueError("structural relation refers to a location outside its receipt")
    coverage_value = _mapping(contract["coverage"], "structural receipt coverage")
    allowed_coverage = {
        "area",
        "status",
        "observedCount",
        "totalCount",
        "truncated",
        "limitations",
    }
    if set(coverage_value) - allowed_coverage:
        raise ValueError("structural receipt coverage fields are invalid")
    coverage = _coverage(
        area=_text(coverage_value.get("area"), "coverage area", maximum=50),
        status=_text(coverage_value.get("status"), "coverage status", maximum=50),
        observed_count=_integer(coverage_value.get("observedCount"), "coverage observed count"),
        total_count=(
            _integer(coverage_value["totalCount"], "coverage total count")
            if "totalCount" in coverage_value
            else None
        ),
        truncated=_boolean(coverage_value.get("truncated"), "coverage truncation"),
        limitations=_string_list(coverage_value.get("limitations"), "coverage limitations"),
    )
    if coverage["area"] != STRUCTURAL_ANALYSIS_TOOL_COVERAGE[tool_name]:
        raise ValueError("structural receipt coverage area disagrees with its tool")
    required_capture_limitations = {
        limitation
        for active, limitation in (
            (scope["cacheReused"], _CACHE_REUSE_LIMITATION),
            (scope["sequentialCapture"], _SEQUENTIAL_CAPTURE_LIMITATION),
            (scope["payloadBindingsTruncated"], _PAYLOAD_BINDING_BOUND_LIMITATION),
        )
        if active
    }
    if not required_capture_limitations.issubset(set(coverage["limitations"])) or (
        required_capture_limitations
        and coverage["status"] not in {"partial", "not_observed"}
    ):
        raise ValueError("structural receipt coverage hides capture limitations")
    if tool_name == "project.structure_index" and (
        coverage["status"] != "partial"
        or _PROJECT_STRUCTURE_STATIC_LIMITATION not in coverage["limitations"]
        or structure_truncated
        and (
            coverage["truncated"] is not True
            or _PROJECT_STRUCTURE_FACT_BOUND_LIMITATION
            not in coverage["limitations"]
        )
    ):
        raise ValueError("project structure coverage overstates its static evidence")
    if coverage != dict(coverage_value):
        raise ValueError("structural receipt coverage is not canonical")
    validated_contract = {
        **contract,
        "scope": scope,
        "coverage": coverage,
        "locationFacts": locations,
        "relationFacts": relations,
    }
    if tool_name == "project.structure_index":
        validated_contract["projectSnapshot"] = project_snapshot
        validated_contract["structureFacts"] = structure_facts
    return validated_contract


def location_membership_value(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact fact portion used for final artifact membership checks."""

    return _fact_without_fingerprint(_validate_location_fact(value))


def relation_membership_value(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact relation, retaining each relation-owned nested location."""

    validated = _validate_relation_fact(value)
    return {
        **_fact_without_fingerprint(validated),
        "locations": [
            _fact_without_fingerprint(location) for location in validated["locations"]
        ],
    }


def structure_membership_value(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return one exact project-structure fact without its receipt fingerprint."""

    return _fact_without_fingerprint(_validate_structure_fact(value))


def structural_observation_statement(
    fact_kind: str,
    value: Mapping[str, Any],
) -> str:
    """Return the only user-visible statement allowed for one receipt fact."""

    if fact_kind == "location":
        selected = location_membership_value(value)
    elif fact_kind == "relation":
        relation = relation_membership_value(value)
        selected = {
            key: relation[key]
            for key in (
                "relationKind",
                "source",
                "target",
                "direct",
                "semanticStatus",
            )
        }
    elif fact_kind == "project_structure":
        fact = structure_membership_value(value)
        selected = {
            key: fact[key]
            for key in (
                "kind",
                "entityId",
                "name",
                "parentId",
                "order",
                "status",
            )
            if key in fact
        }
    else:
        raise ValueError("structural observation fact kind is invalid")
    return json.dumps(
        {"factKind": fact_kind, "fact": selected},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "AMBIGUOUS_STRUCTURAL_SCOPE_LIMITATION",
    "STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA",
    "STRUCTURAL_ANALYSIS_TOOL_COVERAGE",
    "build_structural_evidence_contract",
    "canonical_digest",
    "location_membership_value",
    "project_search_context",
    "relation_membership_value",
    "structure_membership_value",
    "state_model_context",
    "structural_receipt_tool_name",
    "structural_observation_statement",
    "validate_structural_evidence_contract",
]
