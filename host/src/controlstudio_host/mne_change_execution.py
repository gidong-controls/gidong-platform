from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

from .api_models import (
    ChangePlanV3,
    ChangePlanV4,
    ChangePlanV5,
    ChangePlanV6,
    MneInstructionBatchPatchCandidateV5,
    MneInstructionPatchCandidateV4,
    MneRungPatchCandidateV6,
    MneSeriesGuardCandidateV3,
)
from .change_execution import (
    ChangeApplyResult,
    ChangeExecutionFailedSafely,
    ChangeExecutionOutcomeUncertain,
    ChangeExecutionPreconditionError,
    ChangeRecoveryTarget,
    ChangeVerificationResult,
    MCPChangeExecutionService,
    NativeCheckpointCallback,
)
from .mcp_client import MCPGateway, MCPToolError
from .models import ChangeTransaction, ChangeTransactionState

MNE_PLAN_KIND = "mne_full_program_replacement"


class MnePatchPlanContract(NamedTuple):
    candidate_schema: str
    compiler_tool: str
    evidence_schema: str
    plan_operation: str
    candidate_patch_key: str
    supports_xgsim: bool
    candidate_model: type[
        MneSeriesGuardCandidateV3
        | MneInstructionPatchCandidateV4
        | MneInstructionBatchPatchCandidateV5
        | MneRungPatchCandidateV6
    ]
    plan_model: type[ChangePlanV3 | ChangePlanV4 | ChangePlanV5 | ChangePlanV6]


MNE_PATCH_PLAN_CONTRACTS: Mapping[str, MnePatchPlanContract] = {
    "mne_series_guard_patch": MnePatchPlanContract(
        candidate_schema="xg5000.mne-series-guard-candidate/1.0",
        compiler_tool="program.compile_mne_series_guard_candidate",
        evidence_schema="controlstudio-mne-series-guard-candidate-evidence/v1",
        plan_operation="program.plan_mne_series_guard_patch",
        candidate_patch_key="patch",
        supports_xgsim=False,
        candidate_model=MneSeriesGuardCandidateV3,
        plan_model=ChangePlanV3,
    ),
    "mne_instruction_patch": MnePatchPlanContract(
        candidate_schema="xg5000.mne-instruction-patch-candidate/1.0",
        compiler_tool="program.compile_mne_instruction_patch_candidate",
        evidence_schema="controlstudio-mne-instruction-patch-candidate-evidence/v1",
        plan_operation="program.plan_mne_instruction_patch",
        candidate_patch_key="patch",
        supports_xgsim=False,
        candidate_model=MneInstructionPatchCandidateV4,
        plan_model=ChangePlanV4,
    ),
    "mne_instruction_batch_patch": MnePatchPlanContract(
        candidate_schema="xg5000.mne-instruction-batch-patch-candidate/1.0",
        compiler_tool="program.compile_mne_instruction_batch_patch_candidate",
        evidence_schema="controlstudio-mne-instruction-batch-patch-candidate-evidence/v1",
        plan_operation="program.plan_mne_instruction_batch_patch",
        candidate_patch_key="patches",
        supports_xgsim=False,
        candidate_model=MneInstructionBatchPatchCandidateV5,
        plan_model=ChangePlanV5,
    ),
    "mne_rung_patch": MnePatchPlanContract(
        candidate_schema="xg5000.mne-rung-patch-candidate/1.0",
        compiler_tool="program.compile_mne_rung_patch_candidate",
        evidence_schema="controlstudio-mne-rung-patch-candidate-evidence/v1",
        plan_operation="program.plan_mne_rung_patch",
        candidate_patch_key="patch",
        supports_xgsim=True,
        candidate_model=MneRungPatchCandidateV6,
        plan_model=ChangePlanV6,
    ),
}
MNE_PATCH_PLAN_KINDS = frozenset(MNE_PATCH_PLAN_CONTRACTS)
MNE_STRUCTURAL_PLAN_KINDS = frozenset((MNE_PLAN_KIND, *MNE_PATCH_PLAN_KINDS))
MNE_TARGET_PROGRAM_SCHEMA = "controlstudio-mne-target-program/v1"
MNE_PROGRAM_DESIGN_SCHEMA = "controlstudio-mne-program-design/v1"
MNE_APPROVED_BASELINE_SCHEMA = "controlstudio-mne-approved-baseline/v1"
MNE_VERIFICATION_SCOPE = "offline_native_reopen"
MNE_XGSIM_VERIFICATION_SCOPE = "offline_native_reopen_xgsim"
MNE_VALIDATION_STEPS = (
    "structural_diff",
    "xg5000_program_check",
    "project_save",
    "project_reopen",
)
MNE_NOT_EXECUTED_VALIDATION_TIERS = (
    "xg_sim",
    "runtime_or_online_validation",
)
MNE_BASELINE_SCHEMA = "xg5000.program-replacement-baseline/1.0"
MNE_PLAN_SCHEMA = "xg5000.program-replacement-plan/1.0"
MNE_APPLICATION_SCHEMA = "xg5000.program-replacement-application/1.0"
MNE_VERIFICATION_SCHEMA = "xg5000.program-replacement-verification/1.0"
MNE_ROLLBACK_SCHEMA = "xg5000.program-replacement-rollback/1.0"
MNE_PRIVATE_SNAPSHOT_SCHEMA = "controlstudio-mne-private-digest-snapshot/v1"
MNE_MCP_SCHEMA_VERSION = "1.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MNE_PUBLIC_RECEIPT_LIMIT = 2 * 1024 * 1024


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256(value: object, label: str) -> str:
    normalized = str(value or "").strip().casefold()
    if _SHA256.fullmatch(normalized) is None:
        raise ChangeExecutionPreconditionError(f"{label} must be a SHA-256 value")
    return normalized


def _required_text(value: Mapping[str, Any], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ChangeExecutionPreconditionError(f"{label}.{key} must be non-empty text")
    return item


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ChangeExecutionPreconditionError(f"{label} must be an object")
    return value


def _same_path(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return os.path.normcase(str(Path(left).expanduser().resolve())) == os.path.normcase(
        str(Path(right).expanduser().resolve())
    )


def _assert_no_private_payload(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if (
                str(key).startswith("_")
                or normalized_key
                in {"payloadpath", "mnepayload", "ldpayload", "clipboardpayload"}
                or normalized_key.startswith(("rawmne", "rawld", "privatemne", "privateld"))
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE private payload metadata crossed the MCP public boundary"
                )
            _assert_no_private_payload(nested)
        return
    if isinstance(value, list):
        for nested in value:
            _assert_no_private_payload(nested)
        return
    if isinstance(value, bytes | bytearray | memoryview):
        raise ChangeExecutionPreconditionError("MNE binary payload crossed the MCP public boundary")


def _strict_public_receipt(
    value: object,
    *,
    schema: str,
    digest_field: str,
    keys: set[str],
    label: str,
) -> dict[str, Any]:
    receipt = _object(value, label)
    _assert_no_private_payload(receipt)
    if set(receipt) != keys or receipt.get("schema") != schema:
        raise ChangeExecutionPreconditionError(f"{label} has an unexpected contract")
    if len(_canonical_json_bytes(receipt)) > _MNE_PUBLIC_RECEIPT_LIMIT:
        raise ChangeExecutionPreconditionError(f"{label} exceeds its public size bound")
    supplied = _sha256(receipt.get(digest_field), f"{label}.{digest_field}")
    unsigned = {key: item for key, item in receipt.items() if key != digest_field}
    if not hmac.compare_digest(supplied, _digest(unsigned)):
        raise ChangeExecutionPreconditionError(f"{label} digest is invalid")
    return receipt


def _typed_target_program(transaction: ChangeTransaction) -> dict[str, Any]:
    plan_kind = transaction.plan.get("plan_kind")
    patch_contract = MNE_PATCH_PLAN_CONTRACTS.get(plan_kind)
    if patch_contract is not None:
        candidate = _object(transaction.plan.get("candidate"), "plan.candidate")
        expected = {
            "schema", "project_sha256", "program_id", "program_name",
            "baseline_mne_sha256", "baseline_ld_sha256",
            "baseline_instruction_count", patch_contract.candidate_patch_key,
            "target_instruction_count",
            "target_mne_sha256", "design_digest", "target_program_digest",
            "candidate_digest",
        }
        if patch_contract.supports_xgsim:
            expected.add("simulation_profile")
        hardware_fields = {"hardware_context_digest", "field_io_bindings"}
        if (
            set(candidate) not in (expected, expected | hardware_fields)
            or candidate.get("schema") != patch_contract.candidate_schema
        ):
            raise ChangeExecutionPreconditionError("plan.candidate has an unexpected schema")
        supplied = _sha256(candidate.get("candidate_digest"), "plan.candidate.candidate_digest")
        unsigned = {key: item for key, item in candidate.items() if key != "candidate_digest"}
        if not hmac.compare_digest(supplied, _digest(unsigned)):
            raise ChangeExecutionPreconditionError("plan.candidate digest is invalid")
        if candidate.get("project_sha256") != transaction.target.artifact_hash:
            raise ChangeExecutionPreconditionError("plan.candidate project differs from transaction target")
        instruction_count = candidate.get("target_instruction_count")
        if not isinstance(instruction_count, int) or isinstance(instruction_count, bool) or not 2 <= instruction_count <= 4_096:
            raise ChangeExecutionPreconditionError("plan.candidate target instruction count is invalid")
        for key in ("program_id", "program_name"):
            _required_text(candidate, key, "plan.candidate")
        for key in ("design_digest", "target_program_digest", "target_mne_sha256"):
            _sha256(candidate.get(key), f"plan.candidate.{key}")
        result = {
            "program_id": str(candidate["program_id"]),
            "program_name": str(candidate["program_name"]),
            "instruction_count": instruction_count,
            "design_digest": str(candidate["design_digest"]),
            "target_program_digest": str(candidate["target_program_digest"]),
            "target_mne_sha256": str(candidate["target_mne_sha256"]),
        }
        if "hardware_context_digest" in candidate:
            if (
                not _SHA256.fullmatch(str(candidate.get("hardware_context_digest") or ""))
                or not isinstance(candidate.get("field_io_bindings"), list)
                or not candidate["field_io_bindings"]
            ):
                raise ChangeExecutionPreconditionError(
                    "plan.candidate hardware binding is incomplete"
                )
            result["hardware_context_digest"] = str(
                candidate["hardware_context_digest"]
            )
            result["field_io_bindings"] = list(candidate["field_io_bindings"])
        if transaction.plan.get("verification_scope") == MNE_XGSIM_VERIFICATION_SCOPE:
            profile = candidate.get("simulation_profile")
            if not isinstance(profile, Mapping) or not _SHA256.fullmatch(
                str(profile.get("profile_digest") or "")
            ):
                raise ChangeExecutionPreconditionError(
                    "XG-SIM MNE scope requires a compiler-derived simulation profile"
                )
            result["simulation_profile_digest"] = str(profile["profile_digest"])
        return result
    target_program = transaction.plan.get("target_program")
    target = _object(target_program, "plan.target_program")
    expected_keys = {
        "schema",
        "program_id",
        "program_name",
        "design_digest",
        "instruction_count",
        "instructions",
        "target_program_digest",
    }
    if set(target) != expected_keys or target.get("schema") != MNE_TARGET_PROGRAM_SCHEMA:
        raise ChangeExecutionPreconditionError(
            "plan.target_program has an unexpected schema or field set"
        )
    program_id = _required_text(target, "program_id", "plan.target_program")
    program_name = _required_text(target, "program_name", "plan.target_program")
    instructions = target.get("instructions")
    instruction_count = target.get("instruction_count")
    if (
        not isinstance(instructions, list)
        or not isinstance(instruction_count, int)
        or isinstance(instruction_count, bool)
        or instruction_count != len(instructions)
        or not 1 <= instruction_count <= 4_096
    ):
        raise ChangeExecutionPreconditionError("plan.target_program instruction count is invalid")
    for index, instruction in enumerate(instructions):
        if (
            not isinstance(instruction, dict)
            or set(instruction) != {"command", "operand"}
            or not isinstance(instruction.get("command"), str)
            or (
                instruction.get("operand") is not None
                and not isinstance(instruction.get("operand"), str)
            )
        ):
            raise ChangeExecutionPreconditionError(
                f"plan.target_program instruction {index} is invalid"
            )
    if (
        instructions[-1].get("command") != "END"
        or instructions[-1].get("operand") is not None
        or any(item.get("command") == "END" for item in instructions[:-1])
    ):
        raise ChangeExecutionPreconditionError(
            "plan.target_program must contain exactly one final END"
        )
    design = {
        "schema": MNE_PROGRAM_DESIGN_SCHEMA,
        "program_id": program_id,
        "program_name": program_name,
        "instructions": instructions,
    }
    design_digest = _sha256(target.get("design_digest"), "plan.target_program.design_digest")
    if not hmac.compare_digest(design_digest, _digest(design)):
        raise ChangeExecutionPreconditionError("plan.target_program design digest is invalid")
    unsigned = {key: item for key, item in target.items() if key != "target_program_digest"}
    target_digest = _sha256(
        target.get("target_program_digest"),
        "plan.target_program.target_program_digest",
    )
    if not hmac.compare_digest(target_digest, _digest(unsigned)):
        raise ChangeExecutionPreconditionError("plan.target_program digest is invalid")
    return dict(target)


def _approved_baseline_binding(transaction: ChangeTransaction) -> dict[str, str]:
    binding = _object(
        transaction.plan.get("baseline_binding"),
        "plan.baseline_binding",
    )
    if set(binding) != {
        "schema",
        "project_sha256",
        "program_id",
        "program_name",
        "ld_payload_sha256",
    }:
        raise ChangeExecutionPreconditionError(
            "plan.baseline_binding has an unexpected contract"
        )
    target_program = _typed_target_program(transaction)
    project_sha256 = _sha256(
        binding.get("project_sha256"),
        "plan.baseline_binding.project_sha256",
    )
    ld_payload_sha256 = _sha256(
        binding.get("ld_payload_sha256"),
        "plan.baseline_binding.ld_payload_sha256",
    )
    if (
        binding.get("schema") != MNE_APPROVED_BASELINE_SCHEMA
        or project_sha256 != transaction.target.artifact_hash
        or binding.get("program_id") != target_program["program_id"]
        or binding.get("program_name") != target_program["program_name"]
    ):
        raise ChangeExecutionPreconditionError(
            "plan.baseline_binding differs from the immutable transaction target"
        )
    return {
        "schema": MNE_APPROVED_BASELINE_SCHEMA,
        "project_sha256": project_sha256,
        "program_id": str(binding["program_id"]),
        "program_name": str(binding["program_name"]),
        "ld_payload_sha256": ld_payload_sha256,
    }


def _validate_private_snapshot(
    value: object,
    *,
    transaction: ChangeTransaction,
    target_program: Mapping[str, Any],
    approved_baseline: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = _object(value, "project.snapshot private MNE result")
    expected_keys = {
        "schema_version",
        "session_id",
        "operation",
        "ok",
        "verified",
        "operation_id",
        "schema",
        "snapshot_id",
        "project_path",
        "project_hash",
        "plan_kind",
        "artifact_visibility",
        "programs",
        "snapshot_digest",
        "program_count",
        "total_visual_rows",
        "project_unchanged",
        "transaction_id",
        "transaction_state",
        "transaction_checkpoint",
    }
    _assert_no_private_payload(snapshot)
    if set(snapshot) != expected_keys or len(_canonical_json_bytes(snapshot)) > (
        _MNE_PUBLIC_RECEIPT_LIMIT
    ):
        raise ChangeExecutionPreconditionError(
            "project.snapshot did not return the bounded private-digest contract"
        )
    target = transaction.target
    programs = snapshot.get("programs")
    if (
        snapshot.get("schema_version") != MNE_MCP_SCHEMA_VERSION
        or snapshot.get("operation") != "project.snapshot"
        or snapshot.get("ok") is not True
        or snapshot.get("verified") is not True
        or snapshot.get("schema") != MNE_PRIVATE_SNAPSHOT_SCHEMA
        or snapshot.get("session_id") != target.engineering_session_id
        or not _same_path(snapshot.get("project_path"), target.project_path)
        or snapshot.get("project_hash") != target.artifact_hash
        or snapshot.get("plan_kind") != MNE_PLAN_KIND
        or snapshot.get("artifact_visibility") != "private_digest_only"
        or snapshot.get("project_unchanged") is not True
        or snapshot.get("transaction_state") != "SNAPSHOT_CREATED"
        or not isinstance(programs, list)
        or not 1 <= len(programs) <= 4_096
        or snapshot.get("program_count") != len(programs)
    ):
        raise ChangeExecutionPreconditionError(
            "project.snapshot private-digest identity is invalid"
        )
    observed_ids: set[str] = set()
    total_visual_rows = 0
    target_matches = 0
    target_payload_sha256: str | None = None
    for program in programs:
        if (
            not isinstance(program, dict)
            or set(program)
            != {
                "program_id",
                "name",
                "payload_sha256",
                "payload_size",
                "visual_row_count",
                "stable_repeat",
            }
        ):
            raise ChangeExecutionPreconditionError(
                "project.snapshot contains an invalid private-digest program receipt"
            )
        program_id = _required_text(program, "program_id", "snapshot program")
        name = _required_text(program, "name", "snapshot program")
        if (
            program_id in observed_ids
            or program.get("stable_repeat") is not True
            or _SHA256.fullmatch(str(program.get("payload_sha256") or "")) is None
            or not isinstance(program.get("payload_size"), int)
            or isinstance(program.get("payload_size"), bool)
            or int(program["payload_size"]) <= 0
            or not isinstance(program.get("visual_row_count"), int)
            or isinstance(program.get("visual_row_count"), bool)
            or int(program["visual_row_count"]) < 0
        ):
            raise ChangeExecutionPreconditionError(
                "project.snapshot program digest evidence is invalid"
            )
        observed_ids.add(program_id)
        total_visual_rows += int(program["visual_row_count"])
        if (
            program_id == target_program.get("program_id")
            and name == target_program.get("program_name")
        ):
            target_matches += 1
            target_payload_sha256 = str(program["payload_sha256"])
    if (
        target_matches != 1
        or snapshot.get("total_visual_rows") != total_visual_rows
        or snapshot.get("project_hash") != approved_baseline.get("project_sha256")
        or target_payload_sha256 != approved_baseline.get("ld_payload_sha256")
    ):
        raise ChangeExecutionPreconditionError(
            "project.snapshot is not bound to the exact replacement program catalog"
        )
    snapshot_unsigned = {
        key: snapshot[key]
        for key in (
            "schema",
            "snapshot_id",
            "session_id",
            "project_path",
            "project_hash",
            "plan_kind",
            "artifact_visibility",
            "programs",
        )
    }
    if not hmac.compare_digest(
        _sha256(snapshot.get("snapshot_digest"), "MNE snapshot digest"),
        _digest(snapshot_unsigned),
    ):
        raise ChangeExecutionPreconditionError(
            "project.snapshot private-digest receipt digest is invalid"
        )
    checkpoint = _object(
        snapshot.get("transaction_checkpoint"),
        "project.snapshot transaction checkpoint",
    )
    transaction_id = _required_text(snapshot, "transaction_id", "project.snapshot")
    snapshot_id = _required_text(snapshot, "snapshot_id", "project.snapshot")
    operation_id = _required_text(snapshot, "operation_id", "project.snapshot")
    lease_generation = _required_text(
        checkpoint,
        "lease_generation",
        "project.snapshot transaction checkpoint",
    )
    for label, candidate in (
        ("snapshot_id", snapshot_id),
        ("transaction_id", transaction_id),
        ("operation_id", operation_id),
        ("lease_generation", lease_generation),
    ):
        try:
            canonical = str(uuid.UUID(candidate))
        except ValueError as exc:
            raise ChangeExecutionPreconditionError(
                f"project.snapshot {label} must be a canonical UUID"
            ) from exc
        if canonical != candidate:
            raise ChangeExecutionPreconditionError(
                f"project.snapshot {label} must be a canonical UUID"
            )
    if (
        checkpoint.get("host_transaction_id") != transaction.id
        or checkpoint.get("transaction_id") != transaction_id
        or checkpoint.get("snapshot_id") != snapshot_id
        or checkpoint.get("snapshot_digest") != snapshot.get("snapshot_digest")
        or checkpoint.get("artifact_visibility") != "private_digest_only"
        or checkpoint.get("plan_kind") != MNE_PLAN_KIND
        or checkpoint.get("workspace_id") != target.workspace_id
        or checkpoint.get("project_identity_digest")
        != MCPChangeExecutionService._project_identity_digest(target)
        or checkpoint.get("active_session_id") != target.engineering_session_id
        or checkpoint.get("pid") != target.engineering_process_id
        or checkpoint.get("hwnd") != target.engineering_window_handle
        or not _same_path(checkpoint.get("project_path"), target.project_path)
        or checkpoint.get("current_project_hash") != target.artifact_hash
        or checkpoint.get("state") != "SNAPSHOT_CREATED"
        or checkpoint.get("mutation_possible") is not False
        or checkpoint.get("recovery_action") != "abort_safe"
        or checkpoint.get("terminal") is not False
        or checkpoint.get("lease_status") != "live"
    ):
        raise ChangeExecutionPreconditionError(
            "project.snapshot transaction checkpoint is not exact"
        )
    return snapshot, checkpoint


def _validate_verification_contract(transaction: ChangeTransaction) -> None:
    if transaction.plan.get("plan_kind") not in MNE_STRUCTURAL_PLAN_KINDS:
        raise ChangeExecutionPreconditionError("unsupported full-program plan kind")
    if transaction.plan.get("verification_scope") not in {
        MNE_VERIFICATION_SCOPE,
        MNE_XGSIM_VERIFICATION_SCOPE,
    }:
        raise ChangeExecutionPreconditionError(
            "full-program replacement requires offline_native_reopen verification"
        )
    validation = transaction.plan.get("validation")
    if not isinstance(validation, list) or tuple(validation) != MNE_VALIDATION_STEPS:
        raise ChangeExecutionPreconditionError(
            "full-program replacement requires structural diff, Program Check, save, "
            "and independent reopen in that order"
        )
    manual = transaction.plan.get("manual_follow_up")
    if not isinstance(manual, list) or any(
        not isinstance(item, str) or not item.strip() for item in manual
    ):
        raise ChangeExecutionPreconditionError(
            "full-program replacement manual_follow_up is invalid"
        )


def _verification_evidence(transaction: ChangeTransaction) -> dict[str, Any]:
    _validate_verification_contract(transaction)
    return {
        "verification_scope": transaction.plan["verification_scope"],
        "executed_checks": list(MNE_VALIDATION_STEPS),
        "not_executed": list(MNE_NOT_EXECUTED_VALIDATION_TIERS),
        "manual_follow_up": list(transaction.plan["manual_follow_up"]),
    }


def _profile_plc_series(profile: Mapping[str, Any]) -> str:
    addresses: list[str] = []
    if "scenario" in profile:
        scenario = profile.get("scenario")
        steps = scenario.get("steps") if isinstance(scenario, Mapping) else None
        if not isinstance(steps, list) or not steps:
            raise ChangeExecutionPreconditionError("MNE XG-SIM scenario profile is invalid")
        for step in steps:
            if not isinstance(step, Mapping):
                raise ChangeExecutionPreconditionError("MNE XG-SIM scenario step is invalid")
            for field in ("drive", "observe"):
                assignments = step.get(field)
                if not isinstance(assignments, Mapping):
                    raise ChangeExecutionPreconditionError(
                        "MNE XG-SIM scenario assignments are invalid"
                    )
                addresses.extend(str(address) for address in assignments)
    else:
        for field in ("normal_assignments", "deny_assignments", "restore_assignments"):
            assignments = profile.get(field)
            if not isinstance(assignments, Mapping):
                raise ChangeExecutionPreconditionError("MNE XG-SIM profile assignments are invalid")
            addresses.extend(str(address) for address in assignments)
    outputs = profile.get("output_addresses")
    if not isinstance(outputs, list):
        raise ChangeExecutionPreconditionError("MNE XG-SIM profile outputs are invalid")
    addresses.extend(str(address) for address in outputs)
    series: set[str] = set()
    for address in addresses:
        token = address.upper()
        digits = token[1:-1]
        if len(token) < 3 or token[0] not in {"P", "M"} or not digits.isdecimal():
            raise ChangeExecutionPreconditionError("MNE XG-SIM profile address is invalid")
        if len(digits) == 4:
            series.add("XGK")
        elif len(digits) == 3:
            series.add("XGB")
        else:
            raise ChangeExecutionPreconditionError("MNE XG-SIM profile address width is invalid")
    if len(series) != 1:
        raise ChangeExecutionPreconditionError("MNE XG-SIM profile mixes PLC address families")
    return series.pop()


def _expected_mne_xgsim_observation_phases(
    profile: Mapping[str, Any],
) -> tuple[tuple[str, int | dict[str, int]], ...]:
    """Derive receipt expectations only from the approved compiler profile."""

    scenario = profile.get("scenario")
    if scenario is None:
        return (("normal", 1), ("deny", 0), ("restore", 1))
    steps = scenario.get("steps") if isinstance(scenario, Mapping) else None
    if not isinstance(steps, list) or not steps:
        raise ChangeExecutionPreconditionError("MNE XG-SIM scenario profile is invalid")
    phases: list[tuple[str, int | dict[str, int]]] = []
    for step in steps:
        if not isinstance(step, Mapping):
            raise ChangeExecutionPreconditionError("MNE XG-SIM scenario step is invalid")
        name = step.get("name")
        observe = step.get("observe")
        if not isinstance(name, str) or not isinstance(observe, Mapping) or not observe:
            raise ChangeExecutionPreconditionError("MNE XG-SIM scenario expectation is invalid")
        expected = dict(observe)
        if any(isinstance(value, bool) or value not in {0, 1} for value in expected.values()):
            raise ChangeExecutionPreconditionError("MNE XG-SIM scenario expectation is invalid")
        phases.append((name, expected))
    return tuple(phases)


_PUBLIC_CHECKPOINT_EVIDENCE_FIELDS = (
    "schema",
    "host_transaction_id",
    "transaction_id",
    "snapshot_id",
    "snapshot_digest",
    "artifact_visibility",
    "backup_id",
    "plan_id",
    "plan_digest",
    "plan_kind",
    "workspace_id",
    "project_identity_digest",
    "active_session_id",
    "pid",
    "hwnd",
    "current_project_hash",
    "state",
    "program_id",
    "program_name",
    "baseline_digest",
    "before_mne_sha256",
    "target_mne_sha256",
    "adapter_binding_digest",
    "runtime_identity_digest",
    "target_program_digest",
    "rollback_ld_sha256",
    "simulation_receipt_digest",
    "lease_generation",
    "mutation_possible",
    "recovery_action",
    "terminal",
    "lease_status",
)


def _public_checkpoint_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project a validated journal checkpoint onto bounded, non-private evidence."""

    projected: dict[str, Any] = {}
    for field in _PUBLIC_CHECKPOINT_EVIDENCE_FIELDS:
        item = value.get(field)
        if item is None:
            continue
        if not isinstance(item, str | int | bool):
            raise ChangeExecutionPreconditionError(
                "MNE journal checkpoint contains unsafe public evidence"
            )
        projected[field] = item
    _assert_no_private_payload(projected)
    return projected


def _public_recovery_attach_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only identity facts needed to audit a recovery attachment."""

    projected: dict[str, Any] = {}
    for field in (
        "mode",
        "session_id",
        "pid",
        "hwnd",
        "project_hash",
        "plc_policy",
    ):
        item = value.get(field)
        if item is None:
            continue
        if not isinstance(item, str | int | bool):
            raise ChangeExecutionPreconditionError(
                "MNE recovery attachment contains unsafe public evidence"
            )
        projected[field] = item
    _assert_no_private_payload(projected)
    return projected


def _public_failure_kind(exc: BaseException) -> str:
    """Expose only a bounded exception class, never vendor text or filesystem paths."""

    kind = type(exc).__name__
    return kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", kind) else "RecoveryError"


def _transaction_lookup_checkpoint(
    value: object,
    *,
    host_transaction_id: str,
    missing_ok: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    lookup = _object(value, "change.transaction_lookup")
    common = {
        "schema_version",
        "ok",
        "verified",
        "operation",
        "found",
        "host_transaction_id",
    }
    found = lookup.get("found")
    expected = common | (
        {"transaction_checkpoint", "terminal_receipt"} if found is True else set()
    )
    if (
        set(lookup) != expected
        or lookup.get("schema_version") != MNE_MCP_SCHEMA_VERSION
        or lookup.get("ok") is not True
        or lookup.get("verified") is not True
        or lookup.get("operation") != "change.transaction_lookup"
        or lookup.get("host_transaction_id") != host_transaction_id
        or not isinstance(found, bool)
    ):
        raise ChangeExecutionPreconditionError(
            "change.transaction_lookup returned an unexpected contract"
        )
    if found is False:
        if not missing_ok:
            raise ChangeExecutionPreconditionError(
                "change.transaction_lookup did not find the native transaction"
            )
        return None, None
    checkpoint = _object(
        lookup.get("transaction_checkpoint"),
        "change.transaction_lookup checkpoint",
    )
    _assert_no_private_payload(checkpoint)
    terminal_receipt = lookup.get("terminal_receipt")
    if terminal_receipt is not None:
        terminal_receipt = _object(
            terminal_receipt,
            "change.transaction_lookup terminal receipt",
        )
        if (
            set(terminal_receipt) != {"kind", "receipt"}
            or terminal_receipt.get("kind") not in {"verification", "rollback"}
            or not isinstance(terminal_receipt.get("receipt"), dict)
        ):
            raise ChangeExecutionPreconditionError(
                "change.transaction_lookup terminal receipt wrapper is invalid"
            )
        _assert_no_private_payload(terminal_receipt)
    return checkpoint, terminal_receipt


def _transaction_status_checkpoint(
    value: object,
    *,
    transaction_id: str,
) -> dict[str, Any]:
    status = _object(value, "change.transaction_status")
    if (
        set(status)
        != {
            "schema_version",
            "ok",
            "verified",
            "operation",
            "transaction",
            "transaction_checkpoint",
            "events",
        }
        or status.get("schema_version") != MNE_MCP_SCHEMA_VERSION
        or status.get("ok") is not True
        or status.get("verified") is not True
        or status.get("operation") != "change.transaction_status"
        or not isinstance(status.get("transaction"), dict)
        or not isinstance(status.get("events"), list)
    ):
        raise ChangeExecutionPreconditionError(
            "change.transaction_status returned an unexpected contract"
        )
    checkpoint = _object(
        status.get("transaction_checkpoint"),
        "change.transaction_status checkpoint",
    )
    if checkpoint.get("transaction_id") != transaction_id:
        raise ChangeExecutionPreconditionError(
            "change.transaction_status mismatched transaction identity"
        )
    _assert_no_private_payload(checkpoint)
    return checkpoint


def _terminal_lease_release_checkpoint(
    value: object,
    *,
    host_transaction_id: str,
    transaction_id: str,
    terminal_state: str,
    lease_generation: str,
) -> dict[str, Any]:
    result = _object(value, "change.release_terminal_lease")
    if (
        set(result)
        != {
            "schema_version",
            "ok",
            "verified",
            "operation",
            "host_transaction_id",
            "transaction_id",
            "transaction_state",
            "lease_generation",
            "lease_was_present",
            "lease_released",
            "transaction_checkpoint",
        }
        or result.get("schema_version") != MNE_MCP_SCHEMA_VERSION
        or result.get("ok") is not True
        or result.get("verified") is not True
        or result.get("operation") != "change.release_terminal_lease"
        or result.get("host_transaction_id") != host_transaction_id
        or result.get("transaction_id") != transaction_id
        or result.get("transaction_state") != terminal_state
        or result.get("lease_generation") != lease_generation
        or not isinstance(result.get("lease_was_present"), bool)
        or result.get("lease_released") is not True
    ):
        raise ChangeExecutionPreconditionError(
            "change.release_terminal_lease returned an unexpected contract"
        )
    checkpoint = _object(
        result.get("transaction_checkpoint"),
        "change.release_terminal_lease checkpoint",
    )
    if (
        checkpoint.get("host_transaction_id") != host_transaction_id
        or checkpoint.get("transaction_id") != transaction_id
        or checkpoint.get("state") != terminal_state
        or checkpoint.get("lease_generation") != lease_generation
        or checkpoint.get("terminal") is not True
        or checkpoint.get("recovery_action") != "already_terminal"
        or checkpoint.get("lease_status") != "released"
    ):
        raise ChangeExecutionPreconditionError(
            "change.release_terminal_lease did not prove exact terminal cleanup"
        )
    _assert_no_private_payload(checkpoint)
    return checkpoint


def _validate_no_mutation_abort(
    value: object,
    *,
    transaction: ChangeTransaction,
    transaction_id: str,
    expected_state_before: str,
) -> dict[str, Any]:
    aborted = _object(value, "change.abort no-mutation result")
    expected_keys = {
        "schema_version",
        "ok",
        "verified",
        "operation",
        "transaction_id",
        "state_before",
        "state_after",
        "transaction_state",
        "native_mutation_performed",
        "project_hash",
        "project_unchanged",
        "lease_released",
        "transaction_checkpoint",
    }
    _assert_no_private_payload(aborted)
    checkpoint = _object(
        aborted.get("transaction_checkpoint"),
        "change.abort no-mutation checkpoint",
    )
    if (
        set(aborted) != expected_keys
        or aborted.get("schema_version") != MNE_MCP_SCHEMA_VERSION
        or aborted.get("ok") is not True
        or aborted.get("verified") is not True
        or aborted.get("operation") != "change.abort"
        or aborted.get("transaction_id") != transaction_id
        or aborted.get("state_before") != expected_state_before
        or aborted.get("state_after") != "ABORTED_NO_MUTATION"
        or aborted.get("transaction_state") != "ABORTED_NO_MUTATION"
        or aborted.get("native_mutation_performed") is not False
        or aborted.get("project_hash") != transaction.target.artifact_hash
        or aborted.get("project_unchanged") is not True
        or aborted.get("lease_released") is not True
        or checkpoint.get("host_transaction_id") != transaction.id
        or checkpoint.get("transaction_id") != transaction_id
        or checkpoint.get("state") != "ABORTED_NO_MUTATION"
        or checkpoint.get("mutation_possible") is not False
        or checkpoint.get("recovery_action") != "already_terminal"
        or checkpoint.get("terminal") is not True
        or checkpoint.get("lease_status") != "released"
        or checkpoint.get("current_project_hash") != transaction.target.artifact_hash
        or not _same_path(checkpoint.get("project_path"), transaction.target.project_path)
    ):
        raise ChangeExecutionPreconditionError(
            "MCP did not prove an exact terminal no-mutation abort"
        )
    return checkpoint


def _validate_baseline_receipt(
    value: object,
    *,
    transaction: ChangeTransaction,
    target_program: Mapping[str, Any],
    runtime_identity_digest: str,
    approved_baseline: Mapping[str, str],
) -> dict[str, Any]:
    baseline = _strict_public_receipt(
        value,
        schema=MNE_BASELINE_SCHEMA,
        digest_field="baseline_digest",
        keys={
            "schema",
            "baseline_id",
            "captured_at",
            "pid",
            "hwnd",
            "project_path",
            "project_sha256",
            "program",
            "active_project_binding_digest",
            "runtime_identity_digest",
            "mne",
            "ld",
            "baseline_digest",
        },
        label="program.plan_mne_replacement.baseline",
    )
    target = transaction.target
    try:
        canonical_baseline_id = str(uuid.UUID(str(baseline.get("baseline_id"))))
    except ValueError as exc:
        raise ChangeExecutionPreconditionError("MNE baseline id must be a canonical UUID") from exc
    program = _object(baseline.get("program"), "MNE baseline program")
    mne = _object(baseline.get("mne"), "MNE baseline MNE receipt")
    ld = _object(baseline.get("ld"), "MNE baseline LD receipt")
    if (
        canonical_baseline_id != baseline.get("baseline_id")
        or baseline.get("pid") != target.engineering_process_id
        or baseline.get("hwnd") != target.engineering_window_handle
        or not _same_path(baseline.get("project_path"), target.project_path)
        or baseline.get("project_sha256") != target.artifact_hash
        or baseline.get("project_sha256") != approved_baseline.get("project_sha256")
        or baseline.get("runtime_identity_digest") != runtime_identity_digest
        or _SHA256.fullmatch(str(baseline.get("active_project_binding_digest") or "")) is None
        or set(program) != {"program_id", "name", "mdi_hwnd", "editor_hwnd"}
        or program.get("program_id") != target_program.get("program_id")
        or program.get("name") != target_program.get("program_name")
        or any(
            not isinstance(program.get(key), int)
            or isinstance(program.get(key), bool)
            or int(program[key]) <= 0
            for key in ("mdi_hwnd", "editor_hwnd")
        )
        or set(mne)
        != {
            "canonicalizer_version",
            "payload_sha256",
            "payload_size_utf16le",
            "row_count",
            "executable_row_count",
            "terminal_end_count",
            "terminal_end_is_final",
            "stable_repeat",
        }
        or mne.get("stable_repeat") is not True
        or mne.get("terminal_end_count") != 1
        or mne.get("terminal_end_is_final") is not True
        or set(ld)
        != {
            "payload_sha256",
            "canonical_sha256",
            "payload_size",
            "stable_repeat",
            "visual_row_count",
        }
        or ld.get("stable_repeat") is not True
        or ld.get("payload_sha256") != approved_baseline.get("ld_payload_sha256")
    ):
        raise ChangeExecutionPreconditionError(
            "MNE baseline receipt is not bound to the exact target and runtime"
        )
    for label, digest in (
        ("baseline project hash", baseline.get("project_sha256")),
        ("baseline runtime digest", baseline.get("runtime_identity_digest")),
        ("baseline MNE hash", mne.get("payload_sha256")),
        ("baseline LD hash", ld.get("payload_sha256")),
        ("baseline LD canonical hash", ld.get("canonical_sha256")),
    ):
        _sha256(digest, label)
    return baseline


def _validate_plan_receipt(
    value: object,
    *,
    transaction: ChangeTransaction,
    target_program: Mapping[str, Any],
    baseline: Mapping[str, Any],
    adapter_binding_digest: str,
    runtime_identity_digest: str,
) -> dict[str, Any]:
    plan = _strict_public_receipt(
        value,
        schema=MNE_PLAN_SCHEMA,
        digest_field="plan_digest",
        keys={
            "schema",
            "plan_id",
            "created_at",
            "baseline_digest",
            "project_path",
            "project_sha256",
            "program_id",
            "program_name",
            "runtime_identity_digest",
            "adapter_binding_digest",
            "before_mne_sha256",
            "target_mne",
            "rollback_mne_sha256",
            "rollback_ld_sha256",
            "plan_digest",
        },
        label="program.plan_mne_replacement.plan",
    )
    target_mne = _object(plan.get("target_mne"), "MNE target receipt")
    try:
        canonical_plan_id = str(uuid.UUID(str(plan.get("plan_id"))))
    except ValueError as exc:
        raise ChangeExecutionPreconditionError(
            "MNE replacement plan id must be a canonical UUID"
        ) from exc
    if (
        canonical_plan_id != plan.get("plan_id")
        or plan.get("baseline_digest") != baseline.get("baseline_digest")
        or not _same_path(plan.get("project_path"), transaction.target.project_path)
        or plan.get("project_sha256") != transaction.target.artifact_hash
        or plan.get("program_id") != target_program.get("program_id")
        or plan.get("program_name") != target_program.get("program_name")
        or plan.get("runtime_identity_digest") != runtime_identity_digest
        or plan.get("adapter_binding_digest") != adapter_binding_digest
        or plan.get("before_mne_sha256")
        != _object(baseline.get("mne"), "MNE baseline MNE receipt").get("payload_sha256")
        or plan.get("rollback_mne_sha256") != plan.get("before_mne_sha256")
        or plan.get("rollback_ld_sha256")
        != _object(baseline.get("ld"), "MNE baseline LD receipt").get("payload_sha256")
        or set(target_mne)
        != {
            "canonicalizer_version",
            "payload_sha256",
            "payload_size_utf16le",
            "row_count",
            "executable_row_count",
            "terminal_end_count",
            "terminal_end_is_final",
        }
        or target_mne.get("terminal_end_count") != 1
        or target_mne.get("terminal_end_is_final") is not True
        or target_mne.get("row_count") != target_program.get("instruction_count")
        or target_mne.get("payload_sha256") == plan.get("before_mne_sha256")
    ):
        raise ChangeExecutionPreconditionError(
            "MNE plan receipt is not bound to the approved target and baseline"
        )
    for field in (
        "baseline_digest",
        "project_sha256",
        "runtime_identity_digest",
        "adapter_binding_digest",
        "before_mne_sha256",
        "rollback_mne_sha256",
        "rollback_ld_sha256",
    ):
        _sha256(plan.get(field), f"MNE plan {field}")
    _sha256(target_mne.get("payload_sha256"), "MNE plan target payload hash")
    return plan


def _validate_application_receipt(
    value: object,
    *,
    transaction: ChangeTransaction,
    plan: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    application = _strict_public_receipt(
        value,
        schema=MNE_APPLICATION_SCHEMA,
        digest_field="application_digest",
        keys={
            "schema",
            "plan_digest",
            "baseline_digest",
            "pid",
            "hwnd",
            "project_path",
            "project_sha256_before",
            "program_id",
            "program_name",
            "mdi_hwnd",
            "editor_hwnd",
            "view_hwnd",
            "payload_sha256_before",
            "payload_sha256_after",
            "adapter_binding_digest",
            "phase_readbacks",
            "verified_in_editor",
            "application_digest",
        },
        label="program.apply_mne_replacement.application",
    )
    phases = application.get("phase_readbacks")
    if (
        application.get("verified_in_editor") is not True
        or application.get("plan_digest") != plan.get("plan_digest")
        or application.get("baseline_digest") != baseline.get("baseline_digest")
        or application.get("pid") != transaction.target.engineering_process_id
        or application.get("hwnd") != transaction.target.engineering_window_handle
        or not _same_path(application.get("project_path"), transaction.target.project_path)
        or application.get("project_sha256_before") != transaction.target.artifact_hash
        or application.get("program_id") != plan.get("program_id")
        or application.get("program_name") != plan.get("program_name")
        or application.get("payload_sha256_before") != plan.get("before_mne_sha256")
        or application.get("payload_sha256_after")
        != _object(plan.get("target_mne"), "MNE plan target receipt").get("payload_sha256")
        or application.get("adapter_binding_digest") != plan.get("adapter_binding_digest")
        or not isinstance(phases, list)
        or [item.get("phase") for item in phases if isinstance(item, dict)]
        not in (["select", "clear"], ["select", "clear", "paste"])
        or any(
            not isinstance(item, dict)
            or set(item) != {"phase", "delivery", "target", "readback_sha256"}
            or _SHA256.fullmatch(str(item.get("readback_sha256") or "")) is None
            for item in phases
        )
        or phases[0].get("readback_sha256") != plan.get("before_mne_sha256")
        or phases[-1].get("readback_sha256")
        != _object(plan.get("target_mne"), "MNE plan target receipt").get("payload_sha256")
    ):
        raise ChangeExecutionPreconditionError(
            "MNE application receipt is not bound to the exact authorized plan"
        )
    return application


def _validate_reopen_receipt(
    value: object,
    *,
    transaction: ChangeTransaction,
    plan: Mapping[str, Any],
    expected_mne_sha256: str,
    expected_ld_payload_sha256: str | None = None,
    expected_ld_canonical_sha256: str | None = None,
    expected_source_pid: int | None = None,
    expected_source_hwnd: int | None = None,
) -> dict[str, Any]:
    reopen = _object(value, "MNE independent reopen receipt")
    expected_keys = {
        "verified",
        "source_pid",
        "source_hwnd",
        "pid",
        "hwnd",
        "process_is_independent",
        "project_path",
        "project_sha256",
        "program_id",
        "program_name",
        "mdi_hwnd",
        "editor_hwnd",
        "view_hwnd",
        "payload_sha256",
        "ld_payload_sha256",
        "ld_raw_equal",
        "ld_canonical_sha256",
        "stable_readback",
    }
    source_pid = (
        transaction.target.engineering_process_id
        if expected_source_pid is None
        else expected_source_pid
    )
    source_hwnd = (
        transaction.target.engineering_window_handle
        if expected_source_hwnd is None
        else expected_source_hwnd
    )
    if (
        set(reopen) != expected_keys
        or reopen.get("verified") is not True
        or reopen.get("process_is_independent") is not True
        or reopen.get("stable_readback") is not True
        or reopen.get("ld_raw_equal") is not True
        or reopen.get("source_pid") != source_pid
        or reopen.get("source_hwnd") != source_hwnd
        or not isinstance(reopen.get("pid"), int)
        or isinstance(reopen.get("pid"), bool)
        or int(reopen["pid"]) <= 0
        or not isinstance(reopen.get("hwnd"), int)
        or isinstance(reopen.get("hwnd"), bool)
        or int(reopen["hwnd"]) <= 0
        or reopen.get("pid") == reopen.get("source_pid")
        or reopen.get("hwnd") == reopen.get("source_hwnd")
        or not _same_path(reopen.get("project_path"), transaction.target.project_path)
        or reopen.get("program_id") != plan.get("program_id")
        or reopen.get("program_name") != plan.get("program_name")
        or reopen.get("payload_sha256") != expected_mne_sha256
        or (
            expected_ld_payload_sha256 is not None
            and reopen.get("ld_payload_sha256") != expected_ld_payload_sha256
        )
        or (
            expected_ld_canonical_sha256 is not None
            and reopen.get("ld_canonical_sha256") != expected_ld_canonical_sha256
        )
    ):
        raise ChangeExecutionPreconditionError("MNE independent reopen receipt is not exact")
    for field in (
        "project_sha256",
        "payload_sha256",
        "ld_payload_sha256",
        "ld_canonical_sha256",
    ):
        _sha256(reopen.get(field), f"MNE reopen {field}")
    return reopen


def _validate_rollback_transition(
    value: object,
    *,
    baseline_mne_sha256: str,
    target_mne_sha256: str,
    adapter_binding_digest: str,
) -> dict[str, Any]:
    transition = _object(value, "MNE rollback transition")
    _assert_no_private_payload(transition)
    before = transition.get("payload_sha256_before")
    after = transition.get("payload_sha256_after")
    phases = transition.get("phases")
    if set(transition) == {
        "verified",
        "already_restored",
        "payload_sha256_before",
        "payload_sha256_after",
        "phases",
    }:
        if (
            transition.get("verified") is not True
            or transition.get("already_restored") is not True
            or before != baseline_mne_sha256
            or after != baseline_mne_sha256
            or phases != []
        ):
            raise ChangeExecutionPreconditionError(
                "MNE rollback already-restored transition is not exact"
            )
        return transition
    if set(transition) != {
        "ok",
        "verified",
        "operation",
        "payload_sha256_before",
        "payload_sha256_after",
        "row_count_after",
        "strategy",
        "phases",
        "adapter_binding_digest",
    }:
        raise ChangeExecutionPreconditionError("MNE rollback transition shape is unsupported")
    strategy = transition.get("strategy")
    if (
        transition.get("ok") is not True
        or transition.get("verified") is not True
        or transition.get("operation") != "program.replace_existing_mne"
        or before != target_mne_sha256
        or after != baseline_mne_sha256
        or transition.get("adapter_binding_digest") != adapter_binding_digest
        or not isinstance(transition.get("row_count_after"), int)
        or isinstance(transition.get("row_count_after"), bool)
        or int(transition["row_count_after"]) <= 0
        or not isinstance(strategy, dict)
        or set(strategy) != {"schema", "select", "clear", "paste", "paste_payload_mode"}
        or strategy.get("paste_payload_mode") not in {"complete", "without_terminal_end"}
        or not isinstance(phases, list)
        or [item.get("phase") for item in phases if isinstance(item, dict)]
        not in (["select", "clear"], ["select", "clear", "paste"])
        or any(
            not isinstance(item, dict)
            or set(item) != {"phase", "delivery", "target", "readback_sha256"}
            or not isinstance(item.get("delivery"), str)
            or not item.get("delivery")
            or not isinstance(item.get("target"), str)
            or not item.get("target")
            or _SHA256.fullmatch(str(item.get("readback_sha256") or "")) is None
            for item in phases
        )
        or phases[0].get("readback_sha256") != target_mne_sha256
        or phases[-1].get("readback_sha256") != baseline_mne_sha256
    ):
        raise ChangeExecutionPreconditionError("MNE rollback transition is not exact")
    return transition


def _validate_verification_receipt(
    value: object,
    *,
    transaction: ChangeTransaction,
    plan: Mapping[str, Any],
    application: Mapping[str, Any],
) -> dict[str, Any]:
    verification = _strict_public_receipt(
        value,
        schema=MNE_VERIFICATION_SCHEMA,
        digest_field="verification_digest",
        keys={
            "schema",
            "plan_digest",
            "application_digest",
            "program_check_verified",
            "save_verified",
            "project_sha256_after",
            "reopen",
            "verified",
            "verification_digest",
        },
        label="program.verify_mne_replacement.verification",
    )
    target_hash = _sha256(
        _object(plan.get("target_mne"), "MNE plan target receipt").get("payload_sha256"),
        "MNE target hash",
    )
    reopen = _validate_reopen_receipt(
        verification.get("reopen"),
        transaction=transaction,
        plan=plan,
        expected_mne_sha256=target_hash,
    )
    if (
        verification.get("verified") is not True
        or verification.get("program_check_verified") is not True
        or verification.get("save_verified") is not True
        or verification.get("plan_digest") != plan.get("plan_digest")
        or verification.get("application_digest") != application.get("application_digest")
        or verification.get("project_sha256_after") != reopen.get("project_sha256")
    ):
        raise ChangeExecutionPreconditionError(
            "MNE verification receipt is not bound to the exact application"
        )
    return verification


def _validate_rollback_receipt(
    value: object,
    *,
    transaction: ChangeTransaction,
    plan: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    rollback = _strict_public_receipt(
        value,
        schema=MNE_ROLLBACK_SCHEMA,
        digest_field="rollback_digest",
        keys={
            "schema",
            "plan_digest",
            "baseline_digest",
            "transition",
            "program_check_verified",
            "save_verified",
            "project_sha256_after",
            "baseline_ld_raw_sha256",
            "restored_ld_raw_sha256",
            "ld_raw_equal",
            "ld_canonical_sha256",
            "ld_canonical_equal",
            "reopen",
            "verified",
            "rollback_digest",
        },
        label="program.rollback_mne_replacement.rollback",
    )
    baseline_ld = _object(baseline.get("ld"), "MNE rollback baseline LD receipt")
    baseline_ld_raw_sha256 = _sha256(
        baseline_ld.get("payload_sha256"), "MNE rollback baseline LD hash"
    )
    baseline_ld_canonical_sha256 = _sha256(
        baseline_ld.get("canonical_sha256"), "MNE rollback baseline canonical LD hash"
    )
    baseline_mne_sha256 = _sha256(
        plan.get("rollback_mne_sha256"), "MNE rollback baseline hash"
    )
    target_mne_sha256 = _sha256(
        _object(plan.get("target_mne"), "MNE rollback target receipt").get(
            "payload_sha256"
        ),
        "MNE rollback target hash",
    )
    _validate_rollback_transition(
        rollback.get("transition"),
        baseline_mne_sha256=baseline_mne_sha256,
        target_mne_sha256=target_mne_sha256,
        adapter_binding_digest=_sha256(
            plan.get("adapter_binding_digest"),
            "MNE rollback adapter digest",
        ),
    )
    reopen = _validate_reopen_receipt(
        rollback.get("reopen"),
        transaction=transaction,
        plan=plan,
        expected_mne_sha256=baseline_mne_sha256,
        expected_ld_payload_sha256=baseline_ld_raw_sha256,
        expected_ld_canonical_sha256=baseline_ld_canonical_sha256,
    )
    if (
        rollback.get("verified") is not True
        or rollback.get("program_check_verified") is not True
        or rollback.get("save_verified") is not True
        or rollback.get("ld_raw_equal") is not True
        or rollback.get("ld_canonical_equal") is not True
        or rollback.get("plan_digest") != plan.get("plan_digest")
        or rollback.get("baseline_digest") != baseline.get("baseline_digest")
        or rollback.get("baseline_ld_raw_sha256") != baseline_ld_raw_sha256
        or rollback.get("restored_ld_raw_sha256") != baseline_ld_raw_sha256
        or rollback.get("ld_canonical_sha256") != baseline_ld_canonical_sha256
        or rollback.get("project_sha256_after") != reopen.get("project_sha256")
    ):
        raise ChangeExecutionPreconditionError(
            "MNE rollback receipt is not bound to the exact baseline"
        )
    return rollback


def _validated_persisted_apply_receipts(
    transaction: ChangeTransaction,
    checkpoint: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    evidence = _object(transaction.result.get("apply"), "persisted MNE apply evidence")
    expected_keys = {
        "host_transaction_id",
        "host_plan_digest",
        "plan_kind",
        "mcp_transaction_id",
        "mcp_plan_id",
        "mcp_plan_digest",
        "project_identity_digest",
        "target_program_digest",
        "lease_generation",
        "snapshot_id",
        "snapshot_digest",
        "artifact_visibility",
        "backup_id",
        "baseline",
        "plan",
        "application",
        "apply",
    }
    _assert_no_private_payload(evidence)
    if (
        set(evidence) != expected_keys
        or evidence.get("host_transaction_id") != transaction.id
        or evidence.get("host_plan_digest") != transaction.plan_digest
        or evidence.get("plan_kind") != MNE_PLAN_KIND
        or evidence.get("mcp_transaction_id") != checkpoint.get("transaction_id")
        or evidence.get("mcp_plan_id") != checkpoint.get("plan_id")
        or evidence.get("mcp_plan_digest") != checkpoint.get("plan_digest")
        or evidence.get("project_identity_digest")
        != MCPChangeExecutionService._project_identity_digest(transaction.target)
        or evidence.get("target_program_digest") != checkpoint.get("target_program_digest")
        or evidence.get("lease_generation") != checkpoint.get("lease_generation")
        or evidence.get("snapshot_id") != checkpoint.get("snapshot_id")
        or evidence.get("snapshot_digest") != checkpoint.get("snapshot_digest")
        or evidence.get("artifact_visibility") != checkpoint.get("artifact_visibility")
        or evidence.get("backup_id") != checkpoint.get("backup_id")
    ):
        raise ChangeExecutionPreconditionError(
            "persisted MNE apply evidence differs from the terminal journal"
        )
    target_program = _typed_target_program(transaction)
    adapter_binding_digest = _sha256(
        checkpoint.get("adapter_binding_digest"),
        "terminal MNE adapter binding digest",
    )
    runtime_identity_digest = _sha256(
        checkpoint.get("runtime_identity_digest"),
        "terminal MNE runtime digest",
    )
    baseline = _validate_baseline_receipt(
        evidence.get("baseline"),
        transaction=transaction,
        target_program=target_program,
        runtime_identity_digest=runtime_identity_digest,
        approved_baseline=_approved_baseline_binding(transaction),
    )
    plan = _validate_plan_receipt(
        evidence.get("plan"),
        transaction=transaction,
        target_program=target_program,
        baseline=baseline,
        adapter_binding_digest=adapter_binding_digest,
        runtime_identity_digest=runtime_identity_digest,
    )
    application = _validate_application_receipt(
        evidence.get("application"),
        transaction=transaction,
        plan=plan,
        baseline=baseline,
    )
    return baseline, plan, application


def validated_terminal_mne_reopen(
    transaction: ChangeTransaction,
) -> dict[str, Any] | None:
    """Return the exact independently reopened target for a terminal MNE change."""

    if (
        transaction.plan.get("plan_kind") not in MNE_STRUCTURAL_PLAN_KINDS
        or transaction.state
        not in {
            ChangeTransactionState.COMPLETED,
            ChangeTransactionState.ROLLED_BACK,
        }
    ):
        return None
    apply_evidence = _object(
        transaction.result.get("apply"),
        "terminal MNE apply evidence",
    )
    if (
        apply_evidence.get("host_transaction_id") != transaction.id
        or apply_evidence.get("host_plan_digest") != transaction.plan_digest
        or apply_evidence.get("plan_kind") != MNE_PLAN_KIND
    ):
        raise ChangeExecutionPreconditionError(
            "terminal MNE apply evidence differs from the Host transaction"
        )
    plan = _object(apply_evidence.get("plan"), "terminal MNE plan")
    baseline = _object(apply_evidence.get("baseline"), "terminal MNE baseline")
    application = _object(
        apply_evidence.get("application"),
        "terminal MNE application",
    )
    if (
        apply_evidence.get("mcp_plan_id") != plan.get("plan_id")
        or apply_evidence.get("mcp_plan_digest") != plan.get("plan_digest")
    ):
        raise ChangeExecutionPreconditionError(
            "terminal MNE plan identity differs from apply evidence"
        )
    verification_evidence = _object(
        transaction.result.get("verification"),
        "terminal MNE verification evidence",
    )
    if transaction.state is ChangeTransactionState.COMPLETED:
        receipt = _validate_verification_receipt(
            verification_evidence.get("verification"),
            transaction=transaction,
            plan=plan,
            application=application,
        )
        return dict(_object(receipt.get("reopen"), "terminal MNE reopen"))

    simulation_finalization = transaction.result.get("simulation_finalization")
    rollback_value: object = verification_evidence.get("rollback")
    if isinstance(simulation_finalization, dict):
        rollback_value = simulation_finalization.get("rollback")
        if rollback_value is None:
            rollback_value = simulation_finalization.get("terminal_receipt")
    rollback = _validate_rollback_receipt(
        rollback_value,
        transaction=transaction,
        plan=plan,
        baseline=baseline,
    )
    return dict(_object(rollback.get("reopen"), "terminal MNE rollback reopen"))


class CompositeChangeExecutionService:
    """Dispatch immutable v1 operand edits and v2 full-program replacements."""

    def __init__(
        self,
        gateway: MCPGateway,
        *,
        checkpoint_callback: NativeCheckpointCallback | None = None,
    ) -> None:
        self._operand = MCPChangeExecutionService(
            gateway,
            checkpoint_callback=checkpoint_callback,
        )
        self._mne = MNEChangeExecutionService(
            gateway,
            checkpoint_callback=checkpoint_callback,
        )

    def _service(self, transaction: ChangeTransaction):
        if transaction.plan.get("plan_kind") in MNE_STRUCTURAL_PLAN_KINDS:
            return self._mne
        return self._operand

    async def preflight(self, transaction: ChangeTransaction) -> None:
        await self._service(transaction).preflight(transaction)

    async def apply(self, transaction: ChangeTransaction) -> ChangeApplyResult:
        return await self._service(transaction).apply(transaction)

    async def verify(
        self,
        transaction: ChangeTransaction,
        apply_result: ChangeApplyResult,
    ) -> ChangeVerificationResult:
        return await self._service(transaction).verify(transaction, apply_result)

    async def reconcile(
        self,
        transaction: ChangeTransaction,
        recovery_target: ChangeRecoveryTarget,
    ) -> ChangeVerificationResult:
        return await self._service(transaction).reconcile(transaction, recovery_target)


class MNEChangeExecutionService:
    def __init__(
        self,
        gateway: MCPGateway,
        *,
        checkpoint_callback: NativeCheckpointCallback | None,
    ) -> None:
        self._gateway = gateway
        self._checkpoint_callback = checkpoint_callback

    async def preflight(self, transaction: ChangeTransaction) -> None:
        target = transaction.target
        if (
            not target.engineering_session_id
            or target.engineering_process_id is None
            or target.engineering_window_handle is None
        ):
            raise ChangeExecutionPreconditionError(
                "a verified engineering session is required before full-program replacement"
            )
        _typed_target_program(transaction)
        _approved_baseline_binding(transaction)
        _validate_verification_contract(transaction)

    async def apply(self, transaction: ChangeTransaction) -> ChangeApplyResult:
        await self.preflight(transaction)
        target = transaction.target
        target_program = _typed_target_program(transaction)
        approved_baseline = _approved_baseline_binding(transaction)
        session_scope = self._session_scope(transaction)
        mcp_transaction_id: str | None = None
        project_identity_digest = MCPChangeExecutionService._project_identity_digest(target)
        try:
            snapshot = _object(
                await self._gateway.call_change_tool(
                    "project.snapshot",
                    {
                        **session_scope,
                        "workspace_id": target.workspace_id,
                        "begin_change_transaction": True,
                        "host_transaction_id": transaction.id,
                        "plan_kind": MNE_PLAN_KIND,
                    },
                ),
                "project.snapshot",
            )
            snapshot, snapshot_checkpoint = _validate_private_snapshot(
                snapshot,
                transaction=transaction,
                target_program=target_program,
                approved_baseline=approved_baseline,
            )
            mcp_transaction_id = _required_text(snapshot, "transaction_id", "project.snapshot")
            snapshot_id = _required_text(snapshot, "snapshot_id", "project.snapshot")
            await self._persist_checkpoint_or_abort(
                transaction,
                {
                    "stage": "SNAPSHOT_CREATED",
                    "mcp_transaction_id": mcp_transaction_id,
                    "snapshot_id": snapshot_id,
                    "session_id": target.engineering_session_id,
                    "workspace_id": target.workspace_id,
                    "project_path": target.project_path,
                    "project_hash": target.artifact_hash,
                    "project_identity_digest": project_identity_digest,
                    "plan_kind": MNE_PLAN_KIND,
                    "snapshot_digest": snapshot["snapshot_digest"],
                    "artifact_visibility": snapshot["artifact_visibility"],
                    "lease_generation": snapshot_checkpoint["lease_generation"],
                },
                session_scope=session_scope,
                mcp_transaction_id=mcp_transaction_id,
                expected_mcp_state="SNAPSHOT_CREATED",
            )
            backup = _object(
                await self._gateway.call_change_tool(
                    "project.backup",
                    {
                        **session_scope,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "project.backup",
            )
            backup_id = _required_text(backup, "backup_id", "project.backup")
            await self._persist_checkpoint_or_abort(
                transaction,
                {
                    "stage": "BACKUP_CREATED",
                    "mcp_transaction_id": mcp_transaction_id,
                    "snapshot_id": snapshot_id,
                    "backup_id": backup_id,
                    "session_id": target.engineering_session_id,
                    "workspace_id": target.workspace_id,
                    "project_path": target.project_path,
                    "project_hash": target.artifact_hash,
                    "project_identity_digest": project_identity_digest,
                    "plan_kind": MNE_PLAN_KIND,
                },
                session_scope=session_scope,
                mcp_transaction_id=mcp_transaction_id,
                expected_mcp_state="BACKUP_CREATED",
            )
            plan_arguments = {
                **session_scope,
                "transaction_id": mcp_transaction_id,
                "snapshot_id": snapshot_id,
                "backup_id": backup_id,
            }
            plan_kind = transaction.plan.get("plan_kind")
            patch_contract = MNE_PATCH_PLAN_CONTRACTS.get(plan_kind)
            if patch_contract is not None:
                plan_operation = patch_contract.plan_operation
                plan_arguments["candidate"] = transaction.plan["candidate"]
            else:
                plan_operation = "program.plan_mne_replacement"
                plan_arguments.update(
                    {
                        "program_id": target_program["program_id"],
                        "program_name": target_program["program_name"],
                        "target_program": target_program,
                    }
                )
            planned = _object(
                await self._gateway.call_change_tool(
                    plan_operation,
                    plan_arguments,
                ),
                plan_operation,
            )
            self._validate_outer(
                planned,
                operation=plan_operation,
                expected_state="PLANNED",
                expected_transaction_id=mcp_transaction_id,
                required_keys={
                    "plan",
                    "baseline",
                    "target_program_digest",
                    "lease_generation",
                    "impact",
                    "project_unchanged",
                },
            )
            if (
                planned.get("project_unchanged") is not True
                or planned.get("target_program_digest") != target_program["target_program_digest"]
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE planning did not preserve the exact project and target design"
                )
            impact = _object(planned.get("impact"), "MNE plan impact")
            if impact != {
                "project_file_write": False,
                "editor_mutation": False,
                "requires_explicit_apply": True,
                "requires_program_check_save_reopen": True,
            }:
                raise ChangeExecutionPreconditionError("MNE planning impact contract is invalid")
            plan_receipt = _object(planned.get("plan"), "MNE replacement plan receipt")
            adapter_binding_digest = _sha256(
                plan_receipt.get("adapter_binding_digest"),
                "MNE supported adapter digest",
            )
            runtime_identity_digest = _sha256(
                plan_receipt.get("runtime_identity_digest"),
                "MNE live runtime digest",
            )
            baseline = _validate_baseline_receipt(
                planned.get("baseline"),
                transaction=transaction,
                target_program=target_program,
                runtime_identity_digest=runtime_identity_digest,
                approved_baseline=approved_baseline,
            )
            plan = _validate_plan_receipt(
                plan_receipt,
                transaction=transaction,
                target_program=target_program,
                baseline=baseline,
                adapter_binding_digest=adapter_binding_digest,
                runtime_identity_digest=runtime_identity_digest,
            )
            if transaction.plan.get("plan_kind") in MNE_PATCH_PLAN_KINDS:
                candidate = _object(transaction.plan.get("candidate"), "plan.candidate")
                baseline_mne = _object(baseline.get("mne"), "MNE baseline MNE receipt")
                target_mne = _object(plan.get("target_mne"), "MNE target receipt")
                if (
                    baseline_mne.get("payload_sha256") != candidate.get("baseline_mne_sha256")
                    or target_mne.get("payload_sha256") != candidate.get("target_mne_sha256")
                ):
                    raise ChangeExecutionPreconditionError(
                        "MNE plan does not match the approved instruction candidate"
                    )
                if "hardware_context_digest" in candidate and (
                    planned.get("hardware_context_digest")
                    != candidate.get("hardware_context_digest")
                    or planned.get("field_io_bindings")
                    != candidate.get("field_io_bindings")
                ):
                    raise ChangeExecutionPreconditionError(
                        "MNE plan lost its approved hardware terminal binding"
                    )
            mcp_plan_id = _required_text(plan, "plan_id", "MNE plan")
            mcp_plan_digest = _sha256(plan.get("plan_digest"), "MNE plan digest")
            lease_generation = _required_text(
                planned, "lease_generation", plan_operation
            )
            try:
                canonical_lease_generation = str(uuid.UUID(lease_generation))
            except ValueError as exc:
                raise ChangeExecutionPreconditionError(
                    "MNE plan lease generation must be a canonical UUID"
                ) from exc
            if canonical_lease_generation != lease_generation:
                raise ChangeExecutionPreconditionError(
                    "MNE plan lease generation must be a canonical UUID"
                )
            checkpoint = _object(
                planned.get("transaction_checkpoint"),
                "MNE plan transaction checkpoint",
            )
            self._validate_checkpoint(
                transaction=transaction,
                checkpoint=checkpoint,
                expected_transaction_id=mcp_transaction_id,
                expected_state="PLANNED",
                expected_plan_id=mcp_plan_id,
                expected_plan_digest=mcp_plan_digest,
                target_program=target_program,
                adapter_binding_digest=adapter_binding_digest,
                runtime_identity_digest=runtime_identity_digest,
                expected_before_mne_sha256=str(plan["before_mne_sha256"]),
                expected_target_mne_sha256=str(plan["target_mne"]["payload_sha256"]),
                expected_project_hash=target.artifact_hash,
                expected_lease_generation=lease_generation,
                expected_baseline_digest=str(baseline["baseline_digest"]),
                expected_rollback_ld_sha256=str(plan["rollback_ld_sha256"]),
                expected_snapshot_id=snapshot_id,
                expected_snapshot_digest=str(snapshot["snapshot_digest"]),
                expected_artifact_visibility="private_digest_only",
                expected_backup_id=backup_id,
            )
            if (
                checkpoint.get("snapshot_digest") != snapshot["snapshot_digest"]
                or checkpoint.get("artifact_visibility") != "private_digest_only"
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE plan checkpoint lost its private snapshot binding"
                )
            await self._persist_checkpoint_or_abort(
                transaction,
                {
                    "stage": "PLANNED",
                    "mcp_transaction_id": mcp_transaction_id,
                    "mcp_plan_id": mcp_plan_id,
                    "mcp_plan_digest": mcp_plan_digest,
                    "snapshot_id": snapshot_id,
                    "snapshot_digest": snapshot["snapshot_digest"],
                    "artifact_visibility": snapshot["artifact_visibility"],
                    "backup_id": backup_id,
                    "session_id": target.engineering_session_id,
                    "workspace_id": target.workspace_id,
                    "project_path": target.project_path,
                    "project_hash": target.artifact_hash,
                    "project_identity_digest": project_identity_digest,
                    "plan_kind": MNE_PLAN_KIND,
                    "program_id": target_program["program_id"],
                    "program_name": target_program["program_name"],
                    "target_program_digest": target_program["target_program_digest"],
                    "before_mne_sha256": plan["before_mne_sha256"],
                    "target_mne_sha256": plan["target_mne"]["payload_sha256"],
                    "adapter_binding_digest": adapter_binding_digest,
                    "runtime_identity_digest": runtime_identity_digest,
                    "baseline_digest": baseline["baseline_digest"],
                    "rollback_ld_sha256": plan["rollback_ld_sha256"],
                    "baseline_ld_canonical_sha256": _object(
                        baseline.get("ld"), "MNE baseline LD receipt"
                    )["canonical_sha256"],
                    "lease_generation": lease_generation,
                },
                session_scope=session_scope,
                mcp_transaction_id=mcp_transaction_id,
                expected_mcp_state="PLANNED",
            )
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            OSError,
            ValueError,
            TypeError,
        ) as exc:
            try:
                await self._abort_preparation_transaction(
                    transaction=transaction,
                    session_scope=session_scope,
                    expected_transaction_id=mcp_transaction_id,
                )
            except (
                MCPToolError,
                ChangeExecutionPreconditionError,
                ValueError,
                TypeError,
            ) as abort_error:
                raise ChangeExecutionOutcomeUncertain(
                    "MNE preparation failed and a no-mutation abort was not proven",
                    evidence={
                        "known_mcp_transaction_id": mcp_transaction_id,
                        "preparation_error_kind": _public_failure_kind(exc),
                        "abort_error_kind": _public_failure_kind(abort_error),
                    },
                ) from exc
            raise ChangeExecutionFailedSafely(
                "MNE preparation failed; MCP proved an exact no-mutation abort"
            ) from exc

        apply_arguments = {
            **session_scope,
            "transaction_id": mcp_transaction_id,
            "plan_id": mcp_plan_id,
            "plan_digest": mcp_plan_digest,
            "program_id": target_program["program_id"],
            "program_name": target_program["program_name"],
            "target_program_digest": target_program["target_program_digest"],
            "before_mne_sha256": plan["before_mne_sha256"],
            "target_mne_sha256": plan["target_mne"]["payload_sha256"],
            "adapter_binding_digest": adapter_binding_digest,
            "runtime_identity_digest": runtime_identity_digest,
            "lease_generation": lease_generation,
        }
        if "hardware_context_digest" in transaction.plan.get("candidate", {}):
            apply_arguments["hardware_context_digest"] = transaction.plan[
                "candidate"
            ]["hardware_context_digest"]
        authorization = self._authorization(
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            mcp_plan_id=mcp_plan_id,
            mcp_plan_digest=mcp_plan_digest,
            project_identity_digest=project_identity_digest,
            mutation_arguments=apply_arguments,
        )
        try:
            applied = _object(
                await self._gateway.call_change_tool(
                    "program.apply_mne_replacement",
                    {**apply_arguments, "authorization": authorization},
                ),
                "program.apply_mne_replacement",
            )
            self._validate_outer(
                applied,
                operation="program.apply_mne_replacement",
                expected_state="APPLIED",
                expected_transaction_id=mcp_transaction_id,
                required_keys={"application", "project_unchanged"},
            )
            if applied.get("project_unchanged") is not True:
                raise ChangeExecutionPreconditionError(
                    "MNE application changed the project file before verified save"
                )
            application = _validate_application_receipt(
                applied.get("application"),
                transaction=transaction,
                plan=plan,
                baseline=baseline,
            )
            if "hardware_context_digest" in transaction.plan.get("candidate", {}) and (
                applied.get("hardware_context_digest")
                != transaction.plan["candidate"].get("hardware_context_digest")
                or applied.get("field_io_bindings")
                != transaction.plan["candidate"].get("field_io_bindings")
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE application receipt lost its approved hardware terminal binding"
                )
            self._validate_checkpoint(
                transaction=transaction,
                checkpoint=_object(
                    applied.get("transaction_checkpoint"),
                    "MNE apply transaction checkpoint",
                ),
                expected_transaction_id=mcp_transaction_id,
                expected_state="APPLIED",
                expected_plan_id=mcp_plan_id,
                expected_plan_digest=mcp_plan_digest,
                target_program=target_program,
                adapter_binding_digest=adapter_binding_digest,
                runtime_identity_digest=runtime_identity_digest,
                expected_before_mne_sha256=str(plan["before_mne_sha256"]),
                expected_target_mne_sha256=str(plan["target_mne"]["payload_sha256"]),
                expected_project_hash=target.artifact_hash,
                expected_lease_generation=lease_generation,
                expected_baseline_digest=str(baseline["baseline_digest"]),
                expected_rollback_ld_sha256=str(plan["rollback_ld_sha256"]),
                expected_snapshot_id=snapshot_id,
                expected_snapshot_digest=str(snapshot["snapshot_digest"]),
                expected_artifact_visibility="private_digest_only",
                expected_backup_id=backup_id,
            )
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as exc:
            raise ChangeExecutionOutcomeUncertain(
                "MNE full-program application is uncertain and requires reconciliation",
                evidence={
                    "mcp_transaction_id": mcp_transaction_id,
                    "mcp_plan_id": mcp_plan_id,
                    "mcp_plan_digest": mcp_plan_digest,
                    "project_identity_digest": project_identity_digest,
                    "target_program_digest": target_program["target_program_digest"],
                    "before_mne_sha256": plan["before_mne_sha256"],
                    "target_mne_sha256": plan["target_mne"]["payload_sha256"],
                    "lease_generation": lease_generation,
                    "snapshot_id": snapshot_id,
                    "snapshot_digest": snapshot["snapshot_digest"],
                    "artifact_visibility": snapshot["artifact_visibility"],
                    "backup_id": backup_id,
                    "baseline": baseline,
                    "plan": plan,
                },
            ) from exc
        return ChangeApplyResult(
            evidence={
                "host_transaction_id": transaction.id,
                "host_plan_digest": transaction.plan_digest,
                "plan_kind": MNE_PLAN_KIND,
                "mcp_transaction_id": mcp_transaction_id,
                "mcp_plan_id": mcp_plan_id,
                "mcp_plan_digest": mcp_plan_digest,
                "project_identity_digest": project_identity_digest,
                "target_program_digest": target_program["target_program_digest"],
                "lease_generation": lease_generation,
                "snapshot_id": snapshot_id,
                "snapshot_digest": snapshot["snapshot_digest"],
                "artifact_visibility": snapshot["artifact_visibility"],
                "backup_id": backup_id,
                "baseline": baseline,
                "plan": plan,
                "application": application,
                "apply": applied,
            }
        )

    async def verify(
        self,
        transaction: ChangeTransaction,
        apply_result: ChangeApplyResult,
    ) -> ChangeVerificationResult:
        evidence = apply_result.evidence
        try:
            mcp_transaction_id = _required_text(
                evidence, "mcp_transaction_id", "MNE apply evidence"
            )
            mcp_plan_id = _required_text(evidence, "mcp_plan_id", "MNE apply evidence")
            mcp_plan_digest = _sha256(
                evidence.get("mcp_plan_digest"), "MNE apply evidence plan digest"
            )
            lease_generation = _required_text(evidence, "lease_generation", "MNE apply evidence")
            snapshot_id = _required_text(evidence, "snapshot_id", "MNE apply evidence")
            snapshot_digest = _sha256(
                evidence.get("snapshot_digest"), "MNE apply evidence snapshot digest"
            )
            artifact_visibility = _required_text(
                evidence, "artifact_visibility", "MNE apply evidence"
            )
            backup_id = _required_text(evidence, "backup_id", "MNE apply evidence")
            target_program = _typed_target_program(transaction)
            baseline = _object(evidence.get("baseline"), "MNE apply baseline")
            plan = _object(evidence.get("plan"), "MNE apply plan")
            application = _object(evidence.get("application"), "MNE apply application")
            if (
                plan.get("plan_id") != mcp_plan_id
                or plan.get("plan_digest") != mcp_plan_digest
                or evidence.get("target_program_digest") != target_program["target_program_digest"]
                or artifact_visibility != "private_digest_only"
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE apply evidence differs from the immutable Host plan"
                )
        except (ChangeExecutionPreconditionError, ValueError, TypeError) as exc:
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={"verification_bootstrap_error_kind": _public_failure_kind(exc)},
                error_code="mne_verification_identity_unavailable",
                error_detail="The full-program apply evidence cannot be verified safely.",
            )

        verification_evidence = _verification_evidence(transaction)
        verification_scope = transaction.plan["verification_scope"]
        xgsim_scope = verification_scope == MNE_XGSIM_VERIFICATION_SCOPE
        try:
            verified = _object(
                await self._gateway.call_change_tool(
                    "program.verify_mne_replacement",
                    {
                        **self._session_scope(transaction),
                        "transaction_id": mcp_transaction_id,
                        "plan_id": mcp_plan_id,
                        "verification_scope": verification_scope,
                    },
                ),
                "program.verify_mne_replacement",
            )
            self._validate_outer(
                verified,
                operation="program.verify_mne_replacement",
                expected_state="SIM_STOPPED" if xgsim_scope else "COMMITTED",
                expected_transaction_id=mcp_transaction_id,
                required_keys={"verification", "project_hash"},
                optional_keys=(
                    {"project_hash_transition", "simulation_receipt"}
                    if xgsim_scope
                    else {"project_hash_transition"}
                ),
            )
            verification = _validate_verification_receipt(
                verified.get("verification"),
                transaction=transaction,
                plan=plan,
                application=application,
            )
            if verified.get("project_hash") != verification.get("project_sha256_after"):
                raise ChangeExecutionPreconditionError(
                    "MNE verify outer project hash differs from its signed receipt"
                )
            simulation_receipt: dict[str, Any] | None = None
            expected_simulation_receipt_digest: str | None = None
            if xgsim_scope:
                simulation_receipt = _object(
                    verified.get("simulation_receipt"), "MNE XG-SIM receipt"
                )
                expected_simulation_receipt_digest = _sha256(
                    simulation_receipt.get("receipt_digest"),
                    "MNE XG-SIM receipt digest",
                )
            self._validate_checkpoint(
                transaction=transaction,
                checkpoint=_object(
                    verified.get("transaction_checkpoint"),
                    "MNE verify transaction checkpoint",
                ),
                expected_transaction_id=mcp_transaction_id,
                expected_state="SIM_STOPPED" if xgsim_scope else "COMMITTED",
                expected_plan_id=mcp_plan_id,
                expected_plan_digest=mcp_plan_digest,
                target_program=target_program,
                adapter_binding_digest=None,
                runtime_identity_digest=None,
                expected_before_mne_sha256=str(plan["before_mne_sha256"]),
                expected_target_mne_sha256=str(plan["target_mne"]["payload_sha256"]),
                expected_project_hash=str(verification["project_sha256_after"]),
                expected_lease_generation=lease_generation,
                expected_baseline_digest=str(baseline["baseline_digest"]),
                expected_rollback_ld_sha256=str(plan["rollback_ld_sha256"]),
                expected_snapshot_id=snapshot_id,
                expected_snapshot_digest=snapshot_digest,
                expected_artifact_visibility=artifact_visibility,
                expected_backup_id=backup_id,
                expected_simulation_receipt_digest=(
                    expected_simulation_receipt_digest if xgsim_scope else None
                ),
            )
            verified_checkpoint = _object(
                verified.get("transaction_checkpoint"),
                "MNE verify transaction checkpoint",
            )
            if verified_checkpoint.get("adapter_binding_digest") != plan.get(
                "adapter_binding_digest"
            ) or verified_checkpoint.get("runtime_identity_digest") != plan.get(
                "runtime_identity_digest"
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE verify checkpoint changed its capability or runtime binding"
                )
            if xgsim_scope:
                assert simulation_receipt is not None
                unsigned = {
                    key: value for key, value in simulation_receipt.items()
                    if key != "receipt_digest"
                }
                profile_digest = target_program.get("simulation_profile_digest")
                candidate = _object(transaction.plan.get("candidate"), "MNE candidate")
                profile = _object(
                    candidate.get("simulation_profile"), "MNE XG-SIM profile"
                )
                expected_series = _profile_plc_series(profile)
                reopen = _object(verification.get("reopen"), "MNE static reopen")
                observation = _object(
                    simulation_receipt.get("observation"), "MNE XG-SIM observation"
                )
                phases = observation.get("observations")
                restore = simulation_receipt.get("restore")
                stop = simulation_receipt.get("stop")
                expected_phase_values = _expected_mne_xgsim_observation_phases(profile)
                if (
                    set(simulation_receipt)
                    != {
                        "schema", "transaction_id", "plan_digest", "verification_digest",
                        "project_sha256_after", "program_id", "program_name",
                        "target_mne_sha256", "candidate_digest", "profile_digest",
                        "plc_series", "controller_connected", "plc_write_performed",
                        "reopen", "observation", "stop", "restore",
                        "receipt_digest",
                    }
                    or simulation_receipt.get("schema")
                    != "controlstudio-mne-rung-xgsim-receipt/v1"
                    or simulation_receipt.get("transaction_id") != mcp_transaction_id
                    or simulation_receipt.get("plan_digest") != mcp_plan_digest
                    or simulation_receipt.get("verification_digest")
                    != verification.get("verification_digest")
                    or simulation_receipt.get("profile_digest") != profile_digest
                    or simulation_receipt.get("candidate_digest")
                    != candidate.get("candidate_digest")
                    or simulation_receipt.get("project_sha256_after")
                    != verification.get("project_sha256_after")
                    or simulation_receipt.get("program_id") != plan.get("program_id")
                    or simulation_receipt.get("program_name") != plan.get("program_name")
                    or simulation_receipt.get("target_mne_sha256")
                    != _object(plan.get("target_mne"), "MNE target") .get("payload_sha256")
                    or simulation_receipt.get("plc_series") != expected_series
                    or simulation_receipt.get("controller_connected") is not False
                    or simulation_receipt.get("plc_write_performed") is not False
                    or simulation_receipt.get("reopen")
                    != {"pid": reopen.get("pid"), "hwnd": reopen.get("hwnd")}
                    or set(observation)
                    != {"schema", "profile_digest", "writes", "observations", "output_bytes_written"}
                    or observation.get("schema")
                    != "controlstudio-mne-rung-xgsim-observation/v1"
                    or observation.get("profile_digest") != profile_digest
                    or observation.get("output_bytes_written") is not False
                    or not isinstance(phases, list)
                    or len(phases) != len(expected_phase_values)
                    or any(
                        not isinstance(item, dict)
                        or item.get("phase") != expected_name
                        or item.get("expected") != expected_value
                        or item.get("matched") is not True
                        or not isinstance(item.get("outputs"), dict)
                        or not item["outputs"]
                        or (
                            item["outputs"] != expected_value
                            if isinstance(expected_value, dict)
                            else any(
                                value != expected_value
                                for value in item["outputs"].values()
                            )
                        )
                        for item, (expected_name, expected_value) in zip(
                            phases, expected_phase_values, strict=True
                        )
                    )
                    or not isinstance(stop, dict)
                    or stop.get("ok") is not True
                    or stop.get("verified") is not True
                    or not isinstance(restore, list)
                    or not restore
                    or any(
                        not isinstance(item, dict)
                        or item.get("matched") is not True
                        or item.get("readback") != item.get("target")
                        for item in restore
                    )
                    or simulation_receipt.get("receipt_digest") != _digest(unsigned)
                ):
                    raise ChangeExecutionPreconditionError(
                        "MNE XG-SIM receipt is not exactly bound to this plan"
                    )
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as verification_error:
            if xgsim_scope:
                return ChangeVerificationResult(
                    state=ChangeTransactionState.QUARANTINED,
                    evidence={
                        **verification_evidence,
                        "verification_error_kind": _public_failure_kind(verification_error),
                    },
                    error_code="mne_xgsim_verification_outcome_uncertain",
                    error_detail="XG-SIM verification did not prove a safe final outcome.",
                )
            try:
                rollback_outer = await self._rollback_current_transaction(
                    transaction=transaction,
                    mcp_transaction_id=mcp_transaction_id,
                    mcp_plan_id=mcp_plan_id,
                    mcp_plan_digest=mcp_plan_digest,
                    target_program=target_program,
                    plan=plan,
                    baseline=baseline,
                    snapshot_id=snapshot_id,
                    snapshot_digest=snapshot_digest,
                    artifact_visibility=artifact_visibility,
                    backup_id=backup_id,
                )
                rollback = _validate_rollback_receipt(
                    rollback_outer.get("rollback"),
                    transaction=transaction,
                    plan=plan,
                    baseline=baseline,
                )
            except (
                MCPToolError,
                ChangeExecutionPreconditionError,
                ValueError,
                TypeError,
            ) as rollback_error:
                return ChangeVerificationResult(
                    state=ChangeTransactionState.QUARANTINED,
                    evidence={
                        **verification_evidence,
                        "verification_error_kind": _public_failure_kind(
                            verification_error
                        ),
                        "rollback_error_kind": _public_failure_kind(rollback_error),
                    },
                    error_code="mne_rollback_outcome_uncertain",
                    error_detail=(
                        "Full-program verification failed and exact rollback could not be proven."
                    ),
                )
            return ChangeVerificationResult(
                state=ChangeTransactionState.ROLLED_BACK,
                evidence={
                    **verification_evidence,
                    "verification_error_kind": _public_failure_kind(verification_error),
                    "rollback": rollback,
                    "rollback_operation": rollback_outer,
                },
                error_code="mne_verification_failed_rolled_back",
                error_detail=(
                    "Full-program verification failed; the exact raw MNE and LD "
                    "baseline was checked, saved, and independently reopened."
                ),
            )

        return ChangeVerificationResult(
            state=(
                ChangeTransactionState.OBSERVING
                if xgsim_scope
                else ChangeTransactionState.COMPLETED
            ),
            evidence={
                **verification_evidence,
                "verification": verification,
                "verify_operation": verified,
                **(
                    {"simulation_receipt": simulation_receipt}
                    if xgsim_scope and simulation_receipt is not None
                    else {}
                ),
                "completion_claim": (
                    "offline_project_saved_reopened_xgsim_observing"
                    if xgsim_scope
                    else "offline_project_saved_reopened_with_full_program_verification"
                ),
            },
        )

    async def finalize_observing(
        self,
        transaction: ChangeTransaction,
        *,
        decision: str,
        expected_plan_digest: str,
        expected_simulation_receipt_digest: str,
    ) -> ChangeVerificationResult:
        """Resolve the one retained MNE XG-SIM decision without replaying it."""

        if decision not in {"accept", "revert"}:
            raise ChangeExecutionPreconditionError("simulation decision is invalid")
        if transaction.state is not ChangeTransactionState.OBSERVING:
            raise ChangeExecutionPreconditionError("change is not awaiting a simulation decision")
        if transaction.plan_digest != expected_plan_digest:
            raise ChangeExecutionPreconditionError("simulation decision plan digest differs")
        if transaction.plan.get("verification_scope") != MNE_XGSIM_VERIFICATION_SCOPE:
            raise ChangeExecutionPreconditionError("change is not an XG-SIM observing contract")
        verification_evidence = _object(transaction.result.get("verification"), "stored MNE verification")
        receipt = _object(verification_evidence.get("simulation_receipt"), "stored MNE XG-SIM receipt")
        if receipt.get("receipt_digest") != expected_simulation_receipt_digest:
            raise ChangeExecutionPreconditionError("simulation decision receipt digest differs")
        apply = _object(transaction.result.get("apply"), "stored MNE apply evidence")
        mcp_transaction_id = _required_text(apply, "mcp_transaction_id", "stored MNE apply evidence")
        mcp_plan_id = _required_text(apply, "mcp_plan_id", "stored MNE apply evidence")
        mcp_plan_digest = _sha256(apply.get("mcp_plan_digest"), "stored MNE plan digest")
        lease_generation = _required_text(
            apply, "lease_generation", "stored MNE apply evidence"
        )
        target_program = _typed_target_program(transaction)
        plan = _object(apply.get("plan"), "stored MNE plan")
        baseline = _object(apply.get("baseline"), "stored MNE baseline")
        application = _object(apply.get("application"), "stored MNE application")
        snapshot_id = _required_text(apply, "snapshot_id", "stored MNE apply evidence")
        snapshot_digest = _sha256(apply.get("snapshot_digest"), "stored MNE snapshot digest")
        artifact_visibility = _required_text(apply, "artifact_visibility", "stored MNE apply evidence")
        backup_id = _required_text(apply, "backup_id", "stored MNE apply evidence")
        lookup_checkpoint, _terminal = _transaction_lookup_checkpoint(
            await self._gateway.call_recovery_tool(
                "change.transaction_lookup", {"host_transaction_id": transaction.id}
            ),
            host_transaction_id=transaction.id,
        )
        assert lookup_checkpoint is not None
        current_hash = _sha256(
            lookup_checkpoint.get("current_project_hash"), "MNE observing project hash"
        )

        async def validate_terminal_decision(
            checkpoint: dict[str, Any],
            terminal_receipt: dict[str, Any] | None,
            *,
            state: str,
            project_hash: str,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            if checkpoint.get("lease_status") == "release_pending":
                checkpoint = _terminal_lease_release_checkpoint(
                    await self._gateway.call_recovery_tool(
                        "change.release_terminal_lease",
                        {
                            "host_transaction_id": transaction.id,
                            "transaction_id": mcp_transaction_id,
                            "terminal_state": state,
                            "workspace_id": transaction.target.workspace_id,
                            "project_identity_digest": self._project_identity_digest(
                                transaction.target
                            ),
                            "project_path": transaction.target.project_path,
                            "current_project_hash": project_hash,
                            "lease_generation": lease_generation,
                        },
                    ),
                    host_transaction_id=transaction.id,
                    transaction_id=mcp_transaction_id,
                    terminal_state=state,
                    lease_generation=lease_generation,
                )
            self._validate_checkpoint(
                transaction=transaction,
                checkpoint=checkpoint,
                expected_transaction_id=mcp_transaction_id,
                expected_state=state,
                expected_plan_id=mcp_plan_id,
                expected_plan_digest=mcp_plan_digest,
                target_program=target_program,
                adapter_binding_digest=None,
                runtime_identity_digest=None,
                expected_before_mne_sha256=str(plan["before_mne_sha256"]),
                expected_target_mne_sha256=str(plan["target_mne"]["payload_sha256"]),
                expected_project_hash=project_hash,
                expected_lease_generation=lease_generation,
                expected_baseline_digest=str(baseline["baseline_digest"]),
                expected_rollback_ld_sha256=str(plan["rollback_ld_sha256"]),
                expected_snapshot_id=snapshot_id,
                expected_snapshot_digest=snapshot_digest,
                expected_artifact_visibility=artifact_visibility,
                expected_backup_id=backup_id,
                expected_simulation_receipt_digest=expected_simulation_receipt_digest,
            )
            expected_kind = "verification" if state == "COMMITTED" else "rollback"
            if not isinstance(terminal_receipt, dict) or terminal_receipt.get(
                "kind"
            ) != expected_kind:
                raise ChangeExecutionPreconditionError(
                    "terminal simulation decision receipt is unavailable"
                )
            receipt_value = _object(
                terminal_receipt.get("receipt"),
                "terminal simulation decision receipt",
            )
            if state == "COMMITTED":
                _validate_verification_receipt(
                    receipt_value,
                    transaction=transaction,
                    plan=plan,
                    application=application,
                )
            else:
                _validate_rollback_receipt(
                    receipt_value,
                    transaction=transaction,
                    plan=plan,
                    baseline=baseline,
                )
            return checkpoint, receipt_value

        terminal_state = "COMMITTED" if decision == "accept" else "ROLLED_BACK_SAVED"
        if lookup_checkpoint.get("state") == terminal_state:
            _, terminal_receipt = await validate_terminal_decision(
                lookup_checkpoint,
                _terminal,
                state=terminal_state,
                project_hash=current_hash,
            )
            return ChangeVerificationResult(
                state=(ChangeTransactionState.COMPLETED if decision == "accept" else ChangeTransactionState.ROLLED_BACK),
                evidence={"simulation_decision": decision, "response_loss_recovered": True, "terminal_receipt": terminal_receipt},
            )
        if _terminal is not None:
            raise ChangeExecutionPreconditionError(
                "nonterminal simulation decision unexpectedly returned a terminal receipt"
            )
        self._validate_checkpoint(
            transaction=transaction,
            checkpoint=lookup_checkpoint,
            expected_transaction_id=mcp_transaction_id,
            expected_state="SIM_STOPPED",
            expected_plan_id=mcp_plan_id,
            expected_plan_digest=mcp_plan_digest,
            target_program=target_program,
            adapter_binding_digest=None,
            runtime_identity_digest=None,
            expected_before_mne_sha256=str(plan["before_mne_sha256"]),
            expected_target_mne_sha256=str(plan["target_mne"]["payload_sha256"]),
            expected_project_hash=current_hash,
            expected_lease_generation=lease_generation,
            expected_baseline_digest=str(baseline["baseline_digest"]),
            expected_rollback_ld_sha256=str(plan["rollback_ld_sha256"]),
            expected_snapshot_id=snapshot_id,
            expected_snapshot_digest=snapshot_digest,
            expected_artifact_visibility=artifact_visibility,
            expected_backup_id=backup_id,
            expected_simulation_receipt_digest=expected_simulation_receipt_digest,
        )
        if decision == "revert":
            try:
                rolled_back = await self._rollback_current_transaction(
                    transaction=transaction,
                    mcp_transaction_id=mcp_transaction_id,
                    mcp_plan_id=mcp_plan_id,
                    mcp_plan_digest=mcp_plan_digest,
                    target_program=target_program,
                    plan=plan,
                    baseline=baseline,
                    snapshot_id=snapshot_id,
                    snapshot_digest=snapshot_digest,
                    artifact_visibility=artifact_visibility,
                    backup_id=backup_id,
                    expected_simulation_receipt_digest=(
                        expected_simulation_receipt_digest
                    ),
                )
                rollback = _validate_rollback_receipt(rolled_back.get("rollback"), transaction=transaction, plan=plan, baseline=baseline)
            except (MCPToolError, ChangeExecutionPreconditionError, ValueError, TypeError):
                recovered_checkpoint, terminal_receipt = _transaction_lookup_checkpoint(
                    await self._gateway.call_recovery_tool(
                        "change.transaction_lookup", {"host_transaction_id": transaction.id}
                    ),
                    host_transaction_id=transaction.id,
                )
                if (
                    recovered_checkpoint is None
                    or recovered_checkpoint.get("state") != "ROLLED_BACK_SAVED"
                    or not isinstance(terminal_receipt, dict)
                    or terminal_receipt.get("kind") != "rollback"
                ):
                    raise
                rollback_candidate = _object(
                    terminal_receipt.get("receipt"),
                    "recovered MNE rollback receipt",
                )
                _, rollback = await validate_terminal_decision(
                    recovered_checkpoint,
                    terminal_receipt,
                    state="ROLLED_BACK_SAVED",
                    project_hash=_sha256(
                        rollback_candidate.get("project_sha256_after"),
                        "recovered MNE rollback project hash",
                    ),
                )
                rolled_back = {"response_loss_recovered": True, "transaction_checkpoint": recovered_checkpoint}
            return ChangeVerificationResult(
                state=ChangeTransactionState.ROLLED_BACK,
                evidence={"simulation_decision": "revert", "rollback": rollback, "rollback_operation": rolled_back},
            )
        scope = {
            "session_id": lookup_checkpoint.get("active_session_id"),
            "pid": lookup_checkpoint.get("pid"),
            "hwnd": lookup_checkpoint.get("hwnd"),
            "project_path": transaction.target.project_path,
            "project_hash": current_hash,
        }
        if (
            scope["session_id"] != transaction.target.engineering_session_id
            or scope["pid"] != transaction.target.engineering_process_id
            or scope["hwnd"] != transaction.target.engineering_window_handle
        ):
            raise ChangeExecutionPreconditionError("MNE observing session identity changed")
        response_loss_recovered = False
        try:
            committed = _object(
                await self._gateway.call_change_tool(
                    "change.commit",
                    {
                        **scope,
                        "transaction_id": mcp_transaction_id,
                        "expected_plan_digest": mcp_plan_digest,
                        "expected_simulation_receipt_digest": expected_simulation_receipt_digest,
                    },
                ),
                "change.commit",
            )
            self._validate_outer(
                committed,
                operation="change.commit",
                expected_state="COMMITTED",
                expected_transaction_id=mcp_transaction_id,
                required_keys={"project_hash", "project_unchanged"},
            )
            final_checkpoint = _object(committed.get("transaction_checkpoint"), "MNE commit checkpoint")
            if committed.get("project_hash") != current_hash or committed.get("project_unchanged") is not True:
                raise ChangeExecutionPreconditionError("MNE commit did not preserve the observed project hash")
        except (MCPToolError, ChangeExecutionPreconditionError, ValueError, TypeError):
            recovered_checkpoint, terminal_receipt = _transaction_lookup_checkpoint(
                await self._gateway.call_recovery_tool(
                    "change.transaction_lookup", {"host_transaction_id": transaction.id}
                ),
                host_transaction_id=transaction.id,
            )
            if (
                recovered_checkpoint is None
                or recovered_checkpoint.get("state") != "COMMITTED"
                or recovered_checkpoint.get("current_project_hash") != current_hash
                or not isinstance(terminal_receipt, dict)
                or terminal_receipt.get("kind") != "verification"
            ):
                raise
            final_checkpoint, _ = await validate_terminal_decision(
                recovered_checkpoint,
                terminal_receipt,
                state="COMMITTED",
                project_hash=current_hash,
            )
            committed = {"response_loss_recovered": True}
            response_loss_recovered = True
        self._validate_checkpoint(
            transaction=transaction,
            checkpoint=final_checkpoint,
            expected_transaction_id=mcp_transaction_id,
            expected_state="COMMITTED",
            expected_plan_id=mcp_plan_id,
            expected_plan_digest=mcp_plan_digest,
            target_program=target_program,
            adapter_binding_digest=None,
            runtime_identity_digest=None,
            expected_before_mne_sha256=str(plan["before_mne_sha256"]),
            expected_target_mne_sha256=str(plan["target_mne"]["payload_sha256"]),
            expected_project_hash=current_hash,
            expected_lease_generation=lease_generation,
            expected_baseline_digest=str(baseline["baseline_digest"]),
            expected_rollback_ld_sha256=str(plan["rollback_ld_sha256"]),
            expected_snapshot_id=snapshot_id,
            expected_snapshot_digest=snapshot_digest,
            expected_artifact_visibility=artifact_visibility,
            expected_backup_id=backup_id,
            expected_simulation_receipt_digest=expected_simulation_receipt_digest,
        )
        return ChangeVerificationResult(
            state=ChangeTransactionState.COMPLETED,
            evidence={"simulation_decision": "accept", "commit_operation": committed, "response_loss_recovered": response_loss_recovered},
        )

    async def reconcile(
        self,
        transaction: ChangeTransaction,
        recovery_target: ChangeRecoveryTarget,
    ) -> ChangeVerificationResult:
        try:
            target_program = _typed_target_program(transaction)
            _approved_baseline_binding(transaction)
            _validate_verification_contract(transaction)
            checkpoint, terminal_receipt = _transaction_lookup_checkpoint(
                await self._gateway.call_recovery_tool(
                    "change.transaction_lookup",
                    {"host_transaction_id": transaction.id},
                ),
                host_transaction_id=transaction.id,
            )
            if checkpoint is None:  # pragma: no cover - missing_ok is false
                raise ChangeExecutionPreconditionError(
                    "change.transaction_lookup returned no transaction"
                )
            stored = self._stored_checkpoint(
                transaction,
                recovered_checkpoint=checkpoint,
            )
            mcp_transaction_id = _required_text(
                checkpoint, "transaction_id", "MNE recovery checkpoint"
            )
            lookup_lease_generation = self._validate_recovery_checkpoint_against_stored(
                transaction=transaction,
                checkpoint=checkpoint,
                stored=stored,
                target_program=target_program,
                allow_terminal_release_pending=True,
            )
            if (
                checkpoint.get("recovery_action") == "already_terminal"
                and checkpoint.get("lease_status") == "release_pending"
            ):
                terminal_state = _required_text(
                    checkpoint,
                    "state",
                    "terminal MNE recovery checkpoint",
                )
                current_project_hash = _sha256(
                    checkpoint.get("current_project_hash"),
                    "terminal MNE recovery project hash",
                )
                checkpoint = _terminal_lease_release_checkpoint(
                    await self._gateway.call_recovery_tool(
                        "change.release_terminal_lease",
                        {
                            "host_transaction_id": transaction.id,
                            "transaction_id": mcp_transaction_id,
                            "terminal_state": terminal_state,
                            "workspace_id": transaction.target.workspace_id,
                            "project_identity_digest": (
                                MCPChangeExecutionService._project_identity_digest(
                                    transaction.target
                                )
                            ),
                            "project_path": transaction.target.project_path,
                            "current_project_hash": current_project_hash,
                            "lease_generation": lookup_lease_generation,
                        },
                    ),
                    host_transaction_id=transaction.id,
                    transaction_id=mcp_transaction_id,
                    terminal_state=terminal_state,
                    lease_generation=lookup_lease_generation,
                )
                self._validate_recovery_checkpoint_against_stored(
                    transaction=transaction,
                    checkpoint=checkpoint,
                    stored=stored,
                    target_program=target_program,
                    expected_project_hash=current_project_hash,
                    expected_lease_generation=lookup_lease_generation,
                )
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as exc:
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={"recovery_bootstrap_error_kind": _public_failure_kind(exc)},
                error_code="mne_recovery_identity_unavailable",
                error_detail="The native full-program transaction cannot be identified safely.",
            )

        native_state = str(checkpoint.get("state") or "")
        recovery_action = str(checkpoint.get("recovery_action") or "")
        base_evidence = {
            **_verification_evidence(transaction),
            "lookup_checkpoint": _public_checkpoint_evidence(checkpoint),
        }
        try:
            current_project_hash = MCPChangeExecutionService._hash_project_file(
                transaction.target.project_path
            )
            checkpoint_project_hash = _sha256(
                checkpoint.get("current_project_hash"),
                "MNE recovery checkpoint current project hash",
            )
        except (OSError, ChangeExecutionPreconditionError) as exc:
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={**base_evidence, "project_hash_error_kind": _public_failure_kind(exc)},
                error_code="mne_recovery_project_unavailable",
                error_detail="The recovery working-copy file cannot be verified.",
            )
        if not hmac.compare_digest(current_project_hash, checkpoint_project_hash):
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={**base_evidence, "project_hash_matches_checkpoint": False},
                error_code="mne_recovery_project_hash_changed",
                error_detail="The recovery working-copy differs from the terminal journal.",
            )
        validated_terminal_receipt: dict[str, Any] | None = None
        try:
            if native_state == "COMMITTED":
                if terminal_receipt is None or terminal_receipt.get("kind") != "verification":
                    raise ChangeExecutionPreconditionError(
                        "committed MNE recovery has no durable verification receipt"
                    )
                receipt = _object(
                    terminal_receipt.get("receipt"),
                    "committed MNE terminal verification receipt",
                )
                _baseline, plan, application = _validated_persisted_apply_receipts(
                    transaction,
                    checkpoint,
                )
                validated_terminal_receipt = _validate_verification_receipt(
                    receipt,
                    transaction=transaction,
                    plan=plan,
                    application=application,
                )
                if (
                    validated_terminal_receipt.get("project_sha256_after")
                    != current_project_hash
                ):
                    raise ChangeExecutionPreconditionError(
                        "committed MNE receipt differs from the working-copy hash"
                    )
            elif native_state == "ROLLED_BACK_SAVED":
                if terminal_receipt is None or terminal_receipt.get("kind") != "rollback":
                    raise ChangeExecutionPreconditionError(
                        "rolled-back MNE recovery has no durable rollback receipt"
                    )
                receipt = _object(
                    terminal_receipt.get("receipt"),
                    "rolled-back MNE terminal rollback receipt",
                )
                reopen = _object(
                    receipt.get("reopen"),
                    "terminal MNE rollback reopen receipt",
                )
                if (
                    isinstance(transaction.result.get("apply"), dict)
                    and reopen.get("source_pid")
                    == transaction.target.engineering_process_id
                    and reopen.get("source_hwnd")
                    == transaction.target.engineering_window_handle
                ):
                    baseline, plan, _application = _validated_persisted_apply_receipts(
                        transaction,
                        checkpoint,
                    )
                    validated_terminal_receipt = _validate_rollback_receipt(
                        receipt,
                        transaction=transaction,
                        plan=plan,
                        baseline=baseline,
                    )
                else:
                    validated_terminal_receipt = self._validate_recovered_rollback_receipt(
                        receipt,
                        transaction=transaction,
                        checkpoint=stored,
                        expected_source_pid=int(checkpoint.get("pid") or 0),
                        expected_source_hwnd=int(checkpoint.get("hwnd") or 0),
                    )
                if (
                    validated_terminal_receipt.get("project_sha256_after")
                    != current_project_hash
                ):
                    raise ChangeExecutionPreconditionError(
                        "rolled-back MNE receipt differs from the working-copy hash"
                    )
            elif native_state == "ABORTED_NO_MUTATION":
                if terminal_receipt is not None:
                    raise ChangeExecutionPreconditionError(
                        "no-mutation MNE abort returned an unexpected terminal receipt"
                    )
            elif terminal_receipt is not None:
                raise ChangeExecutionPreconditionError(
                    "nonterminal MNE recovery returned a terminal receipt"
                )
        except (ChangeExecutionPreconditionError, ValueError, TypeError) as exc:
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={
                    **base_evidence,
                    "terminal_receipt_error_kind": _public_failure_kind(exc),
                },
                error_code="mne_terminal_receipt_invalid",
                error_detail="The terminal native journal receipt cannot be verified.",
            )
        if recovery_action == "already_terminal" and native_state == "COMMITTED":
            return ChangeVerificationResult(
                state=ChangeTransactionState.COMPLETED,
                evidence={
                    **base_evidence,
                    "terminal_receipt": validated_terminal_receipt,
                    "completion_claim": ("recovered_committed_full_program_replacement_journal"),
                },
            )
        if recovery_action == "already_terminal" and native_state in {
            "ROLLED_BACK_SAVED",
            "ABORTED_NO_MUTATION",
        }:
            return ChangeVerificationResult(
                state=ChangeTransactionState.ROLLED_BACK,
                evidence={
                    **base_evidence,
                    "terminal_receipt": validated_terminal_receipt,
                    "native_mutation_performed": native_state != "ABORTED_NO_MUTATION",
                },
            )
        try:
            if checkpoint.get("lease_status") == "live":
                recovery_session_id = _required_text(
                    checkpoint,
                    "active_session_id",
                    "live MNE recovery checkpoint",
                )
                if (
                    checkpoint.get("pid") != recovery_target.pid
                    or checkpoint.get("hwnd") != recovery_target.hwnd
                    or checkpoint.get("current_project_hash") != current_project_hash
                ):
                    raise ChangeExecutionPreconditionError(
                        "live MNE recovery target differs from its durable checkpoint"
                    )
                recovery_lease_generation = lookup_lease_generation
                attached = {
                    "mode": "existing_bound_session",
                    "session_id": recovery_session_id,
                    "pid": recovery_target.pid,
                    "hwnd": recovery_target.hwnd,
                    "project_path": transaction.target.project_path,
                    "project_hash": current_project_hash,
                }
            else:
                attach_arguments: dict[str, Any] = {
                    "pid": recovery_target.pid,
                    "hwnd": recovery_target.hwnd,
                    "project_path": transaction.target.project_path,
                    "project_hash": current_project_hash,
                    "workspace_id": transaction.target.workspace_id,
                    "recover_transaction_id": mcp_transaction_id,
                    "access_profile": "analysis",
                }
                if isinstance(checkpoint.get("plan_digest"), str):
                    attach_arguments["recover_expected_plan_digest"] = checkpoint["plan_digest"]
                attached = _object(
                    await self._gateway.call_recovery_tool(
                        "xg5000.attach",
                        attach_arguments,
                    ),
                    "xg5000.attach recovery",
                )
                recovery_session_id, recovery_lease_generation = self._validate_recovery_attach(
                    transaction=transaction,
                    recovery_target=recovery_target,
                    expected_project_hash=current_project_hash,
                    expected_transaction_id=mcp_transaction_id,
                    attached=attached,
                    stored=stored,
                    target_program=target_program,
                )
            status = _object(
                await self._gateway.call_change_tool(
                    "change.transaction_status",
                    {
                        "session_id": recovery_session_id,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "change.transaction_status recovery",
            )
            current_checkpoint = _transaction_status_checkpoint(
                status,
                transaction_id=mcp_transaction_id,
            )
            self._validate_recovery_checkpoint_against_stored(
                transaction=transaction,
                checkpoint=current_checkpoint,
                stored=stored,
                target_program=target_program,
                expected_session_id=recovery_session_id,
                expected_pid=recovery_target.pid,
                expected_hwnd=recovery_target.hwnd,
                expected_project_hash=current_project_hash,
                expected_lease_generation=recovery_lease_generation,
            )
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as exc:
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={
                    **base_evidence,
                    "recovery_attach_or_status_error_kind": _public_failure_kind(exc),
                },
                error_code="mne_native_status_unavailable",
                error_detail=("Recovery-only XG5000 attachment or MNE journal proof failed."),
            )

        recovery_evidence = {
            **base_evidence,
            "recovery_attach": _public_recovery_attach_evidence(attached),
            "status_checkpoint": _public_checkpoint_evidence(current_checkpoint),
        }
        recovery_action = str(current_checkpoint.get("recovery_action") or "")
        native_state = str(current_checkpoint.get("state") or "")
        recovery_scope = {
            "session_id": recovery_session_id,
            "pid": recovery_target.pid,
            "hwnd": recovery_target.hwnd,
            "project_path": transaction.target.project_path,
            "project_hash": current_project_hash,
        }
        if recovery_action == "abort_safe":
            try:
                aborted_checkpoint = _validate_no_mutation_abort(
                    await self._gateway.call_change_tool(
                        "change.abort",
                        {
                            **recovery_scope,
                            "transaction_id": mcp_transaction_id,
                            "expected_transaction_state": native_state,
                        },
                    ),
                    transaction=transaction,
                    transaction_id=mcp_transaction_id,
                    expected_state_before=native_state,
                )
                self._validate_recovery_checkpoint_against_stored(
                    transaction=transaction,
                    checkpoint=aborted_checkpoint,
                    stored=stored,
                    target_program=target_program,
                    expected_session_id=recovery_session_id,
                    expected_pid=recovery_target.pid,
                    expected_hwnd=recovery_target.hwnd,
                    expected_project_hash=current_project_hash,
                    expected_lease_generation=recovery_lease_generation,
                )
            except (
                MCPToolError,
                ChangeExecutionPreconditionError,
                ValueError,
                TypeError,
            ) as exc:
                return ChangeVerificationResult(
                    state=ChangeTransactionState.QUARANTINED,
                    evidence={
                        **recovery_evidence,
                        "abort_error_kind": _public_failure_kind(exc),
                    },
                    error_code="mne_native_abort_unproven",
                    error_detail="The pre-mutation MNE transaction could not be aborted.",
                )
            return ChangeVerificationResult(
                state=ChangeTransactionState.ROLLED_BACK,
                evidence={
                    **recovery_evidence,
                    "abort_checkpoint": _public_checkpoint_evidence(aborted_checkpoint),
                    "native_mutation_performed": False,
                },
                error_code="mne_recovery_aborted_before_mutation",
                error_detail="The interrupted MNE transaction was aborted before mutation.",
            )

        if recovery_action != "rollback_required":
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence=recovery_evidence,
                error_code="manual_mne_recovery_required",
                error_detail=(
                    "The MNE journal checkpoint does not permit automatic abort or rollback."
                ),
            )
        try:
            mcp_plan_id = _required_text(current_checkpoint, "plan_id", "MNE recovery checkpoint")
            mcp_plan_digest = _sha256(
                current_checkpoint.get("plan_digest"), "MNE recovery plan digest"
            )
            rolled_back = _object(
                await self._gateway.call_change_tool(
                    "program.rollback_mne_replacement",
                    {
                        **recovery_scope,
                        "transaction_id": mcp_transaction_id,
                        "plan_id": mcp_plan_id,
                    },
                ),
                "program.rollback_mne_replacement recovery",
            )
            self._validate_outer(
                rolled_back,
                operation="program.rollback_mne_replacement",
                expected_state="ROLLED_BACK_SAVED",
                expected_transaction_id=mcp_transaction_id,
                required_keys={"rollback", "project_hash"},
                optional_keys={"project_hash_transition"},
            )
            rollback = self._validate_recovered_rollback_receipt(
                rolled_back.get("rollback"),
                transaction=transaction,
                checkpoint=stored,
                expected_source_pid=recovery_target.pid,
                expected_source_hwnd=recovery_target.hwnd,
            )
            if rolled_back.get("project_hash") != rollback.get("project_sha256_after"):
                raise ChangeExecutionPreconditionError(
                    "recovered MNE rollback project hash differs from its receipt"
                )
            final_checkpoint = _object(
                rolled_back.get("transaction_checkpoint"),
                "recovered MNE rollback checkpoint",
            )
            self._validate_recovery_checkpoint_against_stored(
                transaction=transaction,
                checkpoint=final_checkpoint,
                stored=stored,
                target_program=target_program,
                expected_session_id=recovery_session_id,
                expected_pid=recovery_target.pid,
                expected_hwnd=recovery_target.hwnd,
                expected_project_hash=str(rollback["project_sha256_after"]),
                expected_lease_generation=recovery_lease_generation,
            )
            if (
                final_checkpoint.get("state") != "ROLLED_BACK_SAVED"
                or final_checkpoint.get("terminal") is not True
                or final_checkpoint.get("recovery_action") != "already_terminal"
                or final_checkpoint.get("plan_id") != mcp_plan_id
                or final_checkpoint.get("plan_digest") != mcp_plan_digest
            ):
                raise ChangeExecutionPreconditionError(
                    "recovered MNE rollback did not reach a terminal journal state"
                )
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as exc:
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={
                    **recovery_evidence,
                    "rollback_error_kind": _public_failure_kind(exc),
                },
                error_code="explicit_mne_recovery_unproven",
                error_detail="Explicit MNE rollback could not be proven.",
            )
        return ChangeVerificationResult(
            state=ChangeTransactionState.ROLLED_BACK,
            evidence={
                **recovery_evidence,
                "rollback": rollback,
                "rollback_checkpoint": _public_checkpoint_evidence(final_checkpoint),
            },
            error_code="explicit_mne_recovery_rolled_back",
            error_detail=(
                "The quarantined MNE transaction was exactly restored, checked, "
                "saved, and independently reopened."
            ),
        )

    @staticmethod
    def _session_scope(transaction: ChangeTransaction) -> dict[str, Any]:
        target = transaction.target
        return {
            "session_id": target.engineering_session_id,
            "pid": target.engineering_process_id,
            "hwnd": target.engineering_window_handle,
            "project_path": target.project_path,
            "project_hash": target.artifact_hash,
        }

    @staticmethod
    def _validate_outer(
        value: dict[str, Any],
        *,
        operation: str,
        expected_state: str,
        expected_transaction_id: str,
        required_keys: set[str],
        optional_keys: set[str] | None = None,
    ) -> None:
        common = {
            "schema_version",
            "session_id",
            "operation",
            "ok",
            "verified",
            "operation_id",
            "transaction_id",
            "transaction_state",
            "transaction_checkpoint",
        }
        optional = optional_keys or set()
        if set(value) not in (common | required_keys, common | required_keys | optional):
            raise ChangeExecutionPreconditionError(
                f"{operation} returned an unexpected public field set"
            )
        _assert_no_private_payload(value)
        if (
            value.get("schema_version") != MNE_MCP_SCHEMA_VERSION
            or value.get("operation") != operation
            or value.get("ok") is not True
            or value.get("verified") is not True
            or value.get("transaction_id") != expected_transaction_id
            or value.get("transaction_state") != expected_state
            or not isinstance(value.get("session_id"), str)
            or not value.get("session_id")
            or not isinstance(value.get("operation_id"), str)
            or not value.get("operation_id")
        ):
            raise ChangeExecutionPreconditionError(
                f"{operation} did not prove the expected {expected_state} result"
            )
        transition = value.get("project_hash_transition")
        if transition is not None:
            if (
                not isinstance(transition, dict)
                or set(transition) != {"before", "after", "source"}
                or _SHA256.fullmatch(str(transition.get("before") or "")) is None
                or _SHA256.fullmatch(str(transition.get("after") or "")) is None
                or not isinstance(transition.get("source"), str)
                or not transition.get("source")
            ):
                raise ChangeExecutionPreconditionError(
                    f"{operation} returned an invalid project hash transition"
                )

    @staticmethod
    def _validate_checkpoint(
        *,
        transaction: ChangeTransaction,
        checkpoint: dict[str, Any],
        expected_transaction_id: str,
        expected_state: str,
        expected_plan_id: str,
        expected_plan_digest: str,
        target_program: Mapping[str, Any],
        adapter_binding_digest: str | None,
        runtime_identity_digest: str | None,
        expected_before_mne_sha256: str,
        expected_target_mne_sha256: str,
        expected_project_hash: str,
        expected_snapshot_id: str,
        expected_snapshot_digest: str,
        expected_artifact_visibility: str,
        expected_backup_id: str,
        expected_lease_generation: str | None = None,
        expected_baseline_digest: str | None = None,
        expected_rollback_ld_sha256: str | None = None,
        expected_simulation_receipt_digest: str | None = None,
    ) -> None:
        _assert_no_private_payload(checkpoint)
        target = transaction.target
        if checkpoint.get("schema") != "controlstudio-change-transaction-checkpoint/v1":
            raise ChangeExecutionPreconditionError("MNE journal checkpoint schema is unsupported")
        expected = {
            "host_transaction_id": transaction.id,
            "transaction_id": expected_transaction_id,
            "plan_id": expected_plan_id,
            "plan_digest": expected_plan_digest,
            "plan_kind": MNE_PLAN_KIND,
            "workspace_id": target.workspace_id,
            "project_identity_digest": MCPChangeExecutionService._project_identity_digest(target),
            "active_session_id": target.engineering_session_id,
            "pid": target.engineering_process_id,
            "hwnd": target.engineering_window_handle,
            "current_project_hash": expected_project_hash,
            "state": expected_state,
            "program_id": target_program["program_id"],
            "program_name": target_program["program_name"],
            "target_program_digest": target_program["target_program_digest"],
            "before_mne_sha256": expected_before_mne_sha256,
            "target_mne_sha256": expected_target_mne_sha256,
            "snapshot_id": expected_snapshot_id,
            "snapshot_digest": expected_snapshot_digest,
            "artifact_visibility": expected_artifact_visibility,
            "backup_id": expected_backup_id,
        }
        if adapter_binding_digest is not None or runtime_identity_digest is not None:
            if adapter_binding_digest is None or runtime_identity_digest is None:
                raise ChangeExecutionPreconditionError(
                    "MNE adapter and runtime digest must be supplied together"
                )
            expected.update(
                {
                    "adapter_binding_digest": adapter_binding_digest,
                    "runtime_identity_digest": runtime_identity_digest,
                }
            )
        if expected_lease_generation is not None:
            expected["lease_generation"] = expected_lease_generation
        if expected_baseline_digest is not None:
            expected["baseline_digest"] = expected_baseline_digest
        if expected_rollback_ld_sha256 is not None:
            expected["rollback_ld_sha256"] = expected_rollback_ld_sha256
        if expected_simulation_receipt_digest is not None:
            expected["simulation_receipt_digest"] = expected_simulation_receipt_digest
        for field, expected_value in expected.items():
            if checkpoint.get(field) != expected_value:
                raise ChangeExecutionPreconditionError(f"MNE journal checkpoint mismatched {field}")
        if not _same_path(checkpoint.get("project_path"), target.project_path):
            raise ChangeExecutionPreconditionError("MNE journal checkpoint mismatched project_path")
        lease_generation = checkpoint.get("lease_generation")
        try:
            canonical_lease_generation = str(uuid.UUID(str(lease_generation)))
        except ValueError as exc:
            raise ChangeExecutionPreconditionError(
                "MNE journal lease generation must be a canonical UUID"
            ) from exc
        if canonical_lease_generation != lease_generation:
            raise ChangeExecutionPreconditionError(
                "MNE journal lease generation must be a canonical UUID"
            )
        for field in (
            "plan_digest",
            "project_identity_digest",
            "current_project_hash",
            "target_program_digest",
            "before_mne_sha256",
            "target_mne_sha256",
            "adapter_binding_digest",
            "runtime_identity_digest",
            "baseline_digest",
            "rollback_ld_sha256",
            "simulation_receipt_digest",
        ):
            if checkpoint.get(field) is not None:
                _sha256(checkpoint.get(field), f"MNE checkpoint {field}")
        if expected_state == "PLANNED":
            disposition = (False, "abort_safe", False, "live")
        elif expected_state in {"APPLYING", "APPLIED", "SIM_STOPPED", "QUARANTINED"}:
            disposition = (True, "rollback_required", False, "live")
        elif expected_state in {"COMMITTED", "ROLLED_BACK_SAVED"}:
            disposition = (True, "already_terminal", True, "released")
        elif expected_state == "ABORTED_NO_MUTATION":
            disposition = (False, "already_terminal", True, "released")
        else:
            raise ChangeExecutionPreconditionError(
                "MNE journal checkpoint has an unsupported state disposition"
            )
        expected_mutation, expected_action, expected_terminal, expected_lease = disposition
        if (
            checkpoint.get("mutation_possible") is not expected_mutation
            or checkpoint.get("recovery_action") != expected_action
            or checkpoint.get("terminal") is not expected_terminal
            or checkpoint.get("lease_status") != expected_lease
        ):
            raise ChangeExecutionPreconditionError(
                "MNE journal checkpoint has an unsafe recovery or lease disposition"
            )
        if expected_state in {"COMMITTED", "ROLLED_BACK_SAVED", "ABORTED_NO_MUTATION"}:
            if (
                checkpoint.get("terminal") is not True
                or checkpoint.get("recovery_action") != "already_terminal"
                or checkpoint.get("lease_status") != "released"
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE terminal checkpoint did not prove durable lease release"
                )

    def _authorization(
        self,
        *,
        transaction: ChangeTransaction,
        mcp_transaction_id: str,
        mcp_plan_id: str,
        mcp_plan_digest: str,
        project_identity_digest: str,
        mutation_arguments: dict[str, Any],
    ) -> dict[str, Any]:
        approval = transaction.approval
        if approval is None:
            raise ChangeExecutionPreconditionError(
                "a durable one-time approval is required for MNE replacement"
            )
        target = transaction.target
        issued_at = datetime.now(timezone.utc)
        target_identity_digest = MCPChangeExecutionService._mcp_target_identity_digest(
            session_id=target.engineering_session_id,
            pid=target.engineering_process_id,
            hwnd=target.engineering_window_handle,
            project_path=target.project_path,
            project_hash=target.artifact_hash,
            workspace_id=target.workspace_id,
            project_identity_digest=project_identity_digest,
        )
        arguments_digest = self._mutation_arguments_digest(
            arguments={
                **mutation_arguments,
                "workspace_id": target.workspace_id,
                "project_identity_digest": project_identity_digest,
            }
        )
        return self._gateway.sign_mutation_authorization(
            {
                "schema": "controlstudio-change-authorization/v1",
                "authorization_id": str(uuid.uuid4()),
                "actor_id": approval.actor_id,
                "transaction_id": mcp_transaction_id,
                "session_id": target.engineering_session_id,
                "workspace_id": target.workspace_id,
                "project_identity_digest": project_identity_digest,
                "target_identity_digest": target_identity_digest,
                "plan_id": mcp_plan_id,
                "plan_digest": mcp_plan_digest,
                "capability": "offline_mutate",
                "allowed_operation": "program.apply_mne_replacement",
                "arguments_digest": arguments_digest,
                "issued_at": issued_at.isoformat(timespec="milliseconds"),
                "expires_at": (issued_at + timedelta(minutes=2)).isoformat(timespec="milliseconds"),
                "nonce": str(uuid.uuid4()),
            }
        )

    @staticmethod
    def _mutation_arguments_digest(*, arguments: Mapping[str, Any]) -> str:
        target_identity_digest = MCPChangeExecutionService._mcp_target_identity_digest(
            session_id=arguments.get("session_id"),
            pid=arguments.get("pid"),
            hwnd=arguments.get("hwnd"),
            project_path=str(arguments.get("project_path") or ""),
            project_hash=str(arguments.get("project_hash") or ""),
            workspace_id=str(arguments.get("workspace_id") or ""),
            project_identity_digest=str(arguments.get("project_identity_digest") or ""),
        )
        lease_generation = str(arguments.get("lease_generation") or "")
        try:
            canonical_lease_generation = str(uuid.UUID(lease_generation))
        except ValueError as exc:
            raise ChangeExecutionPreconditionError(
                "MNE mutation lease generation must be a canonical UUID"
            ) from exc
        if canonical_lease_generation != lease_generation:
            raise ChangeExecutionPreconditionError(
                "MNE mutation lease generation must be a canonical UUID"
            )
        payload = {
            "schema": "controlstudio-mutation-arguments/v1",
            "operation": "program.apply_mne_replacement",
            "transaction_id": _required_text(arguments, "transaction_id", "MNE mutation arguments"),
            "plan_id": _required_text(arguments, "plan_id", "MNE mutation arguments"),
            "plan_digest": _sha256(arguments.get("plan_digest"), "MNE mutation plan digest"),
            "target_identity_digest": target_identity_digest,
            "lease_generation": lease_generation,
            "target_program_digest": _sha256(
                arguments.get("target_program_digest"),
                "MNE mutation target program digest",
            ),
            "before_mne_sha256": _sha256(
                arguments.get("before_mne_sha256"),
                "MNE mutation before hash",
            ),
            "target_mne_sha256": _sha256(
                arguments.get("target_mne_sha256"),
                "MNE mutation target hash",
            ),
            "adapter_binding_digest": _sha256(
                arguments.get("adapter_binding_digest"),
                "MNE mutation adapter digest",
            ),
            "runtime_identity_digest": _sha256(
                arguments.get("runtime_identity_digest"),
                "MNE mutation runtime digest",
            ),
            "program_id": _required_text(arguments, "program_id", "MNE mutation arguments"),
            "program_name": _required_text(arguments, "program_name", "MNE mutation arguments"),
        }
        return _digest(payload)

    def _persist_checkpoint(
        self,
        transaction: ChangeTransaction,
        checkpoint: dict[str, Any],
    ) -> None:
        if self._checkpoint_callback is None:
            return
        self._checkpoint_callback(
            transaction.id,
            {
                "host_transaction_id": transaction.id,
                "host_plan_digest": transaction.plan_digest,
                **checkpoint,
            },
        )

    async def _persist_checkpoint_or_abort(
        self,
        transaction: ChangeTransaction,
        checkpoint: dict[str, Any],
        *,
        session_scope: dict[str, Any],
        mcp_transaction_id: str,
        expected_mcp_state: str,
    ) -> None:
        try:
            self._persist_checkpoint(transaction, checkpoint)
            return
        except Exception as checkpoint_error:
            try:
                _validate_no_mutation_abort(
                    await self._gateway.call_change_tool(
                        "change.abort",
                        {
                            **session_scope,
                            "transaction_id": mcp_transaction_id,
                            "expected_transaction_state": expected_mcp_state,
                        },
                    ),
                    transaction=transaction,
                    transaction_id=mcp_transaction_id,
                    expected_state_before=expected_mcp_state,
                )
            except (
                MCPToolError,
                ChangeExecutionPreconditionError,
                ValueError,
                TypeError,
            ) as abort_error:
                raise ChangeExecutionOutcomeUncertain(
                    "Host MNE checkpoint persistence failed and MCP abort was not proven",
                    evidence={
                        "mcp_transaction_id": mcp_transaction_id,
                        "checkpoint_stage": checkpoint.get("stage"),
                        "checkpoint_error_kind": _public_failure_kind(checkpoint_error),
                        "abort_error_kind": _public_failure_kind(abort_error),
                    },
                ) from checkpoint_error
            raise ChangeExecutionFailedSafely(
                "Host MNE checkpoint persistence failed; MCP proved that no native "
                "mutation occurred and released its lease"
            ) from checkpoint_error

    async def _abort_preparation_transaction(
        self,
        *,
        transaction: ChangeTransaction,
        session_scope: dict[str, Any],
        expected_transaction_id: str | None,
    ) -> None:
        checkpoint, _terminal_receipt = _transaction_lookup_checkpoint(
            await self._gateway.call_recovery_tool(
                "change.transaction_lookup",
                {
                    "host_transaction_id": transaction.id,
                    "missing_ok": True,
                },
            ),
            host_transaction_id=transaction.id,
            missing_ok=True,
        )
        if _terminal_receipt is not None:
            raise ChangeExecutionPreconditionError(
                "MNE preparation cleanup found an unexpected terminal receipt"
            )
        if checkpoint is None:
            if expected_transaction_id is not None:
                raise ChangeExecutionPreconditionError(
                    "MNE preparation journal disappeared after returning an identity"
                )
            return
        mcp_transaction_id = _required_text(
            checkpoint,
            "transaction_id",
            "MNE preparation recovery checkpoint",
        )
        if (
            (expected_transaction_id is not None and mcp_transaction_id != expected_transaction_id)
            or checkpoint.get("host_transaction_id") != transaction.id
            or checkpoint.get("workspace_id") != transaction.target.workspace_id
            or checkpoint.get("plan_kind") != MNE_PLAN_KIND
            or checkpoint.get("project_identity_digest")
            != MCPChangeExecutionService._project_identity_digest(transaction.target)
            or not _same_path(checkpoint.get("project_path"), transaction.target.project_path)
        ):
            raise ChangeExecutionPreconditionError(
                "MNE preparation recovery lookup mismatched the Host transaction"
            )
        state = _required_text(
            checkpoint,
            "state",
            "MNE preparation recovery checkpoint",
        )
        if state == "ABORTED_NO_MUTATION":
            if (
                checkpoint.get("recovery_action") != "already_terminal"
                or checkpoint.get("mutation_possible") is not False
                or checkpoint.get("terminal") is not True
                or checkpoint.get("lease_status") != "released"
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE preparation journal has an invalid terminal abort proof"
                )
            return
        if (
            checkpoint.get("recovery_action") != "abort_safe"
            or checkpoint.get("mutation_possible") is not False
            or checkpoint.get("terminal") is not False
            or state not in {"SNAPSHOT_CREATED", "BACKUP_CREATED", "PLANNED"}
        ):
            raise ChangeExecutionPreconditionError(
                "MNE preparation journal does not prove an abort-safe state"
            )
        _validate_no_mutation_abort(
            await self._gateway.call_change_tool(
                "change.abort",
                {
                    **session_scope,
                    "transaction_id": mcp_transaction_id,
                    "expected_transaction_state": state,
                },
            ),
            transaction=transaction,
            transaction_id=mcp_transaction_id,
            expected_state_before=state,
        )

    @staticmethod
    def _stored_checkpoint(
        transaction: ChangeTransaction,
        *,
        recovered_checkpoint: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        checkpoint = transaction.result.get("native_checkpoint")
        if not isinstance(checkpoint, dict):
            recovered = dict(recovered_checkpoint or {})
            state = str(recovered.get("state") or "")
            abort_safe = (
                recovered.get("recovery_action") == "abort_safe"
                and recovered.get("mutation_possible") is False
                and recovered.get("terminal") is False
                and state in {"SNAPSHOT_CREATED", "BACKUP_CREATED", "PLANNED"}
            )
            terminal_no_mutation = (
                state == "ABORTED_NO_MUTATION"
                and recovered.get("recovery_action") == "already_terminal"
                and recovered.get("mutation_possible") is False
                and recovered.get("terminal") is True
                and recovered.get("lease_status") == "released"
            )
            if (
                recovered.get("host_transaction_id") != transaction.id
                or recovered.get("plan_kind") != MNE_PLAN_KIND
                or not (abort_safe or terminal_no_mutation)
            ):
                raise ChangeExecutionPreconditionError(
                    "the Host has no durable native checkpoint for MNE recovery"
                )
            checkpoint = {
                "stage": state,
                "mcp_transaction_id": recovered.get("transaction_id"),
                "snapshot_id": recovered.get("snapshot_id"),
                "snapshot_digest": recovered.get("snapshot_digest"),
                "artifact_visibility": recovered.get("artifact_visibility"),
                "backup_id": recovered.get("backup_id"),
                "mcp_plan_id": recovered.get("plan_id"),
                "mcp_plan_digest": recovered.get("plan_digest"),
                "session_id": transaction.target.engineering_session_id,
                "workspace_id": transaction.target.workspace_id,
                "project_path": transaction.target.project_path,
                "project_identity_digest": MCPChangeExecutionService._project_identity_digest(
                    transaction.target
                ),
                "plan_kind": MNE_PLAN_KIND,
                "program_id": recovered.get("program_id"),
                "program_name": recovered.get("program_name"),
                "target_program_digest": recovered.get("target_program_digest"),
                "before_mne_sha256": recovered.get("before_mne_sha256"),
                "target_mne_sha256": recovered.get("target_mne_sha256"),
                "adapter_binding_digest": recovered.get("adapter_binding_digest"),
                "runtime_identity_digest": recovered.get("runtime_identity_digest"),
                "baseline_digest": recovered.get("baseline_digest"),
                "rollback_ld_sha256": recovered.get("rollback_ld_sha256"),
                "baseline_ld_canonical_sha256": recovered.get(
                    "baseline_ld_canonical_sha256"
                ),
                "lease_generation": recovered.get("lease_generation"),
            }
        required = {
            "mcp_transaction_id",
            "session_id",
            "workspace_id",
            "project_path",
            "project_identity_digest",
            "plan_kind",
        }
        if any(not checkpoint.get(field) for field in required):
            raise ChangeExecutionPreconditionError("the Host MNE recovery checkpoint is incomplete")
        return checkpoint

    @staticmethod
    def _validate_recovery_checkpoint_against_stored(
        *,
        transaction: ChangeTransaction,
        checkpoint: dict[str, Any],
        stored: dict[str, Any],
        target_program: Mapping[str, Any],
        expected_session_id: str | None = None,
        expected_pid: int | None = None,
        expected_hwnd: int | None = None,
        expected_project_hash: str | None = None,
        expected_lease_generation: str | None = None,
        allow_lease_rotation: bool = False,
        allow_terminal_release_pending: bool = False,
    ) -> str:
        _assert_no_private_payload(checkpoint)
        target = transaction.target
        if checkpoint.get("schema") != "controlstudio-change-transaction-checkpoint/v1":
            raise ChangeExecutionPreconditionError("MNE recovery checkpoint schema is unsupported")
        exact = {
            "host_transaction_id": transaction.id,
            "transaction_id": stored.get("mcp_transaction_id"),
            "snapshot_id": stored.get("snapshot_id"),
            "snapshot_digest": stored.get("snapshot_digest"),
            "artifact_visibility": stored.get("artifact_visibility"),
            "backup_id": stored.get("backup_id"),
            "plan_kind": MNE_PLAN_KIND,
            "workspace_id": target.workspace_id,
            "project_identity_digest": MCPChangeExecutionService._project_identity_digest(target),
        }
        if stored.get("stage") == "PLANNED":
            exact.update(
                {
                    "plan_id": stored.get("mcp_plan_id"),
                    "plan_digest": stored.get("mcp_plan_digest"),
                    "program_id": target_program["program_id"],
                    "program_name": target_program["program_name"],
                    "target_program_digest": target_program["target_program_digest"],
                    "before_mne_sha256": stored.get("before_mne_sha256"),
                    "target_mne_sha256": stored.get("target_mne_sha256"),
                    "adapter_binding_digest": stored.get("adapter_binding_digest"),
                    "runtime_identity_digest": stored.get("runtime_identity_digest"),
                    "baseline_digest": stored.get("baseline_digest"),
                    "rollback_ld_sha256": stored.get("rollback_ld_sha256"),
                }
            )
        if expected_session_id is not None:
            exact["active_session_id"] = expected_session_id
        if expected_pid is not None:
            exact["pid"] = expected_pid
        if expected_hwnd is not None:
            exact["hwnd"] = expected_hwnd
        if expected_project_hash is not None:
            exact["current_project_hash"] = expected_project_hash
        for field, expected in exact.items():
            if expected is not None and checkpoint.get(field) != expected:
                raise ChangeExecutionPreconditionError(
                    f"Host and MCP MNE recovery checkpoints disagree on {field}"
                )
        if not _same_path(checkpoint.get("project_path"), target.project_path):
            raise ChangeExecutionPreconditionError(
                "MNE recovery checkpoint mismatched project_path"
            )
        lease_generation = _required_text(
            checkpoint,
            "lease_generation",
            "MNE recovery checkpoint",
        )
        try:
            canonical_lease_generation = str(uuid.UUID(lease_generation))
        except ValueError as exc:
            raise ChangeExecutionPreconditionError(
                "MNE recovery lease generation must be a canonical UUID"
            ) from exc
        if canonical_lease_generation != lease_generation:
            raise ChangeExecutionPreconditionError(
                "MNE recovery lease generation must be a canonical UUID"
            )
        if expected_lease_generation is not None:
            if lease_generation != expected_lease_generation:
                raise ChangeExecutionPreconditionError(
                    "MNE recovery lease generation changed during recovery"
                )
        elif not allow_lease_rotation and stored.get("lease_generation") is not None:
            if lease_generation != stored.get("lease_generation"):
                raise ChangeExecutionPreconditionError(
                    "MNE recovery checkpoint changed its durable lease generation"
                )
        state = checkpoint.get("state")
        action = checkpoint.get("recovery_action")
        if not isinstance(state, str) or not state:
            raise ChangeExecutionPreconditionError("MNE recovery checkpoint has no state")
        if action not in {
            "abort_safe",
            "rollback_required",
            "already_terminal",
            "manual_quarantine",
        }:
            raise ChangeExecutionPreconditionError(
                "MNE recovery checkpoint has an invalid recovery action"
            )
        mutation_possible = checkpoint.get("mutation_possible")
        lease_status = checkpoint.get("lease_status")
        if state in {"COMMITTED", "ROLLED_BACK_SAVED", "ABORTED_NO_MUTATION"}:
            expected_mutation = state != "ABORTED_NO_MUTATION"
            if (
                action != "already_terminal"
                or mutation_possible is not expected_mutation
                or checkpoint.get("terminal") is not True
                or lease_status
                not in (
                    {"released", "release_pending"}
                    if allow_terminal_release_pending
                    else {"released"}
                )
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE terminal recovery checkpoint has an unsafe disposition"
                )
        elif state in {"SNAPSHOT_CREATED", "BACKUP_CREATED", "PLANNED"}:
            if (
                action != "abort_safe"
                or mutation_possible is not False
                or checkpoint.get("terminal") is not False
                or lease_status not in {"live", "abandoned"}
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE abort-safe recovery checkpoint has an unsafe disposition"
                )
        elif state in {"APPLYING", "APPLIED"}:
            if (
                action != "rollback_required"
                or mutation_possible is not True
                or checkpoint.get("terminal") is not False
                or lease_status not in {"live", "abandoned"}
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE rollback recovery checkpoint has an unsafe disposition"
                )
        elif state == "QUARANTINED":
            if (
                checkpoint.get("terminal") is not False
                or lease_status not in {"live", "abandoned"}
                or (
                    action == "rollback_required"
                    and mutation_possible is not True
                )
                or (
                    action == "manual_quarantine"
                    and not isinstance(mutation_possible, bool)
                )
                or action not in {"rollback_required", "manual_quarantine"}
            ):
                raise ChangeExecutionPreconditionError(
                    "MNE quarantined recovery checkpoint has an unsafe disposition"
                )
        else:
            raise ChangeExecutionPreconditionError(
                "MNE recovery checkpoint state has no supported disposition"
            )
        if action == "rollback_required" and (
            not checkpoint.get("plan_id")
            or not checkpoint.get("plan_digest")
            or checkpoint.get("plan_kind") != MNE_PLAN_KIND
        ):
            raise ChangeExecutionPreconditionError(
                "MNE rollback checkpoint has no immutable full-program plan"
            )
        return lease_generation

    @classmethod
    def _validate_recovery_attach(
        cls,
        *,
        transaction: ChangeTransaction,
        recovery_target: ChangeRecoveryTarget,
        expected_project_hash: str,
        expected_transaction_id: str,
        attached: dict[str, Any],
        stored: dict[str, Any],
        target_program: Mapping[str, Any],
    ) -> tuple[str, str]:
        session_id = _required_text(attached, "session_id", "MNE recovery attach")
        if (
            attached.get("pid") != recovery_target.pid
            or attached.get("hwnd") != recovery_target.hwnd
            or attached.get("project_hash") != expected_project_hash
            or not _same_path(attached.get("project_path"), transaction.target.project_path)
        ):
            raise ChangeExecutionPreconditionError(
                "MNE recovery attach mismatched the selected project target"
            )
        checkpoint = _object(attached.get("recovery"), "MNE recovery attach checkpoint")
        recovery_action = checkpoint.get("recovery_action")
        expected_policy = {
            "abort_safe": "offline_abort_recovery_only",
            "rollback_required": "offline_rollback_recovery_only",
        }.get(recovery_action)
        if expected_policy is None or attached.get("plc_policy") != expected_policy:
            raise ChangeExecutionPreconditionError(
                "MNE recovery attach returned a mismatched recovery policy"
            )
        recovery_lease_generation = cls._validate_recovery_checkpoint_against_stored(
            transaction=transaction,
            checkpoint=checkpoint,
            stored=stored,
            target_program=target_program,
            expected_session_id=session_id,
            expected_pid=recovery_target.pid,
            expected_hwnd=recovery_target.hwnd,
            expected_project_hash=expected_project_hash,
            allow_lease_rotation=True,
        )
        if checkpoint.get("transaction_id") != expected_transaction_id:
            raise ChangeExecutionPreconditionError(
                "MNE recovery attach mismatched transaction identity"
            )
        return session_id, recovery_lease_generation

    async def _rollback_current_transaction(
        self,
        *,
        transaction: ChangeTransaction,
        mcp_transaction_id: str,
        mcp_plan_id: str,
        mcp_plan_digest: str,
        target_program: Mapping[str, Any],
        plan: Mapping[str, Any],
        baseline: Mapping[str, Any],
        snapshot_id: str,
        snapshot_digest: str,
        artifact_visibility: str,
        backup_id: str,
        expected_simulation_receipt_digest: str | None = None,
    ) -> dict[str, Any]:
        status = _object(
            await self._gateway.call_change_tool(
                "change.transaction_status",
                {
                    "session_id": transaction.target.engineering_session_id,
                    "transaction_id": mcp_transaction_id,
                },
            ),
            "change.transaction_status before MNE rollback",
        )
        checkpoint = _transaction_status_checkpoint(
            status,
            transaction_id=mcp_transaction_id,
        )
        current_project_hash = _sha256(
            checkpoint.get("current_project_hash"),
            "MNE rollback current project hash",
        )
        self._validate_checkpoint(
            transaction=transaction,
            checkpoint=checkpoint,
            expected_transaction_id=mcp_transaction_id,
            expected_state=str(checkpoint.get("state") or ""),
            expected_plan_id=mcp_plan_id,
            expected_plan_digest=mcp_plan_digest,
            target_program=target_program,
            adapter_binding_digest=None,
            runtime_identity_digest=None,
            expected_before_mne_sha256=str(plan["before_mne_sha256"]),
            expected_target_mne_sha256=str(plan["target_mne"]["payload_sha256"]),
            expected_project_hash=current_project_hash,
            expected_lease_generation=str(checkpoint.get("lease_generation") or ""),
            expected_baseline_digest=str(baseline["baseline_digest"]),
            expected_rollback_ld_sha256=str(plan["rollback_ld_sha256"]),
            expected_snapshot_id=snapshot_id,
            expected_snapshot_digest=snapshot_digest,
            expected_artifact_visibility=artifact_visibility,
            expected_backup_id=backup_id,
            expected_simulation_receipt_digest=expected_simulation_receipt_digest,
        )
        if (
            checkpoint.get("adapter_binding_digest") != plan.get("adapter_binding_digest")
            or checkpoint.get("runtime_identity_digest") != plan.get("runtime_identity_digest")
            or checkpoint.get("recovery_action") != "rollback_required"
            or checkpoint.get("state") not in {"APPLYING", "APPLIED", "SIM_STOPPED", "QUARANTINED"}
        ):
            raise ChangeExecutionPreconditionError("MNE journal does not authorize exact rollback")
        scope = {
            "session_id": checkpoint.get("active_session_id"),
            "pid": checkpoint.get("pid"),
            "hwnd": checkpoint.get("hwnd"),
            "project_path": transaction.target.project_path,
            "project_hash": current_project_hash,
        }
        if (
            scope["session_id"] != transaction.target.engineering_session_id
            or scope["pid"] != transaction.target.engineering_process_id
            or scope["hwnd"] != transaction.target.engineering_window_handle
        ):
            raise ChangeExecutionPreconditionError(
                "MNE rollback target session changed before explicit recovery"
            )
        rolled_back = _object(
            await self._gateway.call_change_tool(
                "program.rollback_mne_replacement",
                {
                    **scope,
                    "transaction_id": mcp_transaction_id,
                    "plan_id": mcp_plan_id,
                },
            ),
            "program.rollback_mne_replacement",
        )
        self._validate_outer(
            rolled_back,
            operation="program.rollback_mne_replacement",
            expected_state="ROLLED_BACK_SAVED",
            expected_transaction_id=mcp_transaction_id,
            required_keys={"rollback", "project_hash"},
            optional_keys={"project_hash_transition"},
        )
        rollback = _validate_rollback_receipt(
            rolled_back.get("rollback"),
            transaction=transaction,
            plan=plan,
            baseline=baseline,
        )
        if rolled_back.get("project_hash") != rollback.get("project_sha256_after"):
            raise ChangeExecutionPreconditionError(
                "MNE rollback outer project hash differs from its receipt"
            )
        final_checkpoint = _object(
            rolled_back.get("transaction_checkpoint"),
            "MNE rollback terminal checkpoint",
        )
        self._validate_checkpoint(
            transaction=transaction,
            checkpoint=final_checkpoint,
            expected_transaction_id=mcp_transaction_id,
            expected_state="ROLLED_BACK_SAVED",
            expected_plan_id=mcp_plan_id,
            expected_plan_digest=mcp_plan_digest,
            target_program=target_program,
            adapter_binding_digest=None,
            runtime_identity_digest=None,
            expected_before_mne_sha256=str(plan["before_mne_sha256"]),
            expected_target_mne_sha256=str(plan["target_mne"]["payload_sha256"]),
            expected_project_hash=str(rollback["project_sha256_after"]),
            expected_lease_generation=str(checkpoint["lease_generation"]),
            expected_baseline_digest=str(baseline["baseline_digest"]),
            expected_rollback_ld_sha256=str(plan["rollback_ld_sha256"]),
            expected_snapshot_id=snapshot_id,
            expected_snapshot_digest=snapshot_digest,
            expected_artifact_visibility=artifact_visibility,
            expected_backup_id=backup_id,
            expected_simulation_receipt_digest=expected_simulation_receipt_digest,
        )
        if (
            final_checkpoint.get("terminal") is not True
            or final_checkpoint.get("recovery_action") != "already_terminal"
            or final_checkpoint.get("adapter_binding_digest")
            != plan.get("adapter_binding_digest")
            or final_checkpoint.get("runtime_identity_digest")
            != plan.get("runtime_identity_digest")
        ):
            raise ChangeExecutionPreconditionError(
                "MNE rollback did not reach a terminal journal checkpoint"
            )
        return rolled_back

    @staticmethod
    def _validate_recovered_rollback_receipt(
        value: object,
        *,
        transaction: ChangeTransaction,
        checkpoint: Mapping[str, Any],
        expected_source_pid: int,
        expected_source_hwnd: int,
    ) -> dict[str, Any]:
        rollback = _strict_public_receipt(
            value,
            schema=MNE_ROLLBACK_SCHEMA,
            digest_field="rollback_digest",
            keys={
                "schema",
                "plan_digest",
                "baseline_digest",
                "transition",
                "program_check_verified",
                "save_verified",
                "project_sha256_after",
                "baseline_ld_raw_sha256",
                "restored_ld_raw_sha256",
                "ld_raw_equal",
                "ld_canonical_sha256",
                "ld_canonical_equal",
                "reopen",
                "verified",
                "rollback_digest",
            },
            label="recovered program.rollback_mne_replacement.rollback",
        )
        pseudo_plan = {
            "program_id": checkpoint.get("program_id"),
            "program_name": checkpoint.get("program_name"),
        }
        baseline_mne_sha256 = _sha256(
            checkpoint.get("before_mne_sha256"),
            "recovered MNE baseline hash",
        )
        target_mne_sha256 = _sha256(
            checkpoint.get("target_mne_sha256"),
            "recovered MNE target hash",
        )
        baseline_ld_raw_sha256 = _sha256(
            checkpoint.get("rollback_ld_sha256"),
            "recovered MNE baseline LD hash",
        )
        baseline_ld_canonical_sha256 = _sha256(
            checkpoint.get("baseline_ld_canonical_sha256"),
            "recovered MNE baseline canonical LD hash",
        )
        _validate_rollback_transition(
            rollback.get("transition"),
            baseline_mne_sha256=baseline_mne_sha256,
            target_mne_sha256=target_mne_sha256,
            adapter_binding_digest=_sha256(
                checkpoint.get("adapter_binding_digest"),
                "recovered MNE rollback adapter digest",
            ),
        )
        reopen = _validate_reopen_receipt(
            rollback.get("reopen"),
            transaction=transaction,
            plan=pseudo_plan,
            expected_mne_sha256=baseline_mne_sha256,
            expected_ld_payload_sha256=baseline_ld_raw_sha256,
            expected_ld_canonical_sha256=baseline_ld_canonical_sha256,
            expected_source_pid=expected_source_pid,
            expected_source_hwnd=expected_source_hwnd,
        )
        if (
            rollback.get("verified") is not True
            or rollback.get("program_check_verified") is not True
            or rollback.get("save_verified") is not True
            or rollback.get("ld_raw_equal") is not True
            or rollback.get("ld_canonical_equal") is not True
            or rollback.get("plan_digest") != checkpoint.get("mcp_plan_digest")
            or rollback.get("baseline_digest") != checkpoint.get("baseline_digest")
            or rollback.get("baseline_ld_raw_sha256") != baseline_ld_raw_sha256
            or rollback.get("restored_ld_raw_sha256") != baseline_ld_raw_sha256
            or rollback.get("ld_canonical_sha256") != baseline_ld_canonical_sha256
            or rollback.get("project_sha256_after") != reopen.get("project_sha256")
        ):
            raise ChangeExecutionPreconditionError(
                "recovered MNE rollback receipt is not bound to the durable checkpoint"
            )
        return rollback


__all__ = [
    "CompositeChangeExecutionService",
    "MNE_PLAN_KIND",
    "MNE_TARGET_PROGRAM_SCHEMA",
    "MNE_VERIFICATION_SCOPE",
    "validated_terminal_mne_reopen",
]
