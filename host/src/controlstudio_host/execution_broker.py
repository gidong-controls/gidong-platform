"""Platform-owned execution boundary for Agent Protocol requests.

The broker exposes a serializable catalog only.  Pydantic AI toolsets and raw
MCP clients are intentionally private to neither the Host nor the Agent
protocol: the Host maps each proposal to its existing MCP gateway after it has
bound the active run and target.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any, Protocol

from controlstudio_contracts import (
    AgentToolDefinitionV1,
    ExternalToolRequestV1,
    ExternalToolResultV1,
)

from .agent_protocol import scrub_agent_tool_result
from .api_models import (
    LocalWorkspaceCompareV1,
    LSControllerObservationV1,
    LSControllerReadV1,
    LSControllerTraceV1,
    MneInstructionBatchPatchCandidateV5,
    MneInstructionPatchCandidateV4,
    MneRungPatchCandidateV6,
    MneSeriesGuardCandidateV3,
    is_hardware_free_m_operand_target,
)
from .mcp_client import (
    AGENT_CONTROLLER_MCP_TOOLS,
    AGENT_TOOL_NAMES,
    AGENT_TOOL_REQUIRED_IDENTITY_FIELDS,
    CONTROLLER_ENDPOINT_FIELDS,
    SESSION_IDENTITY_FIELDS,
    WORKSPACE_STATIC_IDENTITY_FIELDS,
    MCPGateway,
    MCPStartupError,
    MCPToolError,
    MCPToolRejectedError,
    MCPTransportError,
)
from .structural_evidence import (
    STRUCTURAL_ANALYSIS_TOOL_COVERAGE,
    build_structural_evidence_contract,
    project_search_context,
)

logger = logging.getLogger(__name__)

ToolEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
ToolCompletionGate = Callable[[dict[str, Any], Any], Awaitable[None]]
ControllerPreflight = Callable[[str, str], Awaitable[bool]]

_WORKSPACE_COMPARE_FIELDS = {
    "schema",
    "ok",
    "verified",
    "workspace_id",
    "revision_id",
    "source_kind",
    "source",
    "working_copy",
    "file_identical",
    "status",
    "structure",
    "limitations",
    "compare_digest",
}
_WORKSPACE_HARDWARE_CONTEXT_FIELDS = {
    "schema",
    "workspace_id",
    "revision_id",
    "source_kind",
    "source_sha256",
    "working_sha256",
    "hardware_context",
    "receipt_digest",
}
_WORKSPACE_STRUCTURE_INDEX_FIELDS = {
    "schema",
    "workspace_binding",
    "structure_index",
    "receipt_digest",
}
_WORKSPACE_SEARCH_FIELDS = {
    "schema",
    "workspace_binding",
    "project_search",
    "receipt_digest",
}
_WORKSPACE_STATIC_BINDING_FIELDS = {
    "workspace_id",
    "revision_id",
    "source_kind",
    "provenance_receipt_digest",
    "source_sha256",
    "working_path",
    "working_sha256",
}
_WORKSPACE_STATIC_TOOLS = frozenset(
    {
        "workspace.compare",
        "workspace.hardware_context",
        "workspace.structure_index",
        "workspace.search",
    }
)
_PROJECT_STRUCTURE_INDEX_ENVELOPE_FIELDS = {
    "session_id",
    "operation",
    "ok",
    "verified",
    "structure_index",
    "index_digest",
    "receipt_path",
    "project_hash_before",
    "project_hash_after",
    "project_unchanged",
    "operation_id",
}
_PROGRAM_ID = re.compile(r"^ld:[0-9a-f]{16}$")
_SAFE_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,255}$")
_HARDWARE_MODEL = re.compile(
    r"^(?:(?:XBC|XBM|XEC|XMC|XEM|XBE|XBF|XGF|XGL)-[A-Z0-9/+.\-]+|"
    r"(?:XGK|XGI|XGR)-CPU[A-Z0-9/+.\-]*)$",
    re.IGNORECASE,
)
_CONTROLLER_VARIABLE = re.compile(r"^%[A-Za-z][XxBbWwDdLl][A-Za-z0-9.]{0,13}$")
_CONTROLLER_DATA_TYPES = frozenset({"BIT", "BYTE", "WORD", "DWORD", "LWORD"})
_CONTROLLER_SCHEMA_VERSION = "1.0"
_CONTROLLER_EVIDENCE_STRENGTH = (
    "protocol_status_without_serial_or_project_digest"
)
_CONTROLLER_WRITE_AUTHORIZATION_REASON = (
    "FEnet status does not prove a controller serial number or the online "
    "project digest; this observation cannot authorize writes."
)
_CONTROLLER_TRACE_LIMITATIONS = (
    "provisional_identity_without_serial_or_project_digest",
    "samples_are_sequential_fenet_reads_not_synchronous_controller_scan_data",
)
_CONTROLLER_DATA_TYPE_WIDTHS = {
    "BIT": 1,
    "BYTE": 1,
    "WORD": 2,
    "DWORD": 4,
    "LWORD": 8,
}
_CONTROLLER_STATUS_FLAGS = (
    (0, "RUN"),
    (1, "STOP"),
    (2, "ERROR"),
    (3, "DEBUG"),
    (4, "LOCAL_CONTROL"),
    (5, "MODBUS"),
    (6, "REMOTE"),
    (8, "RUN_EDIT_STANDBY"),
    (9, "RUN_EDIT_PROCESSING"),
    (10, "RUN_EDIT_DONE"),
    (11, "RUN_EDIT_END"),
    (16, "FORCE_INPUT"),
    (17, "FORCE_OUTPUT"),
    (18, "IO_SKIP"),
    (19, "ERROR_MASK"),
)
_CONTROLLER_CPU_FAMILIES = {
    0xA0: "XGK",
    0xA4: "XGI",
    0xA8: "XGR",
    0xB0: "XGB_MK",
    0xB4: "XGB_IEC",
}
_HARDWARE_STATUS_VALUES = frozenset(
    {"conflict", "documented", "inactive", "observed", "partial", "unobserved"}
)
_HARDWARE_DIRECTION_VALUES = frozenset({"input", "output", "unknown"})
_HARDWARE_BINDING_KINDS = frozenset(
    {"built_in_terminal_range", "slot_reservation"}
)
_HARDWARE_POINT_KINDS = frozenset({"digital_input", "digital_output"})
_HARDWARE_CHANNEL_KINDS = frozenset(
    {"analog_input", "analog_output", "digital_input", "digital_output"}
)
_AGENT_HARDWARE_TIMER_LIMIT = 16
_AGENT_HARDWARE_MODULE_LIMIT = 256
_AGENT_HARDWARE_BINDING_LIMIT = 64
_AGENT_HARDWARE_CHANNEL_LIMIT = 16
_AGENT_BLOCKER_LIMIT = 128
_AGENT_SNAPSHOT_PROGRAM_LIMIT = 128
_MNE_FIELD_IO_BINDING_LIMIT = 64
_MNE_SIMULATION_BIT_LIMIT = 32
_HARDWARE_CONTEXT_BINDING_PRECONDITION = (
    "project.hardware_context must be called before binding its digest"
)
_FIELD_IO_CHANNEL_DERIVATION = (
    "address offset within the exact configured module range"
)
_FIELD_IO_PROPOSAL_REASON = (
    "project configuration does not prove controller identity, field wiring, "
    "interlocks, or runtime state"
)
_SESSION_STATUS_FIELDS = {
    "schema_version",
    "session_id",
    "pid",
    "hwnd",
    "stage",
    "project_path",
    "project_hash",
    "target",
    "plc_policy",
    "created_at",
    "updated_at",
    "events",
    "metadata",
    "capabilities",
    "authorization",
    "valid_stages",
    "include_events",
}
_SESSION_STATUS_EVENT_FIELDS = {
    "ts",
    "operation",
    "stage_before",
    "stage_after",
    "pid",
    "hwnd",
    "project_path",
    "project_hash",
    "input",
    "observed",
    "success",
    "reason",
    "artifact_dir",
    "screenshot_path",
    "json_path",
}
_SESSION_STATUS_EVENT_LIMIT = 4_096
_SESSION_STAGES = (
    "DETACHED",
    "ATTACHED",
    "PROJECT_OPEN",
    "EDITOR_IL",
    "IL_READBACK_OK",
    "LD_OK",
    "CHECK_OK",
    "SAVED_OK",
    "SAVED_REOPENED_UNVERIFIED",
    "SIM_READY",
    "PROGRAM_WRITTEN",
    "RUN",
    "RUNTIME_PROVED",
    "RESTORED",
    "STOPPED_UNVERIFIED",
)
_PRIVATE_HARDWARE_FACT_FIELDS = frozenset(
    {
        "artifact_dir",
        "catalog_path",
        "error",
        "executable",
        "hwnd",
        "json_path",
        "message",
        "path",
        "payload_path",
        "pid",
        "project_path",
        "receipt_path",
        "screenshot_path",
        "session_id",
    }
)
_SESSION_TARGETS = frozenset({"ENGINEERING_IDE", "LOCAL_VALIDATION"})
_SESSION_PLC_POLICIES = frozenset(
    {
        "read_only_unverified",
        "read_only_project_identity_verified",
        "read_only_controlled_launch",
        "unsafe_local_validation_enabled",
        "read_only_terminal_change_rebind",
        "offline_abort_recovery_only",
        "offline_rollback_recovery_only",
        "offline_change_authorized",
    }
)
_SESSION_ACCESS_PROFILE_GRANTS = {
    "analysis": ("read", "analyze"),
    "simulation_operator": ("read", "analyze", "simulate"),
    "offline_engineering": ("read", "analyze", "offline_mutate", "simulate"),
}
_SESSION_UNSUPPORTED_CAPABILITIES = ("direct_control", "online_modify")
_SESSION_CAPABILITY_NAMES = frozenset({"analysis", "mutation", "simulation"})
_SESSION_CAPABILITY_STATES = frozenset(
    {"unavailable", "idle", "ready", "running", "error"}
)
_SESSION_IDENTITY_ASSURANCE = frozenset(
    {"unverified", "strong_readback", "controlled_launch_read_only"}
)


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _is_canonical_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _bounded_text(
    value: Any,
    label: str,
    *,
    maximum: int,
    nullable: bool = False,
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise MCPToolError(f"{label} is invalid")
    return value


def _bounded_integer(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 2**31 - 1,
    nullable: bool = False,
) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MCPToolError(f"{label} is invalid")
    return value


def _bounded_number(
    value: Any,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not minimum <= float(value) <= maximum
    ):
        raise MCPToolError(f"{label} is invalid")
    return value


def _bounded_code_list(
    value: Any,
    label: str,
    *,
    maximum_items: int = _AGENT_BLOCKER_LIMIT,
) -> list[str]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) > maximum_items
        or any(
            not isinstance(item, str)
            or _SAFE_CODE.fullmatch(item) is None
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise MCPToolError(f"{label} is invalid")
    return list(value)


def _validate_schema_only_request(
    value: Mapping[str, Any],
    *,
    label: str,
) -> None:
    if (
        set(value) - {"schema_version"}
        or value.get("schema_version", "1.0") != "1.0"
    ):
        raise MCPToolError(f"{label} request shape is invalid")


def _project_program_identity(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise MCPToolError(f"{label} is invalid")
    program_id = _bounded_text(
        value.get("program_id"),
        f"{label} id",
        maximum=512,
    )
    name = _bounded_text(value.get("name"), f"{label} name", maximum=512)
    if _PROGRAM_ID.fullmatch(program_id or "") is None or value.get("language") != "LD":
        raise MCPToolError(f"{label} is invalid")
    assert isinstance(program_id, str) and isinstance(name, str)
    return {"program_id": program_id, "name": name, "language": "LD"}


def _ld_text(value: Any, label: str, *, nullable: bool = False) -> str | None:
    return _bounded_text(value, label, maximum=512, nullable=nullable)


def _ld_string_list(value: Any, label: str, *, maximum_items: int) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > maximum_items
        or any(not isinstance(item, str) or len(item) > 512 for item in value)
    ):
        raise MCPToolError(f"{label} is invalid")
    return list(value)


def _ld_contact_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "operand", "column"}:
        raise MCPToolError("LD contact is invalid")
    return {
        "kind": _ld_text(value.get("kind"), "LD contact kind"),
        "operand": _ld_text(value.get("operand"), "LD contact operand"),
        "column": _bounded_integer(
            value.get("column"),
            "LD contact column",
            maximum=4_095,
        ),
    }


def _ld_instruction_projection(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "kind",
        "operand",
        "anchor",
        "target_coordinate",
    }:
        raise MCPToolError("LD instruction is invalid")
    return {
        "kind": _ld_text(value.get("kind"), "LD instruction kind"),
        "operand": _ld_text(
            value.get("operand"),
            "LD instruction operand",
            nullable=True,
        ),
        "anchor": _bounded_integer(
            value.get("anchor"),
            "LD instruction anchor",
            maximum=2**31 - 1,
        ),
        "target_coordinate": _bounded_integer(
            value.get("target_coordinate"),
            "LD instruction target coordinate",
            maximum=2**31 - 1,
        ),
    }


def _ld_record_projection(value: Any) -> dict[str, Any]:
    required = {
        "opcode",
        "coordinate",
        "value",
        "kind",
        "visual_row",
        "column",
        "token_offset",
        "value_token_offset",
        "raw_tokens",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise MCPToolError("LD record is invalid")
    return {
        "opcode": _ld_text(value.get("opcode"), "LD record opcode"),
        "coordinate": _bounded_integer(
            value.get("coordinate"),
            "LD record coordinate",
            maximum=2**31 - 1,
            nullable=True,
        ),
        "value": _ld_text(value.get("value"), "LD record value"),
        "kind": _ld_text(value.get("kind"), "LD record kind"),
        "visual_row": _bounded_integer(
            value.get("visual_row"),
            "LD record row",
            maximum=10_000,
        ),
        "column": _bounded_integer(
            value.get("column"),
            "LD record column",
            maximum=4_095,
            nullable=True,
        ),
        "token_offset": _bounded_integer(
            value.get("token_offset"),
            "LD record token offset",
            maximum=64 * 1024 * 1024,
        ),
        "value_token_offset": _bounded_integer(
            value.get("value_token_offset"),
            "LD record value token offset",
            maximum=64 * 1024 * 1024,
        ),
        "raw_tokens": _ld_string_list(
            value.get("raw_tokens"),
            "LD record tokens",
            maximum_items=128,
        ),
    }


def _ld_decoded_projection(value: Any, *, expected_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "name",
        "rows",
        "topology",
        "semantic_rungs",
    }:
        raise MCPToolError("decoded LD program is invalid")
    name = _ld_text(value.get("name"), "decoded LD program name")
    rows = value.get("rows")
    if name != expected_name or not isinstance(rows, list) or len(rows) > 10_000:
        raise MCPToolError("decoded LD program is invalid")
    projected_rows: list[dict[str, Any]] = []
    seen_visual_rows: set[int] = set()
    for row in rows:
        required = {
            "visual_row",
            "contacts",
            "comparisons",
            "instruction",
            "raw_tokens",
            "records",
            "unparsed_tokens",
        }
        if not isinstance(row, Mapping) or set(row) != required:
            raise MCPToolError("decoded LD row is invalid")
        visual_row = _bounded_integer(
            row.get("visual_row"),
            "decoded LD visual row",
            maximum=10_000,
        )
        contacts = row.get("contacts")
        comparisons = row.get("comparisons")
        records = row.get("records")
        if (
            not isinstance(visual_row, int)
            or visual_row in seen_visual_rows
            or not isinstance(contacts, list)
            or len(contacts) > 128
            or not isinstance(comparisons, list)
            or len(comparisons) > 128
            or not isinstance(records, list)
            or len(records) > 512
        ):
            raise MCPToolError("decoded LD row is invalid")
        seen_visual_rows.add(visual_row)
        projected_rows.append(
            {
                "visual_row": visual_row,
                "contacts": [_ld_contact_projection(item) for item in contacts],
                "comparisons": [
                    _ld_contact_projection(item) for item in comparisons
                ],
                "instruction": _ld_instruction_projection(row.get("instruction")),
                "raw_tokens": _ld_string_list(
                    row.get("raw_tokens"),
                    "decoded LD row tokens",
                    maximum_items=512,
                ),
                "records": [_ld_record_projection(item) for item in records],
                "unparsed_tokens": _ld_string_list(
                    row.get("unparsed_tokens"),
                    "decoded LD unparsed tokens",
                    maximum_items=512,
                ),
            }
        )
    return {"name": name, "rows": projected_rows}


def _hardware_device(value: Any, label: str) -> str:
    text = _bounded_text(value, label, maximum=32)
    if not isinstance(text, str) or re.fullmatch(r"^[A-Z][A-Z0-9.]{0,31}$", text) is None:
        raise MCPToolError(f"{label} is invalid")
    return text


def _hardware_cpu_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("status") not in {
        "conflict",
        "observed",
        "partial",
    }:
        raise MCPToolError("hardware context CPU facts are invalid")
    projection: dict[str, Any] = {"status": value["status"]}
    if "model" in value:
        model = _bounded_text(
            value.get("model"),
            "hardware context CPU model",
            maximum=128,
            nullable=True,
        )
        if model is not None and _HARDWARE_MODEL.fullmatch(model) is None:
            raise MCPToolError("hardware context CPU model is invalid")
        projection["model"] = model
    candidates = value.get("observed_candidates")
    if candidates is not None:
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 8:
            raise MCPToolError("hardware context CPU candidates are invalid")
        projected_candidates: list[dict[str, str]] = []
        for item in candidates:
            if not isinstance(item, Mapping):
                raise MCPToolError("hardware context CPU candidates are invalid")
            model = _bounded_text(
                item.get("model"),
                "hardware context CPU candidate",
                maximum=128,
            )
            if not isinstance(model, str) or _HARDWARE_MODEL.fullmatch(model) is None:
                raise MCPToolError("hardware context CPU candidate is invalid")
            projected_candidates.append({"model": model})
        projection["observedCandidates"] = projected_candidates
    return projection


def _hardware_timer_projection(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > _AGENT_HARDWARE_TIMER_LIMIT:
        raise MCPToolError("hardware context timer areas are invalid")
    projected: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("status") not in {
            "inactive",
            "observed",
        }:
            raise MCPToolError("hardware context timer area is invalid")
        start_index = _bounded_integer(
            item.get("start_index"),
            "hardware context timer start",
            maximum=1_000_000,
        )
        end_index = _bounded_integer(
            item.get("end_index"),
            "hardware context timer end",
            maximum=1_000_000,
        )
        if not isinstance(start_index, int) or not isinstance(end_index, int) or end_index < start_index:
            raise MCPToolError("hardware context timer range is invalid")
        projected.append(
            {
                "deviceStart": _hardware_device(
                    item.get("device_start"),
                    "hardware context timer start device",
                ),
                "deviceEnd": _hardware_device(
                    item.get("device_end"),
                    "hardware context timer end device",
                ),
                "startIndex": start_index,
                "endIndex": end_index,
                "timeBaseMs": _bounded_number(
                    item.get("time_base_ms"),
                    "hardware context timer base",
                    minimum=0.001,
                    maximum=1_000_000,
                ),
                "status": item["status"],
            }
        )
    return projected


def _hardware_binding_projection(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or value.get("direction") not in _HARDWARE_DIRECTION_VALUES
        or value.get("status") not in {"documented", "observed"}
    ):
        raise MCPToolError("hardware context device binding is invalid")
    projection: dict[str, Any] = {
        "direction": value["direction"],
        "addressStart": _hardware_device(
            value.get("address_start"),
            "hardware context binding start",
        ),
        "addressEnd": _hardware_device(
            value.get("address_end"),
            "hardware context binding end",
        ),
        "status": value["status"],
    }
    binding_kind = value.get("binding_kind")
    if binding_kind is not None:
        if binding_kind not in _HARDWARE_BINDING_KINDS:
            raise MCPToolError("hardware context binding kind is invalid")
        projection["bindingKind"] = binding_kind
    point_kind = value.get("point_kind")
    if point_kind is not None:
        if point_kind not in _HARDWARE_POINT_KINDS:
            raise MCPToolError("hardware context binding point kind is invalid")
        projection["pointKind"] = point_kind
    for source, target in (
        ("channel_start", "channelStart"),
        ("channel_end", "channelEnd"),
    ):
        if source in value:
            projection[target] = _bounded_integer(
                value.get(source),
                "hardware context binding channel",
                maximum=65_535,
            )
    if ("channelStart" in projection) != ("channelEnd" in projection) or (
        "channelStart" in projection
        and projection["channelEnd"] < projection["channelStart"]
    ):
        raise MCPToolError("hardware context binding channel range is invalid")
    return projection


def _hardware_module_projection(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > _AGENT_HARDWARE_MODULE_LIMIT:
        raise MCPToolError("hardware context I/O modules are invalid")
    projected: list[dict[str, Any]] = []
    for item in value:
        bindings = item.get("device_bindings") if isinstance(item, Mapping) else None
        if (
            not isinstance(item, Mapping)
            or item.get("status") != "observed"
            or item.get("device_binding_status")
            not in {"documented", "observed", "unobserved"}
            or not isinstance(bindings, list)
            or len(bindings) > _AGENT_HARDWARE_BINDING_LIMIT
        ):
            raise MCPToolError("hardware context I/O module is invalid")
        model = _bounded_text(
            item.get("model"),
            "hardware context module model",
            maximum=128,
            nullable=True,
        )
        if model is not None and _HARDWARE_MODEL.fullmatch(model) is None:
            raise MCPToolError("hardware context module model is invalid")
        module: dict[str, Any] = {
            "base": _bounded_integer(
                item.get("base"),
                "hardware context module base",
                maximum=255,
            ),
            "slot": _bounded_integer(
                item.get("slot"),
                "hardware context module slot",
                maximum=255,
            ),
            "moduleId": _bounded_integer(
                item.get("module_id"),
                "hardware context module id",
                maximum=65_535,
            ),
            "subtype": _bounded_integer(
                item.get("subtype"),
                "hardware context module subtype",
                maximum=65_535,
                nullable=True,
            ),
            "model": model,
            "status": "observed",
            "deviceBindingStatus": item["device_binding_status"],
            "deviceBindings": [
                _hardware_binding_projection(binding) for binding in bindings
            ],
        }
        projected.append(module)
    return projected


def _hardware_io_mapping_projection(value: Any) -> dict[str, Any]:
    channels = value.get("channels") if isinstance(value, Mapping) else None
    if (
        not isinstance(value, Mapping)
        or value.get("status") not in {"observed", "unobserved"}
        or value.get("io_parameter_status") not in {"observed", "unobserved"}
        or value.get("module_configuration_status")
        not in {"observed", "unobserved"}
        or value.get("device_offset_status") not in {"observed", "unobserved"}
        or value.get("module_device_binding_status")
        not in {"documented", "observed", "unobserved"}
        or type(value.get("module_address_ranges_observed")) is not bool
        or type(value.get("module_address_ranges_documented")) is not bool
        or not isinstance(channels, list)
        or len(channels) > _AGENT_HARDWARE_CHANNEL_LIMIT
    ):
        raise MCPToolError("hardware context I/O mapping is invalid")
    projected_channels: list[dict[str, Any]] = []
    for channel in channels:
        if (
            not isinstance(channel, Mapping)
            or channel.get("kind") not in _HARDWARE_CHANNEL_KINDS
            or channel.get("status") != "observed"
        ):
            raise MCPToolError("hardware context I/O channel is invalid")
        prefix = _bounded_text(
            channel.get("device_prefix"),
            "hardware context channel prefix",
            maximum=4,
        )
        if not isinstance(prefix, str) or re.fullmatch(r"^[A-Z]{1,4}$", prefix) is None:
            raise MCPToolError("hardware context channel prefix is invalid")
        projected_channels.append(
            {
                "kind": channel["kind"],
                "devicePrefix": prefix,
                "deviceTypeCode": _bounded_integer(
                    channel.get("device_type_code"),
                    "hardware context channel device type",
                    maximum=255,
                ),
                "offset": _bounded_integer(
                    channel.get("offset"),
                    "hardware context channel offset",
                    maximum=1_000_000,
                ),
                "status": "observed",
            }
        )
    return {
        "status": value["status"],
        "ioParameterStatus": value["io_parameter_status"],
        "moduleConfigurationStatus": value["module_configuration_status"],
        "deviceOffsetStatus": value["device_offset_status"],
        "channels": projected_channels,
        "moduleAddressRangesObserved": value["module_address_ranges_observed"],
        "moduleAddressRangesDocumented": value[
            "module_address_ranges_documented"
        ],
        "moduleDeviceBindingStatus": value["module_device_binding_status"],
    }


def _controller_status_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "mode",
        "stateFlags",
        "systemStateRaw",
        "padtConnectionFlag",
        "configurationError",
        "configurationWarning",
    }:
        raise MCPToolError("controller status is invalid")
    system_state = _bounded_integer(
        value.get("systemStateRaw"),
        "controller system state",
        maximum=2**32 - 1,
    )
    padt_flag = _bounded_integer(
        value.get("padtConnectionFlag"),
        "controller PADT flag",
        maximum=65_535,
    )
    configuration_error = _bounded_integer(
        value.get("configurationError"),
        "controller configuration error",
        maximum=2**32 - 1,
    )
    configuration_warning = _bounded_integer(
        value.get("configurationWarning"),
        "controller configuration warning",
        maximum=2**32 - 1,
    )
    assert isinstance(system_state, int)
    expected_flags = [
        name for bit, name in _CONTROLLER_STATUS_FLAGS if system_state & (1 << bit)
    ]
    mode_flags = [
        name
        for bit, name in _CONTROLLER_STATUS_FLAGS[:4]
        if system_state & (1 << bit)
    ]
    expected_mode = (
        mode_flags[0]
        if len(mode_flags) == 1
        else "UNKNOWN"
        if not mode_flags
        else "AMBIGUOUS"
    )
    if value.get("mode") != expected_mode or value.get("stateFlags") != expected_flags:
        raise MCPToolError("controller status fields are inconsistent")
    return {
        "mode": expected_mode,
        "stateFlags": expected_flags,
        "systemStateRaw": system_state,
        "padtConnectionFlag": padt_flag,
        "configurationError": configuration_error,
        "configurationWarning": configuration_warning,
    }


def _controller_identity_projection(
    value: Any,
    *,
    endpoint_host: str,
    endpoint_port: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "vendor",
        "protocol",
        "cpuFamily",
        "cpuInfoCode",
        "cpuTypeFlag",
        "osVersion",
        "module",
        "fingerprintSha256",
        "evidenceStrength",
        "strongEnoughForWrite",
        "writeAuthorizationReason",
    }:
        raise MCPToolError("controller provisional identity is invalid")
    cpu_info = _bounded_integer(
        value.get("cpuInfoCode"),
        "controller CPU info code",
        maximum=255,
    )
    cpu_type = _bounded_integer(
        value.get("cpuTypeFlag"),
        "controller CPU type flag",
        maximum=65_535,
    )
    module = value.get("module")
    os_version = value.get("osVersion")
    if (
        not isinstance(cpu_info, int)
        or not isinstance(cpu_type, int)
        or not isinstance(module, Mapping)
        or set(module) != {"base", "slot"}
        or not isinstance(os_version, str)
        or re.fullmatch(r"[0-9A-F]{2}\.[0-9A-F]{2}", os_version) is None
    ):
        raise MCPToolError("controller provisional identity is invalid")
    module_base = _bounded_integer(
        module.get("base"),
        "controller module base",
        maximum=15,
    )
    module_slot = _bounded_integer(
        module.get("slot"),
        "controller module slot",
        maximum=15,
    )
    expected_family = _CONTROLLER_CPU_FAMILIES.get(cpu_info)
    os_version_raw = int(os_version.replace(".", ""), 16)
    fingerprint_material = "|".join(
        (
            endpoint_host,
            str(endpoint_port),
            f"{cpu_info:02X}",
            f"{cpu_type:04X}",
            f"{os_version_raw:04X}",
            str(module_base),
            str(module_slot),
        )
    )
    expected_fingerprint = hashlib.sha256(
        fingerprint_material.encode("utf-8")
    ).hexdigest()
    if (
        value.get("vendor") != "LS ELECTRIC"
        or value.get("protocol") != "XGT_FENET"
        or value.get("cpuFamily") != expected_family
        or value.get("fingerprintSha256") != expected_fingerprint
        or value.get("evidenceStrength")
        != "protocol_status_without_serial_or_project_digest"
        or value.get("strongEnoughForWrite") is not False
        or value.get("writeAuthorizationReason")
        != _CONTROLLER_WRITE_AUTHORIZATION_REASON
    ):
        raise MCPToolError("controller provisional identity is inconsistent")
    return {
        "vendor": "LS ELECTRIC",
        "protocol": "XGT_FENET",
        "cpuFamily": expected_family,
        "cpuInfoCode": cpu_info,
        "cpuTypeFlag": cpu_type,
        "osVersion": os_version,
        "module": {"base": module_base, "slot": module_slot},
        "fingerprintSha256": expected_fingerprint,
        "evidenceStrength": "protocol_status_without_serial_or_project_digest",
        "strongEnoughForWrite": False,
        "writeAuthorizationReason": _CONTROLLER_WRITE_AUTHORIZATION_REASON,
    }


def _controller_variable_projection(
    value: Any,
    *,
    data_type: str,
    expected_variable: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "variable",
        "rawHex",
        "unsignedValue",
    }:
        raise MCPToolError("controller value is invalid")
    variable = value.get("variable")
    raw_hex = value.get("rawHex")
    unsigned = value.get("unsignedValue")
    type_marker = {
        "BIT": "X",
        "BYTE": "B",
        "WORD": "W",
        "DWORD": "D",
        "LWORD": "L",
    }[data_type]
    if (
        not isinstance(variable, str)
        or variable != expected_variable
        or _CONTROLLER_VARIABLE.fullmatch(variable) is None
        or variable[2] != type_marker
        or not isinstance(raw_hex, str)
        or re.fullmatch(r"[0-9A-F]+", raw_hex) is None
        or len(raw_hex) != _CONTROLLER_DATA_TYPE_WIDTHS[data_type] * 2
        or isinstance(unsigned, bool)
        or not isinstance(unsigned, int)
        or unsigned != int.from_bytes(bytes.fromhex(raw_hex), "little")
    ):
        raise MCPToolError("controller value is inconsistent")
    return {
        "variable": variable,
        "rawHex": raw_hex,
        "unsignedValue": unsigned,
    }


def _expected_workspace_identity(
    value: Mapping[str, Any] | None,
) -> tuple[str, str]:
    workspace_id = value.get("workspace_id") if isinstance(value, Mapping) else None
    revision_id = value.get("revision_id") if isinstance(value, Mapping) else None
    if not isinstance(workspace_id, str) or not isinstance(revision_id, str):
        raise MCPToolError("workspace target is not bound")
    return workspace_id, revision_id


def _expected_workspace_static_identity(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MCPToolError("workspace target is not bound")
    identity = {field: value.get(field) for field in _WORKSPACE_STATIC_BINDING_FIELDS}
    if any(field not in value for field in _WORKSPACE_STATIC_BINDING_FIELDS):
        raise MCPToolError("workspace target lineage is not bound")
    _validated_workspace_static_binding(
        identity,
        expected_identity=identity,
        label="target",
    )
    return identity


def _validated_workspace_static_binding(
    value: Any,
    *,
    expected_identity: Mapping[str, Any],
    label: str,
) -> tuple[str, str, str, str, str, str | None]:
    if not isinstance(value, Mapping) or set(value) != _WORKSPACE_STATIC_BINDING_FIELDS:
        raise MCPToolError(f"workspace {label} binding is invalid")
    workspace_id = value.get("workspace_id")
    revision_id = value.get("revision_id")
    source_kind = value.get("source_kind")
    provenance_digest = value.get("provenance_receipt_digest")
    source_sha256 = value.get("source_sha256")
    working_path = value.get("working_path")
    working_sha256 = value.get("working_sha256")
    if (
        workspace_id != expected_identity.get("workspace_id")
        or revision_id != expected_identity.get("revision_id")
        or source_kind != expected_identity.get("source_kind")
        or provenance_digest
        != expected_identity.get("provenance_receipt_digest")
        or source_sha256 != expected_identity.get("source_sha256")
        or str(working_path).replace("/", "\\").casefold()
        != str(expected_identity.get("working_path") or "")
        .replace("/", "\\")
        .casefold()
        or working_sha256 != expected_identity.get("working_sha256")
        or source_kind not in {
            "local_file",
            "restored_from_controller",
            "new_build",
        }
        or (source_kind == "local_file") != (provenance_digest is None)
        or (
            provenance_digest is not None
            and not _is_sha256(provenance_digest)
        )
        or not isinstance(working_path, str)
        or not working_path.casefold().endswith(".xgwx")
        or not all(_is_sha256(item) for item in (source_sha256, working_sha256))
    ):
        raise MCPToolError(f"workspace {label} binding is invalid")
    return (
        workspace_id,
        revision_id,
        source_kind,
        working_path,
        working_sha256,
        provenance_digest,
    )


def _validate_workspace_status_lineage(
    value: Any,
    *,
    expected_identity: Mapping[str, Any],
) -> None:
    if not isinstance(value, Mapping):
        raise MCPToolError("workspace status lineage is invalid")
    source = value.get("source")
    working = value.get("working_copy")
    provenance = value.get("provenance")
    if (
        value.get("verified") is not True
        or not isinstance(source, Mapping)
        or not isinstance(working, Mapping)
        or (provenance is not None and not isinstance(provenance, Mapping))
    ):
        raise MCPToolError("workspace status lineage is invalid")
    source_kind = value.get("source_kind")
    provenance_digest = None
    if isinstance(provenance, Mapping):
        provenance_digest = provenance.get(
            "receiptDigest" if source_kind == "restored_from_controller" else "receipt_digest"
        )
    _validated_workspace_static_binding(
        {
            "workspace_id": value.get("workspace_id"),
            "revision_id": value.get("revision_id"),
            "source_kind": source_kind,
            "provenance_receipt_digest": provenance_digest,
            "source_sha256": source.get("sha256"),
            "working_path": working.get("path"),
            "working_sha256": working.get("current_sha256"),
        },
        expected_identity=expected_identity,
        label="status",
    )


def validate_workspace_compare_result(
    result: Any,
    *,
    expected_workspace_id: str,
    expected_revision_id: str | None = None,
) -> LocalWorkspaceCompareV1:
    if not isinstance(result, Mapping):
        raise MCPToolError("workspace comparison returned no structured result")
    if set(result) != _WORKSPACE_COMPARE_FIELDS | {"operation", "schema_version"}:
        raise MCPToolError("workspace comparison shape mismatched")
    supplied_digest = result.get("compare_digest")
    unsigned = {
        name: result[name]
        for name in _WORKSPACE_COMPARE_FIELDS
        if name != "compare_digest"
    }
    if (
        result.get("operation") != "workspace.compare"
        or not isinstance(supplied_digest, str)
        or supplied_digest != _canonical_digest(unsigned)
    ):
        raise MCPToolError("workspace comparison digest is invalid")
    try:
        comparison = LocalWorkspaceCompareV1.model_validate(
            unsigned | {"compare_digest": supplied_digest}
        )
    except ValueError as exc:
        raise MCPToolError("workspace comparison contract is invalid") from exc
    if comparison.workspace_id != expected_workspace_id:
        raise MCPToolError("workspace comparison target changed")
    if (
        expected_revision_id is not None
        and comparison.revision_id != expected_revision_id
    ):
        raise MCPToolError("workspace comparison revision changed")
    return comparison


class ExecutionBroker(Protocol):
    def bind_agent_session(self, session: dict[str, Any]) -> None: ...

    def bind_agent_workspace(self, workspace: dict[str, Any]) -> None: ...

    def clear_agent_session(self) -> None: ...

    def bind_controller_preflight(self, callback: ControllerPreflight) -> None: ...

    async def agent_tool_catalog(
        self, *, conversation_id: str, run_id: str
    ) -> list[AgentToolDefinitionV1]: ...

    async def execute_external_tool_request(
        self,
        request: ExternalToolRequestV1,
        *,
        sink: ToolEventSink,
        completion_gate: ToolCompletionGate | None = None,
    ) -> Any: ...


class MCPExecutionBroker:
    """Map Agent v1 tool proposals to Host-authorized MCP gateway calls."""

    def __init__(self, gateway: MCPGateway) -> None:
        self._gateway = gateway
        self._controller_preflight: ControllerPreflight | None = None
        # Context receipts are Host-validated evidence, retained per Agent run.
        # They are never an authority to mutate or deploy a controller.
        self._retained_hardware_contexts: dict[
            tuple[str, str], dict[str, dict[str, Any]]
        ] = {}

    def bind_agent_session(self, session: dict[str, Any]) -> None:
        # A hardware-context digest is scoped to the exact MCP/XG5000 session.
        self._retained_hardware_contexts.clear()
        self._gateway.bind_agent_session(session)

    def bind_agent_workspace(self, workspace: dict[str, Any]) -> None:
        self._gateway.bind_agent_workspace(workspace)

    def clear_agent_session(self) -> None:
        self._retained_hardware_contexts.clear()
        self._gateway.clear_agent_session()

    def bind_retained_hardware_context_evidence(
        self,
        *,
        conversation_id: str,
        run_id: str,
        evidence: list[Mapping[str, Any]],
    ) -> None:
        """Retain only exact Host-journaled hardware receipts for one run."""

        key = (conversation_id, run_id)
        retained: dict[str, dict[str, Any]] = {}
        for item in evidence:
            if item.get("toolName") != "project.hardware_context":
                continue
            receipt = item.get("receipt")
            contract = self._validated_retained_hardware_context_contract(receipt)
            digest = contract["contextDigest"]
            previous = retained.get(digest)
            if previous is not None and previous != contract:
                raise MCPToolError("retained hardware context evidence is ambiguous")
            retained[digest] = contract
        if retained:
            self._retained_hardware_contexts[key] = retained

    @staticmethod
    def _validated_retained_hardware_context_contract(
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise MCPToolError("retained hardware context evidence is invalid")
        contract = dict(value)
        receipt_digest = contract.pop("receiptDigest", None)
        if (
            contract.get("schema") != "controlstudio-hardware-context-evidence/v1"
            or not _is_sha256(contract.get("contextDigest"))
            or not _is_sha256(contract.get("projectHash"))
            or not isinstance(receipt_digest, str)
            or receipt_digest != _canonical_digest(contract)
        ):
            raise MCPToolError("retained hardware context evidence is invalid")
        return {**contract, "receiptDigest": receipt_digest}

    @classmethod
    def _hardware_context_contract_from_result(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        # The stdio MCP envelope carries the context under hardware_context;
        # in-process fixtures may return the context directly.
        candidate = (
            result.get("hardware_context")
            if isinstance(result, Mapping) and isinstance(result.get("hardware_context"), Mapping)
            else result
        )
        return cls._hardware_context_evidence_contract(
            candidate,
            expected_session_identity=expected_session_identity,
        )

    def _retain_hardware_context_contract(
        self,
        *,
        conversation_id: str,
        run_id: str,
        contract: Mapping[str, Any],
    ) -> None:
        validated = self._validated_retained_hardware_context_contract(contract)
        self._retained_hardware_contexts.setdefault((conversation_id, run_id), {})[
            validated["contextDigest"]
        ] = validated

    def _validate_change_analysis_hardware_context_authority(
        self,
        *,
        request: ExternalToolRequestV1,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> None:
        """Require durable Host evidence before a request may reuse a digest."""

        requested_digest = request.arguments.get("hardware_context_digest")
        if requested_digest is None:
            # Digestless analysis is discovery-only and cannot claim a physical
            # I/O binding.  The MCP contract enforces that distinction too.
            return
        if not _is_sha256(requested_digest):
            raise MCPToolError("hardware context digest is invalid")
        retained_contract = self._retained_hardware_contexts.get(
            (request.conversation_id, request.run_id), {}
        ).get(requested_digest)
        if retained_contract is None:
            raise MCPToolError(
                "hardware context digest has no retained Host evidence"
            )
        expected_project_hash = (
            expected_session_identity.get("project_hash")
            if isinstance(expected_session_identity, Mapping)
            else None
        )
        if retained_contract.get("projectHash") != expected_project_hash:
            raise MCPToolError(
                "retained hardware context does not match the Host target"
            )

    @staticmethod
    def _hardware_context_engineering_facts(
        contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project stable hardware facts, excluding session-local receipt identity."""

        return {
            key: contract.get(key)
            for key in (
                "schema",
                "projectHash",
                "cpu",
                "timerAreas",
                "ioModules",
                "ioMapping",
                "externalWiringObserved",
                "blockers",
                "offlineCandidateReady",
            )
        }

    @staticmethod
    def _is_hardware_context_binding_precondition(exc: MCPToolRejectedError) -> bool:
        """Allow exactly one known session-local ToolError shape to rebind.

        MCPGateway retains a parsed ToolError envelope.  Lightweight test
        gateways may construct the exception directly, so accept the same
        *complete* JSON envelope after the exact gateway prefix as a fallback.
        No substring matching is allowed at this retry boundary.
        """

        payload = getattr(exc, "mcp_error_payload", None)
        if payload is None:
            prefix = "program.change_target_analysis was rejected by MCP: "
            rendered = str(exc)
            if not rendered.startswith(prefix):
                return False
            try:
                payload = json.loads(rendered[len(prefix) :])
            except json.JSONDecodeError:
                return False
        return bool(
            isinstance(payload, Mapping)
            and set(payload) == {"schema_version", "status", "error"}
            and payload.get("schema_version") == "1.0"
            and payload.get("status") == "error"
            and isinstance(payload.get("error"), Mapping)
            and set(payload["error"]) == {"type", "message"}
            and payload["error"].get("type") == "SessionError"
            and payload["error"].get("message")
            == _HARDWARE_CONTEXT_BINDING_PRECONDITION
        )

    async def _rebind_hardware_context_and_retry_change_analysis(
        self,
        *,
        request: ExternalToolRequestV1,
        expected_session_identity: Mapping[str, Any] | None,
        original_error: MCPToolRejectedError,
    ) -> Any:
        requested_digest = request.arguments.get("hardware_context_digest")
        retained = self._retained_hardware_contexts.get(
            (request.conversation_id, request.run_id), {}
        )
        retained_contract = retained.get(requested_digest) if isinstance(
            requested_digest, str
        ) else None
        if retained_contract is None:
            raise original_error
        expected_project_hash = (
            expected_session_identity.get("project_hash")
            if isinstance(expected_session_identity, Mapping)
            else None
        )
        if (
            not _is_sha256(requested_digest)
            or retained_contract.get("projectHash") != expected_project_hash
        ):
            raise MCPToolError("retained hardware context does not match the Host target")

        rebound_result = await self._gateway.execute_agent_tool(
            "project.hardware_context",
            {},
        )
        rebound_contract = self._hardware_context_contract_from_result(
            rebound_result,
            expected_session_identity=expected_session_identity,
        )
        rebound_digest = rebound_contract.get("contextDigest")
        if (
            not _is_sha256(rebound_digest)
            or rebound_contract.get("projectHash") != expected_project_hash
            or self._hardware_context_engineering_facts(rebound_contract)
            != self._hardware_context_engineering_facts(retained_contract)
        ):
            raise MCPToolError(
                "rebound hardware context differs from retained Host evidence"
            )
        if self._gateway.agent_session_identity_snapshot != expected_session_identity:
            raise MCPToolError(
                "Host-bound engineering session changed during hardware context rebind"
            )
        self._retain_hardware_context_contract(
            conversation_id=request.conversation_id,
            run_id=request.run_id,
            contract=rebound_contract,
        )
        # The context digest deliberately contains session_id/PID/HWND.  A
        # verified XG5000 reopen therefore produces a new digest even when the
        # exact project and every engineering hardware fact are unchanged.
        # Rebind only that session-local digest after the strict comparison
        # above.  One retry only; any second cache miss or other error escapes.
        rebound_arguments = dict(request.arguments)
        rebound_arguments["hardware_context_digest"] = rebound_digest
        return await self._gateway.execute_agent_tool(
            "program.change_target_analysis",
            rebound_arguments,
        )

    def bind_controller_preflight(self, callback: ControllerPreflight) -> None:
        self._controller_preflight = callback

    async def agent_tool_catalog(
        self, *, conversation_id: str, run_id: str
    ) -> list[AgentToolDefinitionV1]:
        controller_authorized = (
            await self._controller_preflight(conversation_id, run_id)
            if self._controller_preflight is not None
            else False
        )
        available = {tool.name: tool for tool in await self._gateway.agent_mcp_client.list_tools()}
        definitions: list[AgentToolDefinitionV1] = []
        for alias, raw_name in AGENT_TOOL_NAMES.items():
            if raw_name in AGENT_CONTROLLER_MCP_TOOLS and not controller_authorized:
                continue
            tool = available.get(raw_name)
            if tool is None:
                raise MCPStartupError(f"required Agent tool is missing: {raw_name}")
            schema = getattr(tool, "inputSchema", None)
            if not isinstance(schema, dict):
                schema = getattr(tool, "parameters", None)
            if not isinstance(schema, dict):
                raise MCPStartupError(f"{raw_name} has no input schema")
            prepared = self._public_schema(raw_name, schema)
            if prepared is None:
                continue
            description = getattr(tool, "description", None)
            definitions.append(
                AgentToolDefinitionV1(
                    tool_name=alias,
                    description=description if isinstance(description, str) else None,
                    parameters_json_schema=prepared,
                )
            )
        return definitions

    async def execute_external_tool_request(
        self,
        request: ExternalToolRequestV1,
        *,
        sink: ToolEventSink,
        completion_gate: ToolCompletionGate | None = None,
    ) -> Any:
        raw_name = AGENT_TOOL_NAMES.get(request.tool_name)
        if raw_name is None:
            raise MCPToolError("Agent requested an unknown external tool")
        event_payload = self._event_payload(request)
        evidence_context: dict[str, Any] | None = None
        retained_hardware_context: dict[str, Any] | None = None
        expected_controller_endpoint: dict[str, Any] | None = None
        try:
            controller_authorized = (
                await self._controller_preflight(
                    request.conversation_id,
                    request.run_id,
                )
                if self._controller_preflight is not None
                else False
            )
            if raw_name in AGENT_CONTROLLER_MCP_TOOLS and not controller_authorized:
                raise MCPToolError(
                    "controller tool is not authorized for this engineering target"
                )
            if raw_name in AGENT_CONTROLLER_MCP_TOOLS:
                endpoint = self._gateway.controller_endpoint_snapshot
                if not isinstance(endpoint, Mapping):
                    raise MCPToolError("Host-bound controller endpoint is unavailable")
                expected_controller_endpoint = dict(endpoint)
            expected_session_identity = self._gateway.agent_session_identity_snapshot
            if raw_name == "program.change_target_analysis":
                # A digest cached only inside MCP is not evidence authority. It
                # must already be backed by a Host-journaled receipt retained
                # for this conversation/run before the first MCP dispatch.
                self._validate_change_analysis_hardware_context_authority(
                    request=request,
                    expected_session_identity=expected_session_identity,
                )
            # ``tool.selected`` is emitted by the Host-side Agent transport when
            # the model request arrives.  Do not report actual execution until
            # every Host preflight above has passed and the gateway dispatch is
            # about to begin.
            await self._emit_event(sink, "tool.started", event_payload)
            try:
                result = await self._gateway.execute_agent_tool(raw_name, request.arguments)
            except MCPToolRejectedError as exc:
                if (
                    raw_name == "program.change_target_analysis"
                    and self._is_hardware_context_binding_precondition(exc)
                ):
                    result = await self._rebind_hardware_context_and_retry_change_analysis(
                        request=request,
                        expected_session_identity=expected_session_identity,
                        original_error=exc,
                    )
                else:
                    raise
            if raw_name in _WORKSPACE_STATIC_TOOLS:
                expected_workspace = _expected_workspace_static_identity(
                    expected_session_identity
                )
                current_workspace = await self._gateway.call_workspace_tool(
                    "workspace.status",
                    {"workspace_id": expected_workspace["workspace_id"]},
                )
                _validate_workspace_status_lineage(
                    current_workspace,
                    expected_identity=expected_workspace,
                )
            if (
                self._gateway.agent_session_identity_snapshot
                != expected_session_identity
            ):
                raise MCPToolError(
                    "Host-bound engineering session changed during tool execution"
                )
            evidence_contract = self._evidence_contract(
                raw_name,
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=request.arguments,
            )
            if raw_name == "xg5000.session_status":
                # ``session_status`` is an MCP diagnostic journal, not a model
                # payload.  Return the same bounded receipt that the Host will
                # persist as evidence; never forward raw events, paths, process
                # identity, metadata, or capability error detail to the Agent.
                if evidence_contract is None:
                    raise MCPToolError("XG5000 session status evidence is missing")
                result = evidence_contract
            if raw_name == "project.hardware_context" and evidence_contract is not None:
                # Validate now, but grant the run-local rebind authority only
                # after the same receipt is durably journaled below.
                retained_hardware_context = (
                    self._validated_retained_hardware_context_contract(
                        evidence_contract
                    )
                )
            if raw_name in {"project.search", "workspace.search"}:
                evidence_context = project_search_context(result)
                if (
                    evidence_contract is None
                    or evidence_context["sourceReceiptDigest"]
                    != evidence_contract.get("receiptDigest")
                ):
                    raise MCPToolError(
                        "Project search context is not bound to its evidence receipt"
                    )
            if (
                raw_name != "xg5000.session_status"
                and raw_name not in STRUCTURAL_ANALYSIS_TOOL_COVERAGE
            ):
                # Evidence is derived and validated from the complete MCP
                # result above.  Only then replace the model-facing value with
                # a closed Host-owned projection.  The raw result is never
                # returned to the Agent or used as its retry feedback.
                result = self._agent_result_projection(
                    raw_name,
                    result,
                    evidence_contract=evidence_contract,
                    expected_session_identity=expected_session_identity,
                    request_arguments=request.arguments,
                    expected_controller_endpoint=expected_controller_endpoint,
                )
        except MCPTransportError:
            await self._emit_failure(sink, request, "transport")
            raise
        except MCPToolRejectedError:
            logger.warning(
                "Agent tool rejected by MCP (tool=%s, reason=tool_error)",
                raw_name,
            )
            await self._emit_failure(
                sink,
                request,
                "tool_error",
            )
            raise
        except MCPToolError:
            logger.warning(
                "Agent tool denied by Host (tool=%s, reason=host_policy)",
                raw_name,
            )
            await self._emit_failure(
                sink,
                request,
                "host_policy",
            )
            raise
        except asyncio.CancelledError:
            await self._emit_failure(sink, request, "cancelled")
            raise
        except Exception as exc:
            await self._emit_failure(sink, request, "host_policy")
            raise MCPToolError("Platform execution broker failed") from exc
        ExternalToolResultV1(request_id=request.request_id, tool_name=request.tool_name, status="completed")
        completed_payload = dict(event_payload)
        if raw_name == "sim.target_status" and isinstance(result, Mapping):
            target_status = result.get("target_status")
            verified = result.get("verified")
            classification = (
                target_status.get("classification")
                if isinstance(target_status, Mapping)
                else None
            )
            if (
                isinstance(target_status, Mapping)
                and type(verified) is bool
                and target_status.get("verified") is verified
                and classification in {"SIMULATOR_TARGET", "NOT_SIMULATOR_TARGET"}
            ):
                completed_payload["simulator_target_status"] = {
                    "verified": verified,
                    "classification": classification,
                }
        if evidence_contract is not None:
            completed_payload["evidence_contract"] = evidence_contract
        if evidence_context is not None:
            completed_payload["evidence_context"] = evidence_context
        if completion_gate is not None:
            # The Agent transport may need to validate its bounded projection
            # before this append-only stream records a terminal completion.
            try:
                await completion_gate(dict(completed_payload), result)
            except asyncio.CancelledError:
                await self._emit_failure(sink, request, "cancelled")
                raise
            except Exception as exc:
                await self._emit_failure(sink, request, "host_policy")
                raise MCPToolError(
                    "Host could not project the completed tool result safely"
                ) from exc
        try:
            await self._emit_event(sink, "tool.completed", completed_payload)
        except asyncio.CancelledError:
            await self._emit_failure(sink, request, "cancelled")
            raise
        except Exception as exc:
            await self._emit_failure(sink, request, "host_policy")
            raise MCPToolError(
                "Host could not record the completed tool result safely"
            ) from exc
        if retained_hardware_context is not None:
            self._retained_hardware_contexts.setdefault(
                (request.conversation_id, request.run_id), {}
            )[retained_hardware_context["contextDigest"]] = retained_hardware_context
        return result

    @classmethod
    def _evidence_contract(
        cls,
        raw_name: str,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None = None,
        request_arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        arguments = request_arguments if isinstance(request_arguments, Mapping) else {}
        if raw_name == "xg5000.session_status":
            return cls._session_status_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=arguments,
            )
        if raw_name == "project.hardware_context":
            return cls._hardware_context_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
            )
        if raw_name == "program.read_ld":
            return cls._program_read_ld_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=arguments,
            )
        if raw_name == "workspace.compare":
            return cls._workspace_compare_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
            )
        if raw_name == "workspace.hardware_context":
            return cls._workspace_hardware_context_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
            )
        if raw_name == "workspace.structure_index":
            return cls._workspace_structure_index_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=arguments,
            )
        if raw_name == "workspace.search":
            return cls._workspace_search_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=arguments,
            )
        if raw_name == "project.structure_index":
            return cls._project_structure_index_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=arguments,
            )
        if raw_name == "program.change_target_analysis":
            return cls._change_target_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=arguments,
            )
        if raw_name in {
            "program.compile_mne_instruction_batch_patch_candidate",
            "program.compile_mne_instruction_patch_candidate",
            "program.compile_mne_rung_patch_candidate",
            "program.compile_mne_series_guard_candidate",
        }:
            return cls._mne_patch_candidate_evidence_contract(
                raw_name,
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=arguments,
            )
        return build_structural_evidence_contract(
            raw_name,
            result,
            expected_session_identity=expected_session_identity,
            request_arguments=arguments,
        )

    @classmethod
    def _session_status_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate a raw MCP session journal and emit only closed diagnostics."""

        _validate_schema_only_request(
            request_arguments,
            label="XG5000 session status",
        )

        if (
            not isinstance(result, Mapping)
            or set(result) != _SESSION_STATUS_FIELDS
            or not isinstance(expected_session_identity, Mapping)
            or any(
                field not in expected_session_identity
                for field in SESSION_IDENTITY_FIELDS
            )
        ):
            raise MCPToolError("XG5000 session status shape mismatched")
        expected_identity = {
            field: expected_session_identity[field]
            for field in SESSION_IDENTITY_FIELDS
        }
        if any(result.get(field) != value for field, value in expected_identity.items()):
            raise MCPToolError("XG5000 session status target changed")

        stage = result.get("stage")
        target = result.get("target")
        plc_policy = result.get("plc_policy")
        valid_stages = result.get("valid_stages")
        if (
            result.get("schema_version") != "1.0"
            or stage not in _SESSION_STAGES
            or target not in {"ENGINEERING_IDE", "LOCAL_VALIDATION"}
            or plc_policy
            not in {
                "read_only_unverified",
                "read_only_project_identity_verified",
                "read_only_controlled_launch",
                "unsafe_local_validation_enabled",
                "read_only_terminal_change_rebind",
                "offline_abort_recovery_only",
                "offline_rollback_recovery_only",
                "offline_change_authorized",
            }
            or not isinstance(valid_stages, (list, tuple))
            or tuple(valid_stages) != _SESSION_STAGES
            or result.get("include_events") is not True
        ):
            raise MCPToolError("XG5000 session status lifecycle is invalid")
        for field in ("created_at", "updated_at"):
            value = result.get(field)
            if not isinstance(value, str) or not value or len(value) > 64:
                raise MCPToolError("XG5000 session status timestamp is invalid")

        authorization = result.get("authorization")
        if not isinstance(authorization, Mapping) or set(authorization) != {
            "access_profile",
            "granted_capabilities",
        }:
            raise MCPToolError("XG5000 session authorization is invalid")
        access_profile = authorization.get("access_profile")
        expected_grants = {
            "analysis": ("read", "analyze"),
            "simulation_operator": ("read", "analyze", "simulate"),
            "offline_engineering": (
                "read",
                "analyze",
                "offline_mutate",
                "simulate",
            ),
        }.get(access_profile)
        granted_capabilities = authorization.get("granted_capabilities")
        if (
            expected_grants is None
            or not isinstance(granted_capabilities, (list, tuple))
            or tuple(granted_capabilities) != expected_grants
        ):
            raise MCPToolError("XG5000 session authorization is inconsistent")

        capabilities = result.get("capabilities")
        if not isinstance(capabilities, Mapping) or set(capabilities) != {
            "analysis",
            "mutation",
            "simulation",
        }:
            raise MCPToolError("XG5000 session capabilities are invalid")
        capability_states: dict[str, str] = {}
        allowed_capability_states = {
            "unavailable",
            "idle",
            "ready",
            "running",
            "error",
        }
        for capability_name in ("analysis", "mutation", "simulation"):
            capability = capabilities.get(capability_name)
            if (
                not isinstance(capability, Mapping)
                or "state" not in capability
                or not set(capability).issubset(
                    {"state", "last_operation", "detail"}
                )
                or capability.get("state") not in allowed_capability_states
            ):
                raise MCPToolError("XG5000 session capability state is invalid")
            capability_states[capability_name] = str(capability["state"])

        metadata = result.get("metadata")
        identity_assurance = (
            metadata.get("project_identity_assurance")
            if isinstance(metadata, Mapping)
            else None
        )
        if identity_assurance not in {
            "unverified",
            "strong_readback",
            "controlled_launch_read_only",
        }:
            raise MCPToolError("XG5000 project identity assurance is invalid")

        events = result.get("events")
        if not isinstance(events, list) or len(events) > _SESSION_STATUS_EVENT_LIMIT:
            raise MCPToolError("XG5000 session events are invalid")
        success_count = 0
        failure_count = 0
        latest_transition: dict[str, Any] | None = None
        previous_stage_after: str | None = None
        for event in events:
            if (
                not isinstance(event, Mapping)
                or set(event) != _SESSION_STATUS_EVENT_FIELDS
                or not isinstance(event.get("success"), bool)
                or event.get("stage_before") not in _SESSION_STAGES
                or event.get("stage_after") not in _SESSION_STAGES
                or event.get("pid") != expected_identity["pid"]
                or event.get("hwnd") != expected_identity["hwnd"]
                or event.get("project_path") != expected_identity["project_path"]
                or not _is_sha256(event.get("project_hash"))
                or not isinstance(event.get("ts"), str)
                or not event.get("ts")
                or len(event["ts"]) > 64
                or not isinstance(event.get("operation"), str)
                or not event.get("operation")
                or len(event["operation"]) > 256
                or previous_stage_after is not None
                and event.get("stage_before") != previous_stage_after
            ):
                raise MCPToolError("XG5000 session event is invalid")
            if event["success"]:
                success_count += 1
            else:
                failure_count += 1
            latest_transition = {
                "stageBefore": event["stage_before"],
                "stageAfter": event["stage_after"],
                "outcome": "SUCCEEDED" if event["success"] else "FAILED",
            }
            previous_stage_after = str(event["stage_after"])

        if events and (
            previous_stage_after != stage
            or events[-1].get("project_hash") != expected_identity["project_hash"]
        ):
            raise MCPToolError("XG5000 session event chain is invalid")

        event_summary: dict[str, Any] = {
            "totalCount": len(events),
            "successCount": success_count,
            "failureCount": failure_count,
        }
        if latest_transition is not None:
            event_summary["latestTransition"] = latest_transition
        contract: dict[str, Any] = {
            "schema": "controlstudio-xg5000-session-status-evidence/v1",
            "operation": "xg5000.session_status",
            "verified": True,
            "targetBound": True,
            "lifecycleStage": stage,
            "targetClass": target,
            "plcPolicy": plc_policy,
            "projectIdentityAssurance": identity_assurance,
            "authorization": {
                "accessProfile": access_profile,
                "grantedCapabilities": list(expected_grants),
            },
            "capabilityStates": capability_states,
            "eventSummary": event_summary,
            "rawResultOmitted": True,
        }
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    def _agent_result_projection(
        self,
        raw_name: str,
        result: Any,
        *,
        evidence_contract: Mapping[str, Any] | None,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
        expected_controller_endpoint: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Return the only model-visible shape for a non-structural MCP tool."""

        if raw_name == "project.list_programs":
            return self._program_catalog_result_projection(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=request_arguments,
            )
        if raw_name == "program.focus":
            return self._program_focus_result_projection(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=request_arguments,
            )
        if raw_name == "program.read_ld":
            return self._program_read_result_projection(
                result,
                evidence_contract,
                expected_session_identity=expected_session_identity,
                request_arguments=request_arguments,
            )
        if raw_name == "sim.target_status":
            return self._simulator_status_result_projection(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=request_arguments,
            )
        if raw_name == "project.snapshot":
            return self._snapshot_result_projection(
                result,
                expected_session_identity=expected_session_identity,
                request_arguments=request_arguments,
            )
        if raw_name in {"project.hardware_context", "workspace.hardware_context"}:
            return self._hardware_result_projection(
                raw_name,
                evidence_contract,
                request_arguments=request_arguments,
            )
        if raw_name == "workspace.compare":
            return self._workspace_compare_result_projection(
                evidence_contract,
                request_arguments=request_arguments,
            )
        if raw_name == "program.change_target_analysis":
            return self._change_target_result_projection(evidence_contract)
        if raw_name in {
            "program.compile_mne_instruction_batch_patch_candidate",
            "program.compile_mne_instruction_patch_candidate",
            "program.compile_mne_rung_patch_candidate",
            "program.compile_mne_series_guard_candidate",
        }:
            return self._mne_candidate_result_projection(raw_name, evidence_contract)
        if raw_name in AGENT_CONTROLLER_MCP_TOOLS:
            return self._controller_result_projection(
                raw_name,
                result,
                request_arguments=request_arguments,
                expected_endpoint=expected_controller_endpoint,
            )
        raise MCPToolError("Agent tool has no bounded result projection")

    @staticmethod
    def _validate_session_result_binding(
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        project_hash_fields: tuple[str, ...],
        require_project_path: bool = False,
        require_session_id: bool = False,
        require_pid_hwnd: bool = True,
    ) -> Mapping[str, Any]:
        if not isinstance(result, Mapping) or not isinstance(
            expected_session_identity,
            Mapping,
        ):
            raise MCPToolError("Agent tool result is not target-bound")
        if any(
            field not in expected_session_identity for field in SESSION_IDENTITY_FIELDS
        ):
            raise MCPToolError("Agent tool target identity is incomplete")
        expected_pid = expected_session_identity["pid"]
        expected_hwnd = expected_session_identity["hwnd"]
        expected_path = expected_session_identity["project_path"]
        expected_hash = expected_session_identity["project_hash"]
        if (
            isinstance(expected_pid, bool)
            or not isinstance(expected_pid, int)
            or expected_pid <= 0
            or isinstance(expected_hwnd, bool)
            or not isinstance(expected_hwnd, int)
            or expected_hwnd <= 0
            or not isinstance(expected_path, str)
            or not expected_path
            or not _is_sha256(expected_hash)
            or require_pid_hwnd
            and (
                result.get("pid") != expected_pid
                or result.get("hwnd") != expected_hwnd
            )
            or any(result.get(field) != expected_hash for field in project_hash_fields)
            or require_project_path
            and result.get("project_path") != expected_path
            or require_session_id
            and result.get("session_id") != expected_session_identity["session_id"]
        ):
            raise MCPToolError("Agent tool result target changed")
        return result

    @classmethod
    def _program_catalog_result_projection(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        bound = cls._validate_session_result_binding(
            result,
            expected_session_identity=expected_session_identity,
            project_hash_fields=("project_hash_before", "project_hash_after"),
            require_session_id=True,
        )
        programs = bound.get("programs")
        pagination = bound.get("pagination")
        if (
            set(request_arguments) - {"schema_version", "name_filter", "offset", "limit"}
            or request_arguments.get("schema_version", "1.0") != "1.0"
            or bound.get("operation") != "project.list_programs"
            or bound.get("ok") is not True
            or bound.get("verified") is not True
            or bound.get("project_unchanged") is not True
            or bound.get("catalog_source") != "sha_bound_project_xml"
            or bound.get("live_editor_catalog") is not False
            or not _is_canonical_uuid(bound.get("operation_id"))
            or not _is_sha256(bound.get("catalog_digest"))
            or not isinstance(programs, list)
            or len(programs) > 500
            or bound.get("program_count") != len(programs)
            or not isinstance(pagination, Mapping)
            or set(pagination)
            != {"offset", "limit", "returned", "total", "next_offset"}
        ):
            raise MCPToolError("program catalog result is invalid")
        offset = _bounded_integer(
            pagination.get("offset"),
            "program catalog offset",
            maximum=1_000_000,
        )
        limit = _bounded_integer(
            pagination.get("limit"),
            "program catalog limit",
            minimum=1,
            maximum=500,
        )
        total = _bounded_integer(
            pagination.get("total"),
            "program catalog total",
            maximum=1_000_000,
        )
        next_offset = pagination.get("next_offset")
        if next_offset is not None:
            next_offset = _bounded_integer(
                next_offset,
                "program catalog next offset",
                maximum=1_000_000,
            )
        if (
            pagination.get("returned") != len(programs)
            or bound.get("filtered_program_count") != total
            or not isinstance(offset, int)
            or not isinstance(limit, int)
            or not isinstance(total, int)
            or len(programs) > limit
            or offset != request_arguments.get("offset", 0)
            or limit != request_arguments.get("limit", 100)
            or (next_offset is None) != (offset + len(programs) >= total)
            or next_offset is not None
            and next_offset != offset + len(programs)
        ):
            raise MCPToolError("program catalog pagination is invalid")
        name_filter_value = request_arguments.get("name_filter")
        if name_filter_value is not None and (
            not isinstance(name_filter_value, str)
            or len(name_filter_value) > 512
        ):
            raise MCPToolError("program catalog name filter is invalid")
        name_filter = (
            name_filter_value.strip().casefold()
            if isinstance(name_filter_value, str)
            else ""
        )
        projected_programs = [
            _project_program_identity(item, "program catalog item")
            for item in programs
        ]
        if name_filter and any(
            name_filter not in item["name"].casefold()
            for item in projected_programs
        ):
            raise MCPToolError("program catalog differs from its name filter")
        return {
            "schema": "controlstudio-program-catalog-tool-result/v1",
            "operation": "project.list_programs",
            "ok": True,
            "verified": True,
            "programs": projected_programs,
            "filteredProgramCount": total,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "returned": len(programs),
                "total": total,
                "nextOffset": next_offset,
            },
            "catalogDigest": bound["catalog_digest"],
            "project_unchanged": True,
            "rawResultOmitted": True,
        }

    @classmethod
    def _program_focus_result_projection(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        bound = cls._validate_session_result_binding(
            result,
            expected_session_identity=expected_session_identity,
            project_hash_fields=(
                "project_hash_before",
                "project_hash_after",
                "project_sha256_before",
                "project_sha256_after",
            ),
            require_project_path=True,
            require_session_id=True,
        )
        activation = bound.get("activation")
        if (
            set(request_arguments) - {"schema_version", "program_id"}
            or request_arguments.get("schema_version", "1.0") != "1.0"
            or bound.get("operation") != "program.focus"
            or bound.get("ok") is not True
            or bound.get("verified") is not True
            or bound.get("read_only") is not True
            or bound.get("project_unchanged") is not True
            or not _is_canonical_uuid(bound.get("operation_id"))
            or not isinstance(activation, Mapping)
            or not isinstance(bound.get("program"), Mapping)
            or bound["program"].get("program_id")
            != request_arguments.get("program_id")
            or activation.get("exact_program_active") is not True
            or activation.get("exact_window_foreground") is not True
            or _bounded_integer(
                activation.get("mdi_hwnd"),
                "program focus MDI identity",
                minimum=1,
            )
            is None
            or _bounded_integer(
                activation.get("editor_hwnd"),
                "program focus editor identity",
                minimum=1,
            )
            != _bounded_integer(
                activation.get("active_editor_hwnd"),
                "program focus active editor identity",
                minimum=1,
            )
        ):
            raise MCPToolError("program focus result is invalid")
        return {
            "schema": "controlstudio-program-focus-tool-result/v1",
            "operation": "program.focus",
            "ok": True,
            "verified": True,
            "read_only": True,
            "activation": {
                "exact_program_active": True,
                "exact_window_foreground": True,
            },
            "program": _project_program_identity(
                bound.get("program"),
                "focused program",
            ),
            "project_unchanged": True,
            "rawResultOmitted": True,
        }

    @staticmethod
    def _validated_result_evidence(
        value: Mapping[str, Any] | None,
        *,
        schemas: set[str],
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping) or value.get("schema") not in schemas:
            raise MCPToolError("Agent result evidence is missing or invalid")
        contract = dict(value)
        receipt_digest = contract.pop("receiptDigest", None)
        if not _is_sha256(receipt_digest) or receipt_digest != _canonical_digest(contract):
            raise MCPToolError("Agent result evidence digest is invalid")
        return {**contract, "receiptDigest": receipt_digest}

    @classmethod
    def _program_read_result_projection(
        cls,
        result: Any,
        evidence_contract: Mapping[str, Any] | None,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = cls._validated_result_evidence(
            evidence_contract,
            schemas={"controlstudio-program-read-ld-evidence/v1"},
        )
        bound = cls._validate_session_result_binding(
            result,
            expected_session_identity=expected_session_identity,
            project_hash_fields=(
                "project_hash_before",
                "project_hash_after",
                "project_sha256_before",
                "project_sha256_after",
            ),
            require_project_path=True,
            require_session_id=True,
        )
        program = _project_program_identity(bound.get("program"), "read program")
        payload_size = _bounded_integer(
            bound.get("payload_size"),
            "program read payload size",
            minimum=1,
            maximum=64 * 1024 * 1024,
        )
        visual_row_count = _bounded_integer(
            bound.get("visual_row_count"),
            "program read row count",
            maximum=1_000_000,
        )
        if (
            set(request_arguments) - {"schema_version", "program_id"}
            or request_arguments.get("schema_version", "1.0") != "1.0"
            or program["program_id"] != request_arguments.get("program_id")
            or bound.get("operation") != "program.read_ld"
            or bound.get("ok") is not True
            or bound.get("verified") is not True
            or bound.get("stable_repeat") is not True
            or bound.get("project_unchanged") is not True
            or not _is_canonical_uuid(bound.get("operation_id"))
            or receipt.get("programId") != program["program_id"]
            or receipt.get("programName") != program["name"]
            or receipt.get("payloadSha256") != bound.get("payload_sha256")
            or receipt.get("decodedProgramSha256")
            != bound.get("decoded_program_sha256")
        ):
            raise MCPToolError("program read result differs from its evidence")
        decoded = _ld_decoded_projection(
            bound.get("decoded"),
            expected_name=program["name"],
        )
        if visual_row_count != len(decoded["rows"]):
            raise MCPToolError("program read row count is invalid")
        return {
            "schema": "controlstudio-program-read-ld-tool-result/v1",
            "operation": "program.read_ld",
            "ok": True,
            "verified": True,
            "program": program,
            "payload_size": payload_size,
            "payload_sha256": receipt["payloadSha256"],
            "decoded_program_sha256": receipt["decodedProgramSha256"],
            "visual_row_count": visual_row_count,
            "decoded": decoded,
            "stable_repeat": True,
            "project_unchanged": True,
            "evidenceReceiptDigest": receipt["receiptDigest"],
            "rawResultOmitted": True,
        }

    @classmethod
    def _simulator_status_result_projection(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _validate_schema_only_request(
            request_arguments,
            label="simulator target status",
        )
        bound = cls._validate_session_result_binding(
            result,
            expected_session_identity=expected_session_identity,
            project_hash_fields=(
                "project_sha256_before",
                "project_sha256_after",
            ),
            require_project_path=True,
            require_session_id=False,
        )
        status = bound.get("target_status")
        actions = bound.get("actions")
        verified = bound.get("verified")
        if (
            bound.get("operation") != "sim.target_status"
            or bound.get("ok") is not True
            or bound.get("read_only") is not True
            or type(verified) is not bool
            or bound.get("project_unchanged") is not True
            or not isinstance(status, Mapping)
            or status.get("pid") != bound.get("pid")
            or status.get("hwnd") != bound.get("hwnd")
            or status.get("verified") is not verified
            or status.get("classification")
            not in {"SIMULATOR_TARGET", "NOT_SIMULATOR_TARGET"}
            or (status.get("classification") == "SIMULATOR_TARGET") is not verified
            or not isinstance(actions, Mapping)
            or set(actions) != {"start", "write_program", "run", "stop"}
            or any(actions[action] is not False for action in actions)
        ):
            raise MCPToolError("simulator target status result is invalid")
        return {
            "schema": "controlstudio-simulator-target-tool-result/v1",
            "operation": "sim.target_status",
            "ok": True,
            "verified": verified,
            "target_status": {
                "verified": verified,
                "classification": status["classification"],
            },
            "actions": {
                "start": False,
                "write_program": False,
                "run": False,
                "stop": False,
            },
            "read_only": True,
            "project_unchanged": True,
            "rawResultOmitted": True,
        }

    @classmethod
    def _snapshot_result_projection(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _validate_schema_only_request(
            request_arguments,
            label="project snapshot",
        )
        bound = cls._validate_session_result_binding(
            result,
            expected_session_identity=expected_session_identity,
            project_hash_fields=("project_hash",),
            require_project_path=True,
            require_session_id=True,
            require_pid_hwnd=False,
        )
        programs = bound.get("programs")
        snapshot_id = bound.get("snapshot_id")
        try:
            canonical_snapshot_id = (
                str(uuid.UUID(snapshot_id)) if isinstance(snapshot_id, str) else None
            )
        except ValueError:
            canonical_snapshot_id = None
        if (
            bound.get("operation") != "project.snapshot"
            or bound.get("ok") is not True
            or bound.get("verified") is not True
            or bound.get("project_unchanged") is not True
            or canonical_snapshot_id != snapshot_id
            or not _is_canonical_uuid(bound.get("operation_id"))
            or not isinstance(programs, list)
            or len(programs) > 4_096
            or bound.get("program_count") != len(programs)
            or any(
                bound.get(field) is not None
                for field in (
                    "transaction_id",
                    "transaction_state",
                    "transaction_checkpoint",
                )
            )
        ):
            raise MCPToolError("read-only project snapshot result is invalid")
        projected_programs: list[dict[str, Any]] = []
        total_rows = 0
        for item in programs:
            if not isinstance(item, Mapping):
                raise MCPToolError("project snapshot program is invalid")
            program_id = _bounded_text(
                item.get("program_id"),
                "project snapshot program id",
                maximum=512,
            )
            name = _bounded_text(
                item.get("name"),
                "project snapshot program name",
                maximum=512,
            )
            row_count = _bounded_integer(
                item.get("visual_row_count"),
                "project snapshot row count",
                maximum=1_000_000,
            )
            if (
                not isinstance(program_id, str)
                or _PROGRAM_ID.fullmatch(program_id) is None
                or not isinstance(name, str)
                or not isinstance(row_count, int)
                or not _is_sha256(item.get("payload_sha256"))
                or not isinstance(item.get("payload_path"), str)
                or not item.get("payload_path")
            ):
                raise MCPToolError("project snapshot program is invalid")
            total_rows += row_count
            if len(projected_programs) < _AGENT_SNAPSHOT_PROGRAM_LIMIT:
                projected_programs.append(
                    {
                        "programId": program_id,
                        "name": name,
                        "payloadSha256": item["payload_sha256"],
                        "visualRowCount": row_count,
                    }
                )
        if bound.get("total_visual_rows") != total_rows:
            raise MCPToolError("project snapshot row total is invalid")
        return {
            "schema": "controlstudio-project-snapshot-tool-result/v1",
            "operation": "project.snapshot",
            "ok": True,
            "verified": True,
            "snapshot_id": snapshot_id,
            "programCount": len(programs),
            "totalVisualRows": total_rows,
            "programs": projected_programs,
            "programsTruncated": len(programs) > len(projected_programs),
            "transaction_id": None,
            "transaction_state": None,
            "transaction_checkpoint": None,
            "project_unchanged": True,
            "rawResultOmitted": True,
        }

    @classmethod
    def _hardware_result_projection(
        cls,
        raw_name: str,
        evidence_contract: Mapping[str, Any] | None,
        *,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _validate_schema_only_request(
            request_arguments,
            label=raw_name,
        )
        expected_schema = {
            "project.hardware_context": "controlstudio-hardware-context-evidence/v1",
            "workspace.hardware_context": (
                "controlstudio-workspace-hardware-context-evidence/v1"
            ),
        }.get(raw_name)
        if expected_schema is None:
            raise MCPToolError("hardware projection tool is invalid")
        receipt = cls._validated_result_evidence(
            evidence_contract,
            schemas={expected_schema},
        )
        required = {
            "contextDigest",
            "cpu",
            "timerAreas",
            "ioModules",
            "ioMapping",
            "externalWiringObserved",
            "blockers",
            "offlineCandidateReady",
            "receiptDigest",
        }
        if (
            any(field not in receipt for field in required)
            or not _is_sha256(receipt.get("contextDigest"))
            or type(receipt.get("externalWiringObserved")) is not bool
            or type(receipt.get("offlineCandidateReady")) is not bool
        ):
            raise MCPToolError("hardware result evidence is incomplete")
        blockers = _bounded_code_list(
            receipt.get("blockers"),
            "hardware result blockers",
        )
        if receipt["offlineCandidateReady"] != (not blockers):
            raise MCPToolError("hardware result readiness is inconsistent")
        # Re-project the Host receipt as a defense against accidental future
        # receipt expansion.  Only these typed engineering facts are public.
        return {
            "schema": "controlstudio-hardware-context-tool-result/v1",
            "operation": raw_name,
            "contextDigest": receipt["contextDigest"],
            "cpu": cls._public_hardware_cpu_receipt(receipt["cpu"]),
            "timerAreas": cls._public_hardware_timer_receipt(receipt["timerAreas"]),
            "ioModules": cls._public_hardware_module_receipt(receipt["ioModules"]),
            "ioMapping": cls._public_hardware_io_mapping_receipt(receipt["ioMapping"]),
            "externalWiringObserved": receipt["externalWiringObserved"],
            "blockers": blockers,
            "offlineCandidateReady": receipt["offlineCandidateReady"],
            "evidenceReceiptDigest": receipt["receiptDigest"],
            "rawResultOmitted": True,
        }

    @staticmethod
    def _public_hardware_cpu_receipt(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or not set(value).issubset(
            {"status", "model", "observedCandidates"}
        ):
            raise MCPToolError("hardware CPU receipt is invalid")
        raw: dict[str, Any] = {
            "status": value.get("status"),
            "model": value.get("model"),
        }
        if "observedCandidates" in value:
            candidates = value.get("observedCandidates")
            if not isinstance(candidates, list):
                raise MCPToolError("hardware CPU receipt candidates are invalid")
            raw["observed_candidates"] = candidates
        projected = _hardware_cpu_projection(raw)
        if projected != dict(value):
            raise MCPToolError("hardware CPU receipt is not canonical")
        return projected

    @staticmethod
    def _public_hardware_timer_receipt(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or len(value) > _AGENT_HARDWARE_TIMER_LIMIT:
            raise MCPToolError("hardware timer receipt is invalid")
        required = {
            "deviceStart",
            "deviceEnd",
            "startIndex",
            "endIndex",
            "timeBaseMs",
            "status",
        }
        projected: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping) or set(item) != required:
                raise MCPToolError("hardware timer receipt is invalid")
            canonical = _hardware_timer_projection(
                [
                    {
                        "device_start": item.get("deviceStart"),
                        "device_end": item.get("deviceEnd"),
                        "start_index": item.get("startIndex"),
                        "end_index": item.get("endIndex"),
                        "time_base_ms": item.get("timeBaseMs"),
                        "status": item.get("status"),
                    }
                ]
            )[0]
            if canonical != dict(item):
                raise MCPToolError("hardware timer receipt is not canonical")
            projected.append(canonical)
        return projected

    @staticmethod
    def _public_hardware_module_receipt(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or len(value) > _AGENT_HARDWARE_MODULE_LIMIT:
            raise MCPToolError("hardware module receipt is invalid")
        required = {
            "base",
            "slot",
            "moduleId",
            "subtype",
            "model",
            "status",
            "deviceBindingStatus",
            "deviceBindings",
        }
        projected: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping) or set(item) != required:
                raise MCPToolError("hardware module receipt is invalid")
            bindings = item.get("deviceBindings")
            if not isinstance(bindings, list):
                raise MCPToolError("hardware module receipt bindings are invalid")
            raw_bindings: list[dict[str, Any]] = []
            for binding in bindings:
                if not isinstance(binding, Mapping):
                    raise MCPToolError("hardware module receipt binding is invalid")
                raw_binding = {
                    "direction": binding.get("direction"),
                    "address_start": binding.get("addressStart"),
                    "address_end": binding.get("addressEnd"),
                    "status": binding.get("status"),
                }
                for public_name, raw_name in (
                    ("bindingKind", "binding_kind"),
                    ("pointKind", "point_kind"),
                    ("channelStart", "channel_start"),
                    ("channelEnd", "channel_end"),
                ):
                    if public_name in binding:
                        raw_binding[raw_name] = binding.get(public_name)
                raw_bindings.append(raw_binding)
            canonical = _hardware_module_projection(
                [{
                    "base": item.get("base"),
                    "slot": item.get("slot"),
                    "module_id": item.get("moduleId"),
                    "subtype": item.get("subtype"),
                    "model": item.get("model"),
                    "status": item.get("status"),
                    "device_binding_status": item.get("deviceBindingStatus"),
                    "device_bindings": raw_bindings,
                }]
            )[0]
            if canonical != dict(item):
                raise MCPToolError("hardware module receipt is not canonical")
            projected.append(canonical)
        return projected

    @staticmethod
    def _public_hardware_io_mapping_receipt(value: Any) -> dict[str, Any]:
        required = {
            "status",
            "ioParameterStatus",
            "moduleConfigurationStatus",
            "deviceOffsetStatus",
            "channels",
            "moduleAddressRangesObserved",
            "moduleAddressRangesDocumented",
            "moduleDeviceBindingStatus",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise MCPToolError("hardware I/O mapping receipt is invalid")
        channels = value.get("channels")
        if not isinstance(channels, list) or len(channels) > _AGENT_HARDWARE_CHANNEL_LIMIT:
            raise MCPToolError("hardware I/O mapping receipt is invalid")
        channel_fields = {
            "kind",
            "devicePrefix",
            "deviceTypeCode",
            "offset",
            "status",
        }
        if any(
            not isinstance(item, Mapping) or set(item) != channel_fields
            for item in channels
        ):
            raise MCPToolError("hardware I/O channel receipt is invalid")
        raw = {
            "status": value.get("status"),
            "io_parameter_status": value.get("ioParameterStatus"),
            "module_configuration_status": value.get("moduleConfigurationStatus"),
            "device_offset_status": value.get("deviceOffsetStatus"),
            "channels": [
                {
                    "kind": item.get("kind"),
                    "device_prefix": item.get("devicePrefix"),
                    "device_type_code": item.get("deviceTypeCode"),
                    "offset": item.get("offset"),
                    "status": item.get("status"),
                }
                for item in channels
            ],
            "module_address_ranges_observed": value.get(
                "moduleAddressRangesObserved"
            ),
            "module_address_ranges_documented": value.get(
                "moduleAddressRangesDocumented"
            ),
            "module_device_binding_status": value.get(
                "moduleDeviceBindingStatus"
            ),
        }
        canonical = _hardware_io_mapping_projection(raw)
        if canonical != dict(value):
            raise MCPToolError("hardware I/O mapping receipt is not canonical")
        return canonical

    @classmethod
    def _workspace_compare_result_projection(
        cls,
        evidence_contract: Mapping[str, Any] | None,
        *,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _validate_schema_only_request(
            request_arguments,
            label="workspace comparison",
        )
        receipt = cls._validated_result_evidence(
            evidence_contract,
            schemas={"controlstudio-workspace-compare-evidence/v1"},
        )
        status = receipt.get("status")
        structure_status = receipt.get("structureStatus")
        change_count = _bounded_integer(
            receipt.get("structuralChangeCount"),
            "workspace comparison change count",
            maximum=1_000_000,
        )
        limitation_codes = _bounded_code_list(
            receipt.get("limitationCodes"),
            "workspace comparison limitations",
        )
        if (
            type(receipt.get("fileIdentical")) is not bool
            or not isinstance(status, str)
            or _SAFE_CODE.fullmatch(status) is None
            or structure_status is not None
            and (
                not isinstance(structure_status, str)
                or _SAFE_CODE.fullmatch(structure_status) is None
            )
            or not _is_sha256(receipt.get("compareDigest"))
        ):
            raise MCPToolError("workspace comparison evidence is invalid")
        return {
            "schema": "controlstudio-workspace-compare-tool-result/v1",
            "operation": "workspace.compare",
            "fileIdentical": receipt["fileIdentical"],
            "status": status,
            "structureStatus": structure_status,
            "structuralChangeCount": change_count,
            "limitationCodes": limitation_codes,
            "compareDigest": receipt["compareDigest"],
            "evidenceReceiptDigest": receipt["receiptDigest"],
            "rawResultOmitted": True,
        }

    @staticmethod
    def _change_semantics_projection(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise MCPToolError("change semantics are invalid")
        kind = _bounded_text(
            value.get("kind"),
            "change semantics kind",
            maximum=64,
        )
        status = _bounded_text(
            value.get("status"),
            "change semantics status",
            maximum=32,
        )
        if (
            not isinstance(kind, str)
            or _SAFE_CODE.fullmatch(kind) is None
            or status not in {"confirmed", "partial", "unknown"}
        ):
            raise MCPToolError("change semantics are invalid")
        projection: dict[str, Any] = {"kind": kind, "status": status}
        text_fields = {
            "device",
            "contact",
            "action",
            "expression",
            "record_kind",
            "instruction",
            "timer_device",
            "preset_text",
        }
        for field in text_fields:
            if field in value:
                projection[field] = _bounded_text(
                    value.get(field),
                    "change semantics text",
                    maximum=512,
                )
        arguments = value.get("arguments")
        if arguments is not None:
            projection["arguments"] = _ld_string_list(
                arguments,
                "change semantics arguments",
                maximum_items=16,
            )
        for field in ("preset_value",):
            if field in value:
                projection[field] = _bounded_integer(
                    value.get(field),
                    "change semantics integer",
                    maximum=2**31 - 1,
                )
        for field in (
            "time_base_ms",
            "duration_ms",
            "default_time_base_ms",
            "default_duration_ms",
        ):
            if field in value:
                projection[field] = _bounded_number(
                    value.get(field),
                    "change semantics duration",
                    minimum=0,
                    maximum=10**15,
                )
        if "project_timer_area_parameter_observed" in value:
            observed = value.get("project_timer_area_parameter_observed")
            if type(observed) is not bool:
                raise MCPToolError("change timer observation flag is invalid")
            projection["project_timer_area_parameter_observed"] = observed
        timer_area = value.get("timer_area")
        if timer_area is not None:
            areas = _hardware_timer_projection([timer_area])
            projection["timer_area"] = areas[0]
        return projection

    @staticmethod
    def _field_io_module_projection(value: Any) -> dict[str, Any]:
        required = {
            "rack_base",
            "slot",
            "module_id",
            "model",
            "module_type",
            "status",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise MCPToolError("field I/O module is invalid")
        model = _bounded_text(
            value.get("model"),
            "field I/O module model",
            maximum=128,
            nullable=True,
        )
        if model is not None and _HARDWARE_MODEL.fullmatch(model) is None:
            raise MCPToolError("field I/O module model is invalid")
        module_type = _bounded_text(
            value.get("module_type"),
            "field I/O module type",
            maximum=128,
            nullable=True,
        )
        if module_type is not None and (
            any(character in module_type for character in ("/", "\\", ":"))
            or any(ord(character) < 32 for character in module_type)
        ):
            raise MCPToolError("field I/O module type is invalid")
        if value.get("status") != "observed":
            raise MCPToolError("field I/O module status is invalid")
        return {
            "rack_base": _bounded_integer(
                value.get("rack_base"),
                "field I/O rack base",
                maximum=255,
            ),
            "slot": _bounded_integer(
                value.get("slot"),
                "field I/O slot",
                maximum=255,
            ),
            "module_id": _bounded_integer(
                value.get("module_id"),
                "field I/O module id",
                maximum=65_535,
            ),
            "model": model,
            "module_type": module_type,
            "status": "observed",
        }

    @staticmethod
    def _field_io_channel_projection(value: Any) -> dict[str, Any]:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"index", "status", "derivation"}
            or value.get("status") != "derived_from_verified_terminal_range"
            or value.get("derivation") != _FIELD_IO_CHANNEL_DERIVATION
        ):
            raise MCPToolError("field I/O channel is invalid")
        return {
            "index": _bounded_integer(
                value.get("index"),
                "field I/O channel index",
                maximum=65_535,
            ),
            "status": "derived_from_verified_terminal_range",
            "derivation": _FIELD_IO_CHANNEL_DERIVATION,
        }

    @staticmethod
    def _field_io_terminal_projection(
        value: Any,
        *,
        include_source: bool,
    ) -> dict[str, Any]:
        required = {"address_start", "address_end", "binding_kind", "status"}
        if include_source:
            required.add("source")
        if (
            not isinstance(value, Mapping)
            or set(value) != required
            or value.get("binding_kind") not in _HARDWARE_BINDING_KINDS
            or value.get("status") not in {"documented", "observed"}
            or include_source
            and not isinstance(value.get("source"), Mapping)
        ):
            raise MCPToolError("field I/O terminal range is invalid")
        start = _hardware_device(value.get("address_start"), "field I/O terminal start")
        end = _hardware_device(value.get("address_end"), "field I/O terminal end")
        if (
            re.fullmatch(r"P[0-9A-F]+", start) is None
            or re.fullmatch(r"P[0-9A-F]+", end) is None
            or int(end[1:], 16) < int(start[1:], 16)
        ):
            raise MCPToolError("field I/O terminal range is invalid")
        return {
            "address_start": start,
            "address_end": end,
            "binding_kind": value["binding_kind"],
            "status": value["status"],
        }

    @classmethod
    def _mne_field_io_binding_from_outer(
        cls,
        value: Any,
        *,
        expected_context_digest: str,
    ) -> dict[str, Any]:
        required = {
            "schema",
            "context_digest",
            "device",
            "direction",
            "point_kind",
            "module",
            "channel",
            "terminal_range",
            "proposal_scope",
            "binding_digest",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise MCPToolError("MNE patch field I/O binding is invalid")
        binding = dict(value)
        supplied_digest = binding.pop("binding_digest", None)
        proposal_scope = value.get("proposal_scope")
        direction = value.get("direction")
        point_kind = value.get("point_kind")
        device = _hardware_device(value.get("device"), "MNE field I/O device")
        if (
            value.get("schema") != "xg5000.project.field-io-binding/1.0"
            or value.get("context_digest") != expected_context_digest
            or re.fullmatch(r"P[0-9A-F]+", device) is None
            or direction not in {"input", "output"}
            or (direction == "input") != (point_kind == "digital_input")
            or point_kind not in _HARDWARE_POINT_KINDS
            or not isinstance(proposal_scope, Mapping)
            or set(proposal_scope)
            != {"offline_ld_design", "online_force_write_download", "reason"}
            or proposal_scope.get("offline_ld_design") != "allowed"
            or proposal_scope.get("online_force_write_download") != "unsupported"
            or proposal_scope.get("reason") != _FIELD_IO_PROPOSAL_REASON
            or not _is_sha256(supplied_digest)
            or supplied_digest != _canonical_digest(binding)
        ):
            raise MCPToolError("MNE patch field I/O binding is invalid")
        module = cls._field_io_module_projection(value.get("module"))
        channel = cls._field_io_channel_projection(value.get("channel"))
        terminal = cls._field_io_terminal_projection(
            value.get("terminal_range"),
            include_source=True,
        )
        device_offset = int(device[1:], 16)
        start_offset = int(terminal["address_start"][1:], 16)
        if not start_offset <= device_offset <= int(terminal["address_end"][1:], 16):
            raise MCPToolError("MNE field I/O device is outside its terminal range")
        return {
            "device": device,
            "direction": direction,
            "point_kind": point_kind,
            "module": module,
            "channel": channel,
            "terminal_range": terminal,
            "binding_digest": supplied_digest,
        }

    @staticmethod
    def _xgsim_assignments(value: Any, label: str) -> dict[str, int]:
        if (
            not isinstance(value, Mapping)
            or not value
            or len(value) > _MNE_SIMULATION_BIT_LIMIT
        ):
            raise MCPToolError(f"{label} is invalid")
        projected: dict[str, int] = {}
        for address, assignment in value.items():
            if (
                not isinstance(address, str)
                or re.fullmatch(r"[PM]\d{3,4}[0-9A-F]", address) is None
                or isinstance(assignment, bool)
                or assignment not in {0, 1}
            ):
                raise MCPToolError(f"{label} is invalid")
            projected[address] = assignment
        return dict(sorted(projected.items()))

    @classmethod
    def _mne_condition_assignment(
        cls,
        value: Any,
        *,
        asserted: bool,
    ) -> dict[str, int] | None:
        if not isinstance(value, Mapping):
            return None
        kind = value.get("kind")
        if kind == "contact":
            operand = value.get("operand")
            state = value.get("state")
            if (
                not isinstance(operand, str)
                or re.fullmatch(r"[PM]\d{3,4}[0-9A-F]", operand) is None
                or state not in {"on", "off"}
            ):
                return None
            enabled = 1 if state == "on" else 0
            return {operand: enabled if asserted else 1 - enabled}
        terms = value.get("terms")
        if kind not in {"all_of", "any_of"} or not isinstance(terms, list) or not terms:
            return None
        requested = (
            [asserted] * len(terms)
            if kind == "all_of"
            else ([True, *([False] * (len(terms) - 1))] if asserted else [False] * len(terms))
        )
        result: dict[str, int] = {}
        for term, term_asserted in zip(terms, requested, strict=True):
            nested = cls._mne_condition_assignment(term, asserted=term_asserted)
            if nested is None or any(
                address in result and result[address] != assignment
                for address, assignment in nested.items()
            ):
                return None
            result.update(nested)
        return dict(sorted(result.items()))

    @classmethod
    def _validate_mne_simulation_profile(
        cls,
        candidate: Mapping[str, Any],
        *,
        request_arguments: Mapping[str, Any],
    ) -> None:
        profile = candidate.get("simulation_profile")
        requested_scenario = request_arguments.get("simulation_scenario")
        if profile is None:
            if requested_scenario is not None:
                raise MCPToolError("MNE candidate omitted its requested simulation scenario")
            return
        simple_fields = {
            "schema",
            "normal_assignments",
            "deny_assignments",
            "restore_assignments",
            "output_addresses",
            "target_rung_digest",
            "program_count",
            "catalog_digest",
            "profile_digest",
        }
        scenario_fields = {
            "schema",
            "scenario",
            "output_addresses",
            "target_rung_digest",
            "program_count",
            "catalog_digest",
            "profile_digest",
        }
        patch = candidate.get("patch")
        target_rung = patch.get("target_rung") if isinstance(patch, Mapping) else None
        actions = target_rung.get("actions") if isinstance(target_rung, Mapping) else None
        outputs = (
            [item.get("operand") for item in actions]
            if isinstance(actions, list)
            and all(
                isinstance(item, Mapping)
                and item.get("command") == "OUT"
                and isinstance(item.get("operand"), str)
                for item in actions
            )
            else []
        )
        if (
            not isinstance(profile, Mapping)
            or frozenset(profile)
            not in {frozenset(simple_fields), frozenset(scenario_fields)}
            or profile.get("schema") != "controlstudio-mne-rung-xgsim-profile/v1"
            or not outputs
            or len(outputs) != len(set(outputs))
            or any(re.fullmatch(r"[PM]\d{3,4}[0-9A-F]", item) is None for item in outputs)
            or profile.get("output_addresses") != outputs
            or profile.get("target_rung_digest") != target_rung.get("rung_digest")
            or profile.get("program_count") != 1
            or not _is_sha256(profile.get("catalog_digest"))
            or not _is_sha256(profile.get("profile_digest"))
            or profile.get("profile_digest")
            != _canonical_digest(
                {key: item for key, item in profile.items() if key != "profile_digest"}
            )
        ):
            raise MCPToolError("MNE candidate simulation profile is invalid")
        if set(profile) == simple_fields:
            if requested_scenario is not None:
                raise MCPToolError("MNE candidate simulation profile differs from its request")
            normal = cls._mne_condition_assignment(
                target_rung.get("condition"),
                asserted=True,
            )
            denied = cls._mne_condition_assignment(
                target_rung.get("condition"),
                asserted=False,
            )
            if (
                normal is None
                or denied is None
                or normal == denied
                or cls._xgsim_assignments(
                    profile.get("normal_assignments"),
                    "MNE normal simulator assignments",
                )
                != normal
                or cls._xgsim_assignments(
                    profile.get("deny_assignments"),
                    "MNE deny simulator assignments",
                )
                != denied
                or cls._xgsim_assignments(
                    profile.get("restore_assignments"),
                    "MNE restore simulator assignments",
                )
                != normal
                or set(outputs).intersection(normal)
                or set(outputs).intersection(denied)
            ):
                raise MCPToolError("MNE candidate simulation profile is inconsistent")
            return
        scenario = profile.get("scenario")
        steps = scenario.get("steps") if isinstance(scenario, Mapping) else None
        if (
            not isinstance(scenario, Mapping)
            or set(scenario) != {"steps"}
            or not isinstance(steps, list)
            or not 2 <= len(steps) <= 16
        ):
            raise MCPToolError("MNE candidate simulation scenario is invalid")
        if not isinstance(requested_scenario, Mapping) or set(requested_scenario) != {
            "steps"
        }:
            raise MCPToolError("MNE candidate simulation scenario differs from its request")
        requested_steps = requested_scenario.get("steps")
        if not isinstance(requested_steps, list) or len(requested_steps) != len(steps):
            raise MCPToolError("MNE candidate simulation scenario differs from its request")
        normalized_requested_steps: list[dict[str, Any]] = []
        for requested_step in requested_steps:
            if not isinstance(requested_step, Mapping) or set(requested_step) != {
                "name",
                "drive",
                "observe",
            }:
                raise MCPToolError("MNE simulator scenario request step is invalid")
            normalized_requested_steps.append(
                {
                    "name": requested_step.get("name"),
                    "drive": cls._xgsim_assignments(
                        requested_step.get("drive"),
                        "MNE requested simulator scenario drive",
                    ),
                    "observe": cls._xgsim_assignments(
                        requested_step.get("observe"),
                        "MNE requested simulator scenario observation",
                    ),
                }
            )
        if scenario != {"steps": normalized_requested_steps}:
            raise MCPToolError("MNE candidate simulation scenario differs from its request")
        names: set[str] = set()
        drive_addresses: set[str] | None = None
        observed_addresses: set[str] = set()
        for step in steps:
            if not isinstance(step, Mapping) or set(step) != {"name", "drive", "observe"}:
                raise MCPToolError("MNE candidate simulation scenario step is invalid")
            name = step.get("name")
            drive = cls._xgsim_assignments(
                step.get("drive"),
                "MNE simulator scenario drive",
            )
            observe = cls._xgsim_assignments(
                step.get("observe"),
                "MNE simulator scenario observation",
            )
            if (
                not isinstance(name, str)
                or not name.strip()
                or len(name) > 512
                or name in names
                or set(drive).intersection(observe)
                or drive_addresses is not None
                and set(drive) != drive_addresses
                or not set(outputs).issubset(observe)
            ):
                raise MCPToolError("MNE candidate simulation scenario step is invalid")
            names.add(name)
            drive_addresses = set(drive)
            observed_addresses.update(observe)
        if (
            drive_addresses is None
            or len(drive_addresses) > 32
            or len(observed_addresses) > 32
        ):
            raise MCPToolError("MNE candidate simulation scenario is invalid")

    @classmethod
    def _change_target_result_projection(
        cls,
        evidence_contract: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        receipt = cls._validated_result_evidence(
            evidence_contract,
            schemas={"controlstudio-change-target-evidence/v2"},
        )
        required_text = (
            "analysisDigest",
            "programId",
            "programName",
            "currentValue",
            "recordKind",
            "networkId",
            "networkDigest",
        )
        if any(
            not isinstance(receipt.get(field), str)
            or not receipt[field]
            or len(receipt[field]) > 512
            for field in required_text
        ) or not all(
            _is_sha256(receipt.get(field))
            for field in ("analysisDigest", "networkDigest")
        ):
            raise MCPToolError("change target evidence identity is invalid")
        value_offset = _bounded_integer(
            receipt.get("valueTokenOffset"),
            "change target value offset",
            maximum=64 * 1024 * 1024,
        )
        device_fields: dict[str, list[str]] = {}
        for field in (
            "primaryDevices",
            "physicalIoCandidates",
            "physicalOutputCandidates",
        ):
            values = receipt.get(field)
            if (
                not isinstance(values, list)
                or len(values) > 512
                or len(set(values)) != len(values)
            ):
                raise MCPToolError("change target device set is invalid")
            device_fields[field] = [
                _hardware_device(item, "change target device") for item in values
            ]
        blockers = _bounded_code_list(
            receipt.get("blockingReasons"),
            "change target blockers",
        )
        runtime_blockers = _bounded_code_list(
            receipt.get("runtimeDeploymentBlockingReasons"),
            "change target runtime blockers",
        )
        if (
            type(receipt.get("offlineCandidateReady")) is not bool
            or receipt["offlineCandidateReady"] != (not blockers)
            or receipt.get("runtimeDeploymentReady") is not False
            or "online_runtime_deployment_unsupported" not in runtime_blockers
        ):
            raise MCPToolError("change target readiness is invalid")
        bindings_value = receipt.get("physicalIoBindings")
        if not isinstance(bindings_value, list) or len(bindings_value) > 512:
            raise MCPToolError("change target physical I/O bindings are invalid")
        bindings = [cls._change_binding_projection(item) for item in bindings_value]
        hardware_binding = cls._change_hardware_binding_projection(
            receipt.get("hardwareContextBinding")
        )
        return {
            "schema": "controlstudio-change-target-tool-result/v1",
            "operation": "program.change_target_analysis",
            "analysisDigest": receipt["analysisDigest"],
            "programId": receipt["programId"],
            "programName": receipt["programName"],
            "valueTokenOffset": value_offset,
            "currentValue": receipt["currentValue"],
            "proposedValue": (
                _bounded_text(
                    receipt.get("proposedValue"),
                    "change target proposed value",
                    maximum=512,
                    nullable=True,
                )
            ),
            "recordKind": receipt["recordKind"],
            "networkId": receipt["networkId"],
            "networkDigest": receipt["networkDigest"],
            "currentSemantics": cls._change_semantics_projection(
                receipt.get("currentSemantics")
            ),
            "proposedSemantics": cls._change_semantics_projection(
                receipt.get("proposedSemantics")
            ),
            **device_fields,
            "physicalIoBindings": bindings,
            "hardwareContextBinding": hardware_binding,
            "offlineCandidateReady": receipt["offlineCandidateReady"],
            "blockingReasons": blockers,
            "runtimeDeploymentReady": False,
            "runtimeDeploymentBlockingReasons": runtime_blockers,
            "evidenceReceiptDigest": receipt["receiptDigest"],
            "rawResultOmitted": True,
        }

    @classmethod
    def _change_binding_projection(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {
            "device",
            "direction",
            "pointKind",
            "module",
            "channel",
            "terminalRange",
            "bindingDigest",
        }:
            raise MCPToolError("change target field I/O binding is invalid")
        device = _hardware_device(value.get("device"), "field I/O device")
        direction = value.get("direction")
        point_kind = value.get("pointKind")
        if (
            direction not in {"input", "output"}
            or point_kind not in _HARDWARE_POINT_KINDS
            or (direction == "input") != (point_kind == "digital_input")
            or not _is_sha256(value.get("bindingDigest"))
        ):
            raise MCPToolError("change target field I/O binding is invalid")
        module = cls._field_io_module_projection(value.get("module"))
        channel = cls._field_io_channel_projection(value.get("channel"))
        terminal = cls._field_io_terminal_projection(
            value.get("terminalRange"),
            include_source=False,
        )
        device_offset = int(device[1:], 16)
        if not int(terminal["address_start"][1:], 16) <= device_offset <= int(
            terminal["address_end"][1:], 16
        ):
            raise MCPToolError("field I/O device is outside its terminal range")
        return {
            "device": device,
            "direction": direction,
            "pointKind": point_kind,
            "module": {
                "rackBase": _bounded_integer(
                    module["rack_base"],
                    "field I/O rack base",
                    maximum=255,
                ),
                "slot": _bounded_integer(
                    module["slot"],
                    "field I/O slot",
                    maximum=255,
                ),
                "moduleId": _bounded_integer(
                    module["module_id"],
                    "field I/O module id",
                    maximum=65_535,
                ),
                "model": module["model"],
                "status": "observed",
            },
            "channel": {
                "index": _bounded_integer(
                    channel["index"],
                    "field I/O channel index",
                    maximum=65_535,
                ),
                "status": "derived_from_verified_terminal_range",
            },
            "terminalRange": {
                "addressStart": _hardware_device(
                    terminal.get("address_start"),
                    "field I/O terminal start",
                ),
                "addressEnd": _hardware_device(
                    terminal.get("address_end"),
                    "field I/O terminal end",
                ),
                "bindingKind": terminal["binding_kind"],
                "status": terminal["status"],
            },
            "bindingDigest": value["bindingDigest"],
        }

    @staticmethod
    def _change_hardware_binding_projection(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping) or not _is_sha256(value.get("context_digest")):
            raise MCPToolError("change target hardware binding is invalid")
        required_context = _bounded_code_list(
            value.get("required_context"),
            "change target required hardware context",
            maximum_items=3,
        )
        blockers = _bounded_code_list(
            value.get("blocking_reasons"),
            "change target hardware blockers",
        )
        if (
            value.get("cpu_status") not in _HARDWARE_STATUS_VALUES
            or value.get("timer_area_status") not in {"observed", "unobserved"}
            or value.get("io_mapping_status") not in {"observed", "unobserved"}
            or type(value.get("external_wiring_observed")) is not bool
            or value.get("external_wiring_status") not in {"observed", "unobserved"}
        ):
            raise MCPToolError("change target hardware binding is invalid")
        return {
            "contextDigest": value["context_digest"],
            "cpuStatus": value["cpu_status"],
            "timerAreaStatus": value["timer_area_status"],
            "ioMappingStatus": value["io_mapping_status"],
            "externalWiringObserved": value["external_wiring_observed"],
            "externalWiringStatus": value["external_wiring_status"],
            "requiredContext": required_context,
            "blockingReasons": blockers,
        }

    @classmethod
    def _mne_candidate_result_projection(
        cls,
        raw_name: str,
        evidence_contract: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        receipt = cls._validated_result_evidence(
            evidence_contract,
            schemas={
                "controlstudio-mne-series-guard-candidate-evidence/v1",
                "controlstudio-mne-instruction-patch-candidate-evidence/v1",
                "controlstudio-mne-instruction-batch-patch-candidate-evidence/v1",
                "controlstudio-mne-rung-patch-candidate-evidence/v1",
            },
        )
        candidate = receipt.get("candidate")
        if not isinstance(candidate, Mapping) or not _is_sha256(
            candidate.get("candidate_digest")
        ):
            raise MCPToolError("MNE candidate evidence is invalid")
        return {
            "schema": "controlstudio-mne-candidate-tool-result/v1",
            "operation": raw_name,
            "ok": True,
            "verified": True,
            "candidate": deepcopy(dict(candidate)),
            "evidenceReceiptDigest": receipt["receiptDigest"],
            "rawResultOmitted": True,
        }

    @classmethod
    def _controller_result_projection(
        cls,
        raw_name: str,
        result: Any,
        *,
        request_arguments: Mapping[str, Any],
        expected_endpoint: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping) or not isinstance(
            expected_endpoint,
            Mapping,
        ):
            raise MCPToolError("controller result is not Host-bound")
        if set(expected_endpoint) != {"host", "port", "timeout_seconds"}:
            raise MCPToolError("Host-bound controller endpoint is invalid")
        expected_host = expected_endpoint.get("host")
        expected_port = expected_endpoint.get("port")
        expected_timeout = expected_endpoint.get("timeout_seconds")
        if (
            not isinstance(expected_host, str)
            or not expected_host
            or isinstance(expected_port, bool)
            or expected_port != 2004
        ):
            raise MCPToolError("Host-bound controller endpoint is invalid")
        _bounded_number(
            expected_timeout,
            "Host-bound controller timeout",
            minimum=0.1,
            maximum=10.0,
        )
        model_by_tool = {
            "controller.ls.status": LSControllerObservationV1,
            "controller.ls.read": LSControllerReadV1,
            "controller.ls.trace": LSControllerTraceV1,
        }
        model_type = model_by_tool.get(raw_name)
        if model_type is None:
            raise MCPToolError("controller projection tool is invalid")
        try:
            validated = model_type.model_validate(result)
        except ValueError as exc:
            raise MCPToolError("controller result contract is invalid") from exc
        raw_endpoint = validated.endpoint
        if (
            raw_endpoint.host != expected_host
            or raw_endpoint.port != expected_port
            or raw_endpoint.transport != "tcp"
            or raw_endpoint.protocol != "XGT_FENET"
            or validated.schema_version != _CONTROLLER_SCHEMA_VERSION
        ):
            raise MCPToolError("controller result endpoint changed")

        if raw_name == "controller.ls.status":
            if (
                set(request_arguments) - {"schema_version"}
                or request_arguments.get("schema_version", _CONTROLLER_SCHEMA_VERSION)
                != _CONTROLLER_SCHEMA_VERSION
            ):
                raise MCPToolError("controller status request is invalid")
            observation = validated
            assert isinstance(observation, LSControllerObservationV1)
            if observation.observed_at.utcoffset() is None:
                raise MCPToolError("controller observation timestamp is invalid")
            status = _controller_status_projection(
                observation.status.model_dump(mode="json", by_alias=True)
            )
            identity = _controller_identity_projection(
                observation.provisional_identity.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                endpoint_host=expected_host,
                endpoint_port=expected_port,
            )
            return {
                "schema_version": _CONTROLLER_SCHEMA_VERSION,
                "operation": raw_name,
                "observedAt": observation.model_dump(
                    mode="json", by_alias=True
                )["observedAt"],
                "readOnly": True,
                "status": status,
                "provisionalIdentity": identity,
                "rawResultOmitted": True,
            }

        data_type, variables = cls._controller_request_binding(
            raw_name,
            request_arguments,
        )
        if raw_name == "controller.ls.read":
            read = validated
            assert isinstance(read, LSControllerReadV1)
            if (
                read.data_type != data_type
                or len(read.values) != len(variables)
                or read.observed_at.utcoffset() is None
            ):
                raise MCPToolError("controller read differs from its request")
            values = [
                _controller_variable_projection(
                    item.model_dump(mode="json", by_alias=True),
                    data_type=data_type,
                    expected_variable=variable,
                )
                for item, variable in zip(read.values, variables, strict=True)
            ]
            return {
                "schema_version": _CONTROLLER_SCHEMA_VERSION,
                "operation": raw_name,
                "observedAt": read.model_dump(mode="json", by_alias=True)[
                    "observedAt"
                ],
                "dataType": data_type,
                "values": values,
                "readOnly": True,
                "strongEnoughForWrite": False,
                "rawResultOmitted": True,
            }

        trace = validated
        assert isinstance(trace, LSControllerTraceV1)
        trace_json = trace.model_dump(mode="json", by_alias=True)
        sample_count = request_arguments.get("sample_count")
        interval_ms = request_arguments.get("interval_ms")
        if (
            trace.data_type != data_type
            or trace.variables != variables
            or trace.sample_count != sample_count
            or trace.interval_ms != interval_ms
            or trace.mode_stable is not True
            or trace.identity_stable is not True
            or list(trace.limitations) != list(_CONTROLLER_TRACE_LIMITATIONS)
            or trace.capture_duration_ms > 60_000
            or (trace.completed_at - trace.started_at).total_seconds() > 60
            or trace.started_at.utcoffset() is None
            or trace.completed_at.utcoffset() is None
        ):
            raise MCPToolError("controller trace differs from its request or target")
        status = _controller_status_projection(
            trace.status_before.model_dump(mode="json", by_alias=True)
        )
        status_after = _controller_status_projection(
            trace.status_after.model_dump(mode="json", by_alias=True)
        )
        identity = _controller_identity_projection(
            trace.identity_before.model_dump(mode="json", by_alias=True),
            endpoint_host=expected_host,
            endpoint_port=expected_port,
        )
        identity_after = _controller_identity_projection(
            trace.identity_after.model_dump(mode="json", by_alias=True),
            endpoint_host=expected_host,
            endpoint_port=expected_port,
        )
        if status_after != status or identity_after != identity:
            raise MCPToolError("controller trace target changed")
        projected_samples: list[dict[str, Any]] = []
        previous_timestamp = trace.started_at
        previous_offset = -1
        for sample in trace.samples:
            sample_json = sample.model_dump(mode="json", by_alias=True)
            if (
                sample.observed_at.utcoffset() is None
                or sample.observed_at < previous_timestamp
                or sample.observed_at > trace.completed_at
                or sample.offset_ms < previous_offset
                or sample.offset_ms > trace.capture_duration_ms
                or len(sample.values) != len(variables)
            ):
                raise MCPToolError("controller trace sampling order is invalid")
            sample_values = [
                _controller_variable_projection(
                    item.model_dump(mode="json", by_alias=True),
                    data_type=data_type,
                    expected_variable=variable,
                )
                for item, variable in zip(sample.values, variables, strict=True)
            ]
            projected_samples.append(
                {
                    "index": sample.index,
                    "observedAt": sample_json["observedAt"],
                    "offsetMs": sample.offset_ms,
                    "values": sample_values,
                }
            )
            previous_timestamp = sample.observed_at
            previous_offset = sample.offset_ms

        expected_transitions: list[dict[str, Any]] = []
        change_counts = {variable: 0 for variable in variables}
        for sample_index in range(1, len(projected_samples)):
            previous_values = projected_samples[sample_index - 1]["values"]
            current_values = projected_samples[sample_index]["values"]
            for variable_index, variable in enumerate(variables):
                previous = previous_values[variable_index]
                current = current_values[variable_index]
                if previous["rawHex"] == current["rawHex"]:
                    continue
                change_counts[variable] += 1
                expected_transitions.append(
                    {
                        "variable": variable,
                        "fromSample": sample_index - 1,
                        "toSample": sample_index,
                        "observedAt": projected_samples[sample_index]["observedAt"],
                        "fromRawHex": previous["rawHex"],
                        "toRawHex": current["rawHex"],
                        "fromUnsignedValue": previous["unsignedValue"],
                        "toUnsignedValue": current["unsignedValue"],
                    }
                )

        expected_summaries: list[dict[str, Any]] = []
        for variable_index, variable in enumerate(variables):
            values = [sample["values"][variable_index] for sample in projected_samples]
            unsigned_values = [item["unsignedValue"] for item in values]
            expected_summaries.append(
                {
                    "variable": variable,
                    "firstUnsignedValue": unsigned_values[0],
                    "lastUnsignedValue": unsigned_values[-1],
                    "minimumUnsignedValue": min(unsigned_values),
                    "maximumUnsignedValue": max(unsigned_values),
                    "changeCount": change_counts[variable],
                    "changed": change_counts[variable] > 0,
                }
            )
        retained_transitions = expected_transitions[:256]
        raw_summaries = [
            item.model_dump(mode="json", by_alias=True) for item in trace.summaries
        ]
        raw_transitions = [
            item.model_dump(mode="json", by_alias=True) for item in trace.transitions
        ]
        if (
            raw_summaries != expected_summaries
            or trace.transition_count != len(expected_transitions)
            or trace.transition_returned_count != len(retained_transitions)
            or trace.transitions_truncated
            != (len(retained_transitions) != len(expected_transitions))
            or raw_transitions != retained_transitions
        ):
            raise MCPToolError("controller trace summary is inconsistent")
        return {
            "schema_version": _CONTROLLER_SCHEMA_VERSION,
            "operation": raw_name,
            "startedAt": trace_json["startedAt"],
            "completedAt": trace_json["completedAt"],
            "dataType": data_type,
            "variables": variables,
            "sampleCount": trace.sample_count,
            "intervalMs": trace.interval_ms,
            "captureDurationMs": trace.capture_duration_ms,
            "statusBefore": status,
            "statusAfter": status,
            "identityBefore": identity,
            "identityAfter": identity,
            "identityStable": True,
            "modeStable": True,
            "samples": projected_samples,
            "summaries": expected_summaries,
            "transitionCount": len(expected_transitions),
            "transitionReturnedCount": len(retained_transitions),
            "transitionsTruncated": len(retained_transitions)
            != len(expected_transitions),
            "transitions": retained_transitions,
            "limitations": list(_CONTROLLER_TRACE_LIMITATIONS),
            "readOnly": True,
            "strongEnoughForWrite": False,
            "rawResultOmitted": True,
        }

    @staticmethod
    def _controller_request_binding(
        raw_name: str,
        arguments: Mapping[str, Any],
    ) -> tuple[str, list[str]]:
        allowed = {"schema_version", "data_type", "variables"}
        if raw_name == "controller.ls.trace":
            allowed.update({"sample_count", "interval_ms"})
        if (
            set(arguments) - allowed
            or arguments.get("schema_version", _CONTROLLER_SCHEMA_VERSION)
            != _CONTROLLER_SCHEMA_VERSION
        ):
            raise MCPToolError("controller request shape is invalid")
        data_type_value = arguments.get("data_type")
        variables_value = arguments.get("variables")
        data_type = (
            data_type_value.strip().upper()
            if isinstance(data_type_value, str)
            else None
        )
        if (
            data_type not in _CONTROLLER_DATA_TYPES
            or not isinstance(variables_value, list)
            or not 1 <= len(variables_value) <= 16
        ):
            raise MCPToolError("controller request binding is invalid")
        variables: list[str] = []
        expected_marker = {
            "BIT": "X",
            "BYTE": "B",
            "WORD": "W",
            "DWORD": "D",
            "LWORD": "L",
        }[data_type]
        for raw_variable in variables_value:
            if (
                not isinstance(raw_variable, str)
                or raw_variable != raw_variable.strip()
                or _CONTROLLER_VARIABLE.fullmatch(raw_variable) is None
            ):
                raise MCPToolError("controller request variable is invalid")
            variable = raw_variable.upper()
            if variable[2] != expected_marker:
                raise MCPToolError("controller request variable width is invalid")
            variables.append(variable)
        if len(set(variables)) != len(variables):
            raise MCPToolError("controller request variables are ambiguous")
        if raw_name == "controller.ls.trace":
            sample_count = arguments.get("sample_count")
            interval_ms = arguments.get("interval_ms")
            if (
                isinstance(sample_count, bool)
                or not isinstance(sample_count, int)
                or not 2 <= sample_count <= 64
                or isinstance(interval_ms, bool)
                or not isinstance(interval_ms, int)
                or not 50 <= interval_ms <= 5_000
                or (sample_count - 1) * interval_ms > 30_000
            ):
                raise MCPToolError("controller trace request window is invalid")
        return data_type, variables

    @staticmethod
    def _controller_value_projection(
        value: Any,
        *,
        data_type: str,
        expected_variable: str,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {
            "variable",
            "rawHex",
            "unsignedValue",
        }:
            raise MCPToolError("controller value is invalid")
        variable = value.get("variable")
        raw_hex = value.get("rawHex")
        unsigned_value = value.get("unsignedValue")
        width = _CONTROLLER_DATA_TYPE_WIDTHS[data_type]
        if (
            not isinstance(variable, str)
            or variable != expected_variable
            or not isinstance(raw_hex, str)
            or re.fullmatch(r"[0-9A-F]+", raw_hex) is None
            or len(raw_hex) != width * 2
            or isinstance(unsigned_value, bool)
            or not isinstance(unsigned_value, int)
            or unsigned_value
            != int.from_bytes(bytes.fromhex(raw_hex), "little")
        ):
            raise MCPToolError("controller value encoding is invalid")
        return {
            "variable": expected_variable,
            "rawHex": raw_hex,
            "unsignedValue": unsigned_value,
        }


    @classmethod
    def _program_read_ld_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping) or not isinstance(
            expected_session_identity,
            Mapping,
        ):
            raise MCPToolError("program.read_ld evidence is not target-bound")
        missing_identity = [
            field
            for field in SESSION_IDENTITY_FIELDS
            if field not in expected_session_identity
        ]
        if missing_identity:
            raise MCPToolError("program.read_ld session identity is incomplete")
        cls._validate_session_result_binding(
            result,
            expected_session_identity=expected_session_identity,
            project_hash_fields=(
                "project_hash_before",
                "project_hash_after",
                "project_sha256_before",
                "project_sha256_after",
            ),
            require_project_path=True,
            require_session_id=True,
        )
        session_id = expected_session_identity["session_id"]
        pid = expected_session_identity["pid"]
        hwnd = expected_session_identity["hwnd"]
        project_path = expected_session_identity["project_path"]
        project_hash = expected_session_identity["project_hash"]
        program = result.get("program")
        if (
            result.get("operation") != "program.read_ld"
            or result.get("ok") is not True
            or result.get("verified") is not True
            or result.get("stable_repeat") is not True
            or result.get("project_unchanged") is not True
            or not _is_canonical_uuid(result.get("operation_id"))
            or set(request_arguments) - {"schema_version", "program_id"}
            or request_arguments.get("schema_version", "1.0") != "1.0"
            or not isinstance(session_id, str)
            or not session_id
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or isinstance(hwnd, bool)
            or not isinstance(hwnd, int)
            or hwnd <= 0
            or not isinstance(project_path, str)
            or not project_path
            or not _is_sha256(project_hash)
            or result.get("project_hash_before") != project_hash
            or result.get("project_hash_after") != project_hash
            or result.get("project_sha256_before") != project_hash
            or result.get("project_sha256_after") != project_hash
            or not _is_sha256(result.get("payload_sha256"))
            or not _is_sha256(result.get("decoded_program_sha256"))
            or not isinstance(program, Mapping)
        ):
            raise MCPToolError("program.read_ld evidence binding is invalid")
        projected_program = _project_program_identity(program, "read program")
        if projected_program["program_id"] != request_arguments.get("program_id"):
            raise MCPToolError("program.read_ld differs from its request")
        contract = {
            "schema": "controlstudio-program-read-ld-evidence/v1",
            "operation": "program.read_ld",
            "sessionId": session_id,
            "pid": pid,
            "hwnd": hwnd,
            "projectPath": project_path,
            "projectSha256": project_hash,
            "programId": projected_program["program_id"],
            "programName": projected_program["name"],
            "payloadSha256": result["payload_sha256"],
            "decodedProgramSha256": result["decoded_program_sha256"],
            "verified": True,
            "stableRepeat": True,
            "projectUnchanged": True,
        }
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    @classmethod
    def _project_structure_index_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        session_identity_fields = set(SESSION_IDENTITY_FIELDS)
        allowed_identity_shapes = {
            frozenset(session_identity_fields),
            frozenset(session_identity_fields | set(WORKSPACE_STATIC_IDENTITY_FIELDS)),
        }
        if (
            not isinstance(result, Mapping)
            or set(result) != _PROJECT_STRUCTURE_INDEX_ENVELOPE_FIELDS
            or not isinstance(expected_session_identity, Mapping)
            or frozenset(expected_session_identity) not in allowed_identity_shapes
        ):
            raise MCPToolError("project structure index envelope shape mismatched")
        bound_session_identity = {
            field: expected_session_identity[field] for field in SESSION_IDENTITY_FIELDS
        }
        expected_session_id = bound_session_identity["session_id"]
        expected_project_hash = bound_session_identity["project_hash"]
        structure_index = result.get("structure_index")
        supplied_index_digest = result.get("index_digest")
        receipt_path = result.get("receipt_path")
        operation_id = result.get("operation_id")
        try:
            canonical_operation_id = (
                str(uuid.UUID(operation_id))
                if isinstance(operation_id, str)
                else None
            )
        except ValueError:
            canonical_operation_id = None
        if (
            result.get("operation") != "project.structure_index"
            or result.get("ok") is not True
            or result.get("verified") is not True
            or result.get("project_unchanged") is not True
            or not isinstance(expected_session_id, str)
            or not expected_session_id
            or result.get("session_id") != expected_session_id
            or not _is_sha256(expected_project_hash)
            or result.get("project_hash_before") != expected_project_hash
            or result.get("project_hash_after") != expected_project_hash
            or not _is_sha256(supplied_index_digest)
            or not isinstance(structure_index, Mapping)
            or structure_index.get("index_digest") != supplied_index_digest
            or not isinstance(receipt_path, str)
            or not receipt_path
            or len(receipt_path) > 32_768
            or canonical_operation_id != operation_id
        ):
            raise MCPToolError("project structure index envelope binding is invalid")
        return build_structural_evidence_contract(
            "project.structure_index",
            structure_index,
            expected_session_identity=bound_session_identity,
            request_arguments=request_arguments,
        )

    @classmethod
    def _workspace_compare_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        expected_identity = _expected_workspace_static_identity(expected_session_identity)
        comparison = validate_workspace_compare_result(
            result,
            expected_workspace_id=str(expected_identity["workspace_id"]),
            expected_revision_id=str(expected_identity["revision_id"]),
        )
        if (
            comparison.source_kind != expected_identity["source_kind"]
            or comparison.source.sha256 != expected_identity["source_sha256"]
            or comparison.working_copy.sha256 != expected_identity["working_sha256"]
        ):
            raise MCPToolError("workspace comparison lineage changed")
        sections = comparison.structure.sections if comparison.structure is not None else {}
        contract = {
            "schema": "controlstudio-workspace-compare-evidence/v1",
            "workspaceId": comparison.workspace_id,
            "revisionId": comparison.revision_id,
            "sourceKind": comparison.source_kind,
            "sourceSha256": comparison.source.sha256,
            "workingSha256": comparison.working_copy.sha256,
            "fileIdentical": comparison.file_identical,
            "status": comparison.status,
            "structureStatus": (
                comparison.structure.status if comparison.structure is not None else None
            ),
            "structuralChangeCount": sum(
                item.added_count + item.removed_count + item.changed_count
                for item in sections.values()
            ),
            "limitationCodes": [item.code for item in comparison.limitations],
            "compareDigest": comparison.compare_digest,
        }
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    @classmethod
    def _hardware_context_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        context_fields = {
            "schema",
            "project_binding",
            "project",
            "cpu",
            "timer_areas",
            "timer_area_sentinels",
            "io_modules",
            "io_mapping",
            "observations",
            "external_wiring_observed",
            "blockers",
            "offline_candidate_ready",
            "context_digest",
        }
        if not isinstance(result, Mapping) or set(result) != context_fields:
            raise MCPToolError("hardware context returned no structured result")
        context = dict(result)
        supplied_digest = context.pop("context_digest", None)
        if (
            context.get("schema") != "xg5000.project.hardware-context/1.0"
            or not isinstance(supplied_digest, str)
            or supplied_digest != _canonical_digest(context)
        ):
            raise MCPToolError("hardware context digest is invalid")
        project_binding = result.get("project_binding")
        cpu = result.get("cpu")
        timer_areas = result.get("timer_areas")
        io_modules = result.get("io_modules")
        io_mapping = result.get("io_mapping")
        external_wiring_observed = result.get("external_wiring_observed")
        blockers = result.get("blockers")
        ready = result.get("offline_candidate_ready")
        if (
            not isinstance(project_binding, Mapping)
            or set(project_binding)
            != {
                "session_id",
                "pid",
                "hwnd",
                "project_path",
                "project_hash",
                "source_receipts",
            }
            or not isinstance(cpu, Mapping)
        ):
            raise MCPToolError("hardware context project or CPU binding is invalid")
        if expected_session_identity is not None and (
            not isinstance(expected_session_identity, Mapping)
            or any(
                field not in expected_session_identity
                for field in SESSION_IDENTITY_FIELDS
            )
            or any(
                project_binding.get(field) != expected_session_identity.get(field)
                for field in SESSION_IDENTITY_FIELDS
            )
        ):
            raise MCPToolError("hardware context target changed")
        if not isinstance(timer_areas, list) or any(
            not isinstance(item, Mapping) for item in timer_areas
        ):
            raise MCPToolError("hardware context timer areas are invalid")
        if not isinstance(io_modules, list) or any(
            not isinstance(item, Mapping) for item in io_modules
        ):
            raise MCPToolError("hardware context I/O modules are invalid")
        if not isinstance(io_mapping, Mapping) or not isinstance(
            external_wiring_observed,
            bool,
        ):
            raise MCPToolError("hardware context I/O mapping is invalid")
        if not isinstance(blockers, list) or any(
            not isinstance(item, str) or not item for item in blockers
        ):
            raise MCPToolError("hardware context blockers are invalid")
        if not isinstance(ready, bool):
            raise MCPToolError("hardware context readiness is invalid")

        if ready != (not blockers):
            raise MCPToolError("hardware context readiness contradicts its blockers")

        projected_cpu = _hardware_cpu_projection(cpu)
        projected_timer_areas = _hardware_timer_projection(timer_areas)
        projected_io_modules = _hardware_module_projection(io_modules)
        projected_io_mapping = _hardware_io_mapping_projection(io_mapping)
        projected_blockers = _bounded_code_list(
            blockers,
            "hardware context blockers",
        )
        contract: dict[str, Any] = {
            "schema": "controlstudio-hardware-context-evidence/v1",
            "contextDigest": supplied_digest,
            "projectBindingDigest": _canonical_digest(project_binding),
            "projectHash": project_binding.get("project_hash"),
            "cpu": projected_cpu,
            "timerAreas": projected_timer_areas,
            "ioModules": projected_io_modules,
            "ioMapping": projected_io_mapping,
            "externalWiringObserved": external_wiring_observed,
            "blockers": projected_blockers,
            "offlineCandidateReady": ready,
        }
        if (
            not isinstance(contract["projectHash"], str)
            or not contract["projectHash"]
        ):
            raise MCPToolError("hardware context is missing its project hash")
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    @classmethod
    def _workspace_hardware_context_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        expected_identity = _expected_workspace_static_identity(expected_session_identity)
        expected_workspace_id = expected_identity["workspace_id"]
        expected_revision_id = expected_identity["revision_id"]
        if not isinstance(result, Mapping) or set(result) != (
            _WORKSPACE_HARDWARE_CONTEXT_FIELDS | {"operation", "schema_version"}
        ):
            raise MCPToolError("workspace hardware context shape mismatched")
        unsigned = {
            name: result[name]
            for name in _WORKSPACE_HARDWARE_CONTEXT_FIELDS
            if name != "receipt_digest"
        }
        workspace_id = result.get("workspace_id")
        revision_id = result.get("revision_id")
        source_sha256 = result.get("source_sha256")
        working_sha256 = result.get("working_sha256")
        if (
            result.get("operation") != "workspace.hardware_context"
            or result.get("schema") != "xg5000.workspace-hardware-context/1.0"
            or workspace_id != expected_workspace_id
            or revision_id != expected_revision_id
            or result.get("source_kind") != expected_identity["source_kind"]
            or source_sha256 != expected_identity["source_sha256"]
            or working_sha256 != expected_identity["working_sha256"]
            or not all(_is_sha256(value) for value in (source_sha256, working_sha256))
            or result.get("receipt_digest") != _canonical_digest(unsigned)
        ):
            raise MCPToolError("workspace hardware context binding is invalid")
        hardware_context = result.get("hardware_context")
        if not isinstance(hardware_context, Mapping):
            raise MCPToolError("workspace hardware context is missing")
        context_contract = cls._hardware_context_evidence_contract(hardware_context)
        project_binding = hardware_context.get("project_binding")
        if (
            not isinstance(project_binding, Mapping)
            or project_binding.get("session_id")
            != f"workspace:{workspace_id}:{revision_id}"
            or project_binding.get("pid") != 0
            or project_binding.get("hwnd") != 0
            or project_binding.get("project_hash") != working_sha256
        ):
            raise MCPToolError("workspace hardware project binding is invalid")
        context_projection = scrub_agent_tool_result(
            {
                "cpu": context_contract["cpu"],
                "timerAreas": context_contract["timerAreas"],
                "ioModules": context_contract["ioModules"],
                "ioMapping": context_contract["ioMapping"],
            }
        )
        contract = {
            "schema": "controlstudio-workspace-hardware-context-evidence/v1",
            "workspaceId": workspace_id,
            "revisionId": revision_id,
            "sourceKind": result.get("source_kind"),
            "sourceSha256": source_sha256,
            "workingSha256": working_sha256,
            "contextDigest": context_contract["contextDigest"],
            **context_projection,
            "externalWiringObserved": context_contract["externalWiringObserved"],
            "blockers": context_contract["blockers"],
            "offlineCandidateReady": context_contract["offlineCandidateReady"],
        }
        if contract["sourceKind"] not in {
            "local_file",
            "restored_from_controller",
            "new_build",
        }:
            raise MCPToolError("workspace hardware source kind is invalid")
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    @classmethod
    def _workspace_structure_index_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected_identity = _expected_workspace_static_identity(expected_session_identity)
        if not isinstance(result, Mapping) or set(result) != (
            _WORKSPACE_STRUCTURE_INDEX_FIELDS | {"operation", "schema_version"}
        ):
            raise MCPToolError("workspace structure index shape mismatched")
        unsigned = {
            name: result[name]
            for name in _WORKSPACE_STRUCTURE_INDEX_FIELDS
            if name != "receipt_digest"
        }
        (
            workspace_id,
            revision_id,
            _source_kind,
            working_path,
            working_sha256,
            _provenance_digest,
        ) = _validated_workspace_static_binding(
            result.get("workspace_binding"),
            expected_identity=expected_identity,
            label="structure",
        )
        if (
            result.get("operation") != "workspace.structure_index"
            or result.get("schema") != "xg5000.workspace-structure-index/1.0"
            or result.get("receipt_digest") != _canonical_digest(unsigned)
        ):
            raise MCPToolError("workspace structure binding is invalid")
        structure_index = result.get("structure_index")
        return build_structural_evidence_contract(
            "project.structure_index",
            structure_index,
            expected_session_identity={
                "session_id": f"workspace:{workspace_id}:{revision_id}",
                "pid": 0,
                "hwnd": 0,
                "project_path": working_path,
                "project_hash": working_sha256,
            },
            request_arguments=request_arguments,
            allow_workspace_static_identity=True,
        )

    @classmethod
    def _workspace_search_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected_identity = _expected_workspace_static_identity(expected_session_identity)
        if not isinstance(result, Mapping) or set(result) != (
            _WORKSPACE_SEARCH_FIELDS | {"operation", "schema_version"}
        ):
            raise MCPToolError("workspace search shape mismatched")
        unsigned = {
            name: result[name]
            for name in _WORKSPACE_SEARCH_FIELDS
            if name != "receipt_digest"
        }
        (
            _workspace_id,
            _revision_id,
            _source_kind,
            _working_path,
            working_sha256,
            _provenance_digest,
        ) = _validated_workspace_static_binding(
            result.get("workspace_binding"),
            expected_identity=expected_identity,
            label="search",
        )
        project_search = result.get("project_search")
        if (
            result.get("operation") != "workspace.search"
            or result.get("schema") != "xg5000.workspace-search/1.0"
            or result.get("receipt_digest") != _canonical_digest(unsigned)
            or not isinstance(project_search, Mapping)
            or project_search.get("project_hash_before") != working_sha256
            or project_search.get("project_hash_after") != working_sha256
        ):
            raise MCPToolError("workspace search binding is invalid")
        return build_structural_evidence_contract(
            "project.search",
            project_search,
            request_arguments=request_arguments,
        )

    @classmethod
    def _change_target_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise MCPToolError("change target analysis returned no structured result")
        cls._validate_session_result_binding(
            result,
            expected_session_identity=expected_session_identity,
            project_hash_fields=("project_hash_before", "project_hash_after"),
            require_session_id=True,
            require_pid_hwnd=False,
        )
        allowed_arguments = {
            "schema_version",
            "program_id",
            "value_token_offset",
            "expected_value",
            "proposed_value",
            "hardware_context_digest",
        }
        if (
            set(request_arguments) - allowed_arguments
            or request_arguments.get("schema_version", "1.0") != "1.0"
            or not _is_canonical_uuid(result.get("operation_id"))
            or result.get("operation") != "program.change_target_analysis"
            or result.get("ok") is not True
            or result.get("verified") is not True
            or result.get("project_unchanged") is not True
        ):
            raise MCPToolError("change target analysis envelope is invalid")
        analysis = result.get("analysis")
        if not isinstance(analysis, Mapping):
            raise MCPToolError("change target analysis returned no receipt")
        canonical_analysis = dict(analysis)
        supplied_digest = canonical_analysis.pop("analysis_digest", None)
        if (
            not isinstance(supplied_digest, str)
            or supplied_digest != _canonical_digest(canonical_analysis)
            or result.get("analysis_digest") != supplied_digest
        ):
            raise MCPToolError("change target analysis receipt digest is invalid")
        target = analysis.get("target")
        current_semantics = analysis.get("current_semantics")
        proposed_semantics = analysis.get("proposed_semantics")
        impact = analysis.get("impact")
        project_binding = analysis.get("project_binding")
        hardware_context_binding = analysis.get("hardware_context_binding")
        blockers = analysis.get("blocking_reasons")
        runtime_deployment_ready = analysis.get("runtime_deployment_ready")
        runtime_deployment_blockers = analysis.get(
            "runtime_deployment_blocking_reasons"
        )
        if not all(
            isinstance(value, Mapping)
            for value in (
                target,
                current_semantics,
                impact,
                project_binding,
            )
        ):
            raise MCPToolError("change target analysis receipt is incomplete")
        if hardware_context_binding is not None and not isinstance(
            hardware_context_binding,
            Mapping,
        ):
            raise MCPToolError("change target hardware context binding is invalid")
        if proposed_semantics is not None and not isinstance(proposed_semantics, Mapping):
            raise MCPToolError("change target proposed semantics are invalid")
        if analysis.get("schema") != "xg5000.program.change-target-analysis/1.1":
            raise MCPToolError("change target analysis schema is unsupported")
        if not isinstance(blockers, list) or any(
            not isinstance(item, str) or not item for item in blockers
        ):
            raise MCPToolError("change target blockers are invalid")
        if (
            not isinstance(runtime_deployment_ready, bool)
            or runtime_deployment_ready is not False
            or not isinstance(runtime_deployment_blockers, list)
            or not runtime_deployment_blockers
            or any(
                not isinstance(item, str) or not item
                for item in runtime_deployment_blockers
            )
            or len(set(runtime_deployment_blockers)) != len(runtime_deployment_blockers)
            or "online_runtime_deployment_unsupported"
            not in runtime_deployment_blockers
        ):
            raise MCPToolError("change target runtime deployment boundary is invalid")
        offline_candidate_ready = analysis.get("offline_candidate_ready")
        if (
            not isinstance(offline_candidate_ready, bool)
            or offline_candidate_ready != (not blockers)
        ):
            raise MCPToolError("change target readiness and blockers are inconsistent")
        assert isinstance(target, Mapping)
        assert isinstance(current_semantics, Mapping)
        assert isinstance(impact, Mapping)
        assert isinstance(project_binding, Mapping)
        expected_project_hash = (
            expected_session_identity.get("project_hash")
            if isinstance(expected_session_identity, Mapping)
            else None
        )
        requested_program_id = request_arguments.get("program_id")
        requested_offset = request_arguments.get("value_token_offset")
        requested_value = request_arguments.get("expected_value")
        requested_proposed = request_arguments.get("proposed_value")
        requested_hardware = request_arguments.get("hardware_context_digest")
        if (
            project_binding.get("project_hash") != expected_project_hash
            or target.get("program_id") != requested_program_id
            or target.get("value_token_offset") != requested_offset
            or target.get("value") != requested_value
            or analysis.get("proposed_value") != requested_proposed
            or (
                hardware_context_binding.get("context_digest")
                if isinstance(hardware_context_binding, Mapping)
                else None
            )
            != requested_hardware
        ):
            raise MCPToolError("change target analysis differs from its request")
        physical_io_candidates = impact.get("physical_io_candidates")
        physical_io_hardware_bindings = impact.get("physical_io_hardware_bindings")
        if (
            not isinstance(physical_io_candidates, list)
            or any(not isinstance(item, str) or not item for item in physical_io_candidates)
            or len(set(physical_io_candidates)) != len(physical_io_candidates)
            or not isinstance(physical_io_hardware_bindings, list)
            or any(not isinstance(item, Mapping) for item in physical_io_hardware_bindings)
        ):
            raise MCPToolError("change target physical I/O binding evidence is invalid")
        candidate_devices = set(physical_io_candidates)
        observed_devices: set[str] = set()
        unresolved_devices: set[str] = set()
        field_io_bindings: list[dict[str, Any]] = []
        for item in physical_io_hardware_bindings:
            assert isinstance(item, Mapping)
            device = item.get("device")
            binding_status = item.get("binding_status")
            if (
                not isinstance(device, str)
                or not device
                or device not in candidate_devices
                or device in observed_devices
                or binding_status not in {"bound", "unresolved", "ambiguous"}
            ):
                raise MCPToolError("change target physical I/O status evidence is invalid")
            observed_devices.add(device)
            binding = item.get("field_io_binding")
            if binding_status != "bound":
                if binding is not None:
                    raise MCPToolError("unresolved physical I/O cannot contain a field binding")
                if binding_status == "unresolved" and (
                    not isinstance(item.get("reason"), str) or not item.get("reason")
                ):
                    raise MCPToolError("unresolved physical I/O omitted its reason")
                expected_directions = item.get("expected_directions")
                if binding_status == "ambiguous" and (
                    not isinstance(expected_directions, list)
                    or len(expected_directions) < 2
                    or any(
                        not isinstance(direction, str) or not direction
                        for direction in expected_directions
                    )
                ):
                    raise MCPToolError("ambiguous physical I/O directions are invalid")
                unresolved_devices.add(device)
                continue
            if not isinstance(binding, Mapping) or not isinstance(
                requested_hardware, str
            ):
                raise MCPToolError("bound physical I/O omitted its field binding")
            safe_binding = cls._mne_field_io_binding_from_outer(
                binding,
                expected_context_digest=requested_hardware,
            )
            if safe_binding["device"] != device:
                raise MCPToolError("change target field I/O binding is invalid")
            # The Agent needs typed terminal identity, not a project path or
            # raw XML/profile source material.
            field_io_bindings.append(
                {
                    "device": safe_binding["device"],
                    "direction": safe_binding["direction"],
                    "pointKind": safe_binding["point_kind"],
                    "module": safe_binding["module"],
                    "channel": safe_binding["channel"],
                    "terminalRange": safe_binding["terminal_range"],
                    "bindingDigest": safe_binding["binding_digest"],
                }
            )
        # Before hardware is read, MCP can identify affected physical devices
        # but cannot bind their terminals. Keep that blocked analysis visible
        # so the Agent can collect the missing context and reanalyze.
        hardware_not_observed = (
            hardware_context_binding is None
            and not offline_candidate_ready
            and "hardware_context_unobserved" in blockers
            and not observed_devices
        )
        if observed_devices != candidate_devices and not hardware_not_observed:
            raise MCPToolError(
                "every physical I/O candidate must have one typed binding status"
            )
        unresolved_blocker_prefixes = (
            "device_not_observed",
            "io_ld_role_ambiguous",
            "io_module_rack_slot_channel_unresolved",
        )
        for device in unresolved_devices:
            if not any(
                f"{prefix}:{device}" in runtime_deployment_blockers
                for prefix in unresolved_blocker_prefixes
            ):
                raise MCPToolError(
                    "unresolved physical I/O candidate has no explicit runtime blocker"
                )
        contract: dict[str, Any] = {
            "schema": "controlstudio-change-target-evidence/v2",
            "analysisDigest": supplied_digest,
            "projectBindingDigest": _canonical_digest(project_binding),
            "programId": target.get("program_id"),
            "programName": target.get("program_name"),
            "valueTokenOffset": target.get("value_token_offset"),
            "currentValue": target.get("value"),
            "proposedValue": analysis.get("proposed_value"),
            "recordKind": target.get("record_kind"),
            "visualRow": target.get("visual_row"),
            "column": target.get("column"),
            "networkId": target.get("network_id"),
            "networkDigest": target.get("network_digest"),
            "networkIndex": target.get("network_index"),
            "networkSemanticStatus": target.get("network_semantic_status"),
            "rungId": target.get("rung_id"),
            "rungRole": target.get("rung_role"),
            "rungSemanticStatus": target.get("rung_semantic_status"),
            "currentSemantics": dict(current_semantics),
            "proposedSemantics": (
                dict(proposed_semantics)
                if isinstance(proposed_semantics, Mapping)
                else None
            ),
            "primaryDevices": list(impact.get("primary_devices") or []),
            "physicalIoCandidates": list(physical_io_candidates),
            "physicalIoBindings": field_io_bindings,
            "physicalOutputCandidates": list(
                impact.get("physical_output_candidates") or []
            ),
            "multipleWriterDevices": list(
                impact.get("multiple_writer_devices") or []
            ),
            "hardwareContextBinding": (
                dict(hardware_context_binding)
                if isinstance(hardware_context_binding, Mapping)
                else None
            ),
            "offlineCandidateReady": offline_candidate_ready,
            "blockingReasons": list(blockers),
            "runtimeDeploymentReady": runtime_deployment_ready,
            "runtimeDeploymentBlockingReasons": list(
                runtime_deployment_blockers
            ),
        }
        required_text = (
            "analysisDigest",
            "projectBindingDigest",
            "programId",
            "programName",
            "currentValue",
            "recordKind",
            "networkId",
            "networkDigest",
        )
        if any(not isinstance(contract[field], str) or not contract[field] for field in required_text):
            raise MCPToolError("change target evidence is missing exact native identity")
        if (
            not isinstance(contract["valueTokenOffset"], int)
            or isinstance(contract["valueTokenOffset"], bool)
            or contract["valueTokenOffset"] < 0
        ):
            raise MCPToolError("change target evidence has an invalid value token offset")
        if isinstance(hardware_context_binding, Mapping):
            hardware_digest = hardware_context_binding.get("context_digest")
            if (
                hardware_context_binding.get("schema")
                != "xg5000.project.hardware-context/1.0"
                or not isinstance(hardware_digest, str)
                or len(hardware_digest) != 64
            ):
                raise MCPToolError("change target evidence has an invalid hardware binding")
        elif not is_hardware_free_m_operand_target(
            contract,
            {"value": contract["currentValue"], "semantics": contract["currentSemantics"]},
            {"value": contract["proposedValue"], "semantics": contract["proposedSemantics"]},
        ) and (
            contract["offlineCandidateReady"] is True
            or "hardware_context_unobserved" not in blockers
        ):
            raise MCPToolError("change target evidence omitted required hardware context")
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    @staticmethod
    def _validate_mne_instruction_request_patch(
        candidate_patch: Any,
        request_patch: Any,
    ) -> None:
        if (
            not isinstance(candidate_patch, Mapping)
            or not isinstance(request_patch, Mapping)
            or set(request_patch) != {"operation", "anchor", "instruction"}
        ):
            raise MCPToolError("MNE instruction patch request is invalid")
        requested_anchor = request_patch.get("anchor")
        candidate_anchor = candidate_patch.get("anchor")
        requested_instruction = request_patch.get("instruction")
        candidate_instruction = candidate_patch.get("instruction")
        if (
            not isinstance(requested_anchor, Mapping)
            or set(requested_anchor) != {"command", "operand"}
            or not isinstance(candidate_anchor, Mapping)
            or candidate_patch.get("operation") != request_patch.get("operation")
            or candidate_anchor.get("command") != requested_anchor.get("command")
            or candidate_anchor.get("operand") != requested_anchor.get("operand")
        ):
            raise MCPToolError("MNE instruction patch differs from its request")
        if requested_instruction is None:
            if candidate_instruction is not None:
                raise MCPToolError("MNE instruction patch differs from its request")
            return
        if (
            not isinstance(requested_instruction, Mapping)
            or set(requested_instruction) != {"command", "operand"}
            or not isinstance(candidate_instruction, Mapping)
            or candidate_instruction.get("command")
            != requested_instruction.get("command")
            or candidate_instruction.get("operand")
            != requested_instruction.get("operand")
        ):
            raise MCPToolError("MNE instruction patch differs from its request")

    @staticmethod
    def _validate_mne_rung_request_patch(
        candidate_patch: Any,
        request_arguments: Mapping[str, Any],
    ) -> None:
        if not isinstance(candidate_patch, Mapping):
            raise MCPToolError("MNE rung patch request is invalid")
        requested_anchor = request_arguments.get("anchor")
        candidate_anchor = candidate_patch.get("anchor")
        if (
            not isinstance(requested_anchor, Mapping)
            or set(requested_anchor) != {"command", "operand"}
            or not isinstance(candidate_anchor, Mapping)
            or candidate_patch.get("operation") != request_arguments.get("operation")
            or candidate_anchor.get("command") != requested_anchor.get("command")
            or candidate_anchor.get("operand") != requested_anchor.get("operand")
        ):
            raise MCPToolError("MNE rung patch differs from its request")
        requested_condition = request_arguments.get("condition")
        requested_actions = request_arguments.get("actions")
        target_rung = candidate_patch.get("target_rung")
        if requested_condition is None and requested_actions is None:
            if target_rung is not None:
                raise MCPToolError("MNE rung patch differs from its request")
            return
        if not isinstance(target_rung, Mapping):
            raise MCPToolError("MNE rung patch target is invalid")
        candidate_actions = target_rung.get("actions")
        projected_actions = (
            [
                {"command": item.get("command"), "operand": item.get("operand")}
                for item in candidate_actions
            ]
            if isinstance(candidate_actions, list)
            and all(isinstance(item, Mapping) for item in candidate_actions)
            else None
        )
        if (
            target_rung.get("condition") != requested_condition
            or projected_actions != requested_actions
        ):
            raise MCPToolError("MNE rung patch differs from its request")

    @classmethod
    def _mne_patch_candidate_evidence_contract(
        cls,
        raw_name: str,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
        request_arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        result_fields = {
            "session_id",
            "operation",
            "ok",
            "verified",
            "candidate",
            "hardware_context_digest",
            "field_io_bindings",
            "project_unchanged",
            "operation_id",
        }
        if not isinstance(result, Mapping) or set(result) != result_fields:
            raise MCPToolError("MNE patch compiler returned no structured result")
        expected_session_id = (
            expected_session_identity.get("session_id")
            if isinstance(expected_session_identity, Mapping)
            else None
        )
        expected_project_hash = (
            expected_session_identity.get("project_hash")
            if isinstance(expected_session_identity, Mapping)
            else None
        )
        if (
            not isinstance(expected_session_id, str)
            or not expected_session_id
            or not _is_sha256(expected_project_hash)
            or result.get("session_id") != expected_session_id
            or result.get("operation") != raw_name
            or result.get("ok") is not True
            or result.get("verified") is not True
            or result.get("project_unchanged") is not True
            or not _is_canonical_uuid(result.get("operation_id"))
        ):
            raise MCPToolError("MNE patch compiler target binding is invalid")
        specific_arguments = {
            "program.compile_mne_series_guard_candidate": {
                "output_operand",
                "guard_command",
                "guard_operand",
            },
            "program.compile_mne_instruction_patch_candidate": {
                "operation",
                "anchor",
                "instruction",
            },
            "program.compile_mne_instruction_batch_patch_candidate": {"patches"},
            "program.compile_mne_rung_patch_candidate": {
                "operation",
                "anchor",
                "condition",
                "actions",
                "simulation_scenario",
            },
        }
        allowed_arguments = {
            "schema_version",
            "program_id",
            "program_name",
            "timeout_seconds",
            "hardware_context_digest",
        } | specific_arguments.get(raw_name, set())
        timeout = request_arguments.get("timeout_seconds", 30.0)
        if (
            set(request_arguments) - allowed_arguments
            or request_arguments.get("schema_version", "1.0") != "1.0"
        ):
            raise MCPToolError("MNE patch compiler request shape is invalid")
        _bounded_number(
            timeout,
            "MNE patch compiler timeout",
            minimum=1.0,
            maximum=60.0,
        )
        if not isinstance(result.get("candidate"), Mapping):
            raise MCPToolError("MNE patch compiler returned no candidate receipt")
        contract_by_tool = {
            "program.compile_mne_series_guard_candidate": (
                MneSeriesGuardCandidateV3,
                "controlstudio-mne-series-guard-candidate-evidence/v1",
            ),
            "program.compile_mne_instruction_patch_candidate": (
                MneInstructionPatchCandidateV4,
                "controlstudio-mne-instruction-patch-candidate-evidence/v1",
            ),
            "program.compile_mne_instruction_batch_patch_candidate": (
                MneInstructionBatchPatchCandidateV5,
                "controlstudio-mne-instruction-batch-patch-candidate-evidence/v1",
            ),
            "program.compile_mne_rung_patch_candidate": (
                MneRungPatchCandidateV6,
                "controlstudio-mne-rung-patch-candidate-evidence/v1",
            ),
        }
        model, schema = contract_by_tool[raw_name]
        try:
            candidate = model.model_validate(result["candidate"]).model_dump(
                mode="json",
                by_alias=False,
            )
        except ValueError as exc:
            raise MCPToolError("MNE patch compiler candidate is invalid") from exc
        if (
            candidate.get("project_sha256") != expected_project_hash
            or candidate.get("program_id") != request_arguments.get("program_id")
            or candidate.get("program_name") != request_arguments.get("program_name")
            or candidate.get("hardware_context_digest")
            != request_arguments.get("hardware_context_digest")
        ):
            raise MCPToolError("MNE patch compiler candidate differs from its request")
        candidate_patch = candidate.get("patch")
        if (
            raw_name != "program.compile_mne_instruction_batch_patch_candidate"
            and not isinstance(candidate_patch, Mapping)
        ):
            raise MCPToolError("MNE patch compiler candidate patch is invalid")
        if raw_name == "program.compile_mne_series_guard_candidate":
            for field in ("output_operand", "guard_command", "guard_operand"):
                if candidate_patch.get(field) != request_arguments.get(field):
                    raise MCPToolError("MNE series guard differs from its request")
        elif raw_name == "program.compile_mne_instruction_patch_candidate":
            cls._validate_mne_instruction_request_patch(
                candidate_patch,
                {
                    field: request_arguments.get(field)
                    for field in ("operation", "anchor", "instruction")
                },
            )
        elif raw_name == "program.compile_mne_instruction_batch_patch_candidate":
            requested_patches = request_arguments.get("patches")
            candidate_patches = candidate.get("patches")
            if (
                not isinstance(requested_patches, list)
                or not isinstance(candidate_patches, list)
                or len(candidate_patches) != len(requested_patches)
            ):
                raise MCPToolError("MNE instruction batch differs from its request")
            for candidate_item, requested_item in zip(
                candidate_patches,
                requested_patches,
                strict=True,
            ):
                cls._validate_mne_instruction_request_patch(
                    candidate_item,
                    requested_item,
                )
        else:
            cls._validate_mne_rung_request_patch(
                candidate_patch,
                request_arguments,
            )
            cls._validate_mne_simulation_profile(
                candidate,
                request_arguments=request_arguments,
            )
        contract = {
            "schema": schema,
            "candidate": candidate,
        }
        hardware_context_digest = result.get("hardware_context_digest")
        field_io_bindings = result.get("field_io_bindings")
        if hardware_context_digest is not None or field_io_bindings:
            if (
                not isinstance(hardware_context_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", hardware_context_digest) is None
                or hardware_context_digest
                != request_arguments.get("hardware_context_digest")
                or not isinstance(field_io_bindings, list)
                or not 1 <= len(field_io_bindings) <= _MNE_FIELD_IO_BINDING_LIMIT
            ):
                raise MCPToolError("MNE patch physical I/O binding is invalid")
            safe_bindings: list[dict[str, Any]] = []
            for binding in field_io_bindings:
                safe_bindings.append(
                    cls._mne_field_io_binding_from_outer(
                        binding,
                        expected_context_digest=hardware_context_digest,
                    )
                )
            binding_keys = [
                (item["device"], item["direction"]) for item in safe_bindings
            ]
            if binding_keys != sorted(binding_keys) or len(binding_keys) != len(
                set(binding_keys)
            ):
                raise MCPToolError("MNE patch field I/O bindings are not uniquely ordered")
            candidate_bindings = candidate.get("field_io_bindings")
            if (
                candidate.get("hardware_context_digest") != hardware_context_digest
                or not isinstance(candidate_bindings, list)
                or candidate_bindings != safe_bindings
            ):
                raise MCPToolError(
                    "MNE patch candidate hardware binding differs from compiler evidence"
                )
            contract["hardwareContextDigest"] = hardware_context_digest
            contract["fieldIoBindings"] = [
                {
                    "device": item["device"],
                    "direction": item["direction"],
                    "pointKind": item["point_kind"],
                    "module": item["module"],
                    "channel": item["channel"],
                    "terminalRange": item["terminal_range"],
                    "bindingDigest": item["binding_digest"],
                }
                for item in safe_bindings
            ]
        elif candidate.get("hardware_context_digest") is not None or candidate.get(
            "field_io_bindings"
        ):
            raise MCPToolError("MNE patch candidate has an unproven hardware binding")
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    def _public_schema(self, raw_name: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        engineering_tool = raw_name in AGENT_TOOL_REQUIRED_IDENTITY_FIELDS
        controller_tool = raw_name in AGENT_CONTROLLER_MCP_TOOLS
        if not engineering_tool and not controller_tool:
            raise MCPStartupError(f"agent tool has no Host target policy: {raw_name}")
        if engineering_tool:
            identity = self._gateway.agent_session_identity_snapshot
            required_identity = AGENT_TOOL_REQUIRED_IDENTITY_FIELDS[raw_name]
            if identity is None or any(
                not isinstance(identity.get(field), (str, int)) or identity.get(field) == ""
                for field in required_identity
            ):
                return None
        if controller_tool and not self._gateway.controller_observation_current:
            return None
        result = deepcopy(schema)
        properties = result.get("properties")
        required = result.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise MCPStartupError(f"{raw_name} has an invalid model-facing input schema")
        blocked = set(SESSION_IDENTITY_FIELDS)
        blocked.update(AGENT_TOOL_REQUIRED_IDENTITY_FIELDS.get(raw_name, ()))
        if controller_tool:
            blocked.update(CONTROLLER_ENDPOINT_FIELDS)
        if raw_name == "project.snapshot":
            # Agent snapshots are read-only evidence.  The Host-owned change
            # executor alone can request a private MNE snapshot or bind it to a
            # durable transaction after explicit approval.
            blocked.update(
                {
                    "workspace_id",
                    "begin_change_transaction",
                    "plan_kind",
                    "host_transaction_id",
                }
            )
        for field in blocked:
            properties.pop(field, None)
        result["required"] = [field for field in required if field not in blocked]
        if raw_name == "device.references":
            if "device" not in properties:
                raise MCPStartupError("device.references has no public device argument")
            if "device" not in result["required"]:
                result["required"].append("device")
        return result

    @staticmethod
    def _event_payload(request: ExternalToolRequestV1) -> dict[str, Any]:
        result: dict[str, Any] = {
            "tool_name": request.tool_name,
            "request_id": request.request_id,
        }
        if request.tool_call_id is not None:
            result["tool_call_id"] = request.tool_call_id
        return result

    async def _emit_failure(
        self,
        sink: ToolEventSink,
        request: ExternalToolRequestV1,
        failure_kind: str,
    ) -> None:
        ExternalToolResultV1(
            request_id=request.request_id,
            tool_name=request.tool_name,
            status="failed",
            failure_kind=failure_kind,
        )
        failure_reasons = {
            "tool_error": "도구가 요청을 처리하지 못했습니다.",
            "host_policy": "Host 안전 정책이 도구 실행을 허용하지 않았습니다.",
            "transport": "도구 실행 서비스와 통신하지 못했습니다.",
            "cancelled": "도구 실행이 취소되었습니다.",
        }
        payload = {
            **self._event_payload(request),
            "failure_kind": failure_kind,
            "failure_reason": failure_reasons.get(
                failure_kind,
                "도구 실행을 완료하지 못했습니다.",
            ),
        }
        await self._emit_event(sink, "tool.failed", payload)

    @staticmethod
    async def _emit_event(sink: ToolEventSink, event_type: str, payload: dict[str, Any]) -> None:
        try:
            await sink(event_type, payload)
        except Exception as exc:
            logger.error(
                "Agent tool event sink failed (event_type=%s, exception_type=%s)",
                event_type,
                type(exc).__name__,
            )
            raise
