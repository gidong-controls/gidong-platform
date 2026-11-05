"""Host-side supervisor for the versioned Pydantic AI Agent sidecar."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import shlex
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, get_args

from controlstudio_contracts import (
    AGENT_PROTOCOL_VERSION_V1,
    ExternalToolRequestV1,
    ProgramCheckFailureObservationV1,
)
from controlstudio_contracts.checkpoint_transfer import CheckpointTransfer, checkpoint_chunks
from controlstudio_contracts.native_failure import (
    NativeFailureProtocolError,
    validate_native_failure,
)
from pydantic import ValidationError

from .planning_work_memory import WorkItem

_SAFE_DESIGN_DIAGNOSTIC_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_SAFE_DESIGN_DIAGNOSTIC_PATH = re.compile(r"(?:|/(?:[A-Za-z0-9_.~-]+/?)+)\Z")
_SAFE_DESIGN_POINTER_SEGMENT = re.compile(r"(?:[A-Za-z0-9_.-]|~[01])+\Z")
_DESIGN_POINTER_ROOTS = frozenset(
    {
        "changes",
        "narrative",
        "technicalConfiguration",
        "technical_configuration",
        "indexAcceptanceBindings",
        "index_acceptance_bindings",
        "ioPoints",
        "io_points",
        "operatingModes",
        "operating_modes",
        "controlNetworks",
        "control_networks",
        "ladderRungs",
        "ladder_rungs",
        "timers",
        "sequences",
        "alarms",
        "recoveryRules",
        "recovery_rules",
        "safetyRules",
        "safety_rules",
        "native_design",
        "simulationScenario",
        "simulation_scenario",
        "nativeSymbols",
        "native_symbols",
    }
)
_DESIGN_POINTER_SENSITIVE_SEGMENTS = frozenset(
    {
        "access_token",
        "accesstoken",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "privatekey",
        "secret",
        "secret_key",
        "secretkey",
        "session_token",
        "sessiontoken",
        "token",
    }
)
_MAX_DESIGN_POINTER_LENGTH = 256
_MAX_DESIGN_POINTER_SEGMENTS = 32
_WORK_PLAN_ITEM_STATUSES = tuple(
    status
    for status in get_args(WorkItem.model_fields["status"].annotation)
    if isinstance(status, str)
)
_DESIGN_POINTER_FIELDS = frozenset(
    """
    changes narrative nativeDesign technicalConfiguration indexAcceptanceBindings
    action activation address affectedOutputSignalIds alarmId alarms allowedTransitions
    basis classification collectionScope completeAll condition controlNetworks cpuModel
    description destinationAddress drive durationMs enableAll evidence holdMs instruction
    ioPoints kind ladderRungs language latchSignalId lhs modeId modes name nativeType networkId
    normalState observe operatingModes outputSignalId permitAll permitWhen plcSeries
    presetTicks programName reason recoveryId recoveryRules relation resetAlarmIds resetAll
    resetSequenceIds restartPolicy rhs ruleId rungId safeState safetyRules schemaVersion
    sequenceId sequences signalId signals simulationScenario sourceConstant sourceId
    sourceType startAll state status stepId steps symbol targetId targetType terms
    timeBaseMs timerId timers title triggerAll value whenAll nativeSymbols dataType comment
    operation section itemId beforeItemId expectedRevision
    summary requirements assumptions openQuestions acceptanceCriteria scope
    requirementSources projectName objective userRequirements designProposals
    hardwareAndNetwork ioAndDevices sequence safetyInvariants alarmsAndRecovery
    programStructure validationPlan notApplicable messageId quote field
    contact allOf anyOf compare dataRegister constant out set rst ton mov incp
    """.casefold().split()
)
_MAX_DESIGN_FAILURE_DIAGNOSTICS = 4
_MAX_PRIOR_DESIGN_FAILURE_DIAGNOSTICS = 8
_SAFE_PRIOR_DESIGN_FAILURE_REASON_CODES = frozenset(
    {
        "design_revision_conflict",
        "design_edit_not_applied",
        "design_edit_unchanged",
        "design_edit_scope_rejected",
        "design_edit_schema_invalid",
    }
)
_PRIOR_DESIGN_FAILURE_KEYS = frozenset(
    {
        "kind",
        "runId",
        "eventSequence",
        "eventCreatedAt",
        "reasonCode",
        "diagnostics",
    }
)
_SAFE_PRIOR_DESIGN_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_EDIT_ATTEMPT_OPERATIONS = frozenset({
    "replace_section", "patch_section", "create_item", "patch_item", "upsert_item", "remove_item", "move_item", "invalid",
})
_EDIT_ATTEMPT_SECTIONS = frozenset({
    "narrative", "technical_configuration", "index_acceptance_bindings",
    "simulation_scenario",
    "native_symbols",
    "io_points", "operating_modes", "control_networks", "ladder_rungs", "timers",
    "sequences", "alarms", "recovery_rules", "safety_rules", "invalid",
})
_MAX_EDIT_ATTEMPT_CHANGES = 8
_EDIT_ATTEMPT_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_EDIT_ATTEMPT_VALUE_KINDS = frozenset({
    "null",
    "object",
    "array",
    "string",
    "number",
    "boolean",
})


def _edit_attempt_value_kind(value: Any) -> str | None:
    """Classify an explicit replacement value without retaining its contents."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    return None


def _changes_digest(changes: Any) -> str | None:
    """Return a canonical digest without retaining the proposed values."""

    try:
        encoded = _canonical_json(changes).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _safe_design_edit_attempt_summary(value: Any) -> dict[str, Any] | None:
    """Validate the durable request shape; never accept raw edit values or IDs."""
    if not isinstance(value, Mapping):
        return None
    count, truncated, changes = value.get("requestedCount"), value.get("truncated"), value.get("changes")
    if (
        type(count) is not int or count < 0 or type(truncated) is not bool
        or not isinstance(changes, list) or len(changes) > _MAX_EDIT_ATTEMPT_CHANGES
        or len(changes) != min(count, _MAX_EDIT_ATTEMPT_CHANGES)
        or truncated != (count > len(changes))
    ):
        return None
    changes_digest = value.get("changesDigest")
    if (
        "changesDigest" in value
        and (
            not isinstance(changes_digest, str)
            or _EDIT_ATTEMPT_DIGEST.fullmatch(changes_digest) is None
        )
    ):
        return None
    safe_changes = []
    for change in changes:
        if not isinstance(change, Mapping):
            return None
        operation, section = change.get("operation"), change.get("section")
        if (
            not isinstance(operation, str) or operation not in _EDIT_ATTEMPT_OPERATIONS
            or not isinstance(section, str) or section not in _EDIT_ATTEMPT_SECTIONS
        ):
            return None
        value_kind = change.get("valueKind")
        if "valueKind" in change and (
            operation not in {"replace_section", "patch_section"}
            or not isinstance(value_kind, str)
            or value_kind not in _EDIT_ATTEMPT_VALUE_KINDS
        ):
            return None
        safe_change = {"operation": operation, "section": section}
        if "valueKind" in change:
            safe_change["valueKind"] = value_kind
        safe_changes.append(safe_change)
    summary = {"requestedCount": count, "truncated": truncated, "changes": safe_changes}
    if "changesDigest" in value:
        summary["changesDigest"] = changes_digest
    return summary


def _bounded_design_edit_attempt(arguments: Mapping[str, Any]) -> dict[str, Any] | None:
    changes = arguments.get("changes")
    if not isinstance(changes, list):
        return None
    safe_changes = []
    for change in changes[:_MAX_EDIT_ATTEMPT_CHANGES]:
        change = change if isinstance(change, Mapping) else {}
        operation, section = change.get("operation"), change.get("section")
        safe_change = {
            "operation": operation if isinstance(operation, str) and operation in _EDIT_ATTEMPT_OPERATIONS else "invalid",
            "section": section if isinstance(section, str) and section in _EDIT_ATTEMPT_SECTIONS else "invalid",
        }
        if safe_change["operation"] in {"replace_section", "patch_section"} and "value" in change:
            value_kind = _edit_attempt_value_kind(change["value"])
            if value_kind is not None:
                safe_change["valueKind"] = value_kind
        safe_changes.append(safe_change)
    summary: dict[str, Any] = {
        "requestedCount": len(changes),
        "truncated": len(changes) > len(safe_changes),
        "changes": safe_changes,
    }
    changes_digest = _changes_digest(changes)
    if changes_digest is not None:
        summary["changesDigest"] = changes_digest
    return _safe_design_edit_attempt_summary(summary)


_DESIGN_DIAGNOSTIC_MESSAGES = {
    "schema_invalid": "The proposed design value does not match the required schema.",
    "edit_rejected": "Host validation rejected the proposed design edit.",
    "singleton_requires_section_edit": (
        "Use patch_section with a non-empty partial object for existing narrative or technical_configuration; "
        "omitted siblings are preserved. Use replace_section for initial/explicit full singleton replacement. "
        "Item operations edit collection entries or simulation steps."
    ),
    "edit_patch_empty": "patch_section requires at least one exact top-level field to change.",
    "edit_patch_base_missing": "The section has no existing object to patch; use replace_section for its initial complete value.",
    "collection_requires_item_edit": (
        "Use create_item, patch_item, upsert_item, remove_item, or move_item with item_id "
        "for collection entries. replace_section does not replace an executable collection."
    ),
    "edit_made_no_change": "The proposed edit is identical to the retained design.",
    "ladder_move_repeats_prior_order": "The rung move repeats an order already produced by this run.",
    "repair_section_mismatch": "The proposed edit does not target the diagnosed section.",
    "repair_must_target_diagnosed_item": "The proposed edit does not target the diagnosed item.",
    "repair_must_target_existing_item": "The proposed edit does not target an existing item.",
    "repair_must_patch_not_remove": "The diagnosed item must be repaired rather than removed.",
    "edit_operation_invalid": "Choose an operation and section from the Host-provided allowed lists.",
    "edit_missing_field": "The edit is missing a required field listed in requiredFields.",
    "edit_value_type_invalid": "The edit field has the wrong shape; use expectedType.",
    "edit_value_fields_invalid": (
        "The object fields do not match the Host schema; use requiredFields and allowedFields."
    ),
    "edit_value_value_invalid": "The value must be one of the Host-provided allowedValues.",
    "edit_anchor_not_found": (
        "The move anchor does not identify an existing item; inspect current item IDs."
    ),
    "edit_target_not_found": (
        "The target item does not exist; inspect the current collection before editing it."
    ),
    "edit_target_already_exists": (
        "The create target already exists; inspect it and choose an appropriate item operation."
    ),
    "edit_item_identity_mismatch": "The value object's identity field must match item_id.",
}
_SAFE_DOMAIN_DIAGNOSTIC_MESSAGES = {
    "unknown_reference": "Reference is not declared.",
    "unknown_alarm_latch_reference": "Alarm latch signal is not declared.",
    "sequence_reset_address_reserved": "The sequence reset address is reserved for generated reset handling.",
    "sequence_step_group_mismatch": "Sequence steps must use one address group.",
    "sequence_group_reused": "A sequence address group is already used by another sequence.",
    "duplicate_sequence_step_id": "Sequence step IDs must be unique.",
    "duplicate_sequence_step_address": "Sequence step addresses must be unique.",
    "native_io_address_invalid": (
        "The I/O address is not valid for the selected CPU series; choose a supported "
        "non-conflicting address. No address was changed."
    ),
    "simulation_input_writer_byte_overlap": (
        "The simulator stimulus overlaps a program-written input byte; choose "
        "non-overlapping addresses. No address was changed."
    ),
    "physical_io_forbidden_internal_simulation": (
        "Internal simulation requires supported internal M I/O; no address was changed."
    ),
    "missing_section": "A required design section is missing.",
    "open_decision": "An open design decision remains.",
    "brief_changed": "The project brief binding changed.",
}
_SAFE_REFERENCE_DIAGNOSTIC_CODES = frozenset(
    {"unknown_reference", "unknown_alarm_latch_reference"}
)
_SAFE_ADDRESS_DIAGNOSTIC_CODES = frozenset(
    {
        "native_io_address_invalid",
        "simulation_input_writer_byte_overlap",
        "physical_io_forbidden_internal_simulation",
    }
)
_SAFE_DESIGN_EXPECTED_TYPES = frozenset(
    {
        "execution condition",
        "declared writable alarm latch",
        "controlstudio-index-acceptance-bindings/v1 object",
        "acceptance signal role object",
        "automatic and manual mode roles",
        "emergency, door, and timeout alarm roles",
        "distinct signals except a shared automatic/manual start button",
        "two distinct reviewed mode IDs",
        "one sequence per selected automatic/manual mode",
        "all three distinct reviewed alarm IDs",
        "alarm with an internal_bit latch signal",
        "three distinct internal alarm latch signals",
        "a declared timer ID",
        "TON timer with durationMs 5000",
        "Dxxxxx data-register address",
        "Sxx.01 through Sxx.99",
        "an S group unique to this sequence",
        "alarm",
        "array",
        "boolean",
        "digital_input",
        "digital_output",
        "internal M bit address",
        "M bit outside stimulus input bytes",
        "integer",
        "internal_bit",
        "mode",
        "mapping",
        "number",
        "object",
        "enum",
        "null",
        "sequence",
        "signal",
        "step",
        "string",
        "non-empty string",
        "array of strings",
        "object or null",
        "supported edit operation and section",
        "item edit operation",
        "replace_section operation",
        "lowercase identifier",
        "different valid item_id",
        "identity matching item_id",
        "existing item_id",
        "non-empty string values",
        "timer",
    }
)
_SAFE_DESIGN_EDIT_OPERATIONS = frozenset(
    {
        "replace_section", "patch_section", "create_item", "patch_item", "upsert_item",
        "remove_item", "move_item",
    }
)
_SAFE_DESIGN_EDIT_SECTIONS = frozenset(
    {
        "narrative", "technical_configuration", "index_acceptance_bindings",
        "simulation_scenario", "native_symbols", "io_points", "operating_modes", "control_networks",
        "ladder_rungs", "timers", "sequences", "alarms", "recovery_rules",
        "safety_rules",
    }
)
_SAFE_DESIGN_ERROR_TYPES = frozenset(
    {
        "bool_parsing",
        "bool_type",
        "dict_type",
        "enum",
        "extra_forbidden",
        "finite_number",
        "float_parsing",
        "float_type",
        "greater_than",
        "greater_than_equal",
        "int_parsing",
        "int_type",
        "io_safe_state_relation",
        "less_than",
        "less_than_equal",
        "list_type",
        "literal_error",
        "missing",
        "multiple_of",
        "none_required",
        "set_type",
        "scenario_constraint",
        "string_pattern_mismatch",
        "string_type",
        "too_long",
        "too_short",
        "tuple_type",
        "union_tag_invalid",
        "union_tag_not_found",
        "value_error",
    }
)
_SAFE_DESIGN_ENUM_VALUE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,79}\Z")
_SAFE_DESIGN_PATTERN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789^$\\[]{}().?+*|,:_-"
)
_SAFE_DESIGN_RELATED_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
_SAFE_DESIGN_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}\Z")
_SAFE_DESIGN_CONSTRAINT_KINDS = frozenset(
    {
        "alias_conflict",
        "allowed_fields",
        "consistent_sets",
        "disjoint_sets",
        "item_count",
        "length",
        "numeric_range",
        "required_fields",
        "total_bound",
        "type",
        "scenario_assignment",
        "unique_items",
    }
)
_MAX_DESIGN_CONSTRAINT_BOUND = 1_000_000_000
_MAX_DESIGN_ENUM_VALUES = 16
_MAX_DESIGN_FIELDS = 32


def _safe_design_constraint_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only bounded, Host-authored schema facts; never echo submitted input."""

    projected: dict[str, Any] = {}
    reason_code = raw.get("reasonCode")
    if raw.get("code") == "requirement_source_invalid" and isinstance(reason_code, str) and reason_code in {
        "duplicate_field", "message_not_available", "quote_not_exact", "generic_ack", "unresolved_quote",
    }:
        projected["reasonCode"] = reason_code
    error_type = raw.get("errorType")
    if isinstance(error_type, str) and error_type in _SAFE_DESIGN_ERROR_TYPES:
        projected["errorType"] = error_type
    if error_type == "io_safe_state_relation":
        if (
            raw.get("constraintKind") == "kind_safe_state_relation"
            and raw.get("constraintField") == "safe_state"
            and (raw.get("allowedKinds"), raw.get("allowedStates")) in (
                (["digital_input"], ["not_applicable"]),
                (["digital_output", "internal_bit"], ["on", "off"]),
            )
        ):
            projected.update({
                "constraintKind": "kind_safe_state_relation",
                "constraintField": "safe_state",
                "allowedKinds": list(raw["allowedKinds"]),
                "allowedStates": list(raw["allowedStates"]),
            })
        return projected
    expected_type = raw.get("expectedType")
    if isinstance(expected_type, str):
        normalized = " ".join(expected_type.split())
        if (
            normalized in _SAFE_DESIGN_EXPECTED_TYPES
            or re.fullmatch(r"(S(?:\d{2}|1[01]\d|12[0-7]))\.01 through \1\.99", normalized)
            or (
                len(normalized) <= 512
                and re.fullmatch(r"exact role keys: [a-z_]+(?:, [a-z_]+)*", normalized)
            )
            or (
                1 <= len(normalized.split(" or ")) <= 3
                and set(normalized.split(" or ")).issubset(
                    {"digital_input", "digital_output", "internal_bit"}
                )
            )
        ):
            projected["expectedType"] = normalized
    constraint_kind = raw.get("constraintKind")
    if (
        isinstance(constraint_kind, str)
        and constraint_kind in _SAFE_DESIGN_CONSTRAINT_KINDS
    ):
        projected["constraintKind"] = constraint_kind
    assignment_field = raw.get("assignmentField")
    if error_type == "scenario_constraint" and assignment_field in ("drive", "observe"):
        projected["assignmentField"] = assignment_field
        address_kinds = raw.get("allowedAddressKinds")
        if address_kinds == ["bit"] or (
            assignment_field == "observe" and address_kinds == ["bit", "data_word"]
        ):
            projected["allowedAddressKinds"] = list(address_kinds)
        expected_rules = {
            "bitAddressPattern": r"^[PM]\d{3,4}[0-9A-F]$",
            "bitMinimum": 0,
            "bitMaximum": 1,
        }
        if assignment_field == "observe":
            expected_rules.update({
                "wordAddressPattern": r"^D\d{5}$",
                "wordEncoding": "signed16",
                "wordMinimum": -32_768,
                "wordMaximum": 32_767,
            })
        for key, expected in expected_rules.items():
            actual = raw.get(key)
            if type(actual) is type(expected) and actual == expected:
                projected[key] = actual
    for key in ("requiredFields", "allowedFields"):
        fields = raw.get(key)
        if isinstance(fields, list) and 0 < len(fields) <= _MAX_DESIGN_FIELDS:
            values = []
            for field in fields:
                if (
                    not isinstance(field, str)
                    or _SAFE_DESIGN_FIELD_NAME.fullmatch(field) is None
                ):
                    values = []
                    break
                values.append(field)
            if values:
                projected[key] = values
    for key, allowed in (
        ("allowedOperations", _SAFE_DESIGN_EDIT_OPERATIONS),
        ("allowedSections", _SAFE_DESIGN_EDIT_SECTIONS),
    ):
        values = raw.get(key)
        if (
            isinstance(values, list)
            and 0 < len(values) <= len(allowed)
            and all(isinstance(value, str) and value in allowed for value in values)
        ):
            projected[key] = list(values)
    allowed_values = raw.get("allowedValues")
    if isinstance(allowed_values, list) and len(allowed_values) <= _MAX_DESIGN_ENUM_VALUES:
        values: list[str] = []
        for value in allowed_values:
            if (
                not isinstance(value, str)
                or _SAFE_DESIGN_ENUM_VALUE.fullmatch(value) is None
            ):
                values = []
                break
            values.append(value)
        if values:
            projected["allowedValues"] = values
    pattern = raw.get("pattern")
    if (
        isinstance(pattern, str)
        and 0 < len(pattern) <= 160
        and all(character in _SAFE_DESIGN_PATTERN_CHARS for character in pattern)
    ):
        projected["pattern"] = pattern
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        bound = raw.get(key)
        if (
            isinstance(bound, int)
            and not isinstance(bound, bool)
            and 0 <= bound <= _MAX_DESIGN_CONSTRAINT_BOUND
        ):
            projected[key] = bound
    actual_items = raw.get("actualItems")
    if (
        isinstance(actual_items, int)
        and not isinstance(actual_items, bool)
        and 0 <= actual_items <= _MAX_DESIGN_CONSTRAINT_BOUND
    ):
        projected["actualItems"] = actual_items
    for key in (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
    ):
        bound = raw.get(key)
        if (
            isinstance(bound, (int, float))
            and not isinstance(bound, bool)
            and math.isfinite(float(bound))
            and abs(float(bound)) <= _MAX_DESIGN_CONSTRAINT_BOUND
        ):
            projected[key] = bound
    return projected


def _safe_design_related_ids(raw: Any) -> list[str] | None:
    if not isinstance(raw, list) or len(raw) > 30:
        return None
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str) or _SAFE_DESIGN_RELATED_ID.fullmatch(value) is None:
            return None
        values.append(value)
    return values


def _safe_design_diagnostic_item(
    raw: Any,
    *,
    require_path: bool,
) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    code = raw.get("code")
    path = raw.get("path")
    if (
        not isinstance(code, str)
        or _SAFE_DESIGN_DIAGNOSTIC_CODE.fullmatch(code) is None
        or (
            require_path
            and (
                not isinstance(path, str)
                or len(path) > 256
                or _SAFE_DESIGN_DIAGNOSTIC_PATH.fullmatch(path) is None
            )
        )
    ):
        return None
    constraint_fields = _safe_design_constraint_fields(raw)
    expected_type = constraint_fields.get("expectedType")
    if code in _DESIGN_DIAGNOSTIC_MESSAGES:
        message = _DESIGN_DIAGNOSTIC_MESSAGES[code]
    elif code == "unknown_reference" and isinstance(expected_type, str):
        message = f"Reference is not declared as {expected_type}."
    else:
        message = _SAFE_DOMAIN_DIAGNOSTIC_MESSAGES.get(
            code, "Host validation reported a typed design diagnostic."
        )
    item: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if isinstance(path, str) and len(path) <= 256 and _SAFE_DESIGN_DIAGNOSTIC_PATH.fullmatch(path):
        item["path"] = path
    item.update(constraint_fields)
    related_ids = _safe_design_related_ids(raw.get("relatedIds"))
    if related_ids:
        item["relatedIds"] = related_ids
    if code in _SAFE_REFERENCE_DIAGNOSTIC_CODES:
        received_id = raw.get("received")
        if (
            isinstance(received_id, str)
            and _SAFE_DESIGN_RELATED_ID.fullmatch(received_id) is not None
        ):
            item["receivedId"] = received_id
    elif code in _SAFE_ADDRESS_DIAGNOSTIC_CODES:
        received_address = raw.get("received")
        if (
            isinstance(received_address, str)
            and re.fullmatch(r"[PM]\d{3,4}[0-9A-F]", received_address) is not None
        ):
            item["receivedAddress"] = received_address
    return item


def _bounded_design_failure_diagnostics(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project useful design validation facts without retaining submitted values."""

    raw_diagnostics = value.get("diagnostics")
    if not isinstance(raw_diagnostics, list):
        return []
    projected: list[dict[str, Any]] = []
    for raw in raw_diagnostics[:_MAX_DESIGN_FAILURE_DIAGNOSTICS]:
        item = _safe_design_diagnostic_item(raw, require_path=True)
        if item is None:
            continue
        projected.append(item)
    return projected


def _safe_work_plan_diagnostic_path(value: Any) -> str | None:
    """Keep only paths into the small, fixed WorkPlan input schema."""

    if value == "/":
        return "/"
    if not isinstance(value, str) or len(value) > 128 or not value.startswith("/"):
        return None
    segments = value[1:].split("/")
    if len(segments) == 1 and segments[0] in {
        "expected_version", "objective", "decisions", "next_action",
    }:
        return value
    if (
        segments[0] == "items"
        and len(segments) in {1, 2, 3}
        and (
            len(segments) == 1
            or (
                segments[1].isdecimal()
                and int(segments[1]) < 24
                and str(int(segments[1])) == segments[1]
            )
        )
        and (len(segments) < 3 or segments[2] in {"id", "task", "status"})
    ):
        return value
    return None


def _bounded_work_plan_failure_diagnostics(
    value: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Sanitize WorkPlan errors without treating them as design JSON pointers."""

    raw_diagnostics = value.get("diagnostics")
    if not isinstance(raw_diagnostics, list):
        return []
    projected: list[dict[str, Any]] = []
    for raw in raw_diagnostics[:_MAX_DESIGN_FAILURE_DIAGNOSTICS]:
        item = _safe_design_diagnostic_item(raw, require_path=False)
        if item is None:
            continue
        path = _safe_work_plan_diagnostic_path(raw.get("path"))
        item.pop("path", None)
        item.pop("diagnosticPointer", None)
        if item.get("allowedValues") != list(_WORK_PLAN_ITEM_STATUSES):
            item.pop("allowedValues", None)
        if path is None:
            item = {"code": item["code"]}
        else:
            item["workPlanPointer"] = path
        projected.append(item)
    return projected


def _bounded_prior_design_failure_diagnostics(
    value: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Re-project a stored edit failure with the larger historical bound.

    Durable event diagnostics have already crossed the current-event boundary,
    but a planning run must not trust those dictionaries merely because they
    came from the Host database.  Reusing the same static-message and typed
    constraint projection prevents raw edit values, arguments, or schemas from
    being reintroduced when an older event is carried into a new run.
    """

    raw_diagnostics = value.get("diagnostics")
    if not isinstance(raw_diagnostics, list):
        return []
    projected: list[dict[str, Any]] = []
    for raw in raw_diagnostics[:_MAX_PRIOR_DESIGN_FAILURE_DIAGNOSTICS]:
        item = _safe_design_diagnostic_item(raw, require_path=True)
        if item is not None:
            projected.append(item)
    return projected


def _safe_prior_design_failure_observation(value: Any) -> dict[str, Any]:
    """Validate the Host-owned historical edit observation at the Agent edge."""

    if not isinstance(value, Mapping) or set(value) != _PRIOR_DESIGN_FAILURE_KEYS:
        raise AgentProtocolError("historical design edit failure is invalid")
    if value.get("kind") != "historical_design_edit_failure":
        raise AgentProtocolError("historical design edit failure is invalid")
    run_id = value.get("runId")
    if (
        not isinstance(run_id, str)
        or _SAFE_PRIOR_DESIGN_RUN_ID.fullmatch(run_id) is None
    ):
        raise AgentProtocolError("historical design edit failure is invalid")
    event_sequence = value.get("eventSequence")
    if (
        isinstance(event_sequence, bool)
        or not isinstance(event_sequence, int)
        or not 1 <= event_sequence <= 9_223_372_036_854_775_807
    ):
        raise AgentProtocolError("historical design edit failure is invalid")
    event_created_at = value.get("eventCreatedAt")
    if not isinstance(event_created_at, str) or len(event_created_at) > 64:
        raise AgentProtocolError("historical design edit failure is invalid")
    try:
        parsed_created_at = datetime.fromisoformat(event_created_at)
    except ValueError as exc:
        raise AgentProtocolError("historical design edit failure is invalid") from exc
    if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
        raise AgentProtocolError("historical design edit failure is invalid")
    reason_code = value.get("reasonCode")
    if (
        not isinstance(reason_code, str)
        or reason_code not in _SAFE_PRIOR_DESIGN_FAILURE_REASON_CODES
    ):
        raise AgentProtocolError("historical design edit failure is invalid")
    diagnostics = value.get("diagnostics")
    if not isinstance(diagnostics, list) or len(diagnostics) > _MAX_PRIOR_DESIGN_FAILURE_DIAGNOSTICS:
        raise AgentProtocolError("historical design edit failure is invalid")
    projected_diagnostics = _bounded_prior_design_failure_diagnostics(
        {"diagnostics": diagnostics}
    )
    if len(projected_diagnostics) != len(diagnostics):
        raise AgentProtocolError("historical design edit failure is invalid")
    # Re-emit only canonical fields.  In particular, current revision/digest,
    # submitted arguments, and raw validation text can never cross this edge.
    return {
        "kind": "historical_design_edit_failure",
        "runId": run_id,
        "eventSequence": event_sequence,
        "eventCreatedAt": parsed_created_at.isoformat(),
        "reasonCode": reason_code,
        "diagnostics": projected_diagnostics,
    }


def _safe_design_diagnostic_pointer(value: Any) -> str | None:
    """Accept only a known design JSON Pointer, never a local/path-like string."""

    if (
        not isinstance(value, str)
        or len(value) > _MAX_DESIGN_POINTER_LENGTH
        or not value.startswith("/")
    ):
        return None
    segments = value[1:].split("/")
    if (
        not segments
        or len(segments) > _MAX_DESIGN_POINTER_SEGMENTS
        or segments[0] not in _DESIGN_POINTER_ROOTS
        or any(
            not segment
            or _SAFE_DESIGN_POINTER_SEGMENT.fullmatch(segment) is None
            or segment.casefold() in _DESIGN_POINTER_SENSITIVE_SEGMENTS
            or (
                re.fullmatch(r"0|[1-9][0-9]{0,5}", segment) is None
                and segment.replace("_", "").casefold() not in _DESIGN_POINTER_FIELDS
            )
            for segment in segments
        )
    ):
        return None
    return value


def _project_design_diagnostic_pointers(
    tool_name: str,
    value: Any,
) -> Any:
    """Expose only typed workbench locations before the generic result scrub."""

    if not tool_name.startswith("design.") or not isinstance(value, Mapping):
        return value

    projected = dict(value)

    def diagnostic_list(raw_value: Any) -> Any:
        if not isinstance(raw_value, list):
            return raw_value
        if tool_name == "design.update_work_plan":
            return _bounded_work_plan_failure_diagnostics({
                "diagnostics": raw_value,
            })
        projected_items: list[Any] = []
        for raw_item in raw_value:
            item = _safe_design_diagnostic_item(raw_item, require_path=False)
            if item is None:
                continue
            pointer = _safe_design_diagnostic_pointer(raw_item.get("path"))
            item.pop("path", None)
            item.pop("diagnosticPointer", None)
            if pointer is not None:
                item["diagnosticPointer"] = pointer
            elif raw_item.get("path") == "/":
                # Model-level validators have no more precise location.  The
                # path itself is harmless, but keep only the bounded Host
                # message/constraint projection and never echo raw input.
                pass
            else:
                # Without a valid typed design pointer, retaining a generic
                # message or constraint could give an arbitrary input a model
                # facing authority.  The Host can still report the safe code.
                item = {"code": item["code"]}
            projected_items.append(item)
        return projected_items

    for key in ("diagnostics", "diagnosticRepresentatives"):
        if key in projected:
            projected[key] = diagnostic_list(projected[key])

    if tool_name == "design.update_work_plan":
        if "allowedStatuses" in projected and projected["allowedStatuses"] != list(
            _WORK_PLAN_ITEM_STATUSES
        ):
            projected.pop("allowedStatuses")
        for key in ("currentWorkPlanVersion", "expectedVersion"):
            version = projected.get(key)
            if key in projected and (
                type(version) is not int or version < 0
            ):
                projected.pop(key)
        return projected

    def repair_context(raw_value: Any) -> Any:
        if not isinstance(raw_value, Mapping):
            return raw_value
        context = dict(raw_value)
        context.pop("repairPointer", None)
        pointer = _safe_design_diagnostic_pointer(raw_value.get("path"))
        if pointer is not None:
            context["repairPointer"] = pointer
        context.pop("repairItemPointer", None)
        item_pointer = _safe_design_diagnostic_pointer(raw_value.get("itemPath"))
        if item_pointer is not None:
            context["repairItemPointer"] = item_pointer
        missing = raw_value.get("missingDeclarations")
        if isinstance(missing, list):
            projected_missing = []
            for declaration in missing:
                if not isinstance(declaration, Mapping):
                    continue
                projected_declaration: dict[str, Any] = {}
                declaration_id = declaration.get("id")
                if (
                    isinstance(declaration_id, str)
                    and _SAFE_DESIGN_RELATED_ID.fullmatch(declaration_id) is not None
                ):
                    projected_declaration["id"] = declaration_id
                expected_type = _safe_design_constraint_fields(declaration).get(
                    "expectedType"
                )
                if expected_type is not None:
                    projected_declaration["expectedType"] = expected_type
                usage_pointer = _safe_design_diagnostic_pointer(declaration.get("usePath"))
                if usage_pointer is not None:
                    projected_declaration["usagePointer"] = usage_pointer
                projected_missing.append(projected_declaration)
            context["missingDeclarations"] = projected_missing
        return context

    if "repairContext" in projected:
        projected["repairContext"] = repair_context(projected["repairContext"])
    if isinstance(projected.get("repairContexts"), list):
        projected["repairContexts"] = [
            repair_context(item) for item in projected["repairContexts"]
        ]
    return projected


def _design_edit_failure_code(value: Mapping[str, Any]) -> str:
    diagnostics = value.get("diagnostics")
    codes = {
        item.get("code") for item in diagnostics if isinstance(item, Mapping)
    } if isinstance(diagnostics, list) else set()
    if codes.intersection({"edit_made_no_change", "ladder_move_repeats_prior_order"}):
        return "design_edit_unchanged"
    if codes.intersection({"repair_section_mismatch", "repair_must_target_diagnosed_item",
                           "repair_must_target_existing_item", "repair_must_patch_not_remove"}):
        return "design_edit_scope_rejected"
    if "schema_invalid" in codes:
        return "design_edit_schema_invalid"
    return "design_edit_not_applied"

from .agent_protocol import (
    MAX_PROTOCOL_LINE_BYTES,
    AgentProtocolError,
    AgentProtocolMessage,
    ValidatedNativeCheckpoint,
    assert_agent_safe_payload,
    assert_native_public_payload,
    decode_message,
    encode_message,
    native_checkpoint_boundary,
    native_checkpoint_history_extends,
    parse_manifest,
    scrub_agent_tool_result,
    scrub_native_tool_result,
    validated_native_checkpoint_append,
    validated_native_checkpoint_snapshot,
    validated_native_checkpoint_transfer,
    validated_transcript_checkpoint,
)
from .agent_runtime import (
    _PROVIDER_RUNTIME_ERROR_CODES,
    _PROVIDER_RUNTIME_ERROR_TYPES,
    _PROVIDER_RUNTIME_EXCEPTION_CLASSES,
    AgentOutputContractDiagnostic,
    AgentOutputContractError,
    AgentOutputValidationIssue,
    AgentRunOutput,
    AgentRuntimeDiagnostic,
    AgentRuntimeError,
    ChangePlanCallback,
    MissionCheckpointCallback,
    ModelCapabilityResult,
    ProgressCallback,
    TokenCallback,
    ToolEventCallback,
    TranscriptCheckpointCallback,
    validated_native_project_start,
)
from .api_models import ModelContextV1, OfflineOperandMissionV1
from .execution_broker import ExecutionBroker, mcp_rejection_context
from .mcp_client import (
    AGENT_TOOL_NAMES,
    MCPToolError,
    MCPToolRejectedError,
    MCPTransportError,
)
from .model_connection import StoredModelConnection
from .models import Message, MessageRole

pass
from .structural_evidence import (
    STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA,
    STRUCTURAL_ANALYSIS_TOOL_COVERAGE,
    canonical_digest,
    logic_graph_context,
    project_search_context,
    state_model_context,
    structural_observation_statement,
    structural_receipt_tool_name,
    validate_project_search_context,
    validate_structural_evidence_contract,
)

logger = logging.getLogger(__name__)

_HISTORICAL_EVIDENCE_BUNDLE_KEYS = {
    "schemaVersion",
    "missionDigest",
    "items",
    "bundleDigest",
}
_HISTORICAL_EVIDENCE_ITEM_KEYS = {"sourceRunId", "evidence"}
_MAX_HISTORICAL_EVIDENCE_ITEMS = 2_500
_MAX_HISTORICAL_EVIDENCE_BUNDLE_BYTES = 1_500_000
_MAX_RETAINED_PLANNING_NARRATIVE_BYTES = 250_000
_RETAINED_PLANNING_NARRATIVE_STRING_FIELDS = {
    "project_name": 200,
    "summary": 4_000,
    "objective": 4_000,
}
_RETAINED_PLANNING_NARRATIVE_LIST_LIMITS = {
    "user_requirements": 100,
    "design_proposals": 100,
    "scope": 50,
    "assumptions": 50,
    "open_questions": 5,
    "hardware_and_network": 100,
    "io_and_devices": 200,
    "operating_modes": 50,
    "sequence": 200,
    "safety_invariants": 100,
    "alarms_and_recovery": 100,
    "program_structure": 100,
    "validation_plan": 100,
}
_MAX_PLANNING_NATIVE_FAILURE_BYTES = 100_000
_PLANNING_NATIVE_FAILURE_REQUIRED_KEYS = frozenset(
    {
        "schemaVersion",
        "operationId",
        "planningConversationId",
        "planningMessageId",
        "reviewedPlanDigest",
        "projectBriefDigest",
        "briefRelation",
        "revisionRelation",
        "operationState",
        "failureKind",
        "observationStrength",
        "errorCode",
    }
)
_PLANNING_NATIVE_FAILURE_OPTIONAL_KEYS = frozenset(
    {
        "classification",
        "phase",
        "runtimeErrorCode",
        "projectUnchanged",
        "safeRestorationVerified",
        "cpuStopVerified",
        "scenarioObservation",
        "acceptanceProfile",
        "preflightDiagnostic",
        "nativeDiagnostic",
    }
)
_PLANNING_NATIVE_FAILURE_SCENARIO_KEYS = frozenset(
    {
        "observationLabel",
        "inputAssignment",
        "inputAssignmentScope",
        "expected",
        "observed",
        "observationKind",
    }
)
_PLANNING_NATIVE_FAILURE_PROFILE_KEYS = frozenset(
    {
        "schemaVersion",
        "reasonCode",
        "requiredDesignSection",
        "profilePresent",
        "signalIds",
        "modeIds",
        "alarmIds",
        "timerIds",
        "bindings",
    }
)
_DURABLE_RESUME_SYNC_CAPABILITY = "durable-resume-sync-v1"
_SAFE_ENGINEERING_VALIDATOR_STDERR = re.compile(
    r"^(?:[0-9]{4})-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]) "
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9],[0-9]{3} "
    r"WARNING controlstudio_agent\.runtime "
    r"engineering output validator rejected at runtime frame "
    r"(?P<function>[A-Za-z_][A-Za-z0-9_]*) line "
    r"(?P<line>[1-9][0-9]{0,6})\r?\n?$"
)
_SAFE_RUNTIME_FRAME_STDERR = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]) "
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9],[0-9]{3} "
    r"WARNING controlstudio_agent\.runtime runtime invariant at runtime frame "
    r"(?P<function>[A-Za-z_][A-Za-z0-9_]*) line (?P<line>[1-9][0-9]{0,6}) kind "
    r"(?P<kind>AssertionError|AttributeError|IndexError|KeyError|TypeError|ValueError|"
    r"RuntimeError|RecursionError|Exception)\r?\n?$"
)
_SAFE_AGENT_RUNTIME_FRAME_STDERR = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]) "
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9],[0-9]{3} "
    r"WARNING controlstudio_agent\.runtime runtime invariant at agent frame "
    r"(?P<module>[a-z_][a-z0-9_.]{0,127}) "
    r"(?P<function>[A-Za-z_][A-Za-z0-9_]*) line "
    r"(?P<line>[1-9][0-9]{0,6}) kind "
    r"(?P<kind>AssertionError|AttributeError|IndexError|KeyError|TypeError|ValueError|"
    r"RuntimeError|RecursionError|Exception)\r?\n?$"
)
_SAFE_RUNTIME_EXCEPTION_CLASS_STDERR = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]) "
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9],[0-9]{3} "
    r"WARNING controlstudio_agent\.runtime runtime invariant exception class "
    r"(?P<class_name>(?:builtins|controlstudio_agent|openai|pydantic|pydantic_ai)"
    r"[A-Za-z0-9_.]{0,127})\r?\n?$"
)
_SAFE_NATIVE_RESPONSE_STDERR = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]) "
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9],[0-9]{3} "
    r"WARNING controlstudio_agent\.runtime native response shape "
    r"finish=(?P<finish>stop|length|content_filter|tool_call|error|unknown) "
    r"output_tokens=(?P<tokens>[0-9]{1,9}) text_chars=(?P<chars>[0-9]{1,9})\r?\n?$"
)
_SAFE_WORKSPACE_DECISION_STDERR = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3} "
    r"INFO controlstudio_agent\.runtime workspace decision "
    r"cycle=(?P<cycle>[0-9]{1,4}) "
    r"action=(?P<action>none|design_(?:inspect|read|search|edit|check|publish_review|update_work_plan|finish|clarify)"
    r"|native_(?:create|status|test)) "
    r"outcome=(?P<outcome>selected|schema_rejected|transport_rejected) "
    r"input_tokens=(?P<input>[0-9]{1,9}) output_tokens=(?P<output>[0-9]{1,9})\r?\n?$"
)
# Agent emits this fixed provider-transport summary after reducing the
# provider response to a closed metadata vocabulary.  Keep the Host parser
# independently strict: stderr is an untrusted child-process boundary and
# must never become a reflection channel for provider bodies or arguments.
_SAFE_PROVIDER_HTTP_STDERR = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]) "
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9],[0-9]{3} "
    r"WARNING controlstudio_agent\.runtime provider HTTP error "
    r"status=(?P<status>[0-9]{3}|unknown) "
    r"type=(?P<type>[a-z0-9_]{1,80}) "
    r"code=(?P<code>[a-z0-9_]{1,80}) "
    r"param=(?P<param>[a-z0-9_.\[\]]{1,80})"
    r"(?: message=(?P<message>[^\r\n]{1,2048}))?\r?\n?$"
)
_WORKING_HISTORY_STDERR = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3} "
    r"WARNING controlstudio_agent\.working_history working history rejected "
    r"reason=(?P<reason>core|latest|notice|window|pair) "
    r"budget=(?P<budget>[0-9]{1,10}) fixed=(?P<fixed>[0-9]{1,10}) "
    r"core=(?P<core>[0-9]{1,10}) latest=(?P<latest>[0-9]{1,10})\r?\n?$"
)
_WORKING_HISTORY_PROJECTION_FIELDS = (
    "seq", "budget", "fixed", "schemas", "outputs", "core", "retained", "omitted",
    "core_msgs", "retained_msgs", "omitted_msgs", "reader", "design_edit",
    "full_proposal", "retained_calls", "retained_returns", "omitted_calls",
    "omitted_returns", "reader_calls", "reader_returns", "reader_bytes",
    "reader_error", "retries", "call_tokens", "return_tokens", "other_tokens",
)
_WORKING_HISTORY_PROJECTION_STDERR = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3} "
    r"INFO controlstudio_agent\.working_history working history projected "
    + " ".join(
        rf"{key}=(?P<{key}>[01])" if key in {"reader", "design_edit", "full_proposal", "reader_error"}
        else rf"{key}=(?P<{key}>[0-9]{{1,10}})"
        for key in _WORKING_HISTORY_PROJECTION_FIELDS
    )
    + r"\r?\n?$"
)
_MAX_SAFE_PROVIDER_ERROR_MESSAGE_CHARS = 2048
_PROVIDER_ERROR_SECRET_ASSIGNMENT = re.compile(
    r'''(?ix)(\b(?:authorization|api[_-]?key|x-api-key|credential(?:s)?|cookie|'''
    r'''password|secret|token|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*)'''
    r'''(?:"[^"]*"|'[^']*'|(?:Bearer\s+)?[^\s,;]+)'''
)
_PROVIDER_ERROR_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[^\s,;\"']+")
_PROVIDER_ERROR_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]+")
_PROVIDER_ERROR_URL = re.compile(r"(?i)\b(?:https?|wss?)://[^\s,;]+")
_SAFE_PROVIDER_ERROR_TYPES = frozenset(
    {
        "api_error",
        "authentication_error",
        "conflict_error",
        "content_policy_violation",
        "invalid_request_error",
        "model_not_found",
        "not_found_error",
        "permission_denied",
        "rate_limit_error",
        "server_error",
        "service_unavailable",
        "unknown",
        "redacted",
    }
)
_SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "api_error",
        "authentication_error",
        "context_length_exceeded",
        "content_policy_violation",
        "insufficient_quota",
        "invalid_model",
        "invalid_parameter",
        "invalid_request_error",
        "length",
        "max_tokens",
        "model_not_found",
        "permission_denied",
        "rate_limit_exceeded",
        "server_error",
        "service_unavailable",
        "unsupported_value",
        "unknown",
        "redacted",
    }
)
_SAFE_PROVIDER_PARAM_ROOTS = frozenset(
    {
        "chat_template_kwargs",
        "input",
        "instructions",
        "max_completion_tokens",
        "max_tokens",
        "messages",
        "model",
        "parallel_tool_calls",
        "reasoning_effort",
        "response_format",
        "stream",
        "temperature",
        "timeout",
        "tool_choice",
        "tools",
        "top_p",
    }
)
_SAFE_PROVIDER_PARAM_SUFFIXES = frozenset(
    {
        "arguments",
        "content",
        "function",
        "items",
        "name",
        "parameters",
        "properties",
        "role",
        "schema",
        "strict",
        "tool_calls",
        "type",
        "value",
    }
)
_SAFE_PROVIDER_PARAM_PART = re.compile(
    r"[a-z][a-z0-9_]*(?:\[(?:0|[1-9][0-9]{0,2})\])?\Z"
)
_SAFE_PROVIDER_ERROR_REDACTION_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)
# The sidecar logs this fixed-shape summary immediately before emitting an
# output-contract error.  Capture only bounded ASCII fields here; the helper
# below applies the closed Host allowlists before any value reaches a log,
# exception, or durable error detail.  In particular, model text, arguments,
# provider responses, and arbitrary exception strings are never mirrored.
_SAFE_OUTPUT_CONTRACT_STDERR = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]) "
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9],[0-9]{3} "
    r"WARNING controlstudio_agent\.sidecar "
    r"Agent output violated the private structured contract "
    r"\(run_id=[0-9A-Fa-f-]{1,64}, "
    r"stage=(?P<stage>[a-z_]{1,64}), "
    r"exception_kind=(?P<exception_kind>[a-z_]{1,64}), "
    r"reason_code=(?P<reason_code>[a-z0-9_]{1,64}), "
    r"validator_code=(?P<validator_code>[a-z0-9_]{1,96})\)\r?\n?$"
)

# The exact typed operand route is Host-selected from a durable mission rather
# than inferred from conversational prose.  Its sidecar finalization is fully
# deterministic after an approval-only proposal succeeds.  Keep this wire
# prompt ASCII-only: older sidecars use the prompt's script to select a
# user-prose validator, while their deterministic approval message is English.
# The original objective remains in the signed Host mission snapshot.
_EXACT_OFFLINE_OPERAND_CONTINUATION_PROMPT = (
    "Continue the exact Host-bound offline operand mission. The trusted mission "
    "snapshot is authoritative. Use no target outside that snapshot. Any change "
    "proposal remains approval-only; do not mutate a project, controller, or "
    "runtime state."
)

_SIDECAR_STRIPPED_ENVIRONMENT = frozenset(
    {
        "CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET",
        "CONTROLSTUDIO_APPROVAL_SECRET",
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _exact_offline_operand_continuation_prompt(
    trusted_context: Mapping[str, Any],
) -> str | None:
    """Return the compatibility prompt only for a bound active exact route.

    This does not alter the durable mission, tool catalog, approval authority,
    or final-output validation.  It is deliberately unavailable before the
    Host has supplied a typed operand mission and an existing mission snapshot.
    """

    if trusted_context.get("workflow") != "engineering":
        return None
    try:
        operand_mission = OfflineOperandMissionV1.model_validate(
            trusted_context.get("offline_operand_mission")
        )
    except (TypeError, ValueError):
        return None
    mission = trusted_context.get("engineering_mission")
    if not isinstance(mission, dict) or mission.get("schemaVersion") != "1":
        return None
    if mission.get("status") != "active":
        return None
    objective = mission.get("objective")
    steps = mission.get("steps")
    if (
        not isinstance(objective, str)
        or not isinstance(steps, list)
        or not all(
            value in objective
            for value in (
                operand_mission.program_name,
                operand_mission.expected_operand,
                operand_mission.replacement_operand,
            )
        )
        or mission.get("activeStepId") != "propose_offline_change"
        or not any(
            isinstance(step, dict)
            and step.get("stepId") == "propose_offline_change"
            and step.get("status") == "active"
            for step in steps
        )
    ):
        return None
    return _EXACT_OFFLINE_OPERAND_CONTINUATION_PROMPT


def validate_historical_evidence_bundle(
    value: object,
    *,
    mission_digest: str | None = None,
    expected_evidence_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate the exact Host-owned projection of mission-bound old evidence."""

    if not isinstance(value, dict) or set(value) != _HISTORICAL_EVIDENCE_BUNDLE_KEYS:
        raise AgentProtocolError("historical evidence bundle fields are invalid")
    items = value.get("items")
    bound_mission_digest = value.get("missionDigest")
    bundle_digest = value.get("bundleDigest")
    if (
        value.get("schemaVersion") != "1"
        or not isinstance(bound_mission_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", bound_mission_digest) is None
        or (mission_digest is not None and bound_mission_digest != mission_digest)
        or not isinstance(bundle_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", bundle_digest) is None
        or not isinstance(items, list)
        or len(items) > _MAX_HISTORICAL_EVIDENCE_ITEMS
    ):
        raise AgentProtocolError("historical evidence bundle binding is invalid")
    evidence_ids: list[str] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != _HISTORICAL_EVIDENCE_ITEM_KEYS:
            raise AgentProtocolError("historical evidence item fields are invalid")
        source_run_id = item.get("sourceRunId")
        evidence = item.get("evidence")
        evidence_id = evidence.get("evidenceId") if isinstance(evidence, dict) else None
        tool_name = evidence.get("toolName") if isinstance(evidence, dict) else None
        try:
            canonical_evidence_id = (
                str(uuid.UUID(evidence_id)) if isinstance(evidence_id, str) else None
            )
        except ValueError:
            canonical_evidence_id = None
        if (
            not isinstance(source_run_id, str)
            or not source_run_id
            or len(source_run_id) > 256
            or canonical_evidence_id != evidence_id
            or not isinstance(tool_name, str)
            or not tool_name
        ):
            raise AgentProtocolError("historical evidence item is invalid")
        assert_agent_safe_payload(evidence)
        evidence_ids.append(evidence_id)
    if evidence_ids != sorted(evidence_ids) or len(evidence_ids) != len(set(evidence_ids)):
        raise AgentProtocolError("historical evidence ids are not canonical and unique")
    if expected_evidence_ids is not None and set(evidence_ids) != expected_evidence_ids:
        raise AgentProtocolError("historical evidence does not match the durable mission")
    encoded_bundle = _canonical_json(value).encode("utf-8")
    if len(encoded_bundle) > _MAX_HISTORICAL_EVIDENCE_BUNDLE_BYTES:
        raise AgentProtocolError("historical evidence bundle exceeds its durable bound")
    unsigned = {key: value[key] for key in value if key != "bundleDigest"}
    actual_digest = hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_digest, bundle_digest):
        raise AgentProtocolError("historical evidence bundle digest mismatched")
    return json.loads(_canonical_json(value))


def build_historical_evidence_bundle(
    *,
    mission_digest: str,
    evidence: Mapping[str, tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Build one deterministic bundle from exact durable journal rows."""

    unsigned = {
        "schemaVersion": "1",
        "missionDigest": mission_digest,
        "items": [
            {
                "sourceRunId": source_run_id,
                "evidence": payload,
            }
            for evidence_id in sorted(evidence)
            for source_run_id, payload in [evidence[evidence_id]]
        ],
    }
    bundle = {
        **unsigned,
        "bundleDigest": hashlib.sha256(
            _canonical_json(unsigned).encode("utf-8")
        ).hexdigest(),
    }
    return validate_historical_evidence_bundle(
        bundle,
        mission_digest=mission_digest,
        expected_evidence_ids=set(evidence),
    )
_STRUCTURAL_ANALYSIS_TOOL_ALIASES = {
    alias: raw_name
    for alias, raw_name in AGENT_TOOL_NAMES.items()
    if raw_name in STRUCTURAL_ANALYSIS_TOOL_COVERAGE
}
pass
_READ_ONLY_RECOVERY_DESIGN_TOOL_NAMES = frozenset(
    {"design.inspect", "design.read", "design.search"}
)
_RECOVERY_DESIGN_TOOL_NAMES = _READ_ONLY_RECOVERY_DESIGN_TOOL_NAMES | {
    "design.edit", "design.check", "design.update_work_plan",
}
_SAFE_VERIFICATION_SCOPES = frozenset(
    {
        "offline_native_check_only",
        "offline_native_reopen",
        
    }
)
_QUARANTINE_DIAGNOSTIC_TOOL_NAMES = frozenset(
    {
        "get_xg5000_session_status",
        "inspect_xg5000_modal",
        "cancel_xg5000_modal",
    }
)
_SAFE_QUARANTINE_FAILURE_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
_SAFE_MCP_ERROR_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")




def _safe_mcp_rejection_code(
    error: MCPToolRejectedError,
) -> str | None:
    """Project only a validated MCP diagnostic code into ``reason_code``."""

    payload = error.mcp_error_payload
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"schema_version", "status", "error"}
        or payload.get("schema_version") != "1.0"
        or payload.get("status") != "error"
        or not isinstance(payload.get("error"), Mapping)
        or set(payload["error"]) not in ({"type", "message"}, {"type", "message", "code"})
        or not isinstance(payload["error"].get("type"), str)
        or not isinstance(payload["error"].get("message"), str)
    ):
        return None
    code = payload["error"].get("code")
    if not isinstance(code, str) or _SAFE_MCP_ERROR_CODE.fullmatch(code) is None:
        return None
    return code


def _verified_mcp_native_failure(error: MCPToolRejectedError) -> dict[str, Any] | None:
    """Copy only the validated public nativeFailure from this MCP rejection."""

    payload = error.mcp_error_payload
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"schema_version", "status", "error"}
        or payload.get("schema_version") != "1.0"
        or payload.get("status") != "error"
    ):
        return None
    envelope = payload.get("error")
    if (
        not isinstance(envelope, Mapping)
        or not {"type", "message", "nativeFailure"} <= set(envelope)
        or set(envelope) - {"type", "message", "code", "nativeFailure", "programCheckFailure"}
        or not isinstance(envelope.get("type"), str)
        or not isinstance(envelope.get("message"), str)
    ):
        return None
    try:
        return validate_native_failure(envelope["nativeFailure"])
    except (NativeFailureProtocolError, TypeError, ValueError):
        return None


_STRUCTURAL_TOOL_RESULT_PROJECTION_SCHEMA = (
    "controlstudio-structural-analysis-tool-result-projection/v1"
)
_PROJECT_STRUCTURE_EVIDENCE_PROJECTION_SCHEMA = (
    "controlstudio-project-structure-evidence-projection/v1"
)
_STRUCTURAL_TOOL_RESULT_FRAME_MAX_BYTES = min(
    512_000,
    MAX_PROTOCOL_LINE_BYTES - 1,
)
_STRUCTURAL_TOOL_RESULT_GUIDANCE = (
    "The raw MCP result was intentionally omitted. Use "
    "_controlstudio_evidence.receipt as the authoritative source for canonical "
    "structural facts, scope, coverage, and evidence citation. modelContext, when "
    "present, is a bounded Host-revalidated interpretation for inferences only; "
    "do not copy it into observations."
)
_OUTPUT_CONTRACT_STAGES = frozenset(
    {
        "starting",
        "native_ld_execution",
        "engineering_analysis",
        "planning_narrative",
        "planning_native_design",
        "planning_finalize",
    }
)
_OUTPUT_CONTRACT_EXCEPTION_KINDS = frozenset(
    {
        "model_retry",
        "unexpected_model_behavior",
        "incomplete_tool_call",
        "workspace_edit_rejected",
    }
)
_OUTPUT_CONTRACT_REASON_CODES = frozenset(
    {
        "validator_rejected",
        "schema_validation",
        "incomplete_output",
        "unexpected_model_output",
        "exact_repeated_noop",
        "different_semantic_noop",
        "ineffective_patch_repeat",
        "metadata_only_with_diagnostics",
        "repeated_edit_candidate",
        "create_identity_conflict",
        "create_identity_evidence_missing",
        "repeated_create_identity",
        # Native workspace transport causes are emitted by the provider-neutral
        # conversation layer.  Keep these as closed tokens: no provider text,
        # arguments, or exception detail crosses the Host boundary.
        "invalid_native_tool_call",
        "assistant_message_not_object",
        "completion_choice_count",
        "native_tool_call_truncated",
        "native_tool_call_incomplete",
        "assistant_message_missing",
        "native_tool_call_count",
        "native_tool_calls_missing",
        "native_tool_name_unavailable",
        "native_tool_call_id_missing",
        "native_tool_arguments_missing",
        "native_tool_arguments_invalid_json",
        "native_tool_arguments_not_object",
        "assistant_tool_message_not_retainable",
        "native_tool_call_filtered",
    }
)
_NATIVE_TOOL_REASON_CODES = frozenset(
    {
        "invalid_native_tool_call",
        "assistant_message_not_object",
        "completion_choice_count",
        "native_tool_call_truncated",
        "native_tool_call_incomplete",
        "assistant_message_missing",
        "native_tool_call_count",
        "native_tool_calls_missing",
        "native_tool_name_unavailable",
        "native_tool_call_id_missing",
        "native_tool_arguments_missing",
        "native_tool_arguments_invalid_json",
        "native_tool_arguments_not_object",
        "assistant_tool_message_not_retainable",
        "native_tool_call_filtered",
    }
)
_WORKSPACE_EDIT_REJECTION_REASON_CODES = frozenset(
    {
        "exact_repeated_noop",
        "different_semantic_noop",
        "ineffective_patch_repeat",
        "metadata_only_with_diagnostics",
        "repeated_edit_candidate",
        "create_identity_conflict",
        "create_identity_evidence_missing",
        "repeated_create_identity",
    }
)
_MEMORY_VALIDATION_REASON_CODES = frozenset(('memory_json_invalid', 'memory_delta_schema', 'memory_update_object', 'memory_update_fields', 'memory_entry_ref_invalid', 'memory_entry_ref_section', 'memory_entry_ref_removed', 'memory_summary_schema', 'memory_summary_entries', 'memory_claim_invalid', 'memory_reference_invalid', 'memory_summary_budget', 'memory_context_budget'))
_RUNTIME_EXCEPTION_KINDS = frozenset(
    {
        "usage_limit_exceeded",
        "model_transport",
        "model_api_error",
        "tool_failure",
        "runtime_error",
    }
)
_RUNTIME_REASON_CODES = frozenset(
    {
        "tool_call_budget",
        "request_budget",
        "token_budget",
        "runtime_budget",
        "context_capacity",
        "provider_timeout",
        "provider_connection",
        "provider_rejected",
        "tool_error",
        "host_policy",
        "transport",
        "cancelled",
        "runtime_invariant",
    }
)
_CHANGE_OUTCOME_KEYS = frozenset(
    {
        "schemaVersion",
        "transactionId",
        "planDigest",
        "state",
        "disposition",
        "evidenceId",
        "outcomeDigest",
        "verificationEvidenceDigest",
        "errorCode",
    }
)
_CHANGE_OUTCOME_DISPOSITIONS = {
    "COMPLETED": "applied",
    "ROLLED_BACK": "rolled_back",
    "FAILED": "failed",
    "REJECTED": "rejected",
    "EXPIRED": "expired",
}
_OUTPUT_CONTRACT_VALIDATOR_CODES = frozenset(
    {
        "not_applicable",
        "shortened_knowledge_reference",
        "unreturned_knowledge_reference",
        "invented_controller",
        "invented_device_address",
        "invented_contact_polarity",
        "invalid_collection_evidence",
        "requirement_source_invalid",
        "requirement_source_missing",
        "requirement_brief_unresolved",
        "requirement_brief_unavailable",
        "planning_narrative_incomplete",
        "planning_question_missing",
        "planning_question_repeated",
        "planning_address_question_repeated",
        "planning_polarity_question_repeated",
        "planning_self_answered_question",
        "planning_answered_question_repeated",
        "typed_tool_arguments",
        "candidate_current_run",
        "candidate_coverage_shape",
        "candidate_coverage_source",
        "proposal_narrative",
        "verification_scope",
        "structured_output_contract",
        "native_collection_evidence",
        "native_collection_confirmed_empty",
        "native_collection_not_applicable_nonempty",
        "native_collection_scope",
        "native_recovery_scope",
        "native_safety_scope",
        "native_io_safe_state",
        "native_duplicate_identity",
        "native_unknown_reference",
        "native_condition_invalid",
        "native_sequence_structure",
        "native_sequence_reset_step_reserved",
        "native_sequence_group_mismatch",
        "native_sequence_final_release_missing",
        "native_sequence_reset_release_missing",
        "native_sequence_recovery_release_missing",
        "native_output_network_coverage",
        "native_output_safety_coverage",
        "native_ladder_shape",
        "native_ladder_unknown_reference",
        "native_ladder_invalid_target",
        "native_ladder_action_universe",
        "native_ladder_out_guarantee",
        "native_ladder_execution_guarantee",
        "native_configuration",
        "native_attempt_status",
        "continuation_approval",
        "continuation_citation",
        "continuation_contract",
        "continuation_state_transition",
        "continuation_mission_anchor",
        "continuation_evidence_completion",
        "continuation_evidence_coverage",
        "continuation_mission_binding",
        "continuation_no_progress",
        "continuation_structural_binding",
    }
)
_VALIDATION_LOCATION_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-[],"
)


def _finish_native_checkpoint(transfer: CheckpointTransfer, digest: str, previous=None, *, allow_append=False) -> tuple[dict[str, Any], str]:
    payload = transfer.finish_encoded(digest)
    if payload.text.startswith('{"append":'):
        if allow_append is not True:
            raise AgentProtocolError("Native checkpoint append was not negotiated")
        body = validated_native_checkpoint_append(payload, previous)
    else:
        body = validated_native_checkpoint_transfer(payload)
        if previous is not None and not native_checkpoint_history_extends(body, previous):
            raise AgentProtocolError("Native full checkpoint history regressed or changed")
    return body, body.digest


class AgentSidecarError(RuntimeError):
    """A sanitized failure reported by the supervised Agent process."""


_SIDECAR_FAILURE_REASONS = frozenset({'unclassified','process_start_failed','not_running',
    'pipe_closed','pipe_drain_timeout','stdout_eof','response_timeout',
    'handshake_failed','handshake_timeout','handshake_protocol'})
_SIDECAR_FAILURE_OPERATIONS = frozenset({'unknown','process_start','handshake','protocol_send','protocol_receive',
    'initialize','health','run_start','run_response','knowledge_search','capability','shutdown',
    'native_checkpoint_chunk','native_checkpoint_sync','knowledge_chunk','knowledge_sync',
    'agent_checkpoint_chunk','agent_checkpoint_sync','transcript_checkpoint_chunk','transcript_checkpoint_sync'})


def _sidecar_failure_operation(value: str) -> str:
    if value in _SIDECAR_FAILURE_OPERATIONS:
        return value
    mapping={'initialize':'initialize','health':'health','run.start':'run_start','run response':'run_response',
        'knowledge search':'knowledge_search','capability response':'capability','shutdown response':'shutdown',
        'native_checkpoint.reuse':'native_checkpoint_sync',
        'native_checkpoint.chunk':'native_checkpoint_chunk','native_checkpoint.sync':'native_checkpoint_sync',
        'knowledge.chunk':'knowledge_chunk','knowledge.sync':'knowledge_sync',
        'agent_checkpoint.chunk':'agent_checkpoint_chunk','agent_checkpoint.sync':'agent_checkpoint_sync',
        'transcript_checkpoint.chunk':'transcript_checkpoint_chunk','transcript_checkpoint.sync':'transcript_checkpoint_sync'}
    return mapping.get(value,mapping.get(value.removesuffix(' response'),'unknown'))


class AgentSidecarCrashed(AgentSidecarError):
    """The sidecar exited or violated the protocol while a Host operation ran."""
    def __init__(self,message: str, *, reason='unclassified', operation='unknown', exit_code=None):
        super().__init__(message)
        self.safe_sidecar_diagnostic={'reason':reason if reason in _SIDECAR_FAILURE_REASONS else 'unclassified',
            'operation':_sidecar_failure_operation(operation),
            'exit_code':exit_code if type(exit_code) is int and -(2**31)<=exit_code<=2**32-1 else None}


def sidecar_failure_diagnostic(error: BaseException) -> dict | None:
    """Closed scalar observations only; no exception text, command or stderr."""
    value=getattr(error,'safe_sidecar_diagnostic',None) if isinstance(error,AgentSidecarCrashed) else None
    if (not isinstance(value,dict) or set(value)!={'reason','operation','exit_code'}
            or not isinstance(value.get('reason'),str) or value['reason'] not in _SIDECAR_FAILURE_REASONS
            or not isinstance(value.get('operation'),str) or value['operation'] not in _SIDECAR_FAILURE_OPERATIONS
            or value['exit_code'] is not None and (type(value['exit_code']) is not int or not -(2**31)<=value['exit_code']<=2**32-1)):
        return None
    return dict(value)


def sidecar_failure_detail(value: dict) -> str:
    # Called only with the validated closed projection above.
    return f"reason={value['reason']}; operation={value['operation']}; exitCode={value['exit_code']}"


def _log_agent_startup_observation(command: Sequence[str], stage: str, started_at: float, operation: str) -> None:
    """Host-observed boundaries only; never log argv, paths or environment."""
    executable = str(command[0]).replace('\\', '/').rsplit('/', 1)[-1] if command else 'unknown'
    if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', executable) is None:
        executable = 'unknown'
    module = 'none'
    if '-m' in command:
        index = command.index('-m') + 1
        module = ('controlstudio_agent.sidecar'
                  if index < len(command) and command[index] == 'controlstudio_agent.sidecar' else 'other')
    logging.getLogger('uvicorn.error.controlstudio_agent_startup').info(
        'Agent startup observation: stage=%s operation=%s elapsed_ms=%s executable=%s module=%s',
                stage, operation, max(0, round((time.monotonic() - started_at) * 1000)), executable, module)


class AgentSupervisor:
    """Own the child lifecycle and its stdout-only NDJSON protocol.

    The command is intentionally fixed by default.  Neither the MCP process nor
    its authorization secret is inherited as an explicit command argument.
    """

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        startup_timeout_seconds: float = 30.0,
        shutdown_timeout_seconds: float = 3.0,
        message_timeout_seconds: float = 600.0,
        pipe_drain_timeout_seconds: float = 5.0,
    ) -> None:
        # A shipped Platform resolves the installed private artifact from PATH
        # or an explicitly injected immutable artifact command. It never
        # discovers a checkout-relative or sibling Agent source tree.
        configured_command = _configured_agent_command()
        if command is not None:
            self._command = tuple(command)
        elif configured_command is not None:
            self._command = configured_command
        elif development_command := _monorepo_agent_command():
            self._command = development_command
        else:
            self._command = ("controlstudio-agent",)
        self._startup_timeout_seconds = startup_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._message_timeout_seconds = message_timeout_seconds
        self._pipe_drain_timeout_seconds = pipe_drain_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._process_job: Any | None = None
        self._process_job_api: Any | None = None
        self._process_job_assigned = False
        self._messages: asyncio.Queue[AgentProtocolMessage | None] = asyncio.Queue()
        self._lifecycle_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._manifest_agent_id: str | None = None
        self._manifest_capabilities: frozenset[str] = frozenset()
        self._knowledge_snapshot: tuple[str, int] | None = None
        self._active_run_id: str | None = None
        self._stderr_output_contract_diagnostic: AgentOutputContractDiagnostic | None = None

    @property
    def manifest_agent_id(self) -> str | None:
        return self._manifest_agent_id

    async def ensure_started(self) -> None:
        async with self._lifecycle_lock:
            if self._process is not None and self._process.returncode is None:
                return
            self._knowledge_snapshot = None
            await self._discard_dead_process()
            started_at = time.monotonic()
            try:
                creationflags = 0
                if os.name == 'nt':
                    from .windows_mcp_transport import (
                        _assign_process_to_cleanup_job,
                        _open_process_cleanup_job,
                        _resume_suspended_cleanup_process,
                    )
                    self._process_job, self._process_job_api = _open_process_cleanup_job()
                    # CREATE_SUSPENDED is the Win32 creation flag 0x4; Python
                    # does not export it on all supported Windows runtimes.
                    creationflags = 0x00000004 | subprocess.CREATE_NO_WINDOW
                async def create_owned_process():
                    process = await asyncio.create_subprocess_exec(
                        *self._command,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=_sidecar_environment(),
                        limit=MAX_PROTOCOL_LINE_BYTES + 1,
                        creationflags=creationflags,
                    )
                    self._process = process
                    if self._process_job is not None:
                        _assign_process_to_cleanup_job(process, self._process_job, self._process_job_api)
                        self._process_job_assigned = True
                        _resume_suspended_cleanup_process(process)
                    return process
                process = await native_checkpoint_boundary(create_owned_process())
            except BaseException as exc:
                await self._terminate_process()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise AgentSidecarCrashed("Agent sidecar could not be started", reason="process_start_failed", operation="process_start") from exc
            _log_agent_startup_observation(self._command, "process_started", started_at, "process_start")
            self._stderr_output_contract_diagnostic = None
            messages: asyncio.Queue[AgentProtocolMessage | None] = asyncio.Queue()
            self._messages = messages
            self._stdout_task = asyncio.create_task(
                self._read_stdout(process, messages),
                name="controlstudio-agent-sidecar-stdout",
            )
            self._stderr_task = asyncio.create_task(
                self._drain_stderr(process), name="controlstudio-agent-sidecar-stderr"
            )
            operation = "initialize"
            try:
                initialized = await asyncio.wait_for(
                    self._request({"type": "initialize"}, expected="initialized"),
                    timeout=self._startup_timeout_seconds,
                )
                _log_agent_startup_observation(self._command, "initialize_response", started_at, operation)
                manifest_payload = initialized.payload.get("manifest")
                if not isinstance(manifest_payload, Mapping):
                    raise AgentProtocolError("Agent initialization omitted a manifest")
                manifest = parse_manifest(manifest_payload)
                self._manifest_agent_id = manifest.agent_id
                self._manifest_capabilities = frozenset(manifest.capabilities)
                operation = "health"
                health = await asyncio.wait_for(
                    self._request({"type": "health"}, expected="health.result"),
                    timeout=self._startup_timeout_seconds,
                )
                _log_agent_startup_observation(self._command, "health_response", started_at, operation)
                if health.payload.get("status") != "ready":
                    raise AgentProtocolError("Agent sidecar health check failed")
            except BaseException as exc:
                _log_agent_startup_observation(self._command, "failed", started_at, operation)
                exit_code=process.returncode
                await self._terminate_process()
                if isinstance(exc, AgentSidecarError):
                    raise
                reason=('handshake_timeout' if isinstance(exc,asyncio.TimeoutError) else
                        'handshake_protocol' if isinstance(exc,AgentProtocolError) else 'handshake_failed')
                raise AgentSidecarCrashed("Agent sidecar handshake failed", reason=reason,
                    operation=operation, exit_code=exit_code) from exc

    async def run(
        self,
        payload: Mapping[str, Any],
        *,
        on_message: Callable[[AgentProtocolMessage], Awaitable[None]],
        historical_evidence: Mapping[str, Any] | None = None,
        agent_checkpoint: Mapping[str, Any] | None = None,
        transcript_checkpoint: Mapping[str, Any] | None = None,
        native_checkpoint: Mapping[str, Any] | None = None,
        request_native_checkpoint_append: bool = False,
        on_native_checkpoint_negotiated: Callable[[bool], None] | None = None,
    ) -> None:
        """Run one supervised Agent operation until its terminal sidecar frame."""

        async with self._run_lock:
            run_id = payload.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise AgentProtocolError("Agent run id is invalid")
            cancelled_terminal_received = False
            deferred_messages: list[AgentProtocolMessage] = []
            try:
                await self.ensure_started()
                # A sidecar process is intentionally reusable across runs;
                # never let a previous run's stderr summary become the
                # fallback diagnostic for a later stdout EOF.
                self._stderr_output_contract_diagnostic = None
                self._active_run_id = run_id
                if (
                    historical_evidence is not None or agent_checkpoint is not None
                ) and (
                    _DURABLE_RESUME_SYNC_CAPABILITY
                    not in self._manifest_capabilities
                ):
                    raise AgentProtocolError(
                        "Agent sidecar does not support durable resume sync"
                    )
                if historical_evidence is not None:
                    await self._sync_historical_evidence_locked(
                        historical_evidence
                    )
                if agent_checkpoint is not None:
                    await self._sync_agent_checkpoint_locked(agent_checkpoint)
                if transcript_checkpoint is not None:
                    if "durable-transcript-resume-v1" not in self._manifest_capabilities:
                        raise AgentProtocolError("Agent sidecar does not support transcript resume sync")
                    await self._sync_transcript_checkpoint_locked(transcript_checkpoint)
                if native_checkpoint is not None:
                    native_descriptor = payload.get('trusted_context', {}).get('native_ld_execution', {})
                    await self._sync_native_checkpoint_locked(native_checkpoint,
                        conversation_id=payload.get('conversation_id'), restore_scope=native_descriptor.get('restore_scope'))
                # Only this supervisor owns the current successful handshake.
                # Select the wire mode inside the same serialized run boundary;
                # cold/restarted processes cannot inherit a stale capability.
                native_append_negotiated = (request_native_checkpoint_append is True
                    and "durable-native-append-v1" in self._manifest_capabilities)
                run_payload = dict(payload)
                run_payload.pop("native_memory_ack", None)
                if "durable-native-memory-ack-v1" in self._manifest_capabilities:
                    run_payload["native_memory_ack"] = True
                run_payload.pop("native_checkpoint_append", None)
                if native_append_negotiated:
                    run_payload["native_checkpoint_append"] = True
                # Notify the receiver synchronously before run.start can emit
                # any frame. Send and receive consume one exact negotiation.
                if on_native_checkpoint_negotiated is not None:
                    on_native_checkpoint_negotiated(native_append_negotiated)
                await self._send("run.start", run_payload)
                while True:
                    message = (deferred_messages.pop(0) if deferred_messages else
                        await self._next_message_with_timeout("run response"))
                    if message.type == "run.started":
                        continue
                    if message.type == "run.cancelled":
                        cancelled_terminal_received = True
                    if message.type == "tool.request":
                        # The tool can take minutes. Keep receiving progress while
                        # preserving the order of every other protocol frame.
                        dispatch = asyncio.create_task(on_message(message))
                        receive = None
                        try:
                            while not dispatch.done():
                                receive = asyncio.create_task(self._next_message())
                                done, _ = await asyncio.wait(
                                    {dispatch, receive}, return_when=asyncio.FIRST_COMPLETED)
                                if receive in done:
                                    incoming = receive.result()
                                    receive = None
                                    if incoming.type == "run.progress":
                                        await on_message(incoming)
                                    else:
                                        deferred_messages.append(incoming)
                            if receive is not None:
                                if not receive.done():
                                    receive.cancel()
                                outcome = (await asyncio.gather(receive, return_exceptions=True))[0]
                                receive = None
                                if isinstance(outcome, AgentProtocolMessage):
                                    deferred_messages.append(outcome)
                                elif isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                                    raise outcome
                            await dispatch
                        finally:
                            if receive is not None:
                                if not receive.done():
                                    receive.cancel()
                                outcome = await asyncio.gather(receive, return_exceptions=True)
                                if isinstance(outcome[0], AgentProtocolMessage):
                                    deferred_messages.append(outcome[0])
                            if not dispatch.done():
                                dispatch.cancel()
                            await asyncio.gather(dispatch, return_exceptions=True)
                    else:
                        await on_message(message)
                    if message.type in {"run.final", "run.error", "run.cancelled"}:
                        return
            except asyncio.CancelledError:
                # Normal cancellation first lets the child flush its already
                # completed public prefix. Never dispatch a new tool while
                # draining; bounded shutdown and queue isolation stay intact.
                try:
                    if self._active_run_id == run_id and not cancelled_terminal_received:
                        async def drain_cancelled_prefix() -> None:
                            await self.cancel_run(run_id)
                            while True:
                                message = (deferred_messages.pop(0) if deferred_messages else
                                    await self._next_message())
                                if message.type == "tool.request":
                                    # Cancellation never dispatches queued work.
                                    continue
                                if message.payload.get("run_id") != run_id:
                                    raise AgentProtocolError("Agent cancellation frame belongs to another run")
                                if message.type in {"run.cancelled", "run.final", "run.error"}:
                                    return
                                if message.type in {
                                    "run.transcript_checkpoint.chunk", "run.transcript_checkpoint",
                                    "run.mission_checkpoint.chunk", "run.mission_checkpoint",
                                    "run.native_checkpoint.chunk", "run.native_checkpoint",
                                }:
                                    await on_message(message)
                                elif message.type not in {
                                    "run.cancel.accepted", "run.started", "run.progress", "run.token", "run.knowledge_event",
                                }:
                                    raise AgentProtocolError("Agent cancellation frame is invalid")

                        await asyncio.wait_for(
                            drain_cancelled_prefix(),
                            timeout=self._shutdown_timeout_seconds,
                        )
                except (Exception, asyncio.CancelledError) as exc:
                    logger.warning(
                        "Agent cancellation checkpoint drain failed (type=%s, checkpoint_flush_completed=false)",
                        type(exc).__name__,
                    )
                finally:
                    async with self._lifecycle_lock:
                        await self._terminate_process()
                raise
            except BaseException:
                # A callback may have durably rejected a checkpoint or failed
                # while dispatching a tool. Never let frames from that run
                # remain queued for the next operation: the next request must
                # handshake with a fresh sidecar and a fresh message queue.
                async with self._lifecycle_lock:
                    await self._terminate_process()
                raise
            finally:
                if self._active_run_id == run_id:
                    self._active_run_id = None

    async def _sync_historical_evidence_locked(
        self,
        bundle: Mapping[str, Any],
    ) -> None:
        """Sync one exact bundle while the caller holds the run lock."""

        bundle_digest = bundle.get("bundleDigest")
        mission_digest = bundle.get("missionDigest")
        items = bundle.get("items")
        if (
            not isinstance(bundle_digest, str)
            or not isinstance(mission_digest, str)
            or not isinstance(items, list)
        ):
            raise AgentProtocolError("historical evidence sync bundle is invalid")
        ready = await self._request(
            {
                "type": "historical_evidence.sync.begin",
                "payload": {
                    "schemaVersion": "1",
                    "missionDigest": mission_digest,
                    "bundleDigest": bundle_digest,
                    "itemCount": len(items),
                },
            },
            expected="historical_evidence.sync.ready",
        )
        if ready.payload.get("bundleDigest") != bundle_digest:
            raise AgentProtocolError("historical evidence sync ready digest mismatched")
        for index, item in enumerate(items):
            accepted = await self._request(
                {
                    "type": "historical_evidence.sync.item",
                    "payload": {
                        "bundleDigest": bundle_digest,
                        "index": index,
                        "item": item,
                    },
                },
                expected="historical_evidence.sync.accepted",
            )
            if (
                accepted.payload.get("bundleDigest") != bundle_digest
                or accepted.payload.get("index") != index
            ):
                raise AgentProtocolError(
                    "historical evidence sync item acknowledgement mismatched"
                )
        synced = await self._request(
            {
                "type": "historical_evidence.sync.commit",
                "payload": {"bundleDigest": bundle_digest},
            },
            expected="historical_evidence.synced",
        )
        if synced.payload != {
            "missionDigest": mission_digest,
            "bundleDigest": bundle_digest,
            "itemCount": len(items),
        }:
            raise AgentProtocolError("historical evidence sync acknowledgement is invalid")

    async def _sync_agent_checkpoint_locked(
        self,
        checkpoint: Mapping[str, Any],
    ) -> None:
        """Stream persisted history without coupling it to one protocol frame."""

        canonical_checkpoint = json.loads(_canonical_json(checkpoint))
        assert_agent_safe_payload(canonical_checkpoint)
        encoded = _canonical_json(canonical_checkpoint).encode("utf-8")
        checkpoint_digest = hashlib.sha256(encoded).hexdigest()
        for chunk in checkpoint_chunks(canonical_checkpoint):
            accepted = await self._request(
                {"type": "engineering_checkpoint.chunk", "payload": chunk},
                expected="engineering_checkpoint.chunk.accepted",
            )
            if accepted.payload != {
                "checkpointDigest": checkpoint_digest, "index": chunk["index"],
            }:
                raise AgentProtocolError("Agent checkpoint chunk acknowledgement differs")
        synced = await self._request(
            {
                "type": "engineering_checkpoint.sync",
                "payload": {
                    "checkpointDigest": checkpoint_digest,
                },
            },
            expected="engineering_checkpoint.synced",
        )
        if synced.payload != {"checkpointDigest": checkpoint_digest}:
            raise AgentProtocolError("Agent checkpoint sync acknowledgement is invalid")

    async def _sync_transcript_checkpoint_locked(
        self, checkpoint: Mapping[str, Any],
    ) -> None:
        canonical, digest, _, _ = validated_transcript_checkpoint(checkpoint)
        assert_agent_safe_payload(canonical)
        for chunk in checkpoint_chunks(canonical):
            accepted = await self._request(
                {"type": "transcript_checkpoint.chunk", "payload": chunk},
                expected="transcript_checkpoint.chunk.accepted",
            )
            if accepted.payload != {"checkpointDigest": digest, "index": chunk["index"]}:
                raise AgentProtocolError("Agent transcript chunk acknowledgement differs")
        synced = await self._request(
            {"type": "transcript_checkpoint.sync", "payload": {"checkpointDigest": digest}},
            expected="transcript_checkpoint.synced",
        )
        if synced.payload != {"checkpointDigest": digest}:
            raise AgentProtocolError("Agent transcript sync acknowledgement differs")

    async def send_tool_result(
        self, payload: Mapping[str, Any], *,
        transport_plan: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        for message_type, part in transport_plan or [("tool.result", dict(payload))]:
            await self._send(message_type, part)

    async def _sync_native_checkpoint_locked(self, checkpoint: Mapping[str, Any], *,
                                              conversation_id=None, restore_scope=None) -> None:
        snapshot = (checkpoint if type(checkpoint) is ValidatedNativeCheckpoint else
                    await native_checkpoint_boundary(asyncio.to_thread(
                        validated_native_checkpoint_snapshot, checkpoint, decoded_json=False)))
        digest = snapshot.digest
        if ('durable-native-restore-ref-v1' in self._manifest_capabilities
                and isinstance(conversation_id, str) and isinstance(restore_scope, str)):
            reused = await self._request({'type': 'native_checkpoint.reuse', 'payload': {
                'conversation_id': conversation_id, 'restore_scope': restore_scope,
                'reference': snapshot.restore_reference}}, expected='native_checkpoint.reused')
            if (set(reused.payload) != {'checkpointDigest', 'hit'}
                    or reused.payload.get('checkpointDigest') != digest
                    or type(reused.payload.get('hit')) is not bool):
                raise AgentProtocolError('Native restore reference acknowledgement differs')
            if reused.payload['hit']:
                return
        for chunk in snapshot.encoded.chunks():
            accepted = await self._request(
                {"type": "native_checkpoint.chunk", "payload": chunk},
                expected="native_checkpoint.chunk.accepted",
            )
            if accepted.payload != {"checkpointDigest": digest, "index": chunk["index"]}:
                raise AgentProtocolError("Native checkpoint chunk acknowledgement differs")
        synced = await self._request(
            {"type": "native_checkpoint.sync", "payload": {"checkpointDigest": digest}},
            expected="native_checkpoint.synced",
        )
        if synced.payload != {"checkpointDigest": digest}:
            raise AgentProtocolError("Native checkpoint sync acknowledgement differs")

    async def sync_knowledge(self, documents: Sequence[Mapping[str, Any]]) -> int:
        """Replace the Agent's disposable derived source/index snapshot."""

        async with self._run_lock:
            try:
                await self.ensure_started()
                snapshot = {"documents": [dict(item) for item in documents]}
                digest = hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()
                if (self._knowledge_snapshot is not None and self._knowledge_snapshot[0] == digest
                        and self._process is not None and self._process.returncode is None):
                    return self._knowledge_snapshot[1]
                self._knowledge_snapshot = None
                for chunk in checkpoint_chunks(snapshot):
                    accepted = await self._request(
                        {"type": "knowledge.chunk", "payload": chunk},
                        expected="knowledge.chunk.accepted",
                    )
                    if accepted.payload != {"checkpointDigest": digest, "index": chunk["index"]}:
                        raise AgentProtocolError("Agent knowledge chunk acknowledgement differs")
                response = await self._request(
                    {"type": "knowledge.sync", "payload": {"checkpointDigest": digest}},
                    expected="knowledge.synced",
                )
                count = response.payload.get("count")
                if type(count) is not int or count < 0:
                    raise AgentProtocolError(
                        "Agent knowledge synchronization result is invalid"
                    )
                self._knowledge_snapshot = (digest, count)
                return count
            except BaseException:
                async with self._lifecycle_lock:
                    await self._terminate_process()
                raise

    async def search_knowledge(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Ask the Agent sidecar to rank its disposable derived index."""

        async with self._run_lock:
            try:
                await self.ensure_started()
                await self._send("knowledge.search", payload)
                response = await self._next_message_with_timeout("knowledge search")
                if response.type != "knowledge.search.result":
                    raise AgentProtocolError("Agent sidecar rejected knowledge search")
                results = response.payload.get("results")
                if not isinstance(results, list):
                    raise AgentProtocolError("Agent knowledge search result is invalid")
                return dict(response.payload)
            except BaseException:
                async with self._lifecycle_lock:
                    await self._terminate_process()
                raise

    async def cancel_run(self, run_id: str) -> None:
        process = self._process
        if (
            process is None
            or process.returncode is not None
            or self._active_run_id != run_id
        ):
            return
        try:
            await self._send("run.cancel", {"run_id": run_id})
        except AgentSidecarError:
            # The caller is already cancelling; a child crash cannot make a
            # Host cancellation fail or turn into a different run result.
            return

    async def test_capabilities(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        async with self._run_lock:
            try:
                await self.ensure_started()
                await self._send("capabilities.test", payload)
                while True:
                    message = await self._next_message_with_timeout(
                        "capability response"
                    )
                    if message.type == "capabilities.result":
                        return message.payload
                    if message.type == "protocol.error":
                        raise AgentSidecarError(
                            "Agent sidecar rejected the capability request"
                        )
            except BaseException:
                async with self._lifecycle_lock:
                    await self._terminate_process()
                raise

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            process = self._process
            if process is None:
                await self._terminate_process()
                return
            if process.returncode is None:
                try:
                    await self._send("shutdown", {})
                    while True:
                        response = await self._next_message_with_timeout(
                            "shutdown response",
                            timeout_seconds=self._shutdown_timeout_seconds,
                        )
                        if response.type == "shutdown.complete":
                            break
                except (AgentSidecarError, asyncio.TimeoutError):
                    pass
            await self._terminate_process()

    async def _request(
        self,
        command: Mapping[str, Any],
        *,
        expected: str,
    ) -> AgentProtocolMessage:
        message_type = command.get("type")
        if not isinstance(message_type, str):
            raise AgentProtocolError("Agent command type is invalid")
        await self._send(message_type, command.get("payload", {}))
        while True:
            response = await self._next_message_with_timeout(
                f"{message_type} response"
            )
            if response.type == expected:
                return response
            if response.type == "protocol.error":
                raise AgentProtocolError("Agent sidecar rejected a protocol command")

    async def _send(self, message_type: str, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise AgentSidecarCrashed("Agent sidecar is not running", reason="not_running", operation=_sidecar_failure_operation(message_type), exit_code=getattr(process,"returncode",None))
        try:
            encoded = encode_message(message_type, payload)
            async with self._write_lock:
                process.stdin.write(encoded)
                await asyncio.wait_for(
                    process.stdin.drain(), timeout=self._pipe_drain_timeout_seconds
                )
        except (BrokenPipeError, ConnectionError, OSError, asyncio.TimeoutError) as exc:
            exit_code=process.returncode
            await self._terminate_process()
            raise AgentSidecarCrashed("Agent sidecar pipe closed",
                reason="pipe_drain_timeout" if isinstance(exc,asyncio.TimeoutError) else "pipe_closed",
                operation=_sidecar_failure_operation(message_type), exit_code=exit_code) from exc

    async def _next_message(self) -> AgentProtocolMessage:
        message = await self._messages.get()
        if message is None:
            # stdout can close just before the stderr reader observes the
            # sidecar's final structured-contract summary. Give that reader a
            # bounded chance to publish the already allowlisted diagnostic,
            # then surface it through the same typed exception as a protocol
            # ``run.error`` frame.
            stderr_task = self._stderr_task
            if stderr_task is not None and not stderr_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(stderr_task), timeout=0.25
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            diagnostic = self._stderr_output_contract_diagnostic
            exit_code=getattr(self._process,"returncode",None)
            await self._terminate_process()
            if diagnostic is not None:
                raise AgentOutputContractError(
                    "Agent output failed its structured contract",
                    diagnostic=diagnostic,
                )
            raise AgentSidecarCrashed("Agent sidecar exited unexpectedly", reason="stdout_eof", operation="protocol_receive", exit_code=exit_code)
        if message.type == "protocol.error":
            code = message.payload.get("code")
            diagnostic = _runtime_diagnostic_from_payload(message.payload.get("diagnostic"))
            if code in ("invalid_message", "command_failed") and diagnostic is not None:
                error = AgentRuntimeError(
                    f"Agent sidecar reported a protocol error ({code})", diagnostic=diagnostic)
                error.protocol_code = code
                raise error
            raise AgentSidecarError("Agent sidecar reported a protocol error")
        return message

    async def _next_message_with_timeout(
        self,
        operation: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AgentProtocolMessage:
        try:
            return await asyncio.wait_for(
                self._next_message(),
                timeout=(
                    self._message_timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds
                ),
            )
        except AgentRuntimeError as exc:
            # Only _next_message's accepted producer code and the existing
            # operation mapper cross this command boundary, never raw payloads.
            if getattr(exc, 'protocol_code', None) in ('invalid_message', 'command_failed'):
                exc.protocol_operation = _sidecar_failure_operation(operation)
                diagnostic = exc.diagnostic
                logger.warning(
                    'Agent sidecar protocol error code=%s operation=%s stage=%s exception_kind=%s reason_code=%s',
                    exc.protocol_code, exc.protocol_operation, diagnostic.stage,
                    diagnostic.exception_kind, diagnostic.reason_code)
            raise
        except AgentSidecarCrashed as exc:
            diagnostic=sidecar_failure_diagnostic(exc)
            if diagnostic is not None and diagnostic['operation']=='protocol_receive':
                exc.safe_sidecar_diagnostic={**diagnostic,'operation':_sidecar_failure_operation(operation)}
            raise
        except asyncio.TimeoutError as exc:
            exit_code=getattr(self._process,"returncode",None)
            await self._terminate_process()
            raise AgentSidecarCrashed(f"Agent sidecar timed out waiting for {operation}",
                reason="response_timeout", operation=_sidecar_failure_operation(operation), exit_code=exit_code) from exc

    async def _read_stdout(
        self,
        process: asyncio.subprocess.Process,
        messages: asyncio.Queue[AgentProtocolMessage | None],
    ) -> None:
        assert process.stdout is not None
        try:
            while line := await process.stdout.readline():
                try:
                    message = decode_message(line)
                except AgentProtocolError:
                    logger.warning("Agent sidecar emitted an invalid stdout frame")
                    break
                await messages.put(message)
        finally:
            await messages.put(None)

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        # stderr is allowed for diagnostics, but do not mirror arbitrary child
        # text into Host logs where endpoint data or credentials could spread.
        emitted = False
        while line := await process.stderr.readline():
            emitted = True
            try:
                diagnostic = line.decode("utf-8")
            except UnicodeDecodeError:
                continue
            match = _SAFE_ENGINEERING_VALIDATOR_STDERR.fullmatch(diagnostic)
            shape_match = _SAFE_NATIVE_RESPONSE_STDERR.fullmatch(diagnostic)
            workspace_match = _SAFE_WORKSPACE_DECISION_STDERR.fullmatch(diagnostic)
            if workspace_match is not None:
                logger.warning(
                    "Agent workspace decision: cycle=%s action=%s outcome=%s input_tokens=%s output_tokens=%s",
                    workspace_match["cycle"], workspace_match["action"],
                    workspace_match["outcome"], workspace_match["input"], workspace_match["output"],
                )
            output_contract = _output_contract_diagnostic_from_stderr(diagnostic)
            if output_contract is not None:
                self._stderr_output_contract_diagnostic = output_contract
                logger.warning(
                    "Agent sidecar: structured output contract stage=%s "
                    "exception_kind=%s reason_code=%s validator_code=%s",
                    output_contract.stage,
                    output_contract.exception_kind,
                    output_contract.reason_code,
                    output_contract.validator_code,
                )
            if shape_match is not None:
                logger.warning(
                    "Agent native response: finish=%s output_tokens=%d text_chars=%d",
                    shape_match["finish"], int(shape_match["tokens"]), int(shape_match["chars"]),
                )
            if match is not None:
                logger.warning(
                    "Agent sidecar: engineering output validator rejected at "
                    "runtime frame %s line %s",
                    match["function"],
                    match["line"],
                )
            runtime_match = _SAFE_RUNTIME_FRAME_STDERR.fullmatch(diagnostic)
            if runtime_match is not None:
                logger.warning(
                    "Agent sidecar: runtime invariant at runtime frame %s line %s kind %s",
                    runtime_match["function"], runtime_match["line"], runtime_match["kind"],
                )
            working_history = _working_history_diagnostic_from_stderr(diagnostic)
            if working_history is not None:
                logger.warning(
                    "Agent working history rejected: reason=%s budget=%d fixed=%d core=%d latest=%d",
                    working_history["reason"], working_history["budget"],
                    working_history["fixed"], working_history["core"], working_history["latest"],
                )
            projection = _WORKING_HISTORY_PROJECTION_STDERR.fullmatch(diagnostic)
            if projection is not None:
                logger.warning(
                    "Agent working history projected: %s",
                    " ".join(
                        f"{key}={int(projection[key])}"
                        for key in _WORKING_HISTORY_PROJECTION_FIELDS
                    ),
                )
            provider_http = _provider_http_diagnostic_from_stderr(diagnostic)
            if provider_http is not None:
                provider_message = provider_http.get("message")
                if provider_message is None:
                    logger.warning(
                        "Agent sidecar: provider HTTP error status=%s type=%s code=%s param=%s",
                        provider_http["status"],
                        provider_http["type"],
                        provider_http["code"],
                        provider_http["param"],
                    )
                else:
                    logger.warning(
                        "Agent sidecar: provider HTTP error status=%s type=%s code=%s "
                        "param=%s message=%s",
                        provider_http["status"],
                        provider_http["type"],
                        provider_http["code"],
                        provider_http["param"],
                        provider_message,
                    )
            agent_runtime_match = _SAFE_AGENT_RUNTIME_FRAME_STDERR.fullmatch(diagnostic)
            if agent_runtime_match is not None:
                logger.warning(
                    "Agent sidecar: runtime invariant at agent frame %s.%s line %s kind %s",
                    agent_runtime_match["module"],
                    agent_runtime_match["function"],
                    agent_runtime_match["line"],
                    agent_runtime_match["kind"],
                )
            exception_class_match = _SAFE_RUNTIME_EXCEPTION_CLASS_STDERR.fullmatch(diagnostic)
            if exception_class_match is not None:
                logger.warning(
                    "Agent sidecar: runtime invariant exception class %s",
                    exception_class_match["class_name"],
                )
        if emitted:
            logger.warning("Agent sidecar emitted diagnostics on stderr")

    async def _discard_dead_process(self) -> None:
        if self._process is not None or self._process_job is not None:
            await self._terminate_process()

    async def _terminate_process(self) -> None:
        process = self._process
        stdout_task = self._stdout_task
        stderr_task = self._stderr_task
        self._process = None
        self._stdout_task = None
        self._stderr_task = None
        self._manifest_agent_id = None
        self._manifest_capabilities = frozenset()
        self._knowledge_snapshot = None
        self._active_run_id = None
        try:
            # Cancellation drains the checkpoint prefix before this boundary.
            # Closing stdin lets the existing sidecar EOF shutdown finish normally.
            if process is not None and process.stdin is not None:
                try:
                    process.stdin.close()
                except (BrokenPipeError, ConnectionResetError, RuntimeError):
                    pass
            if process is not None and process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout_seconds)
                except asyncio.TimeoutError:
                    if not self._process_job_assigned:
                        # Non-Windows, or startup failed before a suspended root
                        # entered its Job. This is the exact created Popen handle.
                        try:
                            process.terminate()
                        except ProcessLookupError:
                            pass
                        try:
                            await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout_seconds)
                        except asyncio.TimeoutError:
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                    # Assigned Windows roots/children retire together below,
                    # before another bounded wait for process/pipe completion.
            if self._process_job is not None:
                from .windows_mcp_transport import _retire_process_cleanup_job
                # Keep ownership if retirement cannot be verified; a later startup
                # must finish this cleanup before acquiring a new process tree.
                await _retire_process_cleanup_job(self._process_job, self._process_job_api,
                    timeout=self._shutdown_timeout_seconds)
                self._process_job = self._process_job_api = None
                self._process_job_assigned = False
            if process is not None and process.returncode is None:
                await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout_seconds)
        finally:
            tasks = [task for task in (stdout_task, stderr_task) if task is not None]
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=self._shutdown_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            self._stderr_output_contract_diagnostic = None


class SubprocessAgentClient:
    """Production Agent runtime adapter; Host retains all external authority."""

    def __init__(
        self,
        settings: StoredModelConnection,
        broker: ExecutionBroker,
        supervisor: AgentSupervisor,
    ) -> None:
        self._settings = settings
        self._broker = broker
        self._supervisor = supervisor
        self._change_plan_callback: ChangePlanCallback | None = None
        self._knowledge_snapshot_callback: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None

    def bind_change_planner(self, callback: ChangePlanCallback) -> None:
        self._change_plan_callback = callback

    def bind_knowledge_snapshot(
        self, callback: Callable[[], Awaitable[list[dict[str, Any]]]]
    ) -> None:
        self._knowledge_snapshot_callback = callback


    async def run(
        self,
        *,
        prompt: str,
        history: Sequence[Message],
        conversation_id: str,
        run_id: str,
        trusted_context: dict[str, Any] | None = None,
        agent_checkpoint: dict[str, Any] | None = None,
        transcript_checkpoint: dict[str, Any] | None = None,
        native_checkpoint: dict[str, Any] | None = None,
        model_context: ModelContextV1 | Mapping[str, Any] | None = None,
        on_token: TokenCallback | None = None,
        on_tool_event: ToolEventCallback | None = None,
        on_progress: ProgressCallback | None = None,
        on_mission_checkpoint: MissionCheckpointCallback | None = None,
        on_transcript_checkpoint: TranscriptCheckpointCallback | None = None,
        on_native_checkpoint: TranscriptCheckpointCallback | None = None,
    ) -> AgentRunOutput:
        native_run = bool(isinstance(trusted_context, dict)
                          and isinstance(trusted_context.get("native_ld_execution"), dict))
        if native_run and native_checkpoint is not None and on_progress is not None:
            await on_progress("native_restore_loading", 0)
        quarantine_diagnostic_only = _quarantine_diagnostic_only_context(
            trusted_context
        )
        targetless_analysis = bool(
            isinstance(trusted_context, dict)
            and trusted_context.get("targetless_analysis") is True
            and trusted_context.get("change_request_scope") == "analysis_only"
        )
        if quarantine_diagnostic_only and targetless_analysis:
            raise AgentProtocolError(
                "quarantine diagnostics cannot use the targetless analysis route"
            )
        if self._knowledge_snapshot_callback is not None and not quarantine_diagnostic_only:
            # Sync before every run: deletion/update and a restarted sidecar
            # cannot retain stale Agent-derived index entries.
            documents = await self._knowledge_snapshot_callback()
            await self._supervisor.sync_knowledge(documents)
        planning_workflow = bool(
            isinstance(trusted_context, dict)
            and trusted_context.get("workflow") == "new_project_planning"
        )
        broker_tools = (
            []
            if targetless_analysis or (planning_workflow and not native_run)
            else await self._broker.agent_tool_catalog(
                conversation_id=conversation_id,
                run_id=run_id,
            )
        )
        if quarantine_diagnostic_only:
            broker_tools = [
                tool
                for tool in broker_tools
                if tool.tool_name in _QUARANTINE_DIAGNOSTIC_TOOL_NAMES
            ]
        tools = list(broker_tools)
        catalog = {tool.tool_name for tool in broker_tools}
        design_catalog: set[str] = set()
        safe_trusted_context = _safe_trusted_context(
            trusted_context,
            knowledge_search_available=(
                self._knowledge_snapshot_callback is not None
                and not quarantine_diagnostic_only
            ),
            change_planner_available=(
                self._change_plan_callback is not None
                and not targetless_analysis
                and not quarantine_diagnostic_only
            ),
            
        ) or {}
        historical_evidence = safe_trusted_context.pop(
            "historical_evidence",
            None,
        )
        if historical_evidence is not None:
            safe_trusted_context["historical_evidence_ref"] = {
                "schemaVersion": "1",
                "missionDigest": historical_evidence["missionDigest"],
                "bundleDigest": historical_evidence["bundleDigest"],
                "itemCount": len(historical_evidence["items"]),
            }
        checkpoint_to_send = None if quarantine_diagnostic_only else agent_checkpoint
        transcript_to_send = (
            transcript_checkpoint if not quarantine_diagnostic_only else None
        )
        transcript_digest: str | None = None
        if transcript_to_send is not None:
            transcript_to_send, transcript_digest, _, _ = validated_transcript_checkpoint(transcript_to_send)
        checkpoint_digest: str | None = None
        if checkpoint_to_send is not None:
            if "engineering_mission" not in safe_trusted_context:
                raise AgentProtocolError(
                    "Agent checkpoint requires a durable engineering mission"
                )
            encoded_checkpoint = _canonical_json(checkpoint_to_send).encode("utf-8")
            checkpoint_digest = hashlib.sha256(encoded_checkpoint).hexdigest()
        sidecar_prompt = (
            _exact_offline_operand_continuation_prompt(safe_trusted_context)
            or prompt
        )
        context_payload = _model_context_payload(
            model_context
            if model_context is not None
            else getattr(self._settings, "model_context", None),
            client_input_limit_tokens=getattr(self._settings, "client_input_limit_tokens", None),
        )
        payload = {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "prompt": sidecar_prompt,
            "history": [] if checkpoint_to_send is not None or transcript_to_send is not None else _safe_history(history),
            "trusted_context": safe_trusted_context,
            "model": _model_payload(self._settings),
            "tools": [tool.model_dump(mode="json") for tool in tools],
        }
        if context_payload is not None:
            payload["model_context"] = context_payload
        if checkpoint_digest is not None:
            payload["agent_checkpoint_ref"] = {
                "schemaVersion": "1",
                "checkpointDigest": checkpoint_digest,
            }
        if transcript_digest is not None:
            payload["transcript_checkpoint_ref"] = {
                "schemaVersion": "1", "checkpointDigest": transcript_digest,
            }
        if native_checkpoint is not None:
            if not native_run:
                raise AgentProtocolError("Native checkpoint requires a native run")
            if type(native_checkpoint) is not ValidatedNativeCheckpoint:
                native_checkpoint = await native_checkpoint_boundary(asyncio.to_thread(
                    validated_native_checkpoint_snapshot, native_checkpoint, decoded_json=False))
            native_digest = native_checkpoint.digest
            payload["native_checkpoint_ref"] = {"schemaVersion": "1", "checkpointDigest": native_digest}
        assert_agent_safe_payload(payload)
        result: AgentRunOutput | None = None
        last_progress: tuple[str, int] | None = None
        last_mission_cycle = 0
        last_mission_disposition: str | None = None
        handled_tool_identities: set[str] = set()
        checkpoint_transfer = CheckpointTransfer()
        transcript_transfer = CheckpointTransfer()
        native_transfer = CheckpointTransfer()
        native_append_negotiated = False
        def native_checkpoint_negotiated(enabled: bool) -> None:
            nonlocal native_append_negotiated
            native_append_negotiated = enabled
        native_previous = native_checkpoint

        async def handle(message: AgentProtocolMessage) -> None:
            nonlocal last_mission_cycle, last_mission_disposition
            nonlocal last_progress, result, native_previous
            if message.type == "run.native_checkpoint.chunk":
                if not native_run or message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Native checkpoint chunk belongs to another run")
                try:
                    native_transfer.append({key: value for key, value in message.payload.items() if key != "run_id"})
                except ValueError as exc:
                    raise AgentProtocolError("Native checkpoint chunk is invalid") from exc
                return
            if message.type == "run.native_checkpoint":
                if (not native_run or set(message.payload) not in ({"run_id", "checkpoint_digest"}, {"run_id", "checkpoint_digest", "ack_id"})
                        or message.payload.get("run_id") != run_id or on_native_checkpoint is None):
                    raise AgentProtocolError("Native checkpoint completion is invalid")
                ack_id = message.payload.get('ack_id')
                if ack_id is not None:
                    try:
                        if str(uuid.UUID(ack_id)) != ack_id:raise ValueError()
                    except (ValueError, TypeError, AttributeError):
                        raise AgentProtocolError('Native memory ACK identity is invalid') from None
                async def complete_native_checkpoint() -> None:
                    nonlocal native_previous
                    checkpoint_started = time.perf_counter()
                    body, digest = await asyncio.to_thread(
                        _finish_native_checkpoint, native_transfer, message.payload["checkpoint_digest"], native_previous,
                        allow_append=native_append_negotiated)
                    transfer_validated_at = time.perf_counter()
                    try:
                        await on_native_checkpoint(body, digest)
                    except BaseException:
                        if ack_id is not None:
                            await self._supervisor._send('native_checkpoint.ack', {'run_id':run_id,
                                'checkpoint_digest':message.payload['checkpoint_digest'], 'ack_id':ack_id, 'status':'failed'})
                        raise
                    if ack_id is not None:
                        await self._supervisor._send('native_checkpoint.ack', {'run_id':run_id,
                            'checkpoint_digest':message.payload['checkpoint_digest'], 'ack_id':ack_id, 'status':'persisted'})
                    native_previous = body
                    persisted_at = time.perf_counter()
                    if on_tool_event is not None:
                        await on_tool_event("native.checkpoint.persisted", {
                            "elapsed_seconds": persisted_at - checkpoint_started,
                            "transfer_validate_elapsed_seconds": transfer_validated_at - checkpoint_started,
                            "callback_persist_elapsed_seconds": persisted_at - transfer_validated_at,
                            "completed_pairs_count": len(body["public_state"]["completed_pairs"]),
                        })
                # Host's sequential message loop cannot consume the next frame
                # until the same full checkpoint has been persisted (also on cancel).
                await native_checkpoint_boundary(complete_native_checkpoint())
                return
            if message.type == "run.transcript_checkpoint.chunk":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent transcript chunk belongs to another run")
                try:
                    transcript_transfer.append({key: value for key, value in message.payload.items() if key != "run_id"})
                except ValueError as exc:
                    raise AgentProtocolError("Agent transcript chunk is invalid") from exc
                return
            if message.type == "run.transcript_checkpoint":
                if set(message.payload) != {"run_id", "checkpoint_digest"} or message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent transcript completion is invalid")
                try:
                    body = transcript_transfer.finish(message.payload["checkpoint_digest"])
                    body, digest, _, _ = validated_transcript_checkpoint(body, message.payload["checkpoint_digest"])
                except (TypeError, ValueError) as exc:
                    raise AgentProtocolError("Agent transcript completion is invalid") from exc
                if on_transcript_checkpoint is None:
                    raise AgentProtocolError("Host transcript persistence callback is unavailable")
                await on_transcript_checkpoint(body, digest)
                return
            if message.type == "run.mission_checkpoint.chunk":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent checkpoint chunk belongs to another run")
                try:
                    checkpoint_transfer.append({
                        key: value for key, value in message.payload.items() if key != "run_id"
                    })
                except ValueError as exc:
                    raise AgentProtocolError(str(exc)) from exc
                return
            if message.type == "run.knowledge_event":
                from .agent_protocol import validated_knowledge_event

                event_type, event = validated_knowledge_event(message.payload, run_id)
                if on_tool_event is not None:
                    await on_tool_event(event_type, event)
                return
            if message.type == "run.progress":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent progress belongs to another run")
                stage = message.payload.get("stage")
                if native_run and stage == 'native_work_plan':
                    from .agent_protocol import validated_native_work_plan_progress
                    event = validated_native_work_plan_progress(message.payload, run_id)
                    if on_tool_event is not None:
                        await on_tool_event('agent.progress', event)
                    return
                elapsed_seconds = message.payload.get("elapsed_seconds")
                if (stage not in {
                    "starting",
                    "engineering_analysis",
                    "planning_narrative",
                    "planning_native_design",
                    "planning_finalize",
                } and not (native_run and stage in {
                    'native_restore_loading',
                    'native_restore_verifying',
                    'native_restore_verified',
                    'native_restore_state',
                    'native_restore_state_ready',
                    'native_restore_context',
                    'native_restore_context_ready',
                    'native_restore_records',
                    'native_restore_records_ready',
                    'native_restore_dialogue',
                    'native_restore_dialogue_ready',
                    'native_ld_execution',
                })) or (
                    isinstance(elapsed_seconds, bool)
                    or not isinstance(elapsed_seconds, int)
                    or elapsed_seconds < 0
                ):
                    raise AgentProtocolError("Agent progress payload is invalid")
                progress = (stage, elapsed_seconds)
                if progress != last_progress:
                    last_progress = progress
                    if on_progress is not None:
                        await on_progress(stage, elapsed_seconds)
                return
            if message.type == "run.token":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent token belongs to another run")
                delta = message.payload.get("delta")
                if isinstance(delta, str) and on_token is not None:
                    await on_token(delta)
                return
            if message.type == "run.mission_checkpoint":
                if set(message.payload) != {
                    "run_id",
                    "cycle",
                    "cycle_disposition",
                    "mission",
                    "mission_digest",
                    "agent_checkpoint",
                    "agent_checkpoint_digest",
                }:
                    raise AgentProtocolError("Agent mission checkpoint payload is invalid")
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError(
                        "Agent mission checkpoint belongs to another run"
                    )
                cycle = message.payload.get("cycle")
                cycle_disposition = message.payload.get("cycle_disposition")
                mission = message.payload.get("mission")
                mission_digest = message.payload.get("mission_digest")
                agent_checkpoint_value = message.payload.get("agent_checkpoint")
                agent_checkpoint_digest = message.payload.get("agent_checkpoint_digest")
                if agent_checkpoint_value is None:
                    try:
                        agent_checkpoint_value = checkpoint_transfer.finish(agent_checkpoint_digest)
                    except ValueError as exc:
                        raise AgentProtocolError(str(exc)) from exc
                if (
                    isinstance(cycle, bool)
                    or not isinstance(cycle, int)
                    or cycle < 1
                    or cycle != last_mission_cycle + 1
                    or last_mission_disposition == "respond"
                    or cycle_disposition not in {"continue", "respond", "checkpoint"}
                    or not isinstance(mission, dict)
                    or not isinstance(mission_digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", mission_digest) is None
                    or not isinstance(agent_checkpoint_value, dict)
                    or not isinstance(agent_checkpoint_digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", agent_checkpoint_digest) is None
                    or (
                        on_mission_checkpoint is None
                        and not quarantine_diagnostic_only
                    )
                ):
                    raise AgentProtocolError("Agent mission checkpoint is invalid")
                assert_agent_safe_payload(mission)
                if not quarantine_diagnostic_only:
                    assert on_mission_checkpoint is not None
                    await on_mission_checkpoint(
                        mission,
                        mission_digest,
                        cycle,
                        cycle_disposition,
                        agent_checkpoint_value,
                        agent_checkpoint_digest,
                    )
                if cycle_disposition != "checkpoint":
                    last_mission_cycle = cycle
                    last_mission_disposition = cycle_disposition
                return
            if message.type == "tool.request":
                request = _tool_request_from_message(message, conversation_id, run_id)
                identities = {request.request_id}
                if request.tool_call_id is not None:
                    identities.add(request.tool_call_id)
                if not identities.isdisjoint(handled_tool_identities):
                    raise AgentProtocolError(
                        "Agent repeated an external tool request identity"
                    )
                handled_tool_identities.update(identities)
                await self._dispatch_tool_request(
                    request,
                    catalog=catalog,
                    design_catalog=design_catalog,
                    on_tool_event=on_tool_event,
                    quarantine_diagnostic_only=quarantine_diagnostic_only,
                    native_run=native_run,
                )
                return
            if message.type == "run.final":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent final result belongs to another run")
                result = _output_from_payload(message.payload.get("output"))
                if result.native_project_start is not None and not (
                        targetless_analysis and safe_trusted_context.get('project_start_available') is True):
                    raise AgentProtocolError('New-project start is outside its Host-selected unbound route')
                if native_run != (result.workflow == "native_ld_partial_edit"):
                    raise AgentProtocolError("Agent final workflow differs from its Host-selected run")
                return
            if message.type == "run.cancelled":
                raise asyncio.CancelledError
            if message.type == "run.error":
                if message.payload.get("code") == "output_contract":
                    diagnostic = _output_contract_diagnostic_from_payload(
                        message.payload.get("diagnostic")
                    )
                    raise AgentOutputContractError(
                        "Agent output failed its structured contract",
                        diagnostic=diagnostic,
                    )
                if message.payload.get("code") == "agent_runtime":
                    diagnostic = _runtime_diagnostic_from_payload(
                        message.payload.get("diagnostic")
                    )
                    raise AgentRuntimeError(
                        "Agent sidecar runtime could not complete the run",
                        diagnostic=diagnostic,
                    )
                raise AgentSidecarError("Agent sidecar could not complete the run")
            raise AgentProtocolError("unexpected Agent sidecar run message")

        try:
            await self._supervisor.run(
                payload,
                on_message=handle,
                historical_evidence=historical_evidence,
                agent_checkpoint=checkpoint_to_send,
                transcript_checkpoint=transcript_to_send,
                native_checkpoint=native_checkpoint,
                request_native_checkpoint_append=native_run,
                on_native_checkpoint_negotiated=native_checkpoint_negotiated,
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._supervisor.cancel_run(run_id))
            raise
        if result is None:
            raise AgentSidecarError("Agent sidecar ended without a final result")
        return result

    async def _dispatch_tool_request(
        self,
        request: ExternalToolRequestV1,
        *,
        catalog: set[str],
        design_catalog: set[str],
        on_tool_event: ToolEventCallback | None,
        quarantine_diagnostic_only: bool = False,
        native_run: bool = False,
    ) -> None:
        completed_tool_result: dict[str, Any] | None = None
        completed_transport_plan: list[tuple[str, dict[str, Any]]] = []

        async def capture_event(
            event_type: str,
            payload: dict[str, Any],
        ) -> None:
            if on_tool_event is None:
                return
            await on_tool_event(event_type, payload)

        event_payload: dict[str, Any] = {
            "tool_name": request.tool_name,
            "request_id": request.request_id,
        }
        if request.tool_call_id is not None:
            event_payload["tool_call_id"] = request.tool_call_id
        if request.tool_name == "design.edit":
            attempt = _bounded_design_edit_attempt(request.arguments)
            if attempt is not None:
                event_payload["edit_attempt"] = attempt
        broker_owns_terminal_event = request.tool_name in catalog
        callback_terminal_event_recorded = False

        async def send_failure(
            failure_kind: str,
            *,
            failure_reason: str | None = None,
            reason_code: str | None = None,
            stage: str | None = None,
            native_failure: dict[str, Any] | None = None,
        ) -> None:
            nonlocal callback_terminal_event_recorded
            diagnostic_by_kind = {
                "cancelled": ("cancelled", "dispatch"),
                "tool_error": ("tool_rejected", "dispatch"),
                "transport": ("transport_error", "dispatch"),
                "host_policy": ("host_policy", "preflight"),
            }
            default_reason_code, default_stage = diagnostic_by_kind.get(
                failure_kind,
                ("tool_failed", "dispatch"),
            )
            reason_code = reason_code or default_reason_code
            stage = stage or default_stage
            if not broker_owns_terminal_event and not callback_terminal_event_recorded:
                failure_payload = {**event_payload, "failure_kind": failure_kind}
                if failure_reason:
                    failure_payload["failure_reason"] = failure_reason
                failure_payload["reason_code"] = reason_code
                await capture_event(
                    "tool.failed",
                    failure_payload,
                )
                callback_terminal_event_recorded = True
            tool_result = {
                "request_id": request.request_id,
                "status": "failed",
                "failure_kind": failure_kind,
                "reason_code": reason_code,
                "stage": stage,
            }
            if failure_reason:
                # This text is selected by Host code at the call sites below;
                # raw exception text, request arguments, and tool results never
                # cross into the Agent protocol.
                tool_result["failure_reason"] = failure_reason
            if native_failure is not None:
                # Only the MCP client's validated public failure is forwarded.
                # The source error envelope, transport metadata, and credentials
                # stay on the Host side.
                tool_result["nativeFailure"] = native_failure
                assert_native_public_payload(tool_result)
            transport_plan = _tool_result_transport_plan(
                tool_result,
                native_run=native_run,
            )
            await self._supervisor.send_tool_result(
                tool_result,
                transport_plan=transport_plan,
            )

        async def validate_tool_completion(
            completed_payload: dict[str, Any],
            raw_result: Any,
        ) -> None:
            nonlocal completed_tool_result
            preview_evidence = (
                await on_tool_event(
                    "tool.completion_preflight",
                    completed_payload,
                )
                if on_tool_event is not None
                else None
            )
            if request.tool_name in _STRUCTURAL_ANALYSIS_TOOL_ALIASES:
                completed_tool_result = _structural_tool_result_payload(
                    request_id=request.request_id,
                    tool_alias=request.tool_name,
                    evidence_ref=preview_evidence,
                    raw_result=raw_result,
                )
            else:
                completed_tool_result = _completed_tool_result_payload(
                    request_id=request.request_id,
                    raw_result=raw_result,
                    evidence_ref=preview_evidence,
                    native_run=native_run,
                    transport_plan=completed_transport_plan,
                )

        try:
            # This is the first Host-owned observation of the model's choice.
            # Execution starts later, after the broker/callback preconditions.
            await capture_event("tool.selected", event_payload)
            if (
                quarantine_diagnostic_only
                and request.tool_name not in _QUARANTINE_DIAGNOSTIC_TOOL_NAMES
            ):
                await send_failure(
                    "host_policy",
                    failure_reason=(
                        "This diagnostic run permits only current-session status "
                        "and owned-modal tools."
                    ),
                    reason_code="diagnostic_tool_not_allowed",
                )
                return
            if request.tool_name in catalog:
                sink = capture_event
                await self._broker.execute_external_tool_request(
                    request,
                    sink=sink,
                    completion_gate=validate_tool_completion,
                )
        except asyncio.CancelledError:
            await send_failure(
                "cancelled",
                failure_reason="선택한 도구 실행이 취소되었습니다.",
            )
            raise
        except MCPToolRejectedError as exc:
            context = mcp_rejection_context(exc.mcp_error_payload) if not native_run else {}
            await send_failure(
                "tool_error",
                failure_reason=context.get("failure_reason", "Host가 선택한 도구 요청을 거부했습니다."),
                reason_code=context.get("reason_code", _safe_mcp_rejection_code(exc)),
                stage=context.get("stage"),
                native_failure=_verified_mcp_native_failure(exc) if native_run else None,
            )
            return
        except MCPTransportError:
            await send_failure(
                "transport",
                failure_reason="도구 실행 서비스와 통신하지 못했습니다.",
            )
            return
        except MCPToolError:
            await send_failure(
                "host_policy",
                failure_reason="Host 안전 정책이 선택한 도구 실행을 허용하지 않았습니다.",
            )
            return
        except Exception as exc:  # noqa: BLE001 - keep callback failures distinct from authority rejection
            logger.error(
                "Host tool callback failed: tool=%s request_id=%s exception_type=%s",
                request.tool_name, request.request_id, type(exc).__name__,
            )
            await send_failure(
                "tool_error",
                reason_code=("tool_result_protocol_invalid"
                             if native_run and isinstance(exc, AgentProtocolError) else "tool_failed"),
                failure_reason=("Host 도구 결과가 Agent 전달 계약을 통과하지 못했습니다."
                                if native_run and isinstance(exc, AgentProtocolError)
                                else "Host 도구 처리 중 내부 오류가 발생했습니다."),
            )
            return
        if completed_tool_result is None:
            raise AgentProtocolError("Tool completion was not prevalidated")
        await self._supervisor.send_tool_result(
            completed_tool_result, transport_plan=completed_transport_plan,
        )

    async def test_capabilities(self) -> ModelCapabilityResult:
        request_id = str(uuid.uuid4())
        payload = {"request_id": request_id, "model": _model_payload(self._settings)}
        assert_agent_safe_payload(payload)
        response = await self._supervisor.test_capabilities(payload)
        if response.get("request_id") != request_id:
            raise AgentProtocolError("Agent capability result belongs to another request")
        result = response.get("result")
        if not isinstance(result, dict):
            failure_messages = {
                "model_authentication": (
                    "모델 엔드포인트 인증에 실패했습니다. API 키 또는 인증 헤더를 확인하세요."
                ),
                "model_timeout": "모델 엔드포인트 연결 시간이 초과되었습니다.",
                "model_connection": "모델 엔드포인트에 연결할 수 없습니다.",
                "model_rate_limited": (
                    "모델 엔드포인트의 요청 한도 또는 용량 제한에 걸렸습니다."
                ),
                "model_http_error": "모델 엔드포인트가 기능 검사 요청을 거부했습니다.",
                "model_runtime_dependency": (
                    "Agent 런타임 의존성을 불러오지 못했습니다. 개발 로그와 설치 BOM을 확인하세요."
                ),
            }
            message = failure_messages.get(
                response.get("code"),
                "Agent sidecar가 모델 기능을 확인하지 못했습니다.",
            )
            return ModelCapabilityResult.failed(message)
        try:
            tested_at = datetime.fromisoformat(str(result["tested_at"]))
            return ModelCapabilityResult(
                success=bool(result["success"]),
                tested_at=tested_at,
                completion=bool(result["completion"]),
                streaming=bool(result["streaming"]),
                tool_calling=bool(result["tool_calling"]),
                tool_result_continuation=bool(result["tool_result_continuation"]),
                structured_output=bool(result["structured_output"]),
                message=str(result["message"]),
                model_context=_decode_capability_model_context(result.get("model_context")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentProtocolError("Agent capability result is invalid") from exc


def _decode_capability_model_context(value: object) -> ModelContextV1 | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise AgentProtocolError("Agent capability model context is invalid")
    payload = dict(value)
    if payload.get("source") is None:
        payload["source"] = "unknown"
    try:
        return ModelContextV1.model_validate(payload)
    except (TypeError, ValueError, ValidationError) as exc:
        raise AgentProtocolError("Agent capability model context is invalid") from exc


def _model_payload(settings: StoredModelConnection) -> dict[str, Any]:
    return {
        "protocol": settings.protocol,
        "base_url": settings.base_url,
        "model": settings.model,
        "timeout_seconds": settings.timeout_seconds,
        "reasoning_effort": settings.reasoning_effort,
        # Model credentials are needed by the process that calls the model, but
        # are never persisted, logged, passed in argv, or sent to MCP.
        "api_key": settings.secrets.api_key,
        "custom_headers": settings.secrets.custom_headers,
    }


def _model_context_payload(
    value: ModelContextV1 | Mapping[str, Any] | None,
    *, client_input_limit_tokens: int | None = None,
) -> dict[str, Any] | None:
    """Validate an explicit provider observation before crossing the sidecar boundary.

    The Host deliberately does not probe ``/props`` or infer capacity from a
    model name.  A missing observation remains absent/unknown.  When a caller
    supplies one, the full contract (including an insufficient 196608 window)
    is forwarded without truncating history or claiming support.
    """

    from controlstudio_contracts.model_input_policy import validate_client_input_limit_tokens
    try:
        client_input_limit_tokens = validate_client_input_limit_tokens(client_input_limit_tokens)
    except ValueError as exc:
        raise AgentProtocolError('client input limit is invalid') from exc
    if value is None:
        return ({'client_input_limit_tokens': client_input_limit_tokens}
                if client_input_limit_tokens is not None else None)
    try:
        contract = (
            value
            if isinstance(value, ModelContextV1)
            else ModelContextV1.model_validate(value)
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise AgentProtocolError("model context observation is invalid") from exc
    # ``status`` is a read-only API projection computed from the capacity
    # fields.  It is intentionally not part of the sidecar input contract;
    # forwarding it would make an otherwise valid observation fail protocol
    # validation before the model is called.
    payload = contract.model_dump(mode="json", by_alias=False, exclude={"status"})
    if contract.history_budget_status == "unknown":
        payload.pop("history_budget_status", None)
        payload.pop("history_budget_tokens", None)
        payload.pop("history_budget_source", None)
    if client_input_limit_tokens is not None:
        payload["client_input_limit_tokens"] = client_input_limit_tokens
    return payload


def _configured_agent_command() -> tuple[str, ...] | None:
    value = os.environ.get("CONTROLSTUDIO_AGENT_COMMAND", "").strip()
    if not value:
        return None
    parts = shlex.split(value, posix=False)
    command = tuple(
        part[1:-1]
        if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}
        else part
        for part in parts
    )
    if not command:
        return None
    return command


def _monorepo_agent_command() -> tuple[str, ...] | None:
    """Resolve the Agent's installed console entry point for local development.

    This intentionally does not import private Agent source or walk its source
    tree.  A release installation has no adjacent development environment and
    therefore falls through to the packaged command on ``PATH``.
    """

    platform_repository = Path(__file__).resolve().parents[3]
    if not (platform_repository / ".git").exists():
        return None
    development_root = platform_repository.parent
    repository = development_root / "gidong-agent"
    for executable in (
        repository / ".venv" / "Scripts" / "controlstudio-agent.exe",
        repository / ".venv" / "bin" / "controlstudio-agent",
    ):
        if executable.is_file():
            return (str(executable),)
    return None


def _sidecar_environment() -> dict[str, str]:
    """Pass ordinary process settings while withholding Host approval material."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _SIDECAR_STRIPPED_ENVIRONMENT
    }
    return environment


def _safe_history(history: Sequence[Message]) -> list[dict[str, Any]]:
    """Project retained history without silently dropping older messages.

    Context admission belongs to the Agent's explicit ``model_context``
    contract.  The previous fixed 180k tail budget could discard user history
    before the sidecar observed it, and therefore could turn a 200k support
    failure into an unreported partial request.
    """

    retained_history: list[Message] = []
    for item in history:
        if item.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
            continue
        retained_history.append(item)
    return [
        {
            "role": item.role.value,
            "content": item.content,
            "created_at": item.created_at.isoformat(),
            "structured_project_brief": bool(
                item.metadata.get("structured_project_brief")
            ),
        }
        for item in retained_history
    ]


def _safe_retained_planning_narrative(value: Any) -> dict[str, Any]:
    """Bound the Host-authored narrative before crossing the sidecar boundary.

    The Agent performs the full typed validation.  This projection prevents an
    unrelated or oversized object from being transported as trusted context
    while preserving the exact narrative and source identities selected by the
    Host.
    """

    if not isinstance(value, dict):
        raise AgentProtocolError("retained design narrative is invalid")
    allowed_keys = {
        "requirement_sources",
        "status",
        *_RETAINED_PLANNING_NARRATIVE_STRING_FIELDS,
        *_RETAINED_PLANNING_NARRATIVE_LIST_LIMITS,
        "not_applicable",
    }
    if not set(value).issubset(allowed_keys):
        raise AgentProtocolError("retained design narrative fields are invalid")
    if value.get("status") != "ready_for_review":
        raise AgentProtocolError("retained design narrative is not ready for review")
    for field, limit in _RETAINED_PLANNING_NARRATIVE_STRING_FIELDS.items():
        item = value.get(field)
        if not isinstance(item, str) or not item.strip() or len(item) > limit:
            raise AgentProtocolError("retained design narrative text is invalid")
    for field, limit in _RETAINED_PLANNING_NARRATIVE_LIST_LIMITS.items():
        items = value.get(field, [])
        if (
            not isinstance(items, list)
            or len(items) > limit
            or any(not isinstance(item, str) for item in items)
        ):
            raise AgentProtocolError("retained design narrative list is invalid")
    requirement_sources = value.get("requirement_sources", [])
    if not isinstance(requirement_sources, list) or len(requirement_sources) > 5:
        raise AgentProtocolError("retained design narrative sources are invalid")
    for source in requirement_sources:
        if (
            not isinstance(source, dict)
            or not set(source).issubset({"field", "quote", "message_id", "messageId"})
            or not isinstance(source.get("field"), str)
            or not isinstance(source.get("quote"), str)
            or not source["quote"]
            or len(source["quote"]) > 20_000
            or (
                source.get("message_id", source.get("messageId")) is not None
                and not isinstance(source.get("message_id", source.get("messageId")), str)
            )
        ):
            raise AgentProtocolError("retained design narrative sources are invalid")
    not_applicable = value.get("not_applicable", [])
    if (
        not isinstance(not_applicable, list)
        or len(not_applicable) > 3
        or any(
            not isinstance(item, dict)
            or not set(item).issubset({"field", "justification"})
            or not isinstance(item.get("field"), str)
            or not isinstance(item.get("justification"), str)
            or not item["justification"].strip()
            or len(item["justification"]) > 2_000
            for item in not_applicable
        )
    ):
        raise AgentProtocolError("retained design narrative exclusions are invalid")
    projected = json.loads(_canonical_json(value))
    if len(_canonical_json(projected).encode("utf-8")) > _MAX_RETAINED_PLANNING_NARRATIVE_BYTES:
        raise AgentProtocolError("retained design narrative is too large")
    return projected




def _safe_quarantine_failure(value: Any) -> dict[str, str]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"state", "code"}
        or value.get("state") != "QUARANTINED"
        or not isinstance(value.get("code"), str)
        or _SAFE_QUARANTINE_FAILURE_CODE.fullmatch(value["code"]) is None
    ):
        raise AgentProtocolError("quarantine diagnostic failure context is invalid")
    return {"state": "QUARANTINED", "code": value["code"]}


def _quarantine_diagnostic_only_context(
    value: dict[str, Any] | None,
) -> bool:
    if not isinstance(value, dict) or value.get("quarantine_diagnostic_only") is not True:
        return False
    if (
        value.get("workflow") != "engineering"
        or value.get("change_request_scope") != "analysis_only"
        or value.get("targetless_analysis") is True
    ):
        raise AgentProtocolError("quarantine diagnostic context is invalid")
    _safe_quarantine_failure(value.get("quarantine_failure"))
    return True




def _safe_trusted_context(
    value: dict[str, Any] | None,
    *,
    knowledge_search_available: bool,
    change_planner_available: bool,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if "native_ld_execution" in value:
        from controlstudio_contracts.native_execution_context import (
            validate_native_execution_descriptor,
        )
        try:
            native = validate_native_execution_descriptor(value["native_ld_execution"],
                workflow=value.get("workflow"), change_request_scope=value.get("change_request_scope"),
                verification_scope=value.get("verification_scope"))
        except ValueError as exc:
            raise AgentProtocolError("Native execution context is invalid") from exc
        return {"workflow": value["workflow"], "change_request_scope": "permanent_offline_revision",
                "verification_scope": "offline_native_check_only", "native_ld_execution": native}
    if (
        value.get("quarantine_diagnostic_only") is True
        and value.get("workflow") != "engineering"
    ):
        raise AgentProtocolError("quarantine diagnostic context is invalid")
    if value.get("workflow") == "new_project_planning":
        brief = value.get("project_brief")
        safe_brief = {
            key: item
            for key, item in brief.items()
            if isinstance(key, str) and isinstance(item, str)
        } if isinstance(brief, dict) else {}
        if isinstance(brief, dict):
            source_ids = brief.get("attachment_source_ids")
            if isinstance(source_ids, list) and all(
                isinstance(item, str) for item in source_ids
            ):
                safe_brief["attachment_source_ids"] = list(source_ids)
            attachments = brief.get("attachments")
            if isinstance(attachments, list):
                safe_brief["attachments"] = [
                    {
                        key: item
                        for key, item in attachment.items()
                        if key
                        in {
                            "source_id",
                            "title",
                            "kind",
                            "scope",
                            "vendor",
                            "product_family",
                            "version",
                            "language",
                            "authority",
                            "sha256",
                            "revision",
                        }
                        and (isinstance(item, str) or item is None)
                    }
                    for attachment in attachments
                    if isinstance(attachment, dict)
                ]
            requirement_sources = brief.get("requirement_message_sources")
            if isinstance(requirement_sources, list):
                safe_brief["requirement_message_sources"] = [
                    {"message_id": item["message_id"], "content": item["content"]}
                    for item in requirement_sources
                    if isinstance(item, dict)
                    and isinstance(item.get("message_id"), str)
                    and isinstance(item.get("content"), str)
                ]
        context = {
            "workflow": "new_project_planning",
            "project_brief": safe_brief,
            "current_prompt_is_structured_brief": bool(
                value.get("current_prompt_is_structured_brief")
            ),
            "knowledge_search_available": knowledge_search_available,
        }
        retained_narrative = value.get("retained_design_narrative")
        if retained_narrative is not None:
            context["retained_design_narrative"] = (
                _safe_retained_planning_narrative(retained_narrative)
            )
        prior_design_failure = value.get("prior_design_failure_observation")
        if prior_design_failure is not None:
            context["prior_design_failure_observation"] = (
                _safe_prior_design_failure_observation(prior_design_failure)
            )
        return context
    if value.get("quarantine_diagnostic_only") is True:
        if (
            value.get("workflow") != "engineering"
            or value.get("change_request_scope") != "analysis_only"
            or value.get("targetless_analysis") is True
        ):
            raise AgentProtocolError("quarantine diagnostic context is invalid")
        return {
            "workflow": "engineering",
            "knowledge_search_available": False,
            "change_planner_available": False,
            
            "change_request_scope": "analysis_only",
            "quarantine_diagnostic_only": True,
            "quarantine_failure": _safe_quarantine_failure(
                value.get("quarantine_failure")
            ),
        }
    context: dict[str, Any] = {
        "workflow": "engineering",
        "knowledge_search_available": knowledge_search_available,
        "change_planner_available": change_planner_available,
        
    }
    if (
        value.get("targetless_analysis") is True
        and value.get("change_request_scope") == "analysis_only"
    ):
        # This flag grants no tool authority.  It tells the Agent that the Host
        # deliberately supplied an empty external catalog for a general chat.
        context["targetless_analysis"] = True
        if value.get("project_start_available") is True:
            context["project_start_available"] = True
    if value.get("change_request_scope") in {
        "analysis_only",
        "offline_candidate_revision",
        "permanent_offline_revision",
    }:
        context["change_request_scope"] = value["change_request_scope"]
    if value.get("verification_scope") in {
        "offline_native_check_only",
        "offline_native_reopen",
        
    }:
        context["verification_scope"] = value["verification_scope"]
    allowed_verification_scopes = value.get("allowed_verification_scopes")
    if allowed_verification_scopes is not None:
        if (
            not isinstance(allowed_verification_scopes, list)
            or not allowed_verification_scopes
            or len(allowed_verification_scopes) > len(_SAFE_VERIFICATION_SCOPES)
            or any(
                not isinstance(scope, str)
                or scope not in _SAFE_VERIFICATION_SCOPES
                for scope in allowed_verification_scopes
            )
            or len(set(allowed_verification_scopes)) != len(allowed_verification_scopes)
            or (
                "verification_scope" in context
                and context["verification_scope"] not in allowed_verification_scopes
            )
        ):
            raise AgentProtocolError("allowed verification scopes are invalid")
        context["allowed_verification_scopes"] = list(allowed_verification_scopes)
    if "offline_operand_mission" in value:
        try:
            mission = OfflineOperandMissionV1.model_validate(
                value["offline_operand_mission"]
            ).model_dump(mode="json", by_alias=True)
        except (TypeError, ValueError) as exc:
            raise AgentProtocolError("offline operand mission is invalid") from exc
        context["offline_operand_mission"] = mission
    if isinstance(value.get("stage"), str):
        context["stage"] = value["stage"]
    engineering_mission = value.get("engineering_mission")
    if isinstance(engineering_mission, dict):
        context["engineering_mission"] = engineering_mission
    if "historical_evidence" in value:
        if not isinstance(engineering_mission, dict):
            raise AgentProtocolError(
                "historical evidence requires a durable engineering mission"
            )
        steps = engineering_mission.get("steps")
        if not isinstance(steps, list):
            raise AgentProtocolError("engineering mission evidence binding is invalid")
        expected_evidence_ids: set[str] = set()
        for step in steps:
            evidence_refs = step.get("evidenceRefs") if isinstance(step, dict) else None
            if not isinstance(evidence_refs, list) or any(
                not isinstance(evidence_id, str) for evidence_id in evidence_refs
            ):
                raise AgentProtocolError(
                    "engineering mission evidence binding is invalid"
                )
            expected_evidence_ids.update(evidence_refs)
        mission_digest = hashlib.sha256(
            _canonical_json(engineering_mission).encode("utf-8")
        ).hexdigest()
        context["historical_evidence"] = validate_historical_evidence_bundle(
            value["historical_evidence"],
            mission_digest=mission_digest,
            expected_evidence_ids=expected_evidence_ids,
        )
    pending_change = value.get("pending_change")
    if isinstance(pending_change, dict) and set(pending_change) == {
        "transactionId",
        "state",
        "planDigest",
        "approvalExpiresAt",
    }:
        context["pending_change"] = dict(pending_change)
    change_outcome = value.get("change_outcome")
    if isinstance(change_outcome, dict) and set(change_outcome) in (
        _CHANGE_OUTCOME_KEYS,
        _CHANGE_OUTCOME_KEYS | {"programCheckFailure"},
    ):
        transaction_id = change_outcome.get("transactionId")
        evidence_id = change_outcome.get("evidenceId")
        state = change_outcome.get("state")
        disposition = change_outcome.get("disposition")
        digests = (
            change_outcome.get("planDigest"),
            change_outcome.get("outcomeDigest"),
        )
        verification_digest = change_outcome.get("verificationEvidenceDigest")
        error_code = change_outcome.get("errorCode")
        try:
            canonical_ids = bool(
                isinstance(transaction_id, str)
                and str(uuid.UUID(transaction_id)) == transaction_id
                and isinstance(evidence_id, str)
                and str(uuid.UUID(evidence_id)) == evidence_id
            )
        except ValueError:
            canonical_ids = False
        failure_fields = {
            key for key in ("programCheckFailure",)
            if key in change_outcome
        }
        unsigned = {
            key: item for key, item in change_outcome.items()
            if key not in {"evidenceId", "outcomeDigest"}
        }
        outcome_digest_valid = (
            hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()
            == change_outcome.get("outcomeDigest")
        )
        failure_valid = not failure_fields
        if failure_fields == {"programCheckFailure"}:
            try:
                compiler_failure = ProgramCheckFailureObservationV1.model_validate(
                    change_outcome["programCheckFailure"],
                )
                failure_valid = bool(
                    state == "ROLLED_BACK"
                    and compiler_failure.model_dump(mode="json", by_alias=True)
                    == change_outcome["programCheckFailure"]
                )
            except (ValidationError, TypeError, ValueError):
                failure_valid = False
        elif failure_fields:
            failure_valid = False
        failure_valid = failure_valid and outcome_digest_valid
        if (
            change_outcome.get("schemaVersion") == "1"
            and canonical_ids
            and failure_valid
            and isinstance(state, str)
            and _CHANGE_OUTCOME_DISPOSITIONS.get(state) == disposition
            and all(
                isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                for digest in digests
            )
            and (
                verification_digest is None
                or (
                    isinstance(verification_digest, str)
                    and re.fullmatch(r"[0-9a-f]{64}", verification_digest)
                    is not None
                )
            )
            and (error_code is None or isinstance(error_code, str))
        ):
            context["change_outcome"] = json.loads(_canonical_json(change_outcome))
            any(
                isinstance(change_outcome.get(key), dict)
                for key in ("programCheckFailure",)
            )
    if (
        value.get("fresh_user_proposal_requested") is True
        and change_planner_available
        and context.get("change_request_scope") == "offline_candidate_revision"
        and "pending_change" not in context
    ):
        context["fresh_user_proposal_requested"] = True
    return context


def _tool_request_from_message(
    message: AgentProtocolMessage,
    conversation_id: str,
    run_id: str,
) -> ExternalToolRequestV1:
    try:
        request = ExternalToolRequestV1.model_validate(message.payload)
    except ValidationError as exc:
        raise AgentProtocolError("Agent external tool request is invalid") from exc
    if request.conversation_id != conversation_id or request.run_id != run_id:
        raise AgentProtocolError("Agent external tool request targets another run")
    return request


def _structural_observation_facts(
    receipt: Mapping[str, Any],
) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    for fact_kind, field in (
        ("location", "locationFacts"),
        ("relation", "relationFacts"),
        ("project_structure", "structureFacts"),
    ):
        values = receipt.get(field, [])
        if not isinstance(values, list):
            raise AgentProtocolError("Structural observation facts are invalid")
        for value in values[:16]:
            if not isinstance(value, Mapping) or not isinstance(
                value.get("fingerprint"),
                str,
            ):
                raise AgentProtocolError("Structural observation fact is invalid")
            facts.append(
                {
                    "factKind": fact_kind,
                    "factFingerprint": str(value["fingerprint"]),
                    "statement": structural_observation_statement(
                        fact_kind,
                        value,
                    ),
                }
            )
    return facts


def _structural_evidence_projection(
    *,
    evidence_tool_name: str,
    evidence_ref: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild the exact evidence object exposed to the Agent."""

    expected_tool_name = structural_receipt_tool_name(evidence_tool_name)
    try:
        validated_receipt = validate_structural_evidence_contract(
            evidence_ref.get("receipt"),
            expected_tool=expected_tool_name,
        )
    except (TypeError, ValueError, MCPToolError) as exc:
        raise AgentProtocolError("Structural analysis evidence receipt is invalid") from exc
    if expected_tool_name == "project.structure_index":
        raw_scope = validated_receipt["scope"]
        projected_receipt: dict[str, Any] = {
            "schema": _PROJECT_STRUCTURE_EVIDENCE_PROJECTION_SCHEMA,
            "toolName": expected_tool_name,
            "scope": {
                "scopeKey": raw_scope["scopeKey"],
                "query": raw_scope["query"],
                "coverageUnitKeys": raw_scope["coverageUnitKeys"],
                "countsAdditive": raw_scope["countsAdditive"],
                "payloadSetDigest": raw_scope["payloadSetDigest"],
            },
            "coverage": validated_receipt["coverage"],
            "receiptIndexTruncated": validated_receipt["receiptIndexTruncated"],
            "receiptIndexLimitations": validated_receipt[
                "receiptIndexLimitations"
            ],
            "structureFactCount": validated_receipt["structureFactCount"],
            "structureFactReturnedCount": validated_receipt[
                "structureFactReturnedCount"
            ],
            "structureFactsTruncated": validated_receipt[
                "structureFactsTruncated"
            ],
            "structureFactsDigest": validated_receipt["structureFactsDigest"],
            "structureFacts": validated_receipt["structureFacts"],
        }
        projected_receipt["projectionDigest"] = canonical_digest(projected_receipt)
        safe_evidence_ref = {
            **{key: value for key, value in evidence_ref.items() if key != "receipt"},
            "receipt": projected_receipt,
        }
    else:
        safe_evidence_ref = scrub_agent_tool_result(evidence_ref)
        if not isinstance(safe_evidence_ref, dict) or safe_evidence_ref != evidence_ref:
            raise AgentProtocolError("Structural analysis evidence is invalid")
    retained_model_context = safe_evidence_ref.get("modelContext")
    retained_model_context_digest = safe_evidence_ref.get("modelContextDigest")
    if expected_tool_name == "project.search" and retained_model_context is not None:
        try:
            canonical_model_context = validate_project_search_context(
                retained_model_context,
                receipt=validated_receipt,
            )
        except (TypeError, ValueError, MCPToolError) as exc:
            raise AgentProtocolError(
                "Project search evidence context is invalid"
            ) from exc
        if canonical_model_context != retained_model_context:
            raise AgentProtocolError(
                "Project search evidence context is not canonical"
            )
        if retained_model_context_digest != canonical_digest(
            canonical_model_context
        ):
            raise AgentProtocolError(
                "Project search evidence context digest is invalid"
            )
    elif expected_tool_name == "project.search" and retained_model_context_digest is not None:
        raise AgentProtocolError(
            "Project search evidence context digest has no context"
        )
    elif expected_tool_name != "project.search" and (
        retained_model_context is not None
        or retained_model_context_digest is not None
    ):
        raise AgentProtocolError(
            "Structural analysis evidence has an unexpected model context"
        )
    safe_evidence_ref = {
        **safe_evidence_ref,
        "observationFacts": _structural_observation_facts(validated_receipt),
    }
    receipt = safe_evidence_ref.get("receipt")
    expected_receipt_schema = (
        _PROJECT_STRUCTURE_EVIDENCE_PROJECTION_SCHEMA
        if expected_tool_name == "project.structure_index"
        else STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA
    )
    if (
        not isinstance(safe_evidence_ref.get("evidenceId"), str)
        or not isinstance(receipt, Mapping)
        or receipt.get("schema") != expected_receipt_schema
        or receipt.get("toolName") != expected_tool_name
    ):
        raise AgentProtocolError("Structural analysis evidence receipt is invalid")
    assert_agent_safe_payload(safe_evidence_ref)
    return safe_evidence_ref, validated_receipt


def _nonstructural_evidence_projection(
    evidence_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a Host receipt without misrepresenting its private source digest."""

    safe_evidence_ref = scrub_agent_tool_result(evidence_ref)
    if not isinstance(safe_evidence_ref, dict):
        raise AgentProtocolError("Evidence is not Agent-safe")
    raw_receipt = evidence_ref.get("receipt")
    safe_receipt = safe_evidence_ref.get("receipt")
    if (
        isinstance(raw_receipt, Mapping)
        and isinstance(safe_receipt, dict)
        and dict(raw_receipt) != safe_receipt
    ):
        projected_receipt = {
            **safe_receipt,
            "targetIdentityOmitted": True,
        }
        projected_receipt["projectionDigest"] = canonical_digest(projected_receipt)
        safe_evidence_ref["receipt"] = projected_receipt
    assert_agent_safe_payload(safe_evidence_ref)
    return safe_evidence_ref


def project_historical_evidence(evidence_ref: Mapping[str, Any]) -> dict[str, Any]:
    """Project a journaled receipt exactly as it was exposed in its source run."""

    tool_name = evidence_ref.get("toolName")
    if not isinstance(tool_name, str) or not tool_name:
        raise AgentProtocolError("Historical evidence toolName is invalid")
    if tool_name in STRUCTURAL_ANALYSIS_TOOL_COVERAGE:
        projected, _ = _structural_evidence_projection(
            evidence_tool_name=tool_name,
            evidence_ref=evidence_ref,
        )
        return projected
    return _nonstructural_evidence_projection(evidence_ref)


def _structural_tool_result_payload(
    *,
    request_id: str,
    tool_alias: str,
    evidence_ref: Mapping[str, Any] | None,
    raw_result: Any,
) -> dict[str, Any]:
    """Project a structural result to its bounded Host-authored receipt."""

    evidence_tool_name = _STRUCTURAL_ANALYSIS_TOOL_ALIASES.get(tool_alias)
    if evidence_tool_name is None or not isinstance(evidence_ref, Mapping):
        raise AgentProtocolError("Structural analysis result has no Host evidence receipt")
    expected_tool_name = structural_receipt_tool_name(evidence_tool_name)
    safe_evidence_ref, validated_receipt = _structural_evidence_projection(
        evidence_tool_name=evidence_tool_name,
        evidence_ref=evidence_ref,
    )
    result: dict[str, Any] = {
        "schema": _STRUCTURAL_TOOL_RESULT_PROJECTION_SCHEMA,
        "toolName": expected_tool_name,
        "rawResultOmitted": True,
        "authoritativeResultPath": "_controlstudio_evidence.receipt",
        "guidance": _STRUCTURAL_TOOL_RESULT_GUIDANCE,
        "_controlstudio_evidence": safe_evidence_ref,
    }
    if expected_tool_name == "program.logic_graph":
        model_context = logic_graph_context(raw_result)
    elif expected_tool_name == "program.state_model":
        model_context = state_model_context(raw_result)
    elif expected_tool_name == "project.search":
        model_context = validate_project_search_context(
            project_search_context(raw_result),
            receipt=validated_receipt,
        )
        if safe_evidence_ref.get("modelContext") != model_context:
            raise AgentProtocolError(
                "Journaled project search context differs from the tool result"
            )
    else:
        model_context = None
    if model_context is not None:
        result["modelContext"] = (
            model_context
            if expected_tool_name == "project.search"
            else {
                "sourceReceiptDigest": validated_receipt["receiptDigest"],
                **model_context,
            }
        )
    payload = {
        "request_id": request_id,
        "status": "completed",
        "result": result,
    }
    _assert_strict_tool_result_json(payload)
    assert_agent_safe_payload(payload)
    encoded = encode_message("tool.result", payload)
    if len(encoded) > _STRUCTURAL_TOOL_RESULT_FRAME_MAX_BYTES:
        raise AgentProtocolError("Structural analysis result projection exceeds its wire budget")
    return payload


def _tool_result_transport_plan(
    payload: dict[str, Any], *, native_run: bool,
) -> list[tuple[str, dict[str, Any]]]:
    """Keep one logical result; only its native wire envelope may be chunked."""
    frame_size = len(json.dumps({
        "protocol_version": AGENT_PROTOCOL_VERSION_V1, "type": "tool.result", "payload": payload,
    }, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) + 1
    if not native_run or frame_size <= MAX_PROTOCOL_LINE_BYTES:
        plan = [("tool.result", payload)]
    else:
        request_id = payload["request_id"]
        chunks = list(checkpoint_chunks(payload))
        plan = [("tool.result.chunk", {"request_id": request_id, **part}) for part in chunks]
        plan.append(("tool.result.sync", {
            "request_id": request_id, "checkpointDigest": chunks[0]["checkpointDigest"],
        }))
    for message_type, part in plan:
        encode_message(message_type, part)
    return plan


def _completed_tool_result_payload(
    *,
    request_id: str,
    raw_result: Any,
    evidence_ref: Mapping[str, Any] | None,
    tool_name: str | None = None,
    native_run: bool = False,
    transport_plan: list[tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Build and wire-validate an Agent-safe result before durable completion."""

    projected_result = (
        _project_design_diagnostic_pointers(tool_name, raw_result)
        if tool_name is not None
        else raw_result
    )
    safe_result = scrub_native_tool_result(projected_result) if native_run else scrub_agent_tool_result(projected_result)
    if evidence_ref is not None:
        safe_evidence_ref = _nonstructural_evidence_projection(evidence_ref)
        if isinstance(safe_result, dict):
            safe_result = {
                **safe_result,
                "_controlstudio_evidence": safe_evidence_ref,
            }
        else:
            safe_result = {
                "result": safe_result,
                "_controlstudio_evidence": safe_evidence_ref,
            }
    payload = {
        "request_id": request_id,
        "status": "completed",
        "result": safe_result,
    }
    _assert_strict_tool_result_json(payload)
    if native_run:
        assert_native_public_payload(payload)
    else:
        assert_agent_safe_payload(payload)
    # JSON serialization and the exact NDJSON frame-size limit must succeed
    # before the Host journals tool.completed and its evidence atomically.
    plan = _tool_result_transport_plan(payload, native_run=native_run)
    if transport_plan is not None:
        transport_plan[:] = plan
    return payload


def _assert_strict_tool_result_json(value: Any) -> None:
    """Reject values that JSON framing would coerce or cannot represent."""

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise AgentProtocolError("Agent tool result JSON keys must be text")
            for nested in item.values():
                visit(nested)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)
            return
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float) and math.isfinite(item):
            return
        raise AgentProtocolError("Agent tool result is not strict JSON")

    visit(value)
    try:
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise AgentProtocolError("Agent tool result is not strict JSON") from exc


def _output_contract_diagnostic_from_payload(
    value: Any,
) -> AgentOutputContractDiagnostic | None:
    """Accept only non-sensitive, allowlisted sidecar failure diagnostics."""

    if not isinstance(value, Mapping) or set(value) != {
        "stage",
        "exceptionKind",
        "reasonCode",
        "validatorCode",
        "validationErrors",
    }:
        return None
    stage = value.get("stage")
    exception_kind = value.get("exceptionKind")
    reason_code = value.get("reasonCode")
    validator_code = value.get("validatorCode")
    raw_errors = value.get("validationErrors")
    if (
        not isinstance(stage, str)
        or stage not in _OUTPUT_CONTRACT_STAGES
        or not isinstance(exception_kind, str)
        or exception_kind not in _OUTPUT_CONTRACT_EXCEPTION_KINDS
        or not isinstance(reason_code, str)
        or reason_code not in _OUTPUT_CONTRACT_REASON_CODES
        or not isinstance(validator_code, str)
        or validator_code not in _OUTPUT_CONTRACT_VALIDATOR_CODES
        or not isinstance(raw_errors, list)
        or len(raw_errors) > 8
    ):
        return None
    # The workspace guard and native transport each have narrower reason
    # contracts than the generic structured-output path.  Enforce the pairing
    # at the Host boundary so a provider/model-controlled string cannot
    # masquerade as a typed outcome.
    if not _valid_output_contract_reason_pair(exception_kind, reason_code):
        return None

    validation_errors: list[AgentOutputValidationIssue] = []
    for raw_error in raw_errors:
        if not isinstance(raw_error, Mapping) or set(raw_error) != {"loc", "type"}:
            return None
        raw_location = raw_error.get("loc")
        error_type = raw_error.get("type")
        if (
            not isinstance(raw_location, list)
            or len(raw_location) > 24
            or not isinstance(error_type, str)
            or not 1 <= len(error_type) <= 80
            or not error_type[0].isascii()
            or not error_type[0].islower()
            or any(
                not character.isascii()
                or (not character.islower() and not character.isdigit())
                and character not in "_.-"
                for character in error_type
            )
        ):
            return None
        location: list[str | int] = []
        for item in raw_location:
            if isinstance(item, int) and not isinstance(item, bool):
                if item < 0 or item > 1_000_000:
                    return None
                location.append(item)
                continue
            if (
                not isinstance(item, str)
                or not 1 <= len(item) <= 120
                or any(
                    character not in _VALIDATION_LOCATION_CHARACTERS
                    for character in item
                )
            ):
                return None
            location.append(item)
        validation_errors.append(
            AgentOutputValidationIssue(
                loc=tuple(location),
                error_type=error_type,
            )
        )

    return AgentOutputContractDiagnostic(
        stage=stage,
        exception_kind=exception_kind,
        reason_code=reason_code,
        validator_code=validator_code,
        validation_errors=tuple(validation_errors),
    )


def _output_contract_diagnostic_from_stderr(
    value: str,
) -> AgentOutputContractDiagnostic | None:
    """Parse only the sidecar's fixed output-contract stderr summary.

    This is a recovery path for a child that closes stdout before its
    ``run.error`` frame reaches the Host.  The regex deliberately captures
    bounded ASCII tokens only; the closed allowlists below decide whether any
    token is typed and exposed.  No captured run id, model text, arguments, or
    provider detail is retained.
    """

    match = _SAFE_OUTPUT_CONTRACT_STDERR.fullmatch(value)
    if match is None:
        return None
    fields = match.groupdict()
    stage = fields["stage"]
    exception_kind = fields["exception_kind"]
    reason_code = fields["reason_code"]
    validator_code = fields["validator_code"]
    if (
        stage not in _OUTPUT_CONTRACT_STAGES
        or exception_kind not in _OUTPUT_CONTRACT_EXCEPTION_KINDS
        or reason_code not in _OUTPUT_CONTRACT_REASON_CODES
        or validator_code not in _OUTPUT_CONTRACT_VALIDATOR_CODES
    ):
        return None
    if not _valid_output_contract_reason_pair(exception_kind, reason_code):
        return None
    return AgentOutputContractDiagnostic(
        stage=stage,
        exception_kind=exception_kind,
        reason_code=reason_code,
        validator_code=validator_code,
        validation_errors=(),
    )


def _working_history_diagnostic_from_stderr(value: str) -> dict[str, str | int] | None:
    match = _WORKING_HISTORY_STDERR.fullmatch(value)
    if match is None:
        return None
    return {"reason": match["reason"], **{
        key: int(match[key]) for key in ("budget", "fixed", "core", "latest")
    }}


def _provider_http_diagnostic_from_stderr(value: str) -> dict[str, str] | None:
    """Project the Agent's fixed provider HTTP stderr summary safely.

    The Agent has already reduced provider responses to the same vocabulary,
    but the Host still treats stderr as untrusted child input.  Unknown
    metadata becomes ``unknown`` and malformed lines are ignored. The optional
    provider explanation is independently bounded and redacted before logging.
    """

    match = _SAFE_PROVIDER_HTTP_STDERR.fullmatch(value)
    if match is None:
        return None
    fields = match.groupdict()
    raw_status = fields["status"]
    if raw_status == "unknown":
        status = "unknown"
    elif raw_status.isdecimal() and 400 <= int(raw_status) <= 599:
        status = raw_status
    else:
        status = "unknown"

    def safe_error_field(raw: str, allowed: frozenset[str]) -> str:
        if any(marker in raw for marker in _SAFE_PROVIDER_ERROR_REDACTION_MARKERS):
            return "unknown"
        return raw if raw in allowed else "unknown"

    raw_param = fields["param"]
    if raw_param in {"unknown", "redacted"}:
        param = raw_param
    else:
        parts = raw_param.split(".")
        if (
            not 1 <= len(parts) <= 5
            or any(_SAFE_PROVIDER_PARAM_PART.fullmatch(part) is None for part in parts)
            or parts[0].split("[")[0] not in _SAFE_PROVIDER_PARAM_ROOTS
            or any(part.split("[")[0] not in _SAFE_PROVIDER_PARAM_SUFFIXES for part in parts[1:])
        ):
            param = "unknown"
        else:
            param = raw_param
    result = {
        "status": status,
        "type": safe_error_field(fields["type"], _SAFE_PROVIDER_ERROR_TYPES),
        "code": safe_error_field(fields["code"], _SAFE_PROVIDER_ERROR_CODES),
        "param": param,
    }
    message = fields.get("message")
    if message is not None:
        safe_message = _safe_provider_http_message(message)
        if safe_message is not None:
            result["message"] = safe_message
    return result


def _safe_provider_http_message(value: str) -> str | None:
    """Bound and redact the producer-capped provider explanation independently."""

    if (
        len(value) > _MAX_SAFE_PROVIDER_ERROR_MESSAGE_CHARS
        or any(not character.isprintable() for character in value)
    ):
        return None
    message = _PROVIDER_ERROR_SECRET_ASSIGNMENT.sub("[redacted credential]", value)
    message = _PROVIDER_ERROR_BEARER_SECRET.sub("Bearer [redacted]", message)
    message = _PROVIDER_ERROR_API_KEY.sub("[redacted]", message)
    message = _PROVIDER_ERROR_URL.sub("[redacted_url]", message)
    message = " ".join(message.split())
    if not message:
        return None
    if len(message) > _MAX_SAFE_PROVIDER_ERROR_MESSAGE_CHARS:
        message = message[: _MAX_SAFE_PROVIDER_ERROR_MESSAGE_CHARS - 3] + "..."
    return message


def _valid_output_contract_reason_pair(
    exception_kind: str,
    reason_code: str,
) -> bool:
    """Keep typed output reasons bound to their Host-owned exception class."""

    if exception_kind == "workspace_edit_rejected":
        return reason_code in _WORKSPACE_EDIT_REJECTION_REASON_CODES
    if reason_code in _WORKSPACE_EDIT_REJECTION_REASON_CODES:
        return False
    if reason_code in _NATIVE_TOOL_REASON_CODES:
        return exception_kind == "unexpected_model_behavior" or (
            exception_kind == "incomplete_tool_call"
            and reason_code == "native_tool_call_truncated"
        )
    return True


def _runtime_diagnostic_from_payload(value: Any) -> AgentRuntimeDiagnostic | None:
    """Accept only the sidecar's fixed non-sensitive runtime diagnostic codes."""

    required = {"stage", "exceptionKind", "reasonCode"}
    optional = {"providerHttpStatus", "providerErrorCode", "providerErrorType", "providerExceptionClass"}
    if not isinstance(value, Mapping) or not required <= set(value) or set(value) - required - optional:
        return None
    stage = value.get("stage")
    exception_kind = value.get("exceptionKind")
    reason_code = value.get("reasonCode")
    if exception_kind == "working_memory_validation":
        if (set(value) != required or type(stage) is not str
                or stage not in _OUTPUT_CONTRACT_STAGES or type(reason_code) is not str
                or reason_code not in _MEMORY_VALIDATION_REASON_CODES):
            return None
        return AgentRuntimeDiagnostic(stage=stage, exception_kind=exception_kind, reason_code=reason_code)
    if (
        not isinstance(stage, str)
        or stage not in _OUTPUT_CONTRACT_STAGES
        or not isinstance(exception_kind, str)
        or exception_kind not in _RUNTIME_EXCEPTION_KINDS
        or not isinstance(reason_code, str)
        or (
            reason_code not in _RUNTIME_REASON_CODES
            and re.fullmatch(r"provider_http_[45][0-9]{2}", reason_code) is None
        )
    ):
        return None
    provider_status = value.get("providerHttpStatus")
    provider_code = value.get("providerErrorCode")
    provider_type = value.get("providerErrorType")
    provider_class = value.get("providerExceptionClass")
    if optional.intersection(value):
        if (exception_kind != "model_api_error" or
                not (reason_code == "provider_rejected" or re.fullmatch(r"provider_http_[45][0-9]{2}", reason_code))):
            return None
        if "providerHttpStatus" in value and (type(provider_status) is not int
                or not 400 <= provider_status <= 599 or reason_code != f"provider_http_{provider_status}"):
            return None
        if "providerErrorCode" in value and (type(provider_code) is not str
                or provider_code not in _PROVIDER_RUNTIME_ERROR_CODES):
            return None
        if "providerErrorType" in value and (type(provider_type) is not str
                or provider_type not in _PROVIDER_RUNTIME_ERROR_TYPES):
            return None
        if "providerExceptionClass" in value and (type(provider_class) is not str
                or provider_class not in _PROVIDER_RUNTIME_EXCEPTION_CLASSES):
            return None
    return AgentRuntimeDiagnostic(
        stage=stage,
        exception_kind=exception_kind,
        reason_code=reason_code,
        provider_http_status=provider_status,
        provider_error_code=provider_code,
        provider_error_type=provider_type,
        provider_exception_class=provider_class,
    )


def _validated_approval_handoff(value: Any) -> dict[str, str]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schemaVersion", "transactionId", "planDigest"}
        or value.get("schemaVersion") != "1"
        or not isinstance(value.get("transactionId"), str)
        or str(uuid.UUID(value["transactionId"])) != value["transactionId"]
        or not isinstance(value.get("planDigest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["planDigest"]) is None
    ):
        raise ValueError("approval handoff output")
    return dict(value)


def _validated_native_request_disposition(value: Any, content: str) -> dict[str, Any] | None:
    # Older Agents may omit this additive field. Never infer a completed goal.
    if value is None:
        return None
    if (not isinstance(value, dict)
            or not {'schema_version', 'status', 'summary', 'is_goal_completion_evidence'} <= set(value)
            or set(value) - {'schema_version', 'status', 'summary', 'is_goal_completion_evidence', 'completion_assessment'}
            or value.get('schema_version') != '1'
            or value.get('status') not in {'not_claimed', 'completed', 'blocked', 'needs_user'}
            or not isinstance(value.get('summary'), str) or not value['summary']
            or value['summary'] != content
            or value.get('is_goal_completion_evidence') is not False):
        raise ValueError('native request disposition')
    result = dict(value)
    if 'completion_assessment' in result:
        from controlstudio_contracts.native_completion import validate_completion_assessment
        result['completion_assessment'] = validate_completion_assessment(result['completion_assessment'])
    return result


def _output_from_payload(value: Any) -> AgentRunOutput:
    if not isinstance(value, Mapping):
        raise AgentProtocolError("Agent final output is invalid")
    try:
        workflow = value.get("workflow", "engineering")
        if workflow not in {"engineering", "new_project_planning", "native_ld_partial_edit"}:
            raise ValueError("workflow")
        plan_status = value.get("plan_status")
        if plan_status not in {None, "needs_clarification", "ready_for_review"}:
            raise ValueError("plan_status")
        content = value["content"]
        model = value["model"]
        if not isinstance(content, str) or not isinstance(model, str):
            raise TypeError("output fields")
        project_start = validated_native_project_start(value.get('native_project_start'))
        if project_start is not None and (workflow != 'engineering' or any(value.get(field) is not None for field in (
                'plan_status', 'project_name', 'plan_artifact', 'plan_digest', 'engineering_analysis',
                'engineering_analysis_digest', 'engineering_mission', 'digest', 'cycle_disposition',
                'approval_handoff', 'native_request_disposition'))):
            raise ValueError('New-project start cannot carry target authority or completion artifacts')
        native_disposition = _validated_native_request_disposition(
            value.get("native_request_disposition"), content)
        if native_disposition is not None and workflow != "native_ld_partial_edit":
            raise ValueError("native disposition outside native workflow")
        engineering_mission = value.get("engineering_mission")
        engineering_mission_digest = value.get("digest")
        cycle_disposition = value.get("cycle_disposition")
        mission_fields_present = (
            engineering_mission is not None,
            engineering_mission_digest is not None,
            cycle_disposition is not None,
        )
        if any(mission_fields_present) and not all(mission_fields_present):
            raise ValueError("incomplete engineering mission output")
        if engineering_mission is not None and (
            not isinstance(engineering_mission, dict)
            or not isinstance(engineering_mission_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", engineering_mission_digest) is None
            or cycle_disposition not in {"continue", "respond"}
        ):
            raise ValueError("engineering mission output")
        approval_handoff = value.get("approval_handoff")
        if approval_handoff is not None:
            approval_handoff = _validated_approval_handoff(approval_handoff)
            if workflow != "engineering" or any(value.get(field) is not None for field in (
                "engineering_mission", "digest", "cycle_disposition",
                "engineering_analysis", "engineering_analysis_digest",
                "plan_status", "plan_artifact", "plan_digest", "project_name",
            )):
                raise ValueError("approval handoff cannot carry model completion artifacts")
        return AgentRunOutput(
            content=content,
            model=model,
            input_tokens=int(value["input_tokens"]),
            output_tokens=int(value["output_tokens"]),
            workflow=workflow,
            plan_status=plan_status,
            project_name=value.get("project_name") if isinstance(value.get("project_name"), str) else None,
            plan_artifact=value.get("plan_artifact") if isinstance(value.get("plan_artifact"), dict) else None,
            plan_digest=value.get("plan_digest") if isinstance(value.get("plan_digest"), str) else None,
            engineering_analysis=(
                value.get("engineering_analysis")
                if isinstance(value.get("engineering_analysis"), dict)
                else None
            ),
            engineering_analysis_digest=(
                value.get("engineering_analysis_digest")
                if isinstance(value.get("engineering_analysis_digest"), str)
                else None
            ),
            engineering_mission=engineering_mission,
            digest=engineering_mission_digest,
            cycle_disposition=cycle_disposition,
            approval_handoff=approval_handoff,
            native_request_disposition=native_disposition,
            native_project_start=project_start,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentProtocolError("Agent final output is invalid") from exc
