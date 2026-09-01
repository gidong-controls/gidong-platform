from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .mcp_client import MCPToolError
from .project_planning import (
    calculate_project_plan_attachment_revision_digest,
    project_plan_review_item_ids,
)

NATIVE_PROJECT_AUTHORIZATION_SCHEMA = "controlstudio-native-project-authorization/v2"
NATIVE_PROJECT_PLAN_SCHEMA = "controlstudio-native-project-plan/v2"
NATIVE_BUILD_BUNDLE_SCHEMA = "controlstudio-native-build-bundle/v2"
NATIVE_BUILD_BUNDLE_RECEIPT_SCHEMA = "controlstudio-native-build-receipt/v3"
NATIVE_REOPEN_RECEIPT_SCHEMA = "controlstudio-native-reopen-receipt/v2"
NATIVE_SIMULATION_RECEIPT_SCHEMA = "controlstudio-native-simulation-receipt/v5"
PREVIOUS_NATIVE_SIMULATION_RECEIPT_SCHEMA = "controlstudio-native-simulation-receipt/v4"
LEGACY_NATIVE_SIMULATION_RECEIPT_SCHEMA = "controlstudio-native-simulation-receipt/v3"
NATIVE_SIMULATION_TRIAL_SCHEMA = "controlstudio-native-simulation-trial/v1"
NEW_BUILD_WORKSPACE_PROVENANCE_SCHEMA = "controlstudio-new-build-workspace-provenance/v1"
NATIVE_BUILD_BUNDLE_PHASE = "engineered_control_program"
NATIVE_COMPILED_PROGRAM_SCHEMA = "controlstudio-xg5000-mnemonic-program/v4"
_INVALID_FILE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_INVALID_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class NativeProjectConflict(RuntimeError):
    """An idempotency or state precondition conflicted with server authority."""


class NativeProjectPreconditionError(RuntimeError):
    """The requested local path or XG5000 target was not safe to use."""


class NativeProjectEvidenceError(RuntimeError):
    """MCP returned evidence that did not prove the requested native project."""


def _native_create_failed_before_confirmation(error: object) -> bool:
    value = str(error)
    return any(
        marker in value
        for marker in (
            "XG5000_ELEVATION_MISMATCH:",
            "XG5000_PROJECT_STATE_UNVERIFIED:",
        )
    )


def _native_create_failed_before_program_write(error: object) -> bool:
    """Return whether the reviewed program is proven not to have been written.

    ``project.create_native`` creates and initially saves the empty XG5000
    project before entering the clipboard transaction used by
    ``insert_il_blank``.  A clipboard snapshot failure occurs while entering
    that transaction, before the first clipboard mutation or paste command.
    The empty artifact must stay preserved, but it is safe to classify the
    operation as failed and let a new operation use a fresh destination.
    """

    return "XG5000_CLIPBOARD_SNAPSHOT_FAILED:" in str(error)


def _retired_native_target_replaced_by_idle_instance(
    raw_instances: object,
    *,
    stored_pid: int,
    stored_hwnd: int,
    selected_pid: int,
    selected_hwnd: int,
) -> bool:
    """Prove the original target is gone and its selected replacement is idle."""
    if not isinstance(raw_instances, dict) or not isinstance(
        raw_instances.get("instances"), list
    ):
        return False
    instances = [item for item in raw_instances["instances"] if isinstance(item, dict)]
    if any(
        item.get("pid") == stored_pid and item.get("hwnd") == stored_hwnd
        for item in instances
    ):
        return False
    selected = [
        item
        for item in instances
        if item.get("pid") == selected_pid and item.get("hwnd") == selected_hwnd
    ]
    return bool(
        len(selected) == 1
        and selected[0].get("visible") is True
        and str(selected[0].get("title") or "").strip().casefold() == "xg5000"
    )


@dataclass(frozen=True)
class _NativeProjectRequest:
    """Public product-contract values; contains no vendor adapter behavior."""

    project_name: str
    destination_directory: Path
    plc_series: str
    cpu_type: str
    program_name: str
    program_language: str
    description: str


def _validated_text(field: str, value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise NativeProjectPreconditionError(f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum or _INVALID_TEXT.search(result):
        raise NativeProjectPreconditionError(
            f"{field} is empty, too long, or contains a control character"
        )
    return result


def _validated_name(field: str, value: object, *, maximum: int) -> str:
    result = _validated_text(field, value, maximum=maximum)
    if _INVALID_FILE_NAME.search(result) or result.endswith((".", " ")):
        raise NativeProjectPreconditionError(
            f"{field} contains a character unavailable to XG5000 or Windows"
        )
    if field == "project_name" and result.casefold() in _RESERVED_WINDOWS_NAMES:
        raise NativeProjectPreconditionError("project_name is a reserved Windows name")
    return result


def _validate_native_project_request(request: _NativeProjectRequest) -> _NativeProjectRequest:
    project_name = _validated_name("project_name", request.project_name, maximum=80)
    program_name = _validated_name("program_name", request.program_name, maximum=80)
    plc_series = _validated_text("plc_series", request.plc_series, maximum=32)
    cpu_type = _validated_text("cpu_type", request.cpu_type, maximum=64)
    language = _validated_text("program_language", request.program_language, maximum=32)
    if not isinstance(request.description, str):
        raise NativeProjectPreconditionError("description must be a string")
    description = request.description.strip()
    if len(description) > 1024 or "\x00" in description:
        raise NativeProjectPreconditionError("description is too long or contains NUL")
    raw_directory = Path(request.destination_directory).expanduser()
    if not raw_directory.is_absolute():
        raise NativeProjectPreconditionError("destination_directory must be absolute")
    try:
        directory = raw_directory.resolve(strict=True)
    except OSError as exc:
        raise NativeProjectPreconditionError("destination_directory does not exist") from exc
    if not directory.is_dir() or directory.is_symlink():
        raise NativeProjectPreconditionError("destination_directory must be a real directory")
    return _NativeProjectRequest(
        project_name=project_name,
        destination_directory=directory,
        plc_series=plc_series,
        cpu_type=cpu_type,
        program_name=program_name,
        program_language=language,
        description=description,
    )


def _require_empty_native_project_destination(directory: Path) -> None:
    """Creation owns an empty directory; plan identity does not."""

    try:
        contents = tuple(directory.iterdir())
    except OSError as exc:
        raise NativeProjectPreconditionError(
            "destination_directory could not be enumerated"
        ) from exc
    if contents:
        raise NativeProjectPreconditionError("destination_directory must be empty")


def _native_project_plan_digest(
    request: _NativeProjectRequest,
    *,
    pid: int,
    hwnd: int,
    build_bundle: dict[str, Any],
) -> str:
    validated = _validate_native_project_request(request)
    if pid <= 0 or hwnd <= 0:
        raise NativeProjectPreconditionError("pid and hwnd must be positive integers")
    payload = {
        "schema": NATIVE_PROJECT_PLAN_SCHEMA,
        "operation": "project.create_native",
        "pid": pid,
        "hwnd": hwnd,
        "project_name": validated.project_name,
        "destination_directory": os.path.normcase(str(validated.destination_directory)),
        "plc_series": validated.plc_series,
        "cpu_type": validated.cpu_type,
        "program_name": validated.program_name,
        "program_language": validated.program_language,
        "description": validated.description,
        "build_bundle": build_bundle,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class NativeProjectSpec:
    operation_id: str
    project_name: str
    workspace_parent: Path
    plc_series: str
    cpu_model: str
    program_name: str
    language: str
    description: str
    xg5000_pid: int
    xg5000_hwnd: int
    planning_conversation_id: str | None = None
    planning_message_id: str | None = None
    planning_plan_digest: str | None = None
    reviewed_section_ids: tuple[str, ...] = ()
    reviewed_item_ids: tuple[str, ...] = ()
    project_brief_digest: str | None = None
    attachment_revision_digest: str | None = None
    planning_artifact: dict[str, Any] | None = None


@dataclass(frozen=True)
class NativeProjectOperation:
    id: str
    operation_id: str
    state: str
    request_digest: str
    actor_id: str
    request: dict[str, Any]
    plan_digest: str | None
    destination_directory: str
    result: dict[str, Any]
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class NativeProjectRecoveryEvidence:
    pid: int
    hwnd: int
    expected_project_path: Path
    expected_project_sha256: str | None
    observed_project_path: Path | None = None


class NativeProjectGateway(Protocol):
    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any: ...

    async def call_workspace_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any: ...

    def sign_mutation_authorization(
        self,
        claims: dict[str, Any],
    ) -> dict[str, Any]: ...

    def bind_agent_session(self, session: dict[str, Any]) -> None: ...

    def clear_agent_session(self) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_load(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise TypeError("stored native-project JSON is not an object")
    return loaded


def _json_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _native_simulator_stop_state_verified(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    samples = value.get("samples")
    dominant_prototype = (229, 20, 0)

    def is_number(item: Any) -> bool:
        return isinstance(item, (int, float)) and not isinstance(item, bool)

    def rect(item: Any) -> list[int] | None:
        if (
            not isinstance(item, list)
            or len(item) != 4
            or any(not isinstance(component, int) for component in item)
        ):
            return None
        return item

    main_rect = rect(value.get("main_rect"))
    status_rect = rect(value.get("status_rect"))
    relative_rect = rect(value.get("status_relative_rect"))
    selected_run = value.get("selected_run")
    selected_stop = value.get("selected_stop")
    run_rect = rect(selected_run.get("rect")) if isinstance(selected_run, dict) else None
    stop_rect = rect(selected_stop.get("rect")) if isinstance(selected_stop, dict) else None
    if (
        main_rect is None
        or status_rect is None
        or relative_rect is None
        or run_rect is None
        or stop_rect is None
    ):
        return False
    expected_relative = [
        status_rect[0] - main_rect[0],
        status_rect[1] - main_rect[1],
        status_rect[2] - main_rect[0],
        status_rect[3] - main_rect[1],
    ]
    geometry_consistent = bool(
        main_rect[0] <= status_rect[0] < status_rect[2] <= main_rect[2]
        and main_rect[1] <= status_rect[1] < status_rect[3] <= main_rect[3]
        and relative_rect == expected_relative
        and value.get("status_bottom_gap") == main_rect[3] - status_rect[3]
        and run_rect[1] == stop_rect[1]
        and run_rect[3] == stop_rect[3]
        and run_rect[2] <= stop_rect[0]
    )
    if not geometry_consistent:
        return False

    samples_verified = bool(
        isinstance(samples, list)
        and len(samples) == 3
        and all(
            isinstance(sample, dict)
            and sample.get("index") == index
            and sample.get("classification") == "STOP"
            and isinstance(sample.get("pixel_count"), int)
            and sample["pixel_count"] > 0
            and isinstance(sample.get("dominant_rgb"), list)
            and len(sample["dominant_rgb"]) == 3
            and all(
                isinstance(component, int) and 0 <= component <= 255
                for component in sample["dominant_rgb"]
            )
            and is_number(sample.get("dominant_share"))
            and sample["dominant_share"] >= 0.75
            and is_number(sample.get("stop_share"))
            and sample["stop_share"] >= 0.85
            and is_number(sample.get("run_share"))
            and 0 <= sample["run_share"] < 0.10
            and is_number(sample.get("offline_share"))
            and 0 <= sample["offline_share"] < 0.10
            and is_number(sample.get("stop_prototype_distance"))
            and 0 <= sample["stop_prototype_distance"] <= 64.0
            and sum(
                (component - expected) ** 2
                for component, expected in zip(
                    sample["dominant_rgb"], dominant_prototype, strict=True
                )
            )
            ** 0.5
            <= 64.0
            for index, sample in enumerate(samples, start=1)
        )
    )
    pid = value.get("pid")
    hwnd = value.get("hwnd")
    return bool(
        value.get("verified") is True
        and value.get("method") == "xg5000-simulator-status-pane/v1"
        and value.get("classification") == "STOP"
        and isinstance(pid, int)
        and pid > 0
        and isinstance(hwnd, int)
        and hwnd > 0
        and value.get("foreground_pid") == pid
        and value.get("foreground_hwnd") == hwnd
        and isinstance(value.get("dpi"), int)
        and value["dpi"] >= 96
        and value.get("status_candidate_count") == 1
        and value.get("status_text")
        in {"L, SIMULATOR, 정상", "L, SIMULATOR, NORMAL"}
        and value.get("geometry_verified") is True
        and value.get("capture_method")
        == "PIL.ImageGrab/foreground-status-pane"
        and value.get("consecutive_count") == 3
        and isinstance(selected_run, dict)
        and selected_run.get("visible") is True
        and selected_run.get("enabled") is True
        and isinstance(selected_stop, dict)
        and selected_stop.get("visible") is True
        and selected_stop.get("enabled") is True
        and samples_verified
    )


def _native_simulator_evidence_verified(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    stages_verified = all(
        isinstance(evidence, dict)
        and evidence.get("ok") is True
        and evidence.get("verified") is True
        for evidence in (value.get(stage) for stage in ("start", "write", "run", "stop"))
    )
    stop = value.get("stop")
    confirmation = stop.get("confirmation") if isinstance(stop, dict) else None
    postcondition = stop.get("postcondition") if isinstance(stop, dict) else None
    ui_state = (
        postcondition.get("ui_state") if isinstance(postcondition, dict) else None
    )
    return bool(
        stages_verified
        and isinstance(confirmation, dict)
        and confirmation.get("closed") is True
        and isinstance(postcondition, dict)
        and postcondition.get("main_enabled") is True
        and isinstance(ui_state, dict)
        and _native_simulator_stop_state_verified(ui_state)
    )


def _native_simulation_trial_id(operation_id: str, bundle_id: str) -> str:
    namespace = uuid.UUID(_canonical_uuid(operation_id, "operation_id"))
    return str(uuid.uuid5(namespace, f"controlstudio-native-simulation:{bundle_id}"))


def _native_simulation_failure_projection(
    raw: object,
    *,
    project_hash: str,
) -> dict[str, Any] | None:
    """Project one bounded MCP simulator-failure receipt for Host classification."""

    if (
        not isinstance(raw, dict)
        or raw.get("ok") is not False
        or raw.get("verified") is not False
        or raw.get("operation") != "project.verify_native_simulation"
    ):
        return None
    failure = raw.get("simulation_failure")
    if not isinstance(failure, dict) or set(failure) != {
        "schemaVersion",
        "classification",
        "phase",
        "detail",
        "runtimeError",
        "dispatch",
        "baseline",
        "recovery",
        "projectHashBefore",
        "projectHashAfter",
        "projectUnchanged",
    }:
        return None
    runtime_error = failure.get("runtimeError")
    dispatch = failure.get("dispatch")
    baseline = failure.get("baseline")
    recovery = failure.get("recovery")
    hash_after = failure.get("projectHashAfter")
    project_unchanged = failure.get("projectUnchanged")
    if (
        failure.get("schemaVersion")
        != "controlstudio-native-simulation-failure/v1"
        or failure.get("classification")
        not in {"PRE_DISPATCH_READ_FAILURE", "UNRESOLVED_RUNTIME_FAILURE"}
        or failure.get("phase")
        not in {
            "simulator_start",
            "target_status_before_program_write",
            "program_write",
            "target_status_after_program_write",
            "post_write_native_readback",
            "capture_input_baseline",
            "cpu_run",
            "scenario_observation",
            "cpu_stop",
            "baseline_restore",
        }
        or not isinstance(failure.get("detail"), str)
        or not 1 <= len(failure["detail"]) <= 1000
        or not isinstance(runtime_error, dict)
        or set(runtime_error)
        != {"code", "workerOperation", "timeoutSeconds", "dispatchState"}
        or not isinstance(runtime_error.get("code"), str)
        or re.fullmatch(r"[A-Za-z0-9_]{1,120}", runtime_error["code"]) is None
        or (
            runtime_error.get("workerOperation") is not None
            and (
                not isinstance(runtime_error["workerOperation"], str)
                or re.fullmatch(r"[a-z_]{1,80}", runtime_error["workerOperation"])
                is None
            )
        )
        or (
            runtime_error.get("timeoutSeconds") is not None
            and (
                not isinstance(runtime_error["timeoutSeconds"], (int, float))
                or isinstance(runtime_error["timeoutSeconds"], bool)
                or not 0 < float(runtime_error["timeoutSeconds"]) <= 120
            )
        )
        or runtime_error.get("dispatchState")
        not in {None, "not_attempted", "not_dispatched", "unknown", "not_applicable"}
        or not isinstance(dispatch, dict)
        or set(dispatch) != {"simulatorStart", "programWrite", "cpuRun"}
        or dispatch.get("simulatorStart") not in {"not_attempted", "attempted", "unknown"}
        or dispatch.get("programWrite") not in {"not_attempted", "attempted"}
        or dispatch.get("cpuRun") not in {"not_attempted", "attempted"}
        or not isinstance(baseline, dict)
        or set(baseline) != {"capture", "restore"}
        or baseline.get("capture") not in {"not_attempted", "attempted"}
        or baseline.get("restore") not in {"not_attempted", "attempted"}
        or not isinstance(recovery, dict)
        or set(recovery) != {"cpuStop", "restorationVerified", "failureStages"}
        or recovery.get("cpuStop") not in {"not_attempted", "attempted"}
        or recovery.get("restorationVerified") not in {True, False}
        or not isinstance(recovery.get("failureStages"), list)
        or any(
            stage not in {"cpu_stop", "baseline_restore"}
            for stage in recovery["failureStages"]
        )
        or len(set(recovery["failureStages"])) != len(recovery["failureStages"])
        or failure.get("projectHashBefore") != project_hash
        or (
            hash_after is not None
            and (
                not isinstance(hash_after, str)
                or re.fullmatch(r"[0-9a-f]{64}", hash_after) is None
            )
        )
        or not isinstance(project_unchanged, bool)
        or raw.get("project_unchanged") is not project_unchanged
        or raw.get("controllerConnected") is not False
        or raw.get("plcWritePerformed") is not False
    ):
        return None
    return {
        "classification": failure["classification"],
        "phase": failure["phase"],
        "detail": failure["detail"],
        "runtimeError": runtime_error,
        "dispatch": dispatch,
        "baseline": baseline,
        "recovery": recovery,
        "projectHashBefore": project_hash,
        "projectHashAfter": hash_after,
        "projectUnchanged": project_unchanged,
    }


def _pre_dispatch_native_simulation_failure(
    raw: object,
    *,
    project_hash: str,
) -> dict[str, Any] | None:
    """Accept only proof that no selected-XG simulator command was dispatched."""

    failure = _native_simulation_failure_projection(raw, project_hash=project_hash)
    if (
        failure is None
        or failure["classification"] != "PRE_DISPATCH_READ_FAILURE"
        or failure["phase"]
        not in {"simulator_start", "target_status_before_program_write"}
        or failure["runtimeError"]["dispatchState"] == "unknown"
        or (
            failure["phase"] == "target_status_before_program_write"
            and failure["runtimeError"]["workerOperation"] != "inspect_buttons"
        )
        or failure["dispatch"]
        != {
            "simulatorStart": "not_attempted",
            "programWrite": "not_attempted",
            "cpuRun": "not_attempted",
        }
        or failure["baseline"]
        != {"capture": "not_attempted", "restore": "not_attempted"}
        or failure["recovery"]
        != {
            "cpuStop": "not_attempted",
            "restorationVerified": False,
            "failureStages": [],
        }
        or failure["projectHashAfter"] != project_hash
        or failure["projectUnchanged"] is not True
    ):
        return None
    return failure


def _unresolved_native_simulation_failure(
    raw: object,
    *,
    project_hash: str,
) -> dict[str, Any] | None:
    """Retain bounded failure facts without claiming safe restoration."""

    failure = _native_simulation_failure_projection(raw, project_hash=project_hash)
    return (
        failure
        if failure is not None
        and failure["classification"] == "UNRESOLVED_RUNTIME_FAILURE"
        else None
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _require_within(path: Path, parent: Path, field: str) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise NativeProjectEvidenceError(
            f"{field} escaped the Host-selected workspace directory"
        ) from exc


def _stored_creation_receipt_matches_reviewed_build(
    result: dict[str, Any],
    *,
    request: dict[str, Any],
    operation_plan_digest: str | None,
    expected_path: Path,
    destination: Path,
) -> bool:
    """Prove that a checkpoint is the exact validated create or resume-build receipt."""

    try:
        build_bundle = request.get("buildBundle")
        reviewed_plan = (
            build_bundle.get("reviewedPlan")
            if isinstance(build_bundle, dict)
            else None
        )
        compiled = (
            build_bundle.get("compiledProgram")
            if isinstance(build_bundle, dict)
            else None
        )
        receipt = result.get("buildBundleReceipt")
        project_path_text = result.get("projectPath")
        project_hash = result.get("projectSha256")
        project_size = result.get("projectSize")
        if (
            not isinstance(build_bundle, dict)
            or not isinstance(reviewed_plan, dict)
            or not isinstance(compiled, dict)
            or not isinstance(receipt, dict)
            or not isinstance(project_path_text, str)
            or not isinstance(project_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", project_hash) is None
            or not isinstance(project_size, int)
            or isinstance(project_size, bool)
            or project_size <= 0
            or not isinstance(compiled.get("source"), str)
            or not isinstance(compiled.get("rungIds"), list)
            or not isinstance(compiled.get("sourceSha256"), str)
            or not isinstance(compiled.get("ladderIrSha256"), str)
            or not isinstance(reviewed_plan.get("planDigest"), str)
        ):
            return False
        if (
            _canonical_path(Path(project_path_text)) != _canonical_path(expected_path)
            or _canonical_path(Path(str(result.get("destinationDirectory") or "")))
            != _canonical_path(destination)
        ):
            return False
        expected_receipt = {
            "schemaVersion": NATIVE_BUILD_BUNDLE_RECEIPT_SCHEMA,
            "bundleId": build_bundle.get("bundleId"),
            "phase": NATIVE_BUILD_BUNDLE_PHASE,
            "status": "APPLIED_VERIFIED",
            "reviewedPlanDigest": reviewed_plan["planDigest"],
            "nativeReadback": {
                "projectName": request.get("projectName"),
                "projectPath": project_path_text,
                "projectSha256": project_hash,
                "projectSize": project_size,
                "destinationDirectory": str(destination),
                "plcSeries": request.get("plcSeries"),
                "cpuModel": request.get("cpuModel"),
                "programName": request.get("programName"),
                "language": request.get("language"),
                "description": _native_bundle_description(build_bundle),
                "identityVerified": True,
            },
            "programReadback": {
                "rungCount": len(compiled["rungIds"]),
                "rungIds": compiled["rungIds"],
                "ladderIrSha256": compiled["ladderIrSha256"],
                "sourceSha256": compiled["sourceSha256"],
                "exactInstructionReadback": compiled["source"].splitlines(),
                "finalView": "LD",
                "programCheckPassed": True,
                "savedProjectSha256": project_hash,
            },
        }
        return bool(
            result.get("identityVerified") is True
            and result.get("operation") in {"project.create_native", "project.resume_native"}
            and result.get("xg5000Pid") == request.get("xg5000Pid")
            and result.get("xg5000Hwnd") == request.get("xg5000Hwnd")
            and result.get("planDigest") == operation_plan_digest
            and result.get("buildBundleId") == build_bundle.get("bundleId")
            and result.get("reviewedPlanDigest") == reviewed_plan["planDigest"]
            and result.get("controllerConnected") is False
            and result.get("plcWritePerformed") is False
            and receipt == expected_receipt
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _canonical_uuid(value: str, field: str) -> str:
    try:
        normalized = str(uuid.UUID(value))
    except ValueError as exc:
        raise NativeProjectPreconditionError(f"{field} must be a UUID") from exc
    if normalized != value:
        raise NativeProjectPreconditionError(f"{field} must use canonical UUID form")
    return normalized


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _new_build_workspace_provenance(
    *,
    native_project_id: str,
    build_bundle: dict[str, Any],
    build_receipt: dict[str, Any],
    project_sha256: str,
) -> dict[str, Any]:
    unsigned = {
        "schema": NEW_BUILD_WORKSPACE_PROVENANCE_SCHEMA,
        "native_project_id": _canonical_uuid(native_project_id, "native_project_id"),
        "reviewed_plan_digest": build_bundle["reviewedPlan"]["planDigest"],
        "build_bundle_id": build_bundle["bundleId"],
        "build_receipt_digest": _json_digest(build_receipt),
        "saved_project_sha256": project_sha256,
    }
    return {**unsigned, "receipt_digest": _json_digest(unsigned)}


def validated_new_build_workspace_provenance(
    operation: NativeProjectOperation,
    workspace: object,
    *,
    require_capture_verification: bool = False,
) -> dict[str, Any] | None:
    """Return the exact native-build lineage carried by ``workspace``.

    A workspace attachment is not native-build authority by itself.  This
    verifier binds the candidate operation, reviewed plan, canonical build
    bundle and receipt, and captured source hash before a handoff may use it.
    ``None`` is deliberately returned for ordinary ``local_file`` workspaces.
    """

    if (
        not isinstance(workspace, dict)
        or workspace.get("source_kind") != "new_build"
        or ("ok" in workspace and workspace.get("ok") is not True)
        or ("verified" in workspace and workspace.get("verified") is not True)
        or (
            require_capture_verification
            and (workspace.get("ok") is not True or workspace.get("verified") is not True)
        )
    ):
        return None
    request = operation.request
    result = operation.result
    reviewed_plan = request.get("reviewedPlan")
    build_bundle = request.get("buildBundle")
    bundle_reviewed_plan = (
        build_bundle.get("reviewedPlan") if isinstance(build_bundle, dict) else None
    )
    build_receipt = result.get("buildBundleReceipt")
    source = workspace.get("source")
    working_copy = workspace.get("working_copy")
    raw_provenance = workspace.get("provenance")
    project_hash = result.get("projectSha256")
    original_path_text = source.get("original_path") if isinstance(source, dict) else None
    captured_path_text = source.get("captured_path") if isinstance(source, dict) else None
    working_path_text = working_copy.get("path") if isinstance(working_copy, dict) else None
    result_path_text = result.get("projectPath")
    if not all(
        isinstance(value, dict)
        for value in (
            reviewed_plan,
            build_bundle,
            bundle_reviewed_plan,
            build_receipt,
            source,
            working_copy,
            raw_provenance,
        )
    ):
        return None
    assert isinstance(reviewed_plan, dict)
    assert isinstance(build_bundle, dict)
    assert isinstance(bundle_reviewed_plan, dict)
    assert isinstance(build_receipt, dict)
    assert isinstance(source, dict)
    assert isinstance(working_copy, dict)
    assert isinstance(raw_provenance, dict)

    bundle_id = build_bundle.get("bundleId")
    reviewed_plan_digest = reviewed_plan.get("planDigest")
    if (
        not _is_canonical_uuid(operation.id)
        or operation.operation_id != operation.id
        or request.get("operationId") != operation.id
        or reviewed_plan != bundle_reviewed_plan
        or not isinstance(reviewed_plan_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", reviewed_plan_digest) is None
        or not isinstance(bundle_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", bundle_id) is None
        or bundle_id
        != _json_digest({key: value for key, value in build_bundle.items() if key != "bundleId"})
        or result.get("buildBundleId") != bundle_id
        or result.get("reviewedPlanDigest") != reviewed_plan_digest
        or not isinstance(project_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", project_hash) is None
        or source.get("sha256") != project_hash
        or source.get("original_state") != "unchanged"
        or source.get("original_current_sha256") != project_hash
        or working_copy.get("baseline_sha256") != project_hash
        or not isinstance(working_copy.get("current_sha256"), str)
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(working_copy.get("current_sha256")),
        )
        is None
        or working_copy.get("state")
        != ("clean" if working_copy.get("current_sha256") == project_hash else "modified")
        or not _is_canonical_uuid(workspace.get("workspace_id"))
        or not _is_canonical_uuid(workspace.get("revision_id"))
        or workspace.get("vendor") != "ls"
        or workspace.get("engineering_system") != "XG5000"
        or not all(
            isinstance(path_text, str) and bool(path_text.strip())
            for path_text in (
                original_path_text,
                captured_path_text,
                working_path_text,
                result_path_text,
            )
        )
    ):
        return None

    try:
        assert isinstance(original_path_text, str)
        assert isinstance(captured_path_text, str)
        assert isinstance(working_path_text, str)
        assert isinstance(result_path_text, str)
        workspace_paths = (
            Path(original_path_text),
            Path(captured_path_text),
            Path(working_path_text),
        )
        if any(
            not path.is_absolute() or path.suffix.casefold() != ".xgwx" for path in workspace_paths
        ):
            return None
        destination = Path(operation.destination_directory).resolve()
        request_destination = Path(str(request.get("destinationDirectory") or "")).resolve()
        project_name = request.get("projectName")
        if (
            not isinstance(project_name, str)
            or not project_name
            or _canonical_path(Path(original_path_text)) != _canonical_path(Path(result_path_text))
            or _canonical_path(request_destination) != _canonical_path(destination)
            or not _stored_creation_receipt_matches_reviewed_build(
                result,
                request=request,
                operation_plan_digest=operation.plan_digest,
                expected_path=destination / f"{project_name}.xgwx",
                destination=destination,
            )
        ):
            return None
        expected = _new_build_workspace_provenance(
            native_project_id=operation.id,
            build_bundle=build_bundle,
            build_receipt=build_receipt,
            project_sha256=project_hash,
        )
    except (KeyError, OSError, TypeError, ValueError, NativeProjectPreconditionError):
        return None

    # ``LocalWorkspaceV1.model_dump()`` uses the Python field name for the one
    # explicitly aliased schema key.  Accept exactly that representation, while
    # rejecting additions or omissions in either form.
    expected_keys = set(expected)
    model_dump_keys = (expected_keys - {"schema"}) | {"receipt_schema"}
    if set(raw_provenance) == expected_keys:
        provenance = raw_provenance
    elif set(raw_provenance) == model_dump_keys:
        provenance = {
            "schema": raw_provenance["receipt_schema"],
            **{key: value for key, value in raw_provenance.items() if key != "receipt_schema"},
        }
    else:
        return None
    return expected if provenance == expected else None


def _reviewed_plan_payload(spec: NativeProjectSpec) -> dict[str, Any]:
    values = (
        spec.planning_conversation_id,
        spec.planning_message_id,
        spec.planning_plan_digest,
        spec.project_brief_digest,
        spec.attachment_revision_digest,
    )
    if not all(isinstance(value, str) and value for value in values):
        raise NativeProjectPreconditionError("reviewed project plan identity must be complete")
    if len(str(spec.planning_plan_digest)) != 64 or any(
        character not in "0123456789abcdef" for character in str(spec.planning_plan_digest)
    ):
        raise NativeProjectPreconditionError(
            "reviewed project plan digest must be a lowercase SHA-256 value"
        )
    if not spec.reviewed_section_ids:
        raise NativeProjectPreconditionError(
            "reviewed project plan section scope must not be empty"
        )
    if len(set(spec.reviewed_section_ids)) != len(spec.reviewed_section_ids):
        raise NativeProjectPreconditionError(
            "reviewed project plan section scope contains duplicates"
        )
    if not spec.reviewed_item_ids:
        raise NativeProjectPreconditionError(
            "reviewed project plan item scope must not be empty"
        )
    if len(set(spec.reviewed_item_ids)) != len(spec.reviewed_item_ids):
        raise NativeProjectPreconditionError(
            "reviewed project plan item scope contains duplicates"
        )
    for field, digest in (
        ("projectBriefDigest", spec.project_brief_digest),
        ("attachmentRevisionDigest", spec.attachment_revision_digest),
    ):
        if not isinstance(digest, str) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise NativeProjectPreconditionError(
                f"reviewed project plan {field} must be a lowercase SHA-256 value"
            )
    return {
        "schemaVersion": "2",
        "conversationId": spec.planning_conversation_id,
        "messageId": spec.planning_message_id,
        "planDigest": spec.planning_plan_digest,
        "projectBriefDigest": spec.project_brief_digest,
        "attachmentRevisionDigest": spec.attachment_revision_digest,
        "reviewedSectionIds": list(spec.reviewed_section_ids),
        "reviewedItemIds": list(spec.reviewed_item_ids),
    }


_NATIVE_COLLECTION_FIELDS = (
    "ioPoints",
    "operatingModes",
    "controlNetworks",
    "timers",
    "sequences",
    "alarms",
    "recoveryRules",
    "safetyRules",
)
_NATIVE_COLLECTION_NA_BASES = {
    "operatingModes": {"single_mode_operation"},
    "timers": {"no_timing_behavior"},
    "sequences": {"no_sequence_behavior"},
    "alarms": {"no_alarm_behavior"},
    "recoveryRules": {"no_recovery_behavior"},
    "safetyRules": {"no_physical_or_hazard_outputs"},
}


def _resolve_native_collection_scope(
    design: dict[str, Any],
    collections: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if any(not isinstance(collections[name], list) for name in _NATIVE_COLLECTION_FIELDS):
        raise NativeProjectPreconditionError(
            "structured native design collection fields are incomplete"
        )

    raw_scope = design.get("collectionScope")
    if raw_scope is None:
        raise NativeProjectPreconditionError(
            "legacy design is readable but must be replanned with explicit "
            "collectionScope before a new executable build"
        )
    if not isinstance(raw_scope, dict) or set(raw_scope) != set(_NATIVE_COLLECTION_FIELDS):
        raise NativeProjectPreconditionError(
            "collectionScope must contain one decision for every native collection"
        )

    resolved: dict[str, dict[str, Any]] = {}
    for name in _NATIVE_COLLECTION_FIELDS:
        decision = raw_scope.get(name)
        if not isinstance(decision, dict) or set(decision) != {
            "status",
            "reason",
            "evidence",
            "basis",
        }:
            raise NativeProjectPreconditionError(
                f"collectionScope.{name} is incomplete"
            )
        status = decision.get("status")
        reason = decision.get("reason")
        evidence = decision.get("evidence")
        basis = decision.get("basis")
        if (
            status not in {"confirmed", "not_applicable"}
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, str) or not item.strip() for item in evidence)
        ):
            raise NativeProjectPreconditionError(
                f"collectionScope.{name} status, reason, or evidence is invalid"
            )
        if status == "confirmed":
            if basis is not None or not collections[name]:
                raise NativeProjectPreconditionError(
                    f"confirmed collectionScope.{name} must be non-empty and have no N/A basis"
                )
        else:
            if collections[name]:
                raise NativeProjectPreconditionError(
                    f"not_applicable collectionScope.{name} must be empty"
                )
            if name in {"ioPoints", "controlNetworks"}:
                raise NativeProjectPreconditionError(
                    f"executable build requires confirmed collectionScope.{name}"
                )
            if (
                not isinstance(basis, str)
                or basis not in _NATIVE_COLLECTION_NA_BASES[name]
            ):
                raise NativeProjectPreconditionError(
                    f"collectionScope.{name} has an unsupported N/A basis"
                )
        resolved[name] = {
            "status": status,
            "reason": reason.strip(),
            "evidence": [item.strip() for item in evidence],
            "basis": basis,
            "source": "explicit",
        }

    if (
        resolved["recoveryRules"]["status"] == "not_applicable"
        and (collections["alarms"] or collections["sequences"])
    ):
        raise NativeProjectPreconditionError(
            "alarm or sequence logic requires confirmed recoveryRules"
        )
    if (
        resolved["safetyRules"]["status"] == "not_applicable"
        and resolved["safetyRules"]["basis"] == "no_physical_or_hazard_outputs"
        and any(
            isinstance(point, dict)
            and (
                point.get("kind") == "digital_output"
                or (
                    point.get("kind") != "digital_input"
                    and str(point.get("address") or "").startswith("P")
                )
            )
            for point in collections["ioPoints"]
        )
    ):
        raise NativeProjectPreconditionError(
            "physical outputs require a confirmed process or external safety boundary"
        )
    return resolved


_XGB_64H_BUILTIN_IO_DIRECTION_MESSAGE = (
    "XBC-DN/DP64H built-in P0000-P001F addresses are digital_input and "
    "P0020-P003F addresses are digital_output."
)


def _xgb_64h_builtin_io_direction_mismatch(
    *,
    cpu_model: str,
    address: str,
    kind: object,
) -> bool:
    return (
        cpu_model.strip().upper() == "XBC-DN/DP64H"
        and len(address) == 5
        and (
            (kind == "digital_output" and "P0000" <= address <= "P001F")
            or (kind == "digital_input" and "P0020" <= address <= "P003F")
        )
    )


def _canonical_native_io_address(address: str, plc_series: str) -> str | None:
    """Expand XGK's compact reviewed P/M spelling to its native operand width."""

    if plc_series == "XGK":
        match = re.fullmatch(r"([PM])(\d{3,4})([0-9A-F])", address)
        if match is None:
            return None
        return f"{match.group(1)}{match.group(2).zfill(4)}{match.group(3)}"
    return (
        address
        if plc_series == "XGB" and re.fullmatch(r"[PM]\d{3}[0-9A-F]", address)
        else None
    )


def _native_simulation_stimulus_ids(
    io_points: list[Any],
    networks: list[Any],
    ladder_rungs: list[Any],
) -> list[str]:
    """Return reviewed digital inputs plus safe, contact-only internal stimuli."""

    output_ids = {
        str(network.get("outputSignalId") or "")
        for network in networks
        if isinstance(network, dict)
    }
    contact_signal_ids: set[str] = set()
    writer_ids: set[str] = set()

    for rung in ladder_rungs:
        if not isinstance(rung, dict):
            continue
        contact_signal_ids.update(
            _native_ladder_signal_contact_ids(rung.get("condition"))
        )
        action = rung.get("action")
        if (
            isinstance(action, dict)
            and action.get("targetType") == "signal"
            and isinstance(action.get("targetId"), str)
        ):
            writer_ids.add(str(action["targetId"]))

    return [
        str(point.get("signalId") or "")
        for point in io_points
        if isinstance(point, dict)
        and (
            point.get("kind") == "digital_input"
            or (
                point.get("kind") == "internal_bit"
                and str(point.get("address") or "").upper().startswith("M")
                and str(point.get("signalId") or "") in contact_signal_ids
                and str(point.get("signalId") or "") not in output_ids
                and str(point.get("signalId") or "") not in writer_ids
            )
        )
    ]


def _native_ladder_signal_contact_ids(value: object) -> set[str]:
    if not isinstance(value, dict):
        return set()
    if value.get("kind") == "contact":
        if value.get("sourceType") == "signal":
            return {str(value.get("sourceId") or "")}
        return set()
    terms = value.get("terms")
    if not isinstance(terms, list):
        return set()
    return set().union(
        *(_native_ladder_signal_contact_ids(term) for term in terms)
    )


def _compile_native_program(
    artifact: dict[str, Any],
    *,
    program_name: str,
    plc_series: str,
) -> dict[str, Any]:
    """Compile only the reviewed, explicitly ordered LD rung IR."""

    technical_configuration = artifact.get("technicalConfiguration")
    cpu_model = (
        technical_configuration.get("cpuModel")
        if isinstance(technical_configuration, dict)
        else None
    )
    if not isinstance(cpu_model, str) or not cpu_model.strip():
        raise NativeProjectPreconditionError(
            "engineering plan has no reviewed CPU model"
        )
    design = artifact.get("design")
    if not isinstance(design, dict) or design.get("schemaVersion") != "2":
        raise NativeProjectPreconditionError(
            "engineering plan has no supported structured native design"
        )
    io_points = design.get("ioPoints")
    modes = design.get("operatingModes")
    networks = design.get("controlNetworks")
    ladder_rungs = design.get("ladderRungs")
    timers = design.get("timers")
    sequences = design.get("sequences")
    alarms = design.get("alarms")
    recovery_rules = design.get("recoveryRules")
    safety_rules = design.get("safetyRules")
    if not isinstance(ladder_rungs, list) or not ladder_rungs:
        raise NativeProjectPreconditionError(
            "legacy design is readable but must be replanned with explicit "
            "ladderRungs before a new executable build"
        )
    collection_scope = _resolve_native_collection_scope(
        design,
        {
            "ioPoints": io_points,
            "operatingModes": modes,
            "controlNetworks": networks,
            "timers": timers,
            "sequences": sequences,
            "alarms": alarms,
            "recoveryRules": recovery_rules,
            "safetyRules": safety_rules,
        },
    )

    normalized_series = plc_series.strip().upper()
    io_by_id: dict[str, dict[str, Any]] = {}
    symbols: set[str] = set()
    addresses: set[str] = set()
    for point in io_points:
        if not isinstance(point, dict):
            raise NativeProjectPreconditionError("native I/O point must be an object")
        signal_id = point.get("signalId")
        symbol = point.get("symbol")
        address = point.get("address")
        kind = point.get("kind")
        canonical_address = (
            _canonical_native_io_address(address, normalized_series)
            if isinstance(address, str)
            else None
        )
        if (
            not isinstance(signal_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", signal_id) is None
            or not isinstance(symbol, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", symbol) is None
            or canonical_address is None
            or kind not in {"digital_input", "digital_output", "internal_bit"}
            or point.get("normalState") not in {"on", "off", "unknown"}
            or point.get("safeState") not in {"on", "off", "not_applicable"}
        ):
            raise NativeProjectPreconditionError(
                "native I/O point has an unsupported identifier, address, or state"
            )
        address = canonical_address
        if _xgb_64h_builtin_io_direction_mismatch(
            cpu_model=cpu_model,
            address=address,
            kind=kind,
        ):
            raise NativeProjectPreconditionError(
                f"{_XGB_64H_BUILTIN_IO_DIRECTION_MESSAGE} "
                f"Invalid signal_id: {signal_id}."
            )
        if signal_id in io_by_id or symbol.casefold() in symbols or address.casefold() in addresses:
            raise NativeProjectPreconditionError(
                "native I/O signal, symbol, and address values must be unique"
            )
        io_by_id[signal_id] = {**point, "address": address}
        symbols.add(symbol.casefold())
        addresses.add(address.casefold())

    mode_by_id: dict[str, dict[str, Any]] = {}
    mode_activation: dict[str, tuple[str, str, str]] = {}
    for mode in modes:
        if not isinstance(mode, dict):
            raise NativeProjectPreconditionError("operating mode must be an object")
        mode_id = mode.get("modeId")
        if (
            not isinstance(mode_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", mode_id) is None
            or mode_id in mode_by_id
        ):
            raise NativeProjectPreconditionError("operating mode identity is invalid")
        activation = mode.get("activation")
        if not isinstance(activation, dict) or set(activation) != {"signalId", "state"}:
            raise NativeProjectPreconditionError(f"mode {mode_id} activation is invalid")
        activation_id = activation.get("signalId")
        activation_state = activation.get("state")
        if activation_id not in io_by_id or activation_state not in {"on", "off"}:
            raise NativeProjectPreconditionError(
                f"mode {mode_id} activation references an unknown I/O signal"
            )
        mode_by_id[mode_id] = mode
        mode_activation[mode_id] = (
            str(activation_id),
            str(activation_state),
            str(io_by_id[str(activation_id)]["address"]),
        )
    for mode_id, mode in mode_by_id.items():
        transitions = mode.get("allowedTransitions")
        if (
            not isinstance(transitions, list)
            or any(not isinstance(target, str) for target in transitions)
            or len(set(transitions)) != len(transitions)
            or any(target not in mode_by_id for target in transitions)
        ):
            raise NativeProjectPreconditionError(f"mode {mode_id} has an invalid transition target")

    timer_by_id: dict[str, dict[str, Any]] = {}
    timer_addresses: set[str] = set()
    timer_address_pattern = r"T\d{4}" if normalized_series == "XGK" else r"T\d{3}"
    for timer in timers:
        if not isinstance(timer, dict):
            raise NativeProjectPreconditionError("timer must be an object")
        timer_id = timer.get("timerId")
        address = timer.get("address")
        time_base = timer.get("timeBaseMs")
        preset = timer.get("presetTicks")
        if (
            not isinstance(timer_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", timer_id) is None
            or timer_id in timer_by_id
            or not isinstance(address, str)
            or re.fullmatch(timer_address_pattern, address) is None
            or address in timer_addresses
            or timer_id in io_by_id
            or timer.get("instruction") != "TON"
            or type(time_base) is not int
            or time_base not in {1, 10, 100}
            or type(preset) is not int
            or not 1 <= preset <= 65_535
            or timer.get("durationMs") != time_base * preset
        ):
            raise NativeProjectPreconditionError(
                "timer identity, address, TON base, preset, or duration is invalid"
            )
        timer_number = int(address[1:])
        if normalized_series == "XGK":
            expected_base = (
                100
                if timer_number <= 999
                else 10
                if timer_number <= 1499
                else 1
                if timer_number <= 1999
                else None
            )
            timer_supported = expected_base == time_base
        elif normalized_series == "XGB":
            expected_base = 100 if timer_number <= 191 else 10 if timer_number <= 200 else 1
            timer_supported = timer_number <= 255 and expected_base == time_base
        else:
            timer_supported = False
        if not timer_supported:
            raise NativeProjectPreconditionError(
                f"timer {address} does not match the {normalized_series} "
                "new-project default timer area"
            )
        timer_by_id[timer_id] = timer
        timer_addresses.add(address)

    sequence_by_id: dict[str, dict[str, Any]] = {}
    step_by_id: dict[str, tuple[str, str]] = {}
    step_addresses: set[str] = set()
    sequence_groups: set[str] = set()
    for sequence in sequences:
        if not isinstance(sequence, dict):
            raise NativeProjectPreconditionError("sequence must be an object")
        sequence_id = sequence.get("sequenceId")
        steps = sequence.get("steps")
        if (
            not isinstance(sequence_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", sequence_id) is None
            or sequence_id in sequence_by_id
            or sequence.get("restartPolicy") not in {"require_start_release", "continuous"}
            or not isinstance(steps, list)
            or not steps
            or len(steps) > 99
            or (sequence.get("modeId") is not None and sequence.get("modeId") not in mode_by_id)
        ):
            raise NativeProjectPreconditionError("sequence identity, mode, or step list is invalid")
        group: str | None = None
        for step in steps:
            if not isinstance(step, dict):
                raise NativeProjectPreconditionError("sequence step must be an object")
            step_id = step.get("stepId")
            address = step.get("address")
            if (
                not isinstance(step_id, str)
                or re.fullmatch(r"[a-z][a-z0-9_]*", step_id) is None
                or step_id in step_by_id
                or not isinstance(address, str)
                or re.fullmatch(r"S(?:\d{2}|1[01]\d|12[0-7])\.\d{2}", address) is None
                or address in step_addresses
            ):
                raise NativeProjectPreconditionError(
                    "sequence step identity or S address is invalid"
                )
            step_group, step_number = address[1:].split(".", 1)
            if step_number == "00" or (group is not None and step_group != group):
                raise NativeProjectPreconditionError(
                    "sequence steps must share one S group and reserve step 00"
                )
            group = step_group
            step_by_id[step_id] = (sequence_id, address)
            step_addresses.add(address)
        assert group is not None
        if group in sequence_groups:
            raise NativeProjectPreconditionError("one S group may belong to only one sequence")
        sequence_groups.add(group)
        sequence_by_id[sequence_id] = {
            "value": sequence,
            "group": group,
            "resetAddress": f"S{group}.00",
            "restartPolicy": sequence["restartPolicy"],
        }

    condition_sources: dict[str, str] = {
        signal_id: str(point["address"]) for signal_id, point in io_by_id.items()
    }
    condition_sources.update(
        {timer_id: str(timer["address"]) for timer_id, timer in timer_by_id.items()}
    )

    def condition(value: object, *, context: str) -> tuple[str, str, str]:
        if not isinstance(value, dict) or set(value) != {"signalId", "state"}:
            raise NativeProjectPreconditionError(f"{context} condition is invalid")
        signal_id = value.get("signalId")
        state = value.get("state")
        if signal_id not in condition_sources or state not in {"on", "off"}:
            raise NativeProjectPreconditionError(
                f"{context} condition references an unknown signal, timer, or state"
            )
        return str(signal_id), str(state), condition_sources[str(signal_id)]

    def conditions(
        values: object,
        *,
        context: str,
        mode_id: object = None,
        step_id: object = None,
        leading: tuple[str, str, str] | None = None,
        trailing: tuple[tuple[str, str, str], ...] = (),
    ) -> list[tuple[str, str]]:
        if not isinstance(values, list) or not values:
            raise NativeProjectPreconditionError(f"{context} conditions are empty")
        source_conditions = [condition(value, context=context) for value in values]
        source_keys = [
            (signal_id, state)
            for signal_id, state, _operand in source_conditions
        ]
        if len(set(source_keys)) != len(source_keys):
            raise NativeProjectPreconditionError(
                f"{context} contains duplicate signal conditions"
            )
        resolved: list[tuple[str, str, str]] = []
        if leading is not None:
            resolved.append(leading)
        if mode_id is not None:
            if not isinstance(mode_id, str) or mode_id not in mode_activation:
                raise NativeProjectPreconditionError(f"{context} mode is invalid")
            resolved.append(mode_activation[mode_id])
        if step_id is not None:
            if not isinstance(step_id, str) or step_id not in step_by_id:
                raise NativeProjectPreconditionError(f"{context} step is invalid")
            resolved.append((f"step:{step_id}", "on", step_by_id[step_id][1]))
        resolved.extend(source_conditions)
        resolved.extend(trailing)
        canonical: list[tuple[str, str]] = []
        state_by_operand: dict[str, str] = {}
        for _source_id, state, operand in resolved:
            previous = state_by_operand.get(operand)
            if previous is not None and previous != state:
                raise NativeProjectPreconditionError(
                    f"{context} contains contradictory signal conditions"
                )
            if previous is None:
                state_by_operand[operand] = state
                canonical.append((operand, state))
        return canonical

    for timer_id, timer in timer_by_id.items():
        enabled = timer.get("enableAll")
        resolved = conditions(
            enabled,
            context=f"timer {timer_id}",
            mode_id=timer.get("modeId"),
            step_id=timer.get("stepId"),
        )
        if any(operand == timer["address"] for operand, _state in resolved):
            raise NativeProjectPreconditionError(
                "timer enable conditions must not reference the same timer"
            )

    for sequence_id, parsed in sequence_by_id.items():
        sequence = parsed["value"]
        start_conditions = sequence.get("startAll")
        reset_conditions = sequence.get("resetAll")
        conditions(
            start_conditions,
            context=f"sequence {sequence_id} start",
            mode_id=sequence.get("modeId"),
            leading=(
                f"sequence-reset:{sequence_id}",
                "on",
                str(parsed["resetAddress"]),
            ),
        )
        conditions(
            reset_conditions,
            context=f"sequence {sequence_id} reset",
        )
        for step in sequence["steps"]:
            conditions(
                step.get("completeAll"),
                context=f"sequence step {step['stepId']}",
                mode_id=sequence.get("modeId"),
                leading=(
                    f"step:{step['stepId']}",
                    "on",
                    str(step["address"]),
                ),
            )
        if parsed["restartPolicy"] == "require_start_release":
            release_conditions = {
                (
                    str(item["signalId"]),
                    "off" if item["state"] == "on" else "on",
                )
                for item in start_conditions
                if isinstance(item, dict)
            }
            final_conditions = {
                (str(item["signalId"]), str(item["state"]))
                for item in sequence["steps"][-1]["completeAll"]
                if isinstance(item, dict)
            }
            explicit_reset_conditions = {
                (str(item["signalId"]), str(item["state"]))
                for item in reset_conditions
                if isinstance(item, dict)
            }
            if release_conditions.isdisjoint(final_conditions):
                raise NativeProjectPreconditionError(
                    "require_start_release sequence must end with an explicit "
                    "inverse start condition"
                )
            if release_conditions.isdisjoint(explicit_reset_conditions):
                raise NativeProjectPreconditionError(
                    "require_start_release sequence reset must include an inverse start condition"
                )

    process_permits: dict[str, list[tuple[str, str]]] = {}
    rule_ids: set[str] = set()
    safety_scoped_outputs: set[str] = set()
    for rule in safety_rules:
        if not isinstance(rule, dict):
            raise NativeProjectPreconditionError("safety rule must be an object")
        rule_id = rule.get("ruleId")
        classification = rule.get("classification")
        action = rule.get("action")
        affected = rule.get("affectedOutputSignalIds")
        if (
            not isinstance(rule_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", rule_id) is None
            or rule_id in rule_ids
            or not isinstance(affected, list)
            or not affected
            or len(set(affected)) != len(affected)
        ):
            raise NativeProjectPreconditionError("safety rule identity or scope is invalid")
        rule_ids.add(rule_id)
        for output_id in affected:
            point = io_by_id.get(output_id) if isinstance(output_id, str) else None
            if point is None or point.get("kind") not in {
                "digital_output",
                "internal_bit",
            }:
                raise NativeProjectPreconditionError("safety rule must reference a writable output")
            safety_scoped_outputs.add(str(output_id))
        if classification == "process_interlock":
            if action != "deenergize" or rule.get("permitWhen") is None:
                raise NativeProjectPreconditionError(
                    "process interlock requires a deenergize permit"
                )
            permit = condition(
                rule.get("permitWhen"),
                context=f"safety rule {rule_id}",
            )
            for output_id in affected:
                if io_by_id[str(output_id)].get("safeState") != "off":
                    raise NativeProjectPreconditionError(
                        "process-interlock output safeState must be off"
                    )
                process_permits.setdefault(str(output_id), []).append((permit[2], permit[1]))
        elif (
            classification != "external_safety_boundary"
            or action != "external_hardware"
            or rule.get("permitWhen") is not None
        ):
            raise NativeProjectPreconditionError(
                "external safety boundary must remain outside PLC logic"
            )

    alarm_by_id: dict[str, dict[str, Any]] = {}
    for alarm in alarms:
        if not isinstance(alarm, dict):
            raise NativeProjectPreconditionError("alarm must be an object")
        alarm_id = alarm.get("alarmId")
        latch_id = alarm.get("latchSignalId")
        affected = alarm.get("affectedOutputSignalIds")
        latch = io_by_id.get(latch_id) if isinstance(latch_id, str) else None
        if (
            not isinstance(alarm_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", alarm_id) is None
            or alarm_id in alarm_by_id
            or latch is None
            or latch.get("kind") != "internal_bit"
            or not isinstance(affected, list)
            or not affected
            or any(
                not isinstance(output_id, str)
                or output_id not in io_by_id
                or io_by_id[output_id].get("kind") != "digital_output"
                for output_id in affected
            )
            or len(set(affected)) != len(affected)
        ):
            raise NativeProjectPreconditionError(
                "alarm identity, latch, or affected output scope is invalid"
            )
        conditions(
            alarm.get("triggerAll"),
            context=f"alarm {alarm_id}",
            mode_id=alarm.get("modeId"),
            step_id=alarm.get("stepId"),
        )
        for output_id in affected:
            protected = any(
                rule.get("classification") == "process_interlock"
                and isinstance(rule.get("permitWhen"), dict)
                and rule["permitWhen"].get("signalId") == latch_id
                and rule["permitWhen"].get("state") == "off"
                and output_id in rule.get("affectedOutputSignalIds", [])
                for rule in safety_rules
                if isinstance(rule, dict)
            )
            if not protected:
                raise NativeProjectPreconditionError(
                    "every alarm-affected output requires an alarm-off process interlock"
                )
        alarm_by_id[alarm_id] = alarm

    recovery_by_id: dict[str, dict[str, Any]] = {}
    recovered_alarms: set[str] = set()
    recovered_sequences: set[str] = set()
    for recovery in recovery_rules:
        if not isinstance(recovery, dict):
            raise NativeProjectPreconditionError("recovery rule must be an object")
        recovery_id = recovery.get("recoveryId")
        alarm_ids = recovery.get("resetAlarmIds")
        sequence_ids = recovery.get("resetSequenceIds")
        if (
            not isinstance(recovery_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", recovery_id) is None
            or recovery_id in recovery_by_id
            or not isinstance(alarm_ids, list)
            or len(set(alarm_ids)) != len(alarm_ids)
            or any(alarm_id not in alarm_by_id for alarm_id in alarm_ids)
            or not isinstance(sequence_ids, list)
            or len(set(sequence_ids)) != len(sequence_ids)
            or any(sequence_id not in sequence_by_id for sequence_id in sequence_ids)
            or (not alarm_ids and not sequence_ids)
        ):
            raise NativeProjectPreconditionError(
                "recovery identity, alarm reset, or sequence reset scope is invalid"
            )
        recovery_conditions = recovery.get("whenAll")
        conditions(recovery_conditions, context=f"recovery {recovery_id}")
        recovery_pairs = {
            (str(item["signalId"]), str(item["state"]))
            for item in recovery_conditions
            if isinstance(item, dict)
        }
        for sequence_id in sequence_ids:
            sequence = sequence_by_id[sequence_id]["value"]
            if sequence_by_id[sequence_id]["restartPolicy"] != "require_start_release":
                continue
            release_conditions = {
                (
                    str(item["signalId"]),
                    "off" if item["state"] == "on" else "on",
                )
                for item in sequence["startAll"]
                if isinstance(item, dict)
            }
            if release_conditions.isdisjoint(recovery_pairs):
                raise NativeProjectPreconditionError(
                    "recovery for a require_start_release sequence must include "
                    "an inverse start condition"
                )
        recovered_alarms.update(alarm_ids)
        recovered_sequences.update(sequence_ids)
        recovery_by_id[recovery_id] = recovery
    if not set(alarm_by_id).issubset(recovered_alarms):
        raise NativeProjectPreconditionError("every alarm requires an explicit recovery")
    if not set(sequence_by_id).issubset(recovered_sequences):
        raise NativeProjectPreconditionError("every sequence requires an explicit reset")

    network_ids: list[str] = []
    output_ids: list[str] = []
    seen_networks: set[str] = set()
    seen_outputs: set[str] = set()
    for network in networks:
        if not isinstance(network, dict):
            raise NativeProjectPreconditionError("control network must be an object")
        network_id = network.get("networkId")
        output_id = network.get("outputSignalId")
        mode_id = network.get("modeId")
        permits = network.get("permitAll")
        output = io_by_id.get(output_id) if isinstance(output_id, str) else None
        if (
            not isinstance(network_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", network_id) is None
            or network_id in seen_networks
            or not isinstance(output_id, str)
            or output_id in seen_outputs
            or output is None
            or output.get("kind") not in {"digital_output", "internal_bit"}
            or not isinstance(permits, list)
            or not permits
            or (mode_id is not None and mode_id not in mode_by_id)
            or (network.get("stepId") is not None and network.get("stepId") not in step_by_id)
        ):
            raise NativeProjectPreconditionError(
                "control network identity, mode, permits, or output is invalid"
            )
        trailing = tuple(
            (f"safety:{output_id}:{index}", state, operand)
            for index, (operand, state) in enumerate(process_permits.get(str(output_id), ()))
        )
        conditions(
            permits,
            context=f"network {network_id}",
            mode_id=mode_id,
            step_id=network.get("stepId"),
            trailing=trailing,
        )
        seen_networks.add(network_id)
        seen_outputs.add(str(output_id))
        network_ids.append(network_id)
        output_ids.append(str(output_id))

    digital_outputs = {
        signal_id for signal_id, point in io_by_id.items() if point.get("kind") == "digital_output"
    }
    if not digital_outputs.issubset(seen_outputs):
        raise NativeProjectPreconditionError("every digital output requires one control network")
    if (
        collection_scope["safetyRules"]["status"] == "confirmed"
        and not digital_outputs.issubset(safety_scoped_outputs)
    ):
        raise NativeProjectPreconditionError(
            "every digital output requires an explicit safety boundary"
        )

    instructions: list[str] = []
    rung_ids: list[str] = []
    seen_rung_ids: set[str] = set()
    seen_condition_actions: set[str] = set()
    action_counts: dict[tuple[str, str, str], int] = {}
    action_conditions: list[tuple[tuple[str, str, str], dict[str, Any]]] = []

    def resolve_typed_reference(
        reference_type: object,
        reference_id: object,
        *,
        writable: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        if not isinstance(reference_id, str):
            raise NativeProjectPreconditionError("ladder reference identity is invalid")
        if reference_type == "signal":
            point = io_by_id.get(reference_id)
            if point is None:
                raise NativeProjectPreconditionError(
                    f"ladder rung references unknown signal {reference_id!r}"
                )
            if writable and point.get("kind") not in {
                "digital_output",
                "internal_bit",
            }:
                raise NativeProjectPreconditionError(
                    f"ladder action target {reference_id!r} is not writable"
                )
            return str(point["address"]), point
        if reference_type == "timer":
            timer = timer_by_id.get(reference_id)
            if timer is None:
                raise NativeProjectPreconditionError(
                    f"ladder rung references unknown timer {reference_id!r}"
                )
            return str(timer["address"]), timer
        if reference_type == "step":
            step = step_by_id.get(reference_id)
            if step is None:
                raise NativeProjectPreconditionError(
                    f"ladder rung references unknown step {reference_id!r}"
                )
            return str(step[1]), None
        if reference_type == "sequence_reset":
            sequence = sequence_by_id.get(reference_id)
            if sequence is None:
                raise NativeProjectPreconditionError(
                    f"ladder rung references unknown sequence reset {reference_id!r}"
                )
            return str(sequence["resetAddress"]), None
        raise NativeProjectPreconditionError("ladder reference type is invalid")

    def compile_condition(value: object, *, depth: int = 1) -> list[str]:
        if depth > 8 or not isinstance(value, dict):
            raise NativeProjectPreconditionError(
                "ladder condition is invalid or exceeds depth 8"
            )
        kind = value.get("kind")
        if kind == "contact":
            if set(value) != {"kind", "sourceType", "sourceId", "state"}:
                raise NativeProjectPreconditionError("ladder contact is incomplete")
            state = value.get("state")
            if state not in {"on", "off"}:
                raise NativeProjectPreconditionError("ladder contact state is invalid")
            operand, _source = resolve_typed_reference(
                value.get("sourceType"),
                value.get("sourceId"),
            )
            command = "LOAD" if state == "on" else "LOAD NOT"
            return [f"{command} {operand}"]
        if kind not in {"all_of", "any_of"} or set(value) != {"kind", "terms"}:
            raise NativeProjectPreconditionError("ladder composite condition is invalid")
        terms = value.get("terms")
        if not isinstance(terms, list) or not 2 <= len(terms) <= 20:
            raise NativeProjectPreconditionError(
                "ladder composite condition requires 2 to 20 terms"
            )
        relation = "AND" if kind == "all_of" else "OR"
        compiled = compile_condition(terms[0], depth=depth + 1)
        for term in terms[1:]:
            if not isinstance(term, dict):
                raise NativeProjectPreconditionError("ladder condition term is invalid")
            if term.get("kind") == "contact":
                if set(term) != {"kind", "sourceType", "sourceId", "state"}:
                    raise NativeProjectPreconditionError("ladder contact is incomplete")
                state = term.get("state")
                if state not in {"on", "off"}:
                    raise NativeProjectPreconditionError("ladder contact state is invalid")
                operand, _source = resolve_typed_reference(
                    term.get("sourceType"),
                    term.get("sourceId"),
                )
                command = relation if state == "on" else f"{relation} NOT"
                compiled.append(f"{command} {operand}")
            else:
                compiled.extend(compile_condition(term, depth=depth + 1))
                compiled.append(f"{relation} LOAD")
        return compiled

    def compile_action(value: object) -> tuple[str, tuple[str, str, str]]:
        if not isinstance(value, dict):
            raise NativeProjectPreconditionError("ladder action is incomplete")
        instruction = value.get("instruction")
        if instruction == "MOV":
            if set(value) != {
                "instruction",
                "sourceConstant",
                "destinationAddress",
            }:
                raise NativeProjectPreconditionError("MOV action is incomplete")
            source_constant = value.get("sourceConstant")
            destination_address = value.get("destinationAddress")
            if (
                isinstance(source_constant, bool)
                or not isinstance(source_constant, int)
                or not -32_768 <= source_constant <= 32_767
                or not isinstance(destination_address, str)
                or re.fullmatch(r"D\d{5}", destination_address) is None
            ):
                raise NativeProjectPreconditionError("MOV action is invalid")
            return (
                f"MOV {source_constant} {destination_address}",
                ("MOV", "data_register", destination_address),
            )
        if set(value) != {"instruction", "targetType", "targetId"}:
            raise NativeProjectPreconditionError("ladder action is incomplete")
        target_type = value.get("targetType")
        target_id = value.get("targetId")
        allowed_targets = {
            "OUT": {"signal"},
            "SET": {"signal", "step", "sequence_reset"},
            "RST": {"signal"},
            "TON": {"timer"},
        }
        if (
            instruction not in allowed_targets
            or target_type not in allowed_targets[instruction]
            or not isinstance(target_id, str)
        ):
            raise NativeProjectPreconditionError(
                "ladder instruction and typed target are incompatible"
            )
        operand, target = resolve_typed_reference(
            target_type,
            target_id,
            writable=target_type == "signal",
        )
        identity = (str(instruction), str(target_type), target_id)
        if instruction == "TON":
            if not isinstance(target, dict):
                raise NativeProjectPreconditionError("TON target timer is invalid")
            return f"TON {operand} {target['presetTicks']}", identity
        return f"{instruction} {operand}", identity

    for rung in ladder_rungs:
        if not isinstance(rung, dict) or set(rung) != {
            "rungId",
            "description",
            "condition",
            "action",
        }:
            raise NativeProjectPreconditionError("ladder rung is incomplete")
        rung_id = rung.get("rungId")
        description = rung.get("description")
        if (
            not isinstance(rung_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", rung_id) is None
            or rung_id in seen_rung_ids
            or not isinstance(description, str)
            or not description.strip()
            or len(description) > 500
        ):
            raise NativeProjectPreconditionError(
                "ladder rung identity or description is invalid"
            )
        condition_instructions = compile_condition(rung.get("condition"))
        action_instruction, action_identity = compile_action(rung.get("action"))
        condition_action_identity = json.dumps(
            {"condition": rung["condition"], "action": rung["action"]},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if condition_action_identity in seen_condition_actions:
            raise NativeProjectPreconditionError(
                "ladderRungs contain a duplicate condition/action pair"
            )
        seen_condition_actions.add(condition_action_identity)
        action_counts[action_identity] = action_counts.get(action_identity, 0) + 1
        condition_value = rung["condition"]
        assert isinstance(condition_value, dict)
        action_conditions.append((action_identity, condition_value))
        instructions.extend(condition_instructions)
        instructions.append(action_instruction)
        rung_ids.append(rung_id)
        seen_rung_ids.add(rung_id)

    def require_exact_actions(
        instruction: str,
        target_type: str,
        target_ids: set[str],
        *,
        label: str,
    ) -> None:
        actual = {
            target_id
            for action_instruction, action_target_type, target_id in action_counts
            if action_instruction == instruction and action_target_type == target_type
        }
        if actual != target_ids or any(
            action_counts.get((instruction, target_type, target_id)) != 1
            for target_id in target_ids
        ):
            raise NativeProjectPreconditionError(label)

    require_exact_actions(
        "OUT",
        "signal",
        set(output_ids),
        label="every control-network output requires exactly one OUT rung",
    )
    require_exact_actions(
        "TON",
        "timer",
        set(timer_by_id),
        label="every timer requires exactly one TON rung",
    )

    ContactIdentity = tuple[str, str, str]

    def required_contact(value: object) -> ContactIdentity:
        if not isinstance(value, dict) or set(value) != {"signalId", "state"}:
            raise NativeProjectPreconditionError(
                "native design semantic condition is invalid"
            )
        source_id = value.get("signalId")
        state = value.get("state")
        if not isinstance(source_id, str) or state not in {"on", "off"}:
            raise NativeProjectPreconditionError(
                "native design semantic condition identity is invalid"
            )
        source_type = "timer" if source_id in timer_by_id else "signal"
        resolve_typed_reference(source_type, source_id)
        return source_type, source_id, str(state)

    def guarantees(value: dict[str, Any], required: ContactIdentity) -> bool:
        kind = value.get("kind")
        if kind == "contact":
            return (
                value.get("sourceType"),
                value.get("sourceId"),
                value.get("state"),
            ) == required
        terms = value.get("terms")
        if not isinstance(terms, list) or any(not isinstance(term, dict) for term in terms):
            return False
        if kind == "all_of":
            return any(guarantees(term, required) for term in terms)
        if kind == "any_of":
            return all(guarantees(term, required) for term in terms)
        return False

    def mode_requirements(mode_id: object) -> list[ContactIdentity]:
        if mode_id is None:
            return []
        mode = mode_by_id.get(mode_id) if isinstance(mode_id, str) else None
        if mode is None:
            raise NativeProjectPreconditionError("native design mode reference is invalid")
        return [required_contact(mode["activation"])]

    def matching_action(
        identity: tuple[str, str, str],
        requirements: list[ContactIdentity],
    ) -> bool:
        return any(
            candidate == identity
            and all(guarantees(condition_value, required) for required in requirements)
            for candidate, condition_value in action_conditions
        )

    for network in networks:
        output_id = str(network["outputSignalId"])
        requirements = [
            required_contact(value) for value in network["permitAll"]
        ]
        requirements.extend(mode_requirements(network.get("modeId")))
        if network.get("stepId") is not None:
            requirements.append(("step", str(network["stepId"]), "on"))
        requirements.extend(
            required_contact(rule["permitWhen"])
            for rule in safety_rules
            if isinstance(rule, dict)
            and rule.get("classification") == "process_interlock"
            and output_id in rule.get("affectedOutputSignalIds", [])
        )
        if not matching_action(("OUT", "signal", output_id), requirements):
            raise NativeProjectPreconditionError(
                f"OUT rung for network {network['networkId']} does not guarantee its permits"
            )

    for timer_id, timer in timer_by_id.items():
        requirements = [
            required_contact(value) for value in timer["enableAll"]
        ]
        requirements.extend(mode_requirements(timer.get("modeId")))
        if timer.get("stepId") is not None:
            requirements.append(("step", str(timer["stepId"]), "on"))
        if not matching_action(("TON", "timer", timer_id), requirements):
            raise NativeProjectPreconditionError(
                f"TON rung for timer {timer_id} does not guarantee its enable conditions"
            )

    alarm_requirements_by_latch: dict[str, list[list[ContactIdentity]]] = {}
    for alarm_id, alarm in alarm_by_id.items():
        requirements = [
            required_contact(value) for value in alarm["triggerAll"]
        ]
        requirements.extend(mode_requirements(alarm.get("modeId")))
        if alarm.get("stepId") is not None:
            requirements.append(("step", str(alarm["stepId"]), "on"))
        latch_id = str(alarm["latchSignalId"])
        alarm_requirements_by_latch.setdefault(latch_id, []).append(requirements)
        if not matching_action(
            ("SET", "signal", latch_id),
            requirements,
        ):
            raise NativeProjectPreconditionError(
                f"alarm latch SET rung for {alarm_id} does not guarantee its trigger"
            )

    recovery_requirements_by_latch: dict[str, list[list[ContactIdentity]]] = {}
    sequence_action_requirements: dict[
        tuple[str, str, str],
        list[list[ContactIdentity]],
    ] = {}
    for recovery_id, recovery in recovery_by_id.items():
        requirements = [
            required_contact(value) for value in recovery["whenAll"]
        ]
        for alarm_id in recovery["resetAlarmIds"]:
            latch_id = str(alarm_by_id[alarm_id]["latchSignalId"])
            recovery_requirements_by_latch.setdefault(latch_id, []).append(
                requirements
            )
            if not matching_action(("RST", "signal", latch_id), requirements):
                raise NativeProjectPreconditionError(
                    f"recovery RST rung for {recovery_id}/{alarm_id} does not guarantee whenAll"
                )
        for sequence_id in recovery["resetSequenceIds"]:
            recovery_action = ("SET", "sequence_reset", str(sequence_id))
            sequence_action_requirements.setdefault(recovery_action, []).append(
                requirements
            )
            if not matching_action(
                recovery_action,
                requirements,
            ):
                raise NativeProjectPreconditionError(
                    f"recovery SET rung for {recovery_id}/{sequence_id} does not guarantee whenAll"
                )

    for action_identity, condition_value in action_conditions:
        instruction, target_type, target_id = action_identity
        if instruction == "SET" and target_type == "signal":
            owners = alarm_requirements_by_latch.get(target_id, [])
            if not owners or not any(
                all(guarantees(condition_value, required) for required in requirements)
                for requirements in owners
            ):
                raise NativeProjectPreconditionError(
                    "SET signal rung is not bound to a reviewed alarm trigger"
                )
        if instruction == "RST" and target_type == "signal":
            owners = recovery_requirements_by_latch.get(target_id, [])
            if not owners or not any(
                all(guarantees(condition_value, required) for required in requirements)
                for requirements in owners
            ):
                raise NativeProjectPreconditionError(
                    "RST signal rung is not bound to a reviewed recovery rule"
                )

    for sequence_id, parsed in sequence_by_id.items():
        sequence = parsed["value"]
        steps = sequence["steps"]
        start_requirements = [
            ("sequence_reset", sequence_id, "on"),
            *mode_requirements(sequence.get("modeId")),
            *(required_contact(value) for value in sequence["startAll"]),
        ]
        if not matching_action(
            ("SET", "step", str(steps[0]["stepId"])),
            start_requirements,
        ):
            raise NativeProjectPreconditionError(
                f"sequence start rung for {sequence_id} does not guarantee startAll"
            )
        sequence_action_requirements.setdefault(
            ("SET", "step", str(steps[0]["stepId"])),
            [],
        ).append(start_requirements)
        for index, step in enumerate(steps):
            action_identity = (
                ("SET", "step", str(steps[index + 1]["stepId"]))
                if index + 1 < len(steps)
                else ("SET", "sequence_reset", sequence_id)
            )
            advance_requirements = [
                ("step", str(step["stepId"]), "on"),
                *mode_requirements(sequence.get("modeId")),
                *(required_contact(value) for value in step["completeAll"]),
            ]
            if not matching_action(action_identity, advance_requirements):
                raise NativeProjectPreconditionError(
                    f"sequence advance rung for {step['stepId']} does not guarantee completeAll"
                )
            sequence_action_requirements.setdefault(action_identity, []).append(
                advance_requirements
            )
        reset_requirements = [
            required_contact(value) for value in sequence["resetAll"]
        ]
        if not matching_action(
            ("SET", "sequence_reset", sequence_id),
            reset_requirements,
        ):
            raise NativeProjectPreconditionError(
                f"sequence reset rung for {sequence_id} does not guarantee resetAll"
            )
        sequence_action_requirements.setdefault(
            ("SET", "sequence_reset", sequence_id),
            [],
        ).append(reset_requirements)

    for action_identity, condition_value in action_conditions:
        instruction, target_type, _target_id = action_identity
        if instruction != "SET" or target_type not in {"step", "sequence_reset"}:
            continue
        owners = sequence_action_requirements.get(action_identity, [])
        if not owners or not any(
            all(guarantees(condition_value, required) for required in requirements)
            for requirements in owners
        ):
            raise NativeProjectPreconditionError(
                "SET step or sequence-reset rung is not bound to reviewed sequence logic"
            )

    instructions.append("END")
    source = "\n".join(instructions)
    ladder_ir = {
        "schemaVersion": "controlstudio-ld-rung-ir/v1",
        "rungs": json.loads(
            json.dumps(ladder_rungs, ensure_ascii=False, separators=(",", ":"))
        ),
    }
    ladder_ir_sha256 = hashlib.sha256(
        json.dumps(
            ladder_ir,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schemaVersion": NATIVE_COMPILED_PROGRAM_SCHEMA,
        "sourceLanguage": "XG5000_MNEMONIC",
        "targetLanguage": "LD",
        "programName": program_name,
        "source": source,
        "sourceSha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "rungIds": rung_ids,
        "ladderIr": ladder_ir,
        "ladderIrSha256": ladder_ir_sha256,
        "networkIds": network_ids,
        "outputSignalIds": output_ids,
        "timerIds": list(timer_by_id),
        "sequenceIds": list(sequence_by_id),
        "sequenceRestartPolicies": {
            sequence_id: parsed["restartPolicy"] for sequence_id, parsed in sequence_by_id.items()
        },
        "stepIds": list(step_by_id),
        "alarmIds": list(alarm_by_id),
        "recoveryIds": list(recovery_by_id),
        "collectionScope": collection_scope,
    }


def _native_build_bundle(spec: NativeProjectSpec) -> dict[str, Any]:
    if not isinstance(spec.planning_artifact, dict):
        raise NativeProjectPreconditionError("reviewed engineering plan artifact is required")
    if spec.plc_series.strip().upper() not in {"XGK", "XGB"}:
        raise NativeProjectPreconditionError(
            "engineered control program phase currently supports XGK or XGB only"
        )
    if spec.language.strip().upper() != "LD":
        raise NativeProjectPreconditionError(
            "engineered control program phase requires an LD program"
        )
    artifact = _json_load(_json_dump(spec.planning_artifact))
    reviewed_plan = _reviewed_plan_payload(spec)
    actual_plan_digest = hashlib.sha256(_json_dump(artifact).encode("utf-8")).hexdigest()
    if actual_plan_digest != reviewed_plan["planDigest"]:
        raise NativeProjectPreconditionError("reviewed engineering plan artifact digest changed")
    sections = artifact.get("sections")
    if not isinstance(sections, list) or not sections:
        raise NativeProjectPreconditionError("reviewed engineering plan sections are missing")
    section_ids = [
        section.get("sectionId") if isinstance(section, dict) else None for section in sections
    ]
    if (
        any(not isinstance(section_id, str) or not section_id for section_id in section_ids)
        or len(set(section_ids)) != len(section_ids)
        or section_ids != reviewed_plan["reviewedSectionIds"]
    ):
        raise NativeProjectPreconditionError("reviewed engineering plan section scope changed")
    try:
        expected_item_ids = project_plan_review_item_ids(artifact)
        attachment_revision_digest = (
            calculate_project_plan_attachment_revision_digest(
                artifact.get("attachments", [])
            )
        )
    except (TypeError, ValueError) as exc:
        raise NativeProjectPreconditionError(
            "reviewed engineering plan semantic authority is invalid"
        ) from exc
    if expected_item_ids != reviewed_plan["reviewedItemIds"]:
        raise NativeProjectPreconditionError(
            "reviewed engineering plan item authority changed"
        )
    if attachment_revision_digest != reviewed_plan["attachmentRevisionDigest"]:
        raise NativeProjectPreconditionError(
            "reviewed engineering plan attachment revisions changed"
        )
    completeness = artifact.get("completeness")
    if (
        artifact.get("schemaVersion") != "2"
        or artifact.get("status") != "ready_for_review"
        or artifact.get("projectName") != spec.project_name
        or artifact.get("openDecisions") != []
        or not isinstance(completeness, dict)
        or completeness.get("blocking") != 0
    ):
        raise NativeProjectPreconditionError(
            "engineering plan is not ready for deterministic project creation"
        )
    reviewed_technical_configuration = {
        "plcSeries": spec.plc_series,
        "cpuModel": spec.cpu_model,
        "programName": spec.program_name,
        "language": spec.language,
        "description": spec.description,
    }
    if artifact.get("technicalConfiguration") != reviewed_technical_configuration:
        raise NativeProjectPreconditionError(
            "native project technical configuration does not match the reviewed plan"
        )
    compiled_program = _compile_native_program(
        artifact,
        program_name=spec.program_name,
        plc_series=spec.plc_series,
    )
    payload = {
        "schemaVersion": NATIVE_BUILD_BUNDLE_SCHEMA,
        "phase": NATIVE_BUILD_BUNDLE_PHASE,
        "reviewedPlan": reviewed_plan,
        "engineeringPlan": artifact,
        "technicalConfiguration": {
            "projectName": spec.project_name,
            **reviewed_technical_configuration,
        },
        "compiledProgram": compiled_program,
    }
    bundle_id = hashlib.sha256(_json_dump(payload).encode("utf-8")).hexdigest()
    return {
        "schemaVersion": NATIVE_BUILD_BUNDLE_SCHEMA,
        "bundleId": bundle_id,
        "phase": NATIVE_BUILD_BUNDLE_PHASE,
        "reviewedPlan": reviewed_plan,
        "engineeringPlan": artifact,
        "technicalConfiguration": payload["technicalConfiguration"],
        "compiledProgram": compiled_program,
    }


def _native_bundle_description(build_bundle: dict[str, Any]) -> str:
    reviewed_plan = build_bundle.get("reviewedPlan")
    technical = build_bundle.get("technicalConfiguration")
    if not isinstance(reviewed_plan, dict) or not isinstance(technical, dict):
        raise NativeProjectPreconditionError("native build bundle is incomplete")
    plan_digest = str(reviewed_plan.get("planDigest") or "")
    bundle_id = str(build_bundle.get("bundleId") or "")
    description = str(technical.get("description") or "").strip()
    marker = f"[GIDONG plan={plan_digest[:12]} bundle={bundle_id[:12]}]"
    effective = f"{description}\n{marker}" if description else marker
    if len(effective) > 1024:
        raise NativeProjectPreconditionError(
            "description leaves no room for the reviewed-plan provenance marker"
        )
    return effective


class NativeProjectRepository:
    """Durable idempotency and crash quarantine for native UI creation."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS native_project_operations (
                    id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'ALLOCATED', 'EXECUTING', 'COMPLETED',
                            'FAILED', 'QUARANTINED'
                        )
                    ),
                    request_digest TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    plan_digest TEXT,
                    destination_directory TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    error_detail TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_native_project_operations_state
                ON native_project_operations(state, updated_at)
                """
            )
            self._migrate_legacy_simulation_trials(connection)

    @staticmethod
    def _migrate_legacy_simulation_trials(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT id, operation_id, actor_id, request_json, result_json,
                   completed_at, updated_at
            FROM native_project_operations
            WHERE state = 'COMPLETED'
            """
        ).fetchall()
        for row in rows:
            result = _json_load(str(row["result_json"]))
            request = _json_load(str(row["request_json"]))
            simulation = result.get("simulationVerification")
            if not isinstance(simulation, dict) or isinstance(simulation.get("trial"), dict):
                continue
            receipt = simulation.get("receipt")
            if (
                simulation.get("state") != "verified"
                or not isinstance(receipt, dict)
                or receipt.get("schemaVersion")
                not in {
                    PREVIOUS_NATIVE_SIMULATION_RECEIPT_SCHEMA,
                    NATIVE_SIMULATION_RECEIPT_SCHEMA,
                }
                or receipt.get("status") != "SIMULATION_VERIFIED"
                or receipt.get("controllerConnected") is not False
                or receipt.get("plcWritePerformed") is not False
            ):
                continue
            simulator = receipt.get("simulator")
            boundary = receipt.get("inputWriteBoundary")
            restore = boundary.get("restore") if isinstance(boundary, dict) else None
            if (
                not isinstance(simulator, dict)
                or simulator.get("cpuStopped") is not True
                or not isinstance(boundary, dict)
                or boundary.get("baselineRestoredAfterCpuStop") is not True
                or not isinstance(restore, dict)
                or restore.get("matched") is not True
            ):
                continue
            receipt_digest = _json_digest(receipt)
            decided_at = str(row["completed_at"] or row["updated_at"])
            scenario_receipt = receipt.get("scenarioReceipt")
            scenario_coverage = (
                scenario_receipt.get("coverage")
                if isinstance(scenario_receipt, dict)
                else None
            )
            build_bundle = request.get("buildBundle")
            compiled_program = (
                build_bundle.get("compiledProgram")
                if isinstance(build_bundle, dict)
                else None
            )
            compiled_scope = (
                compiled_program.get("collectionScope")
                if isinstance(compiled_program, dict)
                else None
            )
            receipt_scope = (
                scenario_coverage.get("collectionScope")
                if isinstance(scenario_coverage, dict)
                else None
            )
            if (
                not isinstance(compiled_scope, dict)
                or set(compiled_scope) != set(_NATIVE_COLLECTION_FIELDS)
                or not isinstance(receipt_scope, dict)
                or set(receipt_scope) != set(_NATIVE_COLLECTION_FIELDS)
                or receipt_scope
                != {
                    name: decision.get("status")
                    for name, decision in compiled_scope.items()
                    if isinstance(decision, dict)
                    and decision.get("source") == "explicit"
                    and decision.get("status") in {"confirmed", "not_applicable"}
                }
            ):
                continue
            observed_at = (
                str(scenario_receipt.get("verifiedAt"))
                if isinstance(scenario_receipt, dict) and scenario_receipt.get("verifiedAt")
                else decided_at
            )
            simulation["trial"] = {
                "schemaVersion": NATIVE_SIMULATION_TRIAL_SCHEMA,
                "trialId": _native_simulation_trial_id(
                    str(row["operation_id"]), str(receipt.get("buildBundleId") or "")
                ),
                "state": "ACCEPTED",
                "receiptDigest": receipt_digest,
                "observedAt": observed_at,
                "safeRestorationVerified": True,
                "decision": {
                    "outcome": "ACCEPTED",
                    "actorId": str(row["actor_id"]),
                    "decidedAt": decided_at,
                    "receiptDigest": receipt_digest,
                    "migratedFrom": "pre-trial-lifecycle",
                },
            }
            connection.execute(
                "UPDATE native_project_operations SET result_json = ? WHERE id = ?",
                (_json_dump(result), str(row["id"])),
            )

    def allocate(
        self,
        *,
        operation_id: str,
        request_digest: str,
        actor_id: str,
        request: dict[str, Any],
        destination_directory: str,
    ) -> tuple[NativeProjectOperation, bool]:
        operation_id = _canonical_uuid(operation_id, "operation_id")
        timestamp = _utc_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT * FROM native_project_operations
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["request_digest"]) != request_digest
                        or str(existing["actor_id"]) != actor_id
                    ):
                        raise NativeProjectConflict(
                            "operationId is already bound to another actor or request"
                        )
                    connection.rollback()
                    return self._from_row(existing), False
                reviewed_plan = request.get("reviewedPlan")
                planning_conversation_id = (
                    reviewed_plan.get("conversationId")
                    if isinstance(reviewed_plan, dict)
                    else None
                )
                if (
                    not isinstance(planning_conversation_id, str)
                    or not planning_conversation_id.strip()
                ):
                    raise NativeProjectConflict(
                        "native project planning conversation identity is missing"
                    )
                completed_handoff = connection.execute(
                    """
                    SELECT handoff_native_project_id
                    FROM conversations
                    WHERE lineage_planning_conversation_id = ?
                    LIMIT 1
                    """,
                    (planning_conversation_id,),
                ).fetchone()
                if completed_handoff is not None:
                    raise NativeProjectConflict(
                        "planning conversation already has an engineering handoff"
                    )
                lineage_rows = connection.execute(
                    """
                    SELECT operation_id, state, request_json, result_json
                    FROM native_project_operations
                    WHERE state IN (
                        'ALLOCATED', 'EXECUTING', 'QUARANTINED', 'COMPLETED'
                    )
                    ORDER BY created_at ASC, operation_id ASC
                    """
                ).fetchall()
                for lineage_row in lineage_rows:
                    lineage_request = _json_load(str(lineage_row["request_json"]))
                    lineage_review = lineage_request.get("reviewedPlan")
                    if (
                        not isinstance(lineage_review, dict)
                        or lineage_review.get("conversationId")
                        != planning_conversation_id
                    ):
                        continue
                    lineage_state = str(lineage_row["state"])
                    blocks_new_creation = lineage_state in {
                        "ALLOCATED",
                        "EXECUTING",
                        "QUARANTINED",
                    }
                    if lineage_state == "COMPLETED":
                        lineage_result = _json_load(str(lineage_row["result_json"]))
                        lineage_simulation = lineage_result.get("simulationVerification")
                        lineage_trial = (
                            lineage_simulation.get("trial")
                            if isinstance(lineage_simulation, dict)
                            else None
                        )
                        # Only an explicit safe revert/quarantine acknowledgement
                        # releases a planning lineage for a new operation. Unknown
                        # legacy terminal outcomes fail closed against duplication.
                        blocks_new_creation = not (
                            isinstance(lineage_trial, dict)
                            and lineage_trial.get("state")
                            in {"REVERTED", "QUARANTINE_ACKNOWLEDGED"}
                        )
                    if blocks_new_creation:
                        raise NativeProjectConflict(
                            "planning conversation already owns native project operation "
                            f"{lineage_row['operation_id']} ({lineage_state.casefold()})"
                        )
                connection.execute(
                    """
                    INSERT INTO native_project_operations(
                        id, operation_id, state, request_digest, actor_id,
                        request_json, plan_digest, destination_directory,
                        result_json, error_code, error_detail,
                        created_at, updated_at, completed_at
                    )
                    VALUES (?, ?, 'ALLOCATED', ?, ?, ?, NULL, ?, '{}',
                            NULL, NULL, ?, ?, NULL)
                    """,
                    (
                        operation_id,
                        operation_id,
                        request_digest,
                        actor_id,
                        _json_dump(request),
                        destination_directory,
                        timestamp,
                        timestamp,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        return self._from_row(row), True

    def set_plan_digest(
        self,
        operation_id: str,
        plan_digest: str,
    ) -> NativeProjectOperation:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise NativeProjectConflict("native project operation not found")
                existing = row["plan_digest"]
                if existing is not None and str(existing) != plan_digest:
                    raise NativeProjectConflict(
                        "native project plan digest changed after allocation"
                    )
                if str(row["state"]) != "ALLOCATED":
                    if existing == plan_digest:
                        connection.rollback()
                        return self._from_row(row)
                    raise NativeProjectConflict(
                        "native project plan cannot change after execution begins"
                    )
                timestamp = _utc_text(_utc_now())
                connection.execute(
                    """
                    UPDATE native_project_operations
                    SET plan_digest = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (plan_digest, timestamp, operation_id),
                )
                updated = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._from_row(updated)

    def begin_execution(self, operation_id: str) -> bool:
        timestamp = _utc_text(_utc_now())
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE native_project_operations
                SET state = 'EXECUTING', updated_at = ?
                WHERE id = ? AND state = 'ALLOCATED' AND plan_digest IS NOT NULL
                """,
                (timestamp, operation_id),
            )
            return cursor.rowcount == 1

    def begin_resume(self, operation_id: str) -> bool:
        """Claim one quarantined post-create operation for its verification tail."""

        timestamp = _utc_text(_utc_now())
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE native_project_operations
                SET state = 'EXECUTING', updated_at = ?
                WHERE id = ? AND state = 'QUARANTINED' AND plan_digest IS NOT NULL
                """,
                (timestamp, operation_id),
            )
            return cursor.rowcount == 1

    def begin_completed_verification(self, operation_id: str) -> bool:
        """Re-open only the unbound verification tail of a completed build."""

        timestamp = _utc_text(_utc_now())
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE native_project_operations
                SET state = 'EXECUTING', completed_at = NULL, updated_at = ?
                WHERE id = ? AND state = 'COMPLETED' AND plan_digest IS NOT NULL
                """,
                (timestamp, operation_id),
            )
            return cursor.rowcount == 1

    def checkpoint_execution(
        self,
        operation_id: str,
        result: dict[str, Any],
    ) -> NativeProjectOperation:
        """Persist verified post-create evidence without making it terminal."""

        timestamp = _utc_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE native_project_operations
                    SET result_json = ?, updated_at = ?
                    WHERE id = ? AND state = 'EXECUTING'
                    """,
                    (_json_dump(result), timestamp, operation_id),
                )
                if cursor.rowcount != 1:
                    raise NativeProjectConflict(
                        "native project operation changed before evidence checkpoint"
                    )
                updated = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._from_row(updated)

    def complete(
        self,
        operation_id: str,
        result: dict[str, Any],
    ) -> NativeProjectOperation:
        return self._terminal(
            operation_id,
            expected_state="EXECUTING",
            state="COMPLETED",
            result=result,
            error_code=None,
            error_detail=None,
        )

    def decide_simulation_trial(
        self,
        operation_id: str,
        *,
        decision: str,
        expected_receipt_digest: str | None,
        actor_id: str,
    ) -> tuple[NativeProjectOperation, bool]:
        operation_id = _canonical_uuid(operation_id, "operation_id")
        normalized_decision = {
            "ACCEPT": "ACCEPTED",
            "ACCEPTED": "ACCEPTED",
            "REVERT": "REVERTED",
            "REVERTED": "REVERTED",
            "ACKNOWLEDGE_QUARANTINE": "QUARANTINE_ACKNOWLEDGED",
            "QUARANTINE_ACKNOWLEDGED": "QUARANTINE_ACKNOWLEDGED",
        }.get(decision.strip().upper())
        if normalized_decision is None:
            raise NativeProjectConflict("unsupported simulation trial decision")
        normalized_receipt_digest = (
            expected_receipt_digest.strip().casefold()
            if isinstance(expected_receipt_digest, str)
            else None
        )
        if normalized_decision != "QUARANTINE_ACKNOWLEDGED" and (
            normalized_receipt_digest is None
            or re.fullmatch(r"[0-9a-f]{64}", normalized_receipt_digest) is None
        ):
            raise NativeProjectConflict("simulation receipt digest is invalid")
        if not actor_id.strip():
            raise NativeProjectConflict("simulation trial actor identity is missing")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise NativeProjectConflict("native project operation not found")
                if str(row["state"]) != "COMPLETED":
                    raise NativeProjectConflict(
                        "simulation trial decision requires a completed native project"
                    )
                result = _json_load(str(row["result_json"]))
                simulation = result.get("simulationVerification")
                if not isinstance(simulation, dict):
                    raise NativeProjectConflict("XG-SIM trial evidence is unavailable")
                trial = simulation.get("trial")
                if not isinstance(trial, dict):
                    raise NativeProjectConflict("XG-SIM trial evidence is incomplete")
                request = _json_load(str(row["request_json"]))
                build_bundle = request.get("buildBundle")
                bundle_id = (
                    build_bundle.get("bundleId")
                    if isinstance(build_bundle, dict)
                    else None
                )
                if (
                    not isinstance(bundle_id, str)
                    or not bundle_id
                    or trial.get("trialId")
                    != _native_simulation_trial_id(
                        str(row["operation_id"]),
                        bundle_id,
                    )
                ):
                    raise NativeProjectConflict(
                        "XG-SIM trial identity does not match the native build bundle"
                    )

                current_state = str(trial.get("state") or "")
                if normalized_decision == "QUARANTINE_ACKNOWLEDGED":
                    if current_state == normalized_decision:
                        connection.rollback()
                        return self._from_row(row), False
                    if (
                        trial.get("schemaVersion") != NATIVE_SIMULATION_TRIAL_SCHEMA
                        or current_state != "QUARANTINED"
                        or trial.get("safeRestorationVerified") is not False
                    ):
                        raise NativeProjectConflict(
                            "only an unresolved quarantined XG-SIM trial can be acknowledged"
                        )
                    timestamp = _utc_text(_utc_now())
                    trial["state"] = normalized_decision
                    trial["decision"] = {
                        "outcome": normalized_decision,
                        "actorId": actor_id,
                        "decidedAt": timestamp,
                        "safeRestorationVerified": False,
                    }
                    result["engineeredProgramReady"] = False
                    result["workspaceBound"] = False
                    result["workspaceBindingReason"] = (
                        "The unresolved XG-SIM quarantine was acknowledged without claiming "
                        "CPU stop or input restoration; the workspace remains unbound."
                    )
                    cursor = connection.execute(
                        """
                        UPDATE native_project_operations
                        SET result_json = ?, updated_at = ?
                        WHERE id = ? AND state = 'COMPLETED'
                        """,
                        (_json_dump(result), timestamp, operation_id),
                    )
                    if cursor.rowcount != 1:
                        raise NativeProjectConflict(
                            "native project changed during quarantine acknowledgement"
                        )
                    updated = connection.execute(
                        "SELECT * FROM native_project_operations WHERE id = ?",
                        (operation_id,),
                    ).fetchone()
                    connection.commit()
                    assert updated is not None
                    return self._from_row(updated), True

                if simulation.get("state") != "verified":
                    raise NativeProjectConflict("verified XG-SIM evidence is unavailable")
                receipt = simulation.get("receipt")
                if not isinstance(receipt, dict):
                    raise NativeProjectConflict("XG-SIM trial evidence is incomplete")
                actual_receipt_digest = _json_digest(receipt)
                if (
                    trial.get("schemaVersion") != NATIVE_SIMULATION_TRIAL_SCHEMA
                    or trial.get("receiptDigest") != actual_receipt_digest
                    or normalized_receipt_digest != actual_receipt_digest
                ):
                    raise NativeProjectConflict("XG-SIM receipt digest changed before decision")
                receipt_schema = receipt.get("schemaVersion")
                if (
                    receipt_schema == LEGACY_NATIVE_SIMULATION_RECEIPT_SCHEMA
                    and trial.get("safeRestorationVerified") is not True
                ):
                    raise NativeProjectConflict(
                        "legacy v3 XG-SIM restoration evidence is not verified"
                    )
                if (
                    receipt_schema == LEGACY_NATIVE_SIMULATION_RECEIPT_SCHEMA
                    and current_state == "OBSERVING"
                    and normalized_decision != "REVERTED"
                ):
                    raise NativeProjectConflict(
                        "legacy v3 XG-SIM observations may only be explicitly reverted"
                    )
                simulator = receipt.get("simulator")
                boundary = receipt.get("inputWriteBoundary")
                restore = boundary.get("restore") if isinstance(boundary, dict) else None
                if (
                    receipt_schema
                    not in {
                        LEGACY_NATIVE_SIMULATION_RECEIPT_SCHEMA,
                        PREVIOUS_NATIVE_SIMULATION_RECEIPT_SCHEMA,
                        NATIVE_SIMULATION_RECEIPT_SCHEMA,
                    }
                    or receipt.get("status") != "SIMULATION_VERIFIED"
                    or not isinstance(simulator, dict)
                    or not _native_simulator_evidence_verified(simulator)
                    or simulator.get("cpuStopped") is not True
                    or not isinstance(boundary, dict)
                    or boundary.get("baselineRestoredAfterCpuStop") is not True
                    or not isinstance(restore, dict)
                    or restore.get("attempted") is not True
                    or restore.get("matched") is not True
                    or (
                        receipt_schema == LEGACY_NATIVE_SIMULATION_RECEIPT_SCHEMA
                        and restore.get("cpuStoppedBeforeRestore") is not True
                    )
                    or receipt.get("controllerConnected") is not False
                    or receipt.get("plcWritePerformed") is not False
                ):
                    raise NativeProjectConflict(
                        "XG-SIM trial cannot be resolved without verified safe restoration"
                    )

                if current_state == normalized_decision:
                    connection.rollback()
                    return self._from_row(row), False
                if current_state != "OBSERVING":
                    raise NativeProjectConflict(
                        f"XG-SIM trial is already {current_state.casefold() or 'invalid'}"
                    )

                timestamp = _utc_text(_utc_now())
                trial["state"] = normalized_decision
                trial["decision"] = {
                    "outcome": normalized_decision,
                    "actorId": actor_id,
                    "decidedAt": timestamp,
                    "receiptDigest": actual_receipt_digest,
                }
                accepted = normalized_decision == "ACCEPTED"
                if accepted:
                    reopen = result.get("reopenVerification")
                    registration = result.get("workspaceRegistration")
                    if (
                        not isinstance(reopen, dict)
                        or reopen.get("state") != "verified"
                        or not isinstance(registration, dict)
                        or registration.get("state") == "capture_failed"
                    ):
                        raise NativeProjectConflict(
                            "verified reopened workspace is unavailable for acceptance"
                        )
                    registration["state"] = "captured_and_bound"
                    registration["detail"] = (
                        "The reviewed XG-SIM receipt was explicitly accepted and the "
                        "independently reopened protected copy is the active target."
                    )
                    result["workspaceBindingReason"] = (
                        "The exact reviewed build, independent reopen, XG-SIM scenarios, "
                        "CPU stop, and input restoration were verified and explicitly accepted."
                    )
                else:
                    registration = result.get("workspaceRegistration")
                    if (
                        isinstance(registration, dict)
                        and registration.get("state") != "capture_failed"
                    ):
                        registration["state"] = "captured_not_bound"
                        registration["detail"] = (
                            "The XG-SIM receipt was reverted after the simulator CPU had "
                            "already stopped and its original input bytes were restored."
                        )
                    result["workspaceBindingReason"] = (
                        "The XG-SIM observation was reverted; no active engineering target "
                        "was adopted and simulator restoration remains verified."
                    )
                result["engineeredProgramReady"] = accepted
                result["workspaceBound"] = accepted
                cursor = connection.execute(
                    """
                    UPDATE native_project_operations
                    SET result_json = ?, updated_at = ?
                    WHERE id = ? AND state = 'COMPLETED'
                    """,
                    (_json_dump(result), timestamp, operation_id),
                )
                if cursor.rowcount != 1:
                    raise NativeProjectConflict("native project changed during trial decision")
                updated = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._from_row(updated), True

    def fail_allocated(
        self,
        operation_id: str,
        *,
        error_code: str,
        error_detail: str,
    ) -> NativeProjectOperation:
        return self._terminal(
            operation_id,
            expected_state="ALLOCATED",
            state="FAILED",
            result={},
            error_code=error_code,
            error_detail=error_detail,
        )

    def quarantine(
        self,
        operation_id: str,
        *,
        error_code: str,
        error_detail: str,
        result: dict[str, Any] | None = None,
    ) -> NativeProjectOperation:
        return self._terminal(
            operation_id,
            expected_state="EXECUTING",
            state="QUARANTINED",
            result={} if result is None else result,
            error_code=error_code,
            error_detail=error_detail,
        )

    def fail_executing(
        self,
        operation_id: str,
        *,
        error_code: str,
        error_detail: str,
    ) -> NativeProjectOperation:
        return self._terminal(
            operation_id,
            expected_state="EXECUTING",
            state="FAILED",
            result={},
            error_code=error_code,
            error_detail=error_detail,
        )

    def resolve_quarantine(
        self,
        operation_id: str,
        *,
        state: str,
        result: dict[str, Any],
        error_code: str | None,
        error_detail: str | None,
    ) -> NativeProjectOperation:
        if state not in {"COMPLETED", "FAILED", "QUARANTINED"}:
            raise ValueError("unsupported native-project recovery state")
        if state == "QUARANTINED":
            timestamp = _utc_text(_utc_now())
            with self._connection() as connection, connection:
                cursor = connection.execute(
                    """
                    UPDATE native_project_operations
                    SET result_json = ?, error_code = ?, error_detail = ?,
                        updated_at = ?, completed_at = NULL
                    WHERE id = ? AND state = 'QUARANTINED'
                    """,
                    (
                        _json_dump(result),
                        error_code,
                        error_detail,
                        timestamp,
                        operation_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise NativeProjectConflict("native project operation is not quarantined")
                row = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
            assert row is not None
            return self._from_row(row)
        return self._terminal(
            operation_id,
            expected_state="QUARANTINED",
            state=state,
            result=result,
            error_code=error_code,
            error_detail=error_detail,
        )

    def reconcile_interrupted(self) -> int:
        timestamp = _utc_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            allocated = connection.execute(
                """
                UPDATE native_project_operations
                SET state = 'FAILED',
                    error_code = 'HOST_RESTARTED_BEFORE_NATIVE_PROJECT_EXECUTION',
                    error_detail = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE state = 'ALLOCATED'
                """,
                (
                    (
                        "Host restarted before native UI execution began; "
                        "no mutation was dispatched."
                    ),
                    timestamp,
                    timestamp,
                ),
            )
            executing = connection.execute(
                """
                UPDATE native_project_operations
                SET state = 'QUARANTINED',
                    error_code = 'HOST_RESTARTED_DURING_NATIVE_PROJECT_CREATION',
                    error_detail = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE state = 'EXECUTING'
                """,
                (
                    (
                        "Host restarted after native UI execution began; "
                        "the operation will not be retried automatically."
                    ),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            return allocated.rowcount + executing.rowcount

    def get(self, operation_id: str) -> NativeProjectOperation | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM native_project_operations WHERE id = ?",
                (operation_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def engineering_handoff_candidates(self) -> list[NativeProjectOperation]:
        """Return explicit accepted operations that may need a Host handoff.

        Conversation/session state is owned by ``HostStore`` and is deliberately
        not inferred here.  The repository only exposes exact durable native
        acceptance candidates in deterministic creation order.
        """

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM native_project_operations
                WHERE state = 'COMPLETED'
                ORDER BY created_at ASC, operation_id ASC
                """
            ).fetchall()
        candidates: list[NativeProjectOperation] = []
        for row in rows:
            operation = self._from_row(row)
            result = operation.result
            reopen_verification = result.get("reopenVerification")
            reopen_receipt = (
                reopen_verification.get("receipt")
                if isinstance(reopen_verification, dict)
                else None
            )
            reopen_project = (
                reopen_receipt.get("projectReadback")
                if isinstance(reopen_receipt, dict)
                else None
            )
            reviewed_plan = operation.request.get("reviewedPlan")
            build_bundle = operation.request.get("buildBundle")
            bundle_id = (
                build_bundle.get("bundleId")
                if isinstance(build_bundle, dict)
                else None
            )
            workspace_registration = result.get("workspaceRegistration")
            workspace = (
                workspace_registration.get("workspace")
                if isinstance(workspace_registration, dict)
                else None
            )
            working_copy = (
                workspace.get("working_copy") if isinstance(workspace, dict) else None
            )
            if (
                result.get("engineeredProgramReady") is not True
                or result.get("workspaceBound") is not True
                or result.get("identityVerified") is not True
                or result.get("controllerConnected") is not False
                or result.get("plcWritePerformed") is not False
                or not isinstance(reopen_verification, dict)
                or reopen_verification.get("state") != "verified"
                or not isinstance(reopen_receipt, dict)
                or reopen_receipt.get("schemaVersion")
                != "controlstudio-native-reopen-receipt/v2"
                or reopen_receipt.get("status") != "REOPEN_VERIFIED"
                or reopen_receipt.get("buildBundleId") != bundle_id
                or not isinstance(reviewed_plan, dict)
                or not isinstance(reviewed_plan.get("conversationId"), str)
                or not str(reviewed_plan.get("conversationId") or "").strip()
                or reopen_receipt.get("reviewedPlanDigest")
                != reviewed_plan.get("planDigest")
                or not isinstance(reopen_project, dict)
                or reopen_project.get("projectSha256") != result.get("projectSha256")
                or reopen_project.get("projectUnchanged") is not True
                or not isinstance(workspace_registration, dict)
                or workspace_registration.get("state") != "captured_and_bound"
                or not isinstance(workspace, dict)
                or not isinstance(working_copy, dict)
                or validated_new_build_workspace_provenance(
                    operation,
                    workspace,
                    require_capture_verification=True,
                )
                is None
            ):
                continue
            candidates.append(operation)
        return candidates

    def blocking_operation(
        self,
        selected_operation_id: str | None,
    ) -> NativeProjectOperation | None:
        """Return only the recovery authority selected by ``HostStore``."""

        if selected_operation_id is None:
            return None
        operation = self.get(selected_operation_id)
        if operation is None:
            return None
        if operation.state in {"EXECUTING", "QUARANTINED"}:
            return operation
        return None

    def _terminal(
        self,
        operation_id: str,
        *,
        expected_state: str,
        state: str,
        result: dict[str, Any],
        error_code: str | None,
        error_detail: str | None,
    ) -> NativeProjectOperation:
        timestamp = _utc_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE native_project_operations
                    SET state = ?, result_json = ?, error_code = ?,
                        error_detail = ?, updated_at = ?, completed_at = ?
                    WHERE id = ? AND state = ?
                    """,
                    (
                        state,
                        _json_dump(result),
                        error_code,
                        error_detail,
                        timestamp,
                        timestamp,
                        operation_id,
                        expected_state,
                    ),
                )
                if cursor.rowcount != 1:
                    raise NativeProjectConflict(
                        f"native project operation is not in {expected_state}"
                    )
                updated = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._from_row(updated)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> NativeProjectOperation:
        return NativeProjectOperation(
            id=str(row["id"]),
            operation_id=str(row["operation_id"]),
            state=str(row["state"]),
            request_digest=str(row["request_digest"]),
            actor_id=str(row["actor_id"]),
            request=_json_load(str(row["request_json"])),
            plan_digest=(str(row["plan_digest"]) if row["plan_digest"] is not None else None),
            destination_directory=str(row["destination_directory"]),
            result=_json_load(str(row["result_json"])),
            error_code=(str(row["error_code"]) if row["error_code"] is not None else None),
            error_detail=(str(row["error_detail"]) if row["error_detail"] is not None else None),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            completed_at=(
                datetime.fromisoformat(str(row["completed_at"]))
                if row["completed_at"] is not None
                else None
            ),
        )


class NativeProjectService:
    """Host-owned, idempotent native XG5000 project creation path."""

    def __init__(
        self,
        repository: NativeProjectRepository,
        gateway: NativeProjectGateway,
    ) -> None:
        self._repository = repository
        self._gateway = gateway

    @staticmethod
    def build_bundle(spec: NativeProjectSpec) -> dict[str, Any]:
        return _native_build_bundle(NativeProjectService._canonicalize_local_request(spec))

    def replay_if_exists(
        self,
        spec: NativeProjectSpec,
        *,
        actor_id: str,
    ) -> NativeProjectOperation | None:
        canonical = self._canonicalize_local_request(spec)
        operation_id = canonical.operation_id
        existing = self._repository.get(operation_id)
        if existing is None:
            return None
        supplied = self._request_payload(
            canonical,
            self._destination_directory(canonical),
        )
        supplied.pop("destinationDirectory", None)
        stored = dict(existing.request)
        stored.pop("destinationDirectory", None)
        if stored != supplied or existing.actor_id != actor_id:
            raise NativeProjectConflict("operationId is already bound to another actor or request")
        return existing

    async def create(
        self,
        spec: NativeProjectSpec,
        *,
        actor_id: str,
    ) -> tuple[NativeProjectOperation, bool]:
        canonical = self._canonicalize_local_request(spec)
        destination = self._destination_directory(canonical)
        request_payload = self._request_payload(canonical, destination)
        request_digest = hashlib.sha256(_json_dump(request_payload).encode("utf-8")).hexdigest()
        existing = self._repository.get(canonical.operation_id)
        if existing is not None:
            if existing.request_digest != request_digest or existing.actor_id != actor_id:
                raise NativeProjectConflict(
                    "operationId is already bound to another actor or request"
                )
            return existing, False

        await self._verify_exact_instance(canonical)
        operation, created = self._repository.allocate(
            operation_id=canonical.operation_id,
            request_digest=request_digest,
            actor_id=actor_id,
            request=request_payload,
            destination_directory=str(destination),
        )
        if not created and operation.state != "ALLOCATED":
            return operation, False

        try:
            self._ensure_allocated_directory(destination, allow_existing=not created)
            build_bundle = _native_build_bundle(canonical)
            effective_description = _native_bundle_description(build_bundle)
            native_request = _validate_native_project_request(
                _NativeProjectRequest(
                    project_name=canonical.project_name,
                    destination_directory=destination,
                    plc_series=canonical.plc_series,
                    cpu_type=canonical.cpu_model,
                    program_name=canonical.program_name,
                    program_language=canonical.language,
                    description=effective_description,
                )
            )
            _require_empty_native_project_destination(
                native_request.destination_directory
            )
            plan_digest = _native_project_plan_digest(
                native_request,
                pid=canonical.xg5000_pid,
                hwnd=canonical.xg5000_hwnd,
                build_bundle=build_bundle,
            )
            operation = self._repository.set_plan_digest(
                canonical.operation_id,
                plan_digest,
            )
        except BaseException as exc:
            current = self._repository.get(canonical.operation_id)
            if current is None:
                raise NativeProjectConflict(
                    "native project operation disappeared during preparation"
                ) from exc
            if current.state == "ALLOCATED":
                try:
                    current = self._repository.fail_allocated(
                        canonical.operation_id,
                        error_code="NATIVE_PROJECT_PREPARATION_FAILED",
                        error_detail=str(exc) or type(exc).__name__,
                    )
                except NativeProjectConflict:
                    # A concurrent idempotent request may have atomically begun
                    # execution after this request observed ALLOCATED.
                    current = self._repository.get(canonical.operation_id)
                    if current is None:
                        raise
            if not isinstance(exc, Exception):
                raise
            return current, created

        if not self._repository.begin_execution(canonical.operation_id):
            current = self._repository.get(canonical.operation_id)
            if current is None:
                raise NativeProjectConflict("native project operation disappeared")
            return current, False

        try:
            authorization = self._authorization(
                actor_id=actor_id,
                plan_digest=plan_digest,
                build_bundle_id=str(build_bundle["bundleId"]),
            )
            arguments = {
                "pid": canonical.xg5000_pid,
                "hwnd": canonical.xg5000_hwnd,
                "destination_directory": str(destination),
                "project_name": canonical.project_name,
                "plc_series": canonical.plc_series,
                "cpu_type": canonical.cpu_model,
                "program_name": canonical.program_name,
                "program_language": canonical.language,
                "description": effective_description,
                "build_bundle": build_bundle,
                "timeout_seconds": 60.0,
                "authorization": authorization,
            }
        except BaseException as exc:
            # Signing and argument construction happen before the MCP coroutine is
            # entered, so no XG5000 mutation can have been dispatched. Persist a
            # terminal non-mutation outcome instead of stranding EXECUTING.
            operation = self._repository.fail_executing(
                canonical.operation_id,
                error_code="NATIVE_PROJECT_PRE_DISPATCH_PREPARATION_FAILED",
                error_detail=str(exc) or type(exc).__name__,
            )
            if not isinstance(exc, Exception):
                raise
            return operation, created
        try:
            raw = await self._gateway.call_workspace_tool(
                "project.create_native",
                arguments,
            )
            result = self._validate_creation_result(
                raw,
                spec=canonical,
                destination=destination,
                plan_digest=plan_digest,
                authorization=authorization,
                build_bundle=build_bundle,
            )
        except BaseException as exc:  # Any post-dispatch fault is uncertain.
            error_detail = str(exc) or type(exc).__name__
            if isinstance(exc, MCPToolError) and _native_create_failed_before_confirmation(exc):
                operation = self._repository.fail_executing(
                    canonical.operation_id,
                    error_code="NATIVE_PROJECT_CONFIRMED_NOT_CREATED",
                    error_detail=error_detail,
                )
            else:
                operation = self._repository.quarantine(
                    canonical.operation_id,
                    error_code=(
                        "NATIVE_PROJECT_MCP_REJECTED"
                        if isinstance(exc, MCPToolError)
                        else "NATIVE_PROJECT_DISPATCH_INTERRUPTED"
                        if not isinstance(exc, Exception)
                        else "NATIVE_PROJECT_EVIDENCE_UNVERIFIED"
                    ),
                    error_detail=error_detail,
                )
            if not isinstance(exc, Exception):
                raise
            return operation, created

        return await self._complete_verified_native_tail(
            canonical,
            build_bundle=build_bundle,
            result=result,
            created=created,
            resume=False,
        )

    async def _complete_verified_native_tail(
        self,
        spec: NativeProjectSpec,
        *,
        build_bundle: dict[str, Any],
        result: dict[str, Any],
        created: bool,
        resume: bool,
        registration: dict[str, Any] | None = None,
        existing_session: dict[str, Any] | None = None,
        reopen_verification: dict[str, Any] | None = None,
        simulation_verification: dict[str, Any] | None = None,
    ) -> tuple[NativeProjectOperation, bool]:
        post_create_stage = "workspace_capture"
        try:
            post_create_stage = "operation_commit"
            self._repository.checkpoint_execution(spec.operation_id, result)
            if registration is None:
                workspace_provenance = _new_build_workspace_provenance(
                    native_project_id=spec.operation_id,
                    build_bundle=build_bundle,
                    build_receipt=result["buildBundleReceipt"],
                    project_sha256=result["projectSha256"],
                )
                registration = await self._capture_workspace(
                    result["projectPath"],
                    result["projectSha256"],
                    provenance=workspace_provenance,
                )
            result["workspaceRegistration"] = registration
            self._repository.checkpoint_execution(spec.operation_id, result)
            post_create_stage = "independent_reopen"
            if reopen_verification is None:
                reopen_verification = await self._verify_fresh_process_reopen(
                    source_pid=spec.xg5000_pid,
                    registration=registration,
                    build_bundle=build_bundle,
                    existing_session=existing_session,
                )
            result["reopenVerification"] = reopen_verification
            self._repository.checkpoint_execution(spec.operation_id, result)
            # Offline engineering acceptance is complete once the exact LD
            # build passed Program Check, was saved, and the protected copy was
            # independently reopened/read back. XG-SIM remains a separate,
            # opt-in future workflow and is never launched from create/resume.
            result["engineeredProgramReady"] = (
                reopen_verification.get("state") == "verified"
            )
            result["workspaceBound"] = result["engineeredProgramReady"]
            if result["workspaceBound"]:
                registration["state"] = "captured_and_bound"
                registration["detail"] = (
                    "The protected working copy is open in the independently verified "
                    "fresh XG5000 process and is the active engineering target."
                )
            result["workspaceBindingReason"] = (
                "The exact reviewed LD build passed Program Check, save, and independent "
                "reopen/readback verification."
                if result["engineeredProgramReady"]
                else "The created source artifact was captured, but independent "
                "fresh-process readback did not complete."
            )
            self._repository.checkpoint_execution(spec.operation_id, result)
            return self._repository.complete(spec.operation_id, result), created
        except BaseException as exc:
            result["engineeredProgramReady"] = False
            result["workspaceBound"] = False
            result["workspaceBindingReason"] = (
                "Verified native resume completed, but post-create verification "
                f"stopped during {post_create_stage}; explicit reconciliation is required."
                if resume
                else "Native project creation completed, but post-create verification "
                f"stopped during {post_create_stage}; explicit reconciliation is required."
            )
            operation = self._repository.quarantine(
                spec.operation_id,
                error_code=(
                    "NATIVE_PROJECT_RESUME_POST_CREATE_VERIFICATION_UNCERTAIN"
                    if resume
                    else "NATIVE_PROJECT_POST_CREATE_VERIFICATION_UNCERTAIN"
                ),
                error_detail=f"{post_create_stage}: {exc}",
                result=result,
            )
            if not isinstance(exc, Exception):
                raise
            return operation, created

    async def _continue_completed_verification(
        self,
        operation: NativeProjectOperation,
        *,
        actor_id: str,
        pid: int,
        hwnd: int,
        expected_request_digest: str,
        reopened_pid: int | None,
        reopened_hwnd: int | None,
    ) -> NativeProjectOperation:
        """Retry only an unbound post-create tail against an existing fresh copy."""

        if operation.request_digest != expected_request_digest:
            raise NativeProjectConflict("native project resume request is stale")
        request = operation.request
        if pid != request.get("xg5000Pid") or hwnd != request.get("xg5000Hwnd"):
            raise NativeProjectConflict("native project resume PID/HWND differs from the stored request")
        build_bundle = request.get("buildBundle")
        if not isinstance(build_bundle, dict) or not isinstance(operation.plan_digest, str):
            raise NativeProjectConflict("completed native project has no stored reviewed build binding")
        try:
            destination = Path(operation.destination_directory).resolve()
            spec = NativeProjectSpec(
                operation_id=operation.operation_id,
                project_name=str(request["projectName"]),
                workspace_parent=Path(str(request["workspaceParent"])),
                plc_series=str(request["plcSeries"]),
                cpu_model=str(request["cpuModel"]),
                program_name=str(request["programName"]),
                language=str(request["language"]),
                description=str(request["description"]),
                xg5000_pid=pid,
                xg5000_hwnd=hwnd,
            )
            expected_path = (destination / f"{spec.project_name}.xgwx").resolve()
            native_request = _NativeProjectRequest(
                project_name=spec.project_name,
                destination_directory=destination,
                plc_series=spec.plc_series,
                cpu_type=spec.cpu_model,
                program_name=spec.program_name,
                program_language=spec.language,
                description=_native_bundle_description(build_bundle),
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise NativeProjectConflict("stored completed native-project binding is invalid") from exc
        if _native_project_plan_digest(
            native_request, pid=pid, hwnd=hwnd, build_bundle=build_bundle
        ) != operation.plan_digest:
            raise NativeProjectConflict("stored native project plan digest is stale")
        result = dict(operation.result)
        registration = result.get("workspaceRegistration")
        reopen = result.get("reopenVerification")
        simulation = result.get("simulationVerification")
        simulation_trial = (
            simulation.get("trial") if isinstance(simulation, dict) else None
        )
        if (
            result.get("engineeredProgramReady") is True
            or result.get("workspaceBound") is True
            or not isinstance(registration, dict)
            or registration.get("state") != "captured_not_bound"
            or not isinstance(registration.get("workspace"), dict)
            or not isinstance(reopen, dict)
            or not _stored_creation_receipt_matches_reviewed_build(
                result,
                request=request,
                operation_plan_digest=operation.plan_digest,
                expected_path=expected_path,
                destination=destination,
            )
        ):
            raise NativeProjectConflict(
                "completed native project is not an exact unbound post-create verification candidate"
            )
        if (
            isinstance(simulation_trial, dict)
            and simulation_trial.get("state")
            in {"QUARANTINED", "QUARANTINE_ACKNOWLEDGED"}
        ):
            raise NativeProjectConflict(
                "completed native project has an unresolved XG-SIM quarantine; "
                "current-state recovery proof is required before another simulation"
            )
        reuse_verified_reopen = reopen.get("state") == "verified"
        if reuse_verified_reopen:
            existing_session = reopen.get("session")
            working = registration["workspace"].get("working_copy")
            if not isinstance(working, dict) or not self._controlled_reopen_session_matches(
                existing_session,
                pid=int(existing_session.get("pid") or 0)
                if isinstance(existing_session, dict)
                else 0,
                hwnd=int(existing_session.get("hwnd") or 0)
                if isinstance(existing_session, dict)
                else 0,
                project_path=str(working.get("path") or ""),
                project_hash=str(working.get("current_sha256") or "").casefold(),
            ):
                raise NativeProjectConflict(
                    "completed native project lost its verified controlled reopen session"
                )
            if (
                (reopened_pid is not None and reopened_pid != existing_session["pid"])
                or (reopened_hwnd is not None and reopened_hwnd != existing_session["hwnd"])
            ):
                raise NativeProjectConflict(
                    "reopened PID/HWND differs from the stored verified reopen session"
                )
        else:
            expected_reopened_process_create_time: float | None = None
            if reopened_pid is None or reopened_hwnd is None:
                working = registration["workspace"].get("working_copy")
                failed_target = self._failed_reopen_target_matches_workspace(
                    reopen.get("reopenTarget"),
                    working=working,
                )
                if failed_target is None:
                    raise NativeProjectConflict(
                        "unbound completed native project resume requires the existing reopened PID/HWND"
                    )
                reopened_pid, reopened_hwnd, expected_reopened_process_create_time = failed_target
            existing_session = await self._rebind_completed_reopen_target(
                operation=operation,
                actor_id=actor_id,
                source_pid=pid,
                reopened_pid=reopened_pid,
                reopened_hwnd=reopened_hwnd,
                registration=registration,
                expected_process_create_time=expected_reopened_process_create_time,
            )
        assert isinstance(existing_session, dict)
        resolved_reopened_pid = int(existing_session["pid"])
        resolved_reopened_hwnd = int(existing_session["hwnd"])
        if not self._repository.begin_completed_verification(operation.operation_id):
            raise NativeProjectConflict("native project changed before completed verification continuation")
        result["resumeProvenance"] = {
            "lifecycleMode": "completed_post_create_verification_continuation",
            "noNewDialog": True,
            "noIlInsert": True,
            "reusedControlledReopen": {
                "pid": resolved_reopened_pid,
                "hwnd": resolved_reopened_hwnd,
            },
        }
        return (
            await self._complete_verified_native_tail(
                spec,
                build_bundle=build_bundle,
                result=result,
                created=False,
                resume=True,
                registration=registration,
                existing_session=existing_session,
                reopen_verification=reopen if reuse_verified_reopen else None,
                simulation_verification=(
                    simulation
                    if isinstance(simulation, dict)
                    and simulation.get("state") == "verified"
                    else None
                ),
            )
        )[0]

    async def _rebind_completed_reopen_target(
        self,
        *,
        operation: NativeProjectOperation,
        actor_id: str,
        source_pid: int,
        reopened_pid: int,
        reopened_hwnd: int,
        registration: dict[str, Any],
        expected_process_create_time: float | None = None,
    ) -> dict[str, Any]:
        """Rebind one persisted controlled workspace process without launching another."""

        workspace = registration.get("workspace")
        working = workspace.get("working_copy") if isinstance(workspace, dict) else None
        if not isinstance(working, dict) or not isinstance(workspace, dict):
            raise NativeProjectConflict("completed native project has no protected working-copy binding")
        project_path = str(working.get("path") or "")
        project_hash = str(working.get("current_sha256") or "").casefold()
        workspace_id = str(workspace.get("workspace_id") or "")
        revision_id = str(workspace.get("revision_id") or "")
        provenance = validated_new_build_workspace_provenance(operation, workspace)
        build_receipt = operation.result.get("buildBundleReceipt")
        if (
            provenance is None
            or not isinstance(build_receipt, dict)
            or not _is_canonical_uuid(workspace_id)
            or not _is_canonical_uuid(revision_id)
        ):
            raise NativeProjectConflict(
                "completed native project has no exact saved-build workspace lineage"
            )
        async def read_rebind_tool(name: str, arguments: dict[str, Any], *, stage: str) -> Any:
            try:
                return await self._gateway.call_read_tool(name, arguments)
            except (MCPToolError, RuntimeError) as exc:
                raise NativeProjectConflict(
                    f"completed native project saved-build rebind failed during {stage}"
                ) from exc

        raw_instances = await read_rebind_tool(
            "xg5000.instances", {}, stage="instance inventory"
        )
        instances = raw_instances.get("instances") if isinstance(raw_instances, dict) else None
        if not isinstance(instances, list):
            raise NativeProjectConflict("MCP returned an invalid XG5000 instance list")
        matches = [
            item
            for item in instances
            if isinstance(item, dict)
            and item.get("pid") == reopened_pid
            and item.get("hwnd") == reopened_hwnd
            and item.get("visible") is True
        ]
        if len(matches) != 1:
            raise NativeProjectConflict("the requested reopened XG5000 PID/HWND is unavailable")
        attached = await read_rebind_tool(
            "xg5000.attach",
            {
                "pid": reopened_pid,
                "hwnd": reopened_hwnd,
                "project_path": project_path,
                "project_hash": project_hash,
                "workspace_id": workspace_id,
                "access_profile": "analysis",
            },
            stage="current native-file identity attach",
        )
        observed_session = (
            attached.get("session")
            if isinstance(attached, dict) and isinstance(attached.get("session"), dict)
            else attached
        )
        observed_identity = (
            observed_session.get("metadata", {}).get("project_identity_proof")
            if isinstance(observed_session, dict)
            and isinstance(observed_session.get("metadata"), dict)
            else None
        )
        if reopened_pid == source_pid or not self._selected_native_file_identity_matches(
            observed_session,
            proof=observed_identity,
            pid=reopened_pid,
            hwnd=reopened_hwnd,
            project_path=project_path,
            project_hash=project_hash,
        ):
            raise NativeProjectConflict(
                "the requested reopened XG5000 process lacks exact current workspace identity"
            )
        assert isinstance(observed_identity, dict)
        observed_metadata = observed_session.get("metadata")
        assert isinstance(observed_metadata, dict)
        target_process_create_time = float(observed_identity["process_create_time"])
        if (
            expected_process_create_time is not None
            and target_process_create_time != expected_process_create_time
        ):
            raise NativeProjectConflict(
                "the requested reopened XG5000 process lifetime differs from failed reopen evidence"
            )
        title = str(observed_metadata["title"])
        issued_at = _utc_now()
        authorization = self._gateway.sign_mutation_authorization(
            {
                "schema": "controlstudio-native-session-resume-authorization/v3",
                "authorization_id": str(uuid.uuid4()),
                "actor_id": actor_id,
                "native_project_id": operation.id,
                "build_receipt_digest": _json_digest(build_receipt),
                "workspace_provenance_receipt_digest": provenance["receipt_digest"],
                "workspace_id": workspace_id,
                "revision_id": revision_id,
                "pid": reopened_pid,
                "hwnd": reopened_hwnd,
                "project_path": project_path,
                "project_hash": project_hash,
                "process_create_time": target_process_create_time,
                "title": title,
                "purpose": "saved_build_reopen_readback",
                "capability": "analysis_saved_build_reopen_resume",
                "allowed_operation": "xg5000.attach",
                "issued_at": issued_at.isoformat(timespec="milliseconds"),
                "expires_at": (issued_at + timedelta(minutes=2)).isoformat(
                    timespec="milliseconds"
                ),
                "nonce": str(uuid.uuid4()),
            }
        )
        rebound = await read_rebind_tool(
            "xg5000.attach",
            {
                "pid": reopened_pid,
                "hwnd": reopened_hwnd,
                "project_path": project_path,
                "project_hash": project_hash,
                "workspace_id": workspace_id,
                "resume_authorization": authorization,
                "access_profile": "analysis",
            },
            stage="signed saved-build read-only attach",
        )
        session = (
            rebound.get("session")
            if isinstance(rebound, dict) and isinstance(rebound.get("session"), dict)
            else rebound
        )
        if not self._controlled_reopen_session_matches(
            session,
            pid=reopened_pid,
            hwnd=reopened_hwnd,
            project_path=project_path,
            project_hash=project_hash,
            saved_build_reopen_authorization=authorization,
        ):
            raise NativeProjectConflict(
                "the requested reopened XG5000 process rejected the saved-build read-only binding"
            )
        return session

    @staticmethod
    def _failed_reopen_target_matches_workspace(
        target: object,
        *,
        working: object,
    ) -> tuple[int, int, float] | None:
        """Recover only a previously observed target; the next attach must revalidate it."""

        if not isinstance(target, dict) or not isinstance(working, dict):
            return None
        try:
            pid = int(target["pid"])
            hwnd = int(target["hwnd"])
            process_create_time = float(target["processCreateTime"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            pid <= 0
            or hwnd <= 0
            or process_create_time <= 0
            or isinstance(target.get("processCreateTime"), bool)
            or _canonical_path(Path(str(target.get("projectPath") or "")))
            != _canonical_path(Path(str(working.get("path") or "")))
            or str(target.get("projectSha256") or "").casefold()
            != str(working.get("current_sha256") or "").casefold()
        ):
            return None
        session = target.get("session")
        return (
            (pid, hwnd, process_create_time)
            if isinstance(session, dict)
            and session.get("pid") == pid
            and session.get("hwnd") == hwnd
            else None
        )

    @staticmethod
    def _selected_native_file_identity_matches(
        session: object,
        *,
        proof: object,
        pid: int,
        hwnd: int,
        project_path: str,
        project_hash: str,
    ) -> bool:
        """Accept only the current selected-process native-file proof before signing v3."""

        return bool(
            isinstance(session, dict)
            and session.get("pid") == pid
            and session.get("hwnd") == hwnd
            and _canonical_path(Path(str(session.get("project_path") or "")))
            == _canonical_path(Path(project_path))
            and str(session.get("project_hash") or "").casefold() == project_hash
            and isinstance(session.get("metadata"), dict)
            and isinstance(session["metadata"].get("title"), str)
            and bool(session["metadata"]["title"].strip())
            and isinstance(proof, dict)
            and proof.get("method") == "selected_pid_exact_window_title_native_file_use_v1"
            and proof.get("pid") == pid
            and proof.get("main_hwnd") == hwnd
            and _canonical_path(Path(str(proof.get("project_path") or "")))
            == _canonical_path(Path(project_path))
            and str(proof.get("project_hash") or "").casefold() == project_hash
            and isinstance(proof.get("process_create_time"), (int, float))
            and not isinstance(proof.get("process_create_time"), bool)
            and float(proof["process_create_time"]) > 0
        )

    @staticmethod
    def _controlled_reopen_session_matches(
        session: object,
        *,
        pid: int,
        hwnd: int,
        project_path: str,
        project_hash: str,
        saved_build_reopen_authorization: dict[str, Any] | None = None,
    ) -> bool:
        """Require retained controlled-launch evidence plus current exact identity."""

        if not isinstance(session, dict):
            return False
        metadata = session.get("metadata")
        if not isinstance(metadata, dict):
            return False
        if (
            session.get("pid") != pid
            or session.get("hwnd") != hwnd
            or _canonical_path(Path(str(session.get("project_path") or "")))
            != _canonical_path(Path(project_path))
            or str(session.get("project_hash") or "").casefold() != project_hash
            or metadata.get("project_identity_read_only_bound_to_selected_pid") is not True
            or metadata.get("project_identity_assurance")
            not in {"controlled_launch_read_only", "strong_readback"}
        ):
            return False
        proof = metadata.get("controlled_open_read_proof")
        controlled_open = bool(
            isinstance(proof, dict)
            and proof.get("method") == "controlled_open_verified_cli_receipt_v3"
            and proof.get("pid") == pid
            and proof.get("main_hwnd") == hwnd
            and isinstance(proof.get("process_create_time"), (int, float))
            and not isinstance(proof.get("process_create_time"), bool)
            and float(proof["process_create_time"]) > 0
            and str(proof.get("project_hash") or "").casefold() == project_hash
            and _canonical_path(Path(str(proof.get("project_path") or "")))
            == _canonical_path(Path(project_path))
        )
        if controlled_open:
            return True
        saved_build_resume = metadata.get("saved_build_reopen_resume")
        if saved_build_reopen_authorization is not None:
            return bool(
                metadata.get("project_identity_assurance") == "controlled_launch_read_only"
                and isinstance(saved_build_resume, dict)
                and saved_build_resume
                == {
                    "purpose": saved_build_reopen_authorization["purpose"],
                    "native_project_id": saved_build_reopen_authorization["native_project_id"],
                    "source_kind": "new_build",
                    "provenance_receipt_digest": saved_build_reopen_authorization[
                        "workspace_provenance_receipt_digest"
                    ],
                    "workspace_id": saved_build_reopen_authorization["workspace_id"],
                    "revision_id": saved_build_reopen_authorization["revision_id"],
                    "pid": saved_build_reopen_authorization["pid"],
                    "main_hwnd": saved_build_reopen_authorization["hwnd"],
                    "project_path": saved_build_reopen_authorization["project_path"],
                    "project_hash": saved_build_reopen_authorization["project_hash"],
                    "process_create_time": saved_build_reopen_authorization[
                        "process_create_time"
                    ],
                    "authorization_id": saved_build_reopen_authorization["authorization_id"],
                    "build_receipt_digest": saved_build_reopen_authorization[
                        "build_receipt_digest"
                    ],
                }
            )
        if isinstance(saved_build_resume, dict):
            return bool(
                metadata.get("project_identity_assurance") == "controlled_launch_read_only"
                and saved_build_resume.get("purpose") == "saved_build_reopen_readback"
                and saved_build_resume.get("source_kind") == "new_build"
                and saved_build_resume.get("pid") == pid
                and saved_build_resume.get("main_hwnd") == hwnd
                and str(saved_build_resume.get("project_hash") or "").casefold()
                == project_hash
                and _canonical_path(Path(str(saved_build_resume.get("project_path") or "")))
                == _canonical_path(Path(project_path))
                and isinstance(saved_build_resume.get("process_create_time"), (int, float))
                and not isinstance(saved_build_resume.get("process_create_time"), bool)
                and float(saved_build_resume["process_create_time"]) > 0
            )
        native_trial_resume = metadata.get("native_trial_resume")
        project_proof = metadata.get("project_identity_proof")
        return bool(
            isinstance(native_trial_resume, dict)
            and isinstance(project_proof, dict)
            and project_proof.get("method") == "same_rot_object_project_tree_and_path_v1"
            and project_proof.get("same_active_object") is True
            and project_proof.get("stable_repeat") is True
            and project_proof.get("pid") == pid
            and project_proof.get("main_hwnd") == hwnd
            and str(project_proof.get("project_hash") or "").casefold() == project_hash
            and _canonical_path(Path(str(project_proof.get("project_path") or "")))
            == _canonical_path(Path(project_path))
            and isinstance(project_proof.get("process_create_time"), (int, float))
            and not isinstance(project_proof.get("process_create_time"), bool)
            and float(project_proof["process_create_time"]) > 0
            and native_trial_resume.get("source_kind") == "new_build"
            and native_trial_resume.get("pid") == pid
            and native_trial_resume.get("main_hwnd") == hwnd
            and str(native_trial_resume.get("project_hash") or "").casefold()
            == project_hash
            and _canonical_path(Path(str(native_trial_resume.get("project_path") or "")))
            == _canonical_path(Path(project_path))
            and isinstance(native_trial_resume.get("process_create_time"), (int, float))
            and not isinstance(native_trial_resume.get("process_create_time"), bool)
            and float(native_trial_resume["process_create_time"]) > 0
        )

    async def resume(
        self,
        operation_id: str,
        *,
        actor_id: str,
        pid: int,
        hwnd: int,
        expected_request_digest: str,
        reopened_pid: int | None = None,
        reopened_hwnd: int | None = None,
    ) -> NativeProjectOperation:
        """Resume only post-dialog verification for one quarantined native create."""

        operation = self._repository.get(operation_id)
        if operation is None:
            raise NativeProjectConflict("native project operation was not found")
        if operation.state == "COMPLETED":
            return await self._continue_completed_verification(
                operation,
                actor_id=actor_id,
                pid=pid,
                hwnd=hwnd,
                expected_request_digest=expected_request_digest,
                reopened_pid=reopened_pid,
                reopened_hwnd=reopened_hwnd,
            )
        if operation.state != "QUARANTINED":
            raise NativeProjectConflict("only a quarantined native project can be resumed")
        if operation.request_digest != expected_request_digest:
            raise NativeProjectConflict("native project resume request is stale")
        request = operation.request
        if pid != request.get("xg5000Pid") or hwnd != request.get("xg5000Hwnd"):
            raise NativeProjectConflict("native project resume PID/HWND differs from the stored request")
        build_bundle = request.get("buildBundle")
        if not isinstance(build_bundle, dict) or not isinstance(operation.plan_digest, str):
            raise NativeProjectConflict("native project resume has no stored reviewed build binding")
        try:
            destination = Path(operation.destination_directory).resolve()
            spec = NativeProjectSpec(
                operation_id=operation.operation_id,
                project_name=str(request["projectName"]),
                workspace_parent=Path(str(request["workspaceParent"])),
                plc_series=str(request["plcSeries"]),
                cpu_model=str(request["cpuModel"]),
                program_name=str(request["programName"]),
                language=str(request["language"]),
                description=str(request["description"]),
                xg5000_pid=pid,
                xg5000_hwnd=hwnd,
            )
            expected_path = (destination / f"{spec.project_name}.xgwx").resolve()
            native_request = _NativeProjectRequest(
                project_name=spec.project_name,
                destination_directory=destination,
                plc_series=spec.plc_series,
                cpu_type=spec.cpu_model,
                program_name=spec.program_name,
                program_language=spec.language,
                description=_native_bundle_description(build_bundle),
            )
            recomputed_plan = _native_project_plan_digest(
                native_request,
                pid=pid,
                hwnd=hwnd,
                build_bundle=build_bundle,
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise NativeProjectConflict("stored native project resume binding is invalid") from exc
        if recomputed_plan != operation.plan_digest:
            raise NativeProjectConflict("stored native project plan digest is stale")
        await self._verify_exact_instance(spec)
        if not self._repository.begin_resume(operation_id):
            raise NativeProjectConflict("native project changed before resume")
        try:
            authorization = self._authorization(
                actor_id=actor_id,
                plan_digest=operation.plan_digest,
                build_bundle_id=str(build_bundle.get("bundleId") or ""),
                allowed_operation="project.resume_native",
            )
            raw = await self._gateway.call_workspace_tool(
                "project.resume_native",
                {
                    "pid": pid,
                    "hwnd": hwnd,
                    "destination_directory": str(destination),
                    "project_name": spec.project_name,
                    "plc_series": spec.plc_series,
                    "cpu_type": spec.cpu_model,
                    "program_name": spec.program_name,
                    "program_language": spec.language,
                    "description": _native_bundle_description(build_bundle),
                    "build_bundle": build_bundle,
                    "authorization": authorization,
                    "timeout_seconds": 60.0,
                },
            )
            result = self._validate_resume_result(
                raw,
                spec=spec,
                destination=destination,
                expected_path=expected_path,
                plan_digest=operation.plan_digest,
                authorization=authorization,
                build_bundle=build_bundle,
                prior_result=operation.result,
            )
        except BaseException as exc:
            preserved = dict(operation.result)
            preserved["resumeProvenance"] = {
                "lifecycleMode": "resume_existing_verified_source",
                "resumeAttempted": True,
                "noNewDialog": True,
                "noIlInsert": True,
            }
            resumed = self._repository.quarantine(
                operation_id,
                error_code="NATIVE_PROJECT_RESUME_REJECTED",
                error_detail=str(exc) or type(exc).__name__,
                result=preserved,
            )
            if not isinstance(exc, Exception):
                raise
            return resumed
        resumed, _ = await self._complete_verified_native_tail(
            spec,
            build_bundle=build_bundle,
            result=result,
            created=False,
            resume=True,
        )
        return resumed

    async def reconcile(
        self,
        operation_id: str,
        evidence: NativeProjectRecoveryEvidence,
    ) -> NativeProjectOperation:
        """Resolve a quarantined create only from current read-only evidence."""

        operation = self._repository.get(operation_id)
        if operation is None:
            raise NativeProjectConflict("native project operation not found")
        if operation.state != "QUARANTINED":
            raise NativeProjectConflict(
                "only a quarantined native project operation can be reconciled"
            )
        request = operation.request
        expected_pid = int(request["xg5000Pid"])
        expected_hwnd = int(request["xg5000Hwnd"])
        destination = Path(operation.destination_directory).resolve()
        expected_path = (destination / f"{request['projectName']!s}.xgwx").resolve()
        supplied_path = Path(evidence.expected_project_path).resolve()
        stored_result = dict(operation.result)
        checkpoint_result = {
            key: value for key, value in stored_result.items() if key != "recovery"
        }
        prior_recovery = stored_result.get("recovery")
        origin_error_code = (
            prior_recovery.get("originErrorCode")
            if isinstance(prior_recovery, dict)
            and isinstance(prior_recovery.get("originErrorCode"), str)
            else operation.error_code
        )
        origin_error_detail = (
            prior_recovery.get("originErrorDetail")
            if isinstance(prior_recovery, dict)
            and isinstance(prior_recovery.get("originErrorDetail"), str)
            else operation.error_detail
        )
        prior_checkpoint_present = (
            prior_recovery.get("checkpointPresent")
            if isinstance(prior_recovery, dict)
            and isinstance(prior_recovery.get("checkpointPresent"), bool)
            else None
        )
        has_checkpoint = (
            prior_checkpoint_present
            if prior_checkpoint_present is not None
            else bool(checkpoint_result)
        )
        stored_project_hash = (
            checkpoint_result.get("projectSha256") if has_checkpoint else None
        )
        if not isinstance(stored_project_hash, str) or re.fullmatch(
            r"[0-9a-f]{64}", stored_project_hash
        ) is None:
            stored_project_hash = None
        stored_simulation = (
            checkpoint_result.get("simulationVerification") if has_checkpoint else None
        )
        stored_trial = (
            stored_simulation.get("trial")
            if isinstance(stored_simulation, dict)
            and isinstance(stored_simulation.get("trial"), dict)
            else None
        )
        computed_checkpoint_receipt_exact = bool(
            has_checkpoint
            and _stored_creation_receipt_matches_reviewed_build(
                checkpoint_result,
                request=request,
                operation_plan_digest=operation.plan_digest,
                expected_path=expected_path,
                destination=destination,
            )
        )
        prior_checkpoint_receipt_exact = (
            prior_recovery.get("checkpointReceiptExact")
            if isinstance(prior_recovery, dict)
            and isinstance(prior_recovery.get("checkpointReceiptExact"), bool)
            else None
        )
        exact_checkpoint_receipt = (
            prior_checkpoint_receipt_exact and computed_checkpoint_receipt_exact
            if prior_checkpoint_receipt_exact is not None
            else computed_checkpoint_receipt_exact
        )
        requires_post_create_recovery = bool(
            origin_error_code == "NATIVE_PROJECT_POST_CREATE_VERIFICATION_UNCERTAIN"
            or (
                origin_error_code
                == "HOST_RESTARTED_DURING_NATIVE_PROJECT_CREATION"
                and has_checkpoint
            )
            or (
                isinstance(stored_trial, dict)
                and stored_trial.get("state") == "QUARANTINED"
            )
        )
        recovery_evidence: dict[str, Any] = {
            "schema": "controlstudio-native-project-recovery/v1",
            "operationId": operation.operation_id,
            "requestDigest": operation.request_digest,
            "planDigest": operation.plan_digest,
            "expectedSpec": {
                key: request[key]
                for key in (
                    "projectName",
                    "plcSeries",
                    "cpuModel",
                    "programName",
                    "language",
                    "description",
                )
            },
            "expectedProjectPath": str(expected_path),
            "expectedProjectSha256": evidence.expected_project_sha256,
            "pid": evidence.pid,
            "hwnd": evidence.hwnd,
            "checkpointPresent": has_checkpoint,
            "checkpointReceiptExact": exact_checkpoint_receipt,
        }
        if isinstance(origin_error_code, str):
            recovery_evidence["originErrorCode"] = origin_error_code
        if isinstance(origin_error_detail, str):
            recovery_evidence["originErrorDetail"] = origin_error_detail
        if stored_project_hash is not None:
            recovery_evidence["storedProjectSha256"] = stored_project_hash

        def merged_recovery_result(
            updates: dict[str, Any] | None = None,
            *,
            observations: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            merged = dict(stored_result)
            if not has_checkpoint:
                # Older recovery attempts may have placed read-only observations
                # at the authoritative result root.  Once the first recovery
                # froze checkpointPresent=false, those values can never become a
                # native-create checkpoint on a later retry.
                for key in (
                    "projectPath",
                    "projectSha256",
                    "identityVerified",
                    "controllerConnected",
                    "plcWritePerformed",
                ):
                    merged.pop(key, None)
            if updates:
                merged.update(updates)
            merged_recovery = (
                dict(prior_recovery) if isinstance(prior_recovery, dict) else {}
            )
            merged_recovery.update(recovery_evidence)
            if observations:
                prior_observations = merged_recovery.get("observations")
                merged_observations = (
                    dict(prior_observations)
                    if isinstance(prior_observations, dict)
                    else {}
                )
                merged_observations.update(observations)
                merged_recovery["observations"] = merged_observations
            merged["recovery"] = merged_recovery
            return merged

        target_changed = evidence.pid != expected_pid or evidence.hwnd != expected_hwnd
        path_changed = _canonical_path(supplied_path) != _canonical_path(expected_path)
        if target_changed and not path_changed and not has_checkpoint:
            try:
                raw_instances = await self._gateway.call_read_tool(
                    "xg5000.instances",
                    {},
                )
                retired_target = _retired_native_target_replaced_by_idle_instance(
                    raw_instances,
                    stored_pid=expected_pid,
                    stored_hwnd=expected_hwnd,
                    selected_pid=evidence.pid,
                    selected_hwnd=evidence.hwnd,
                )
                preserved_artifact = (
                    expected_path.is_file() and not expected_path.is_symlink()
                )
            except (MCPToolError, OSError, TypeError, ValueError) as exc:
                recovery_evidence["retiredTargetProbeError"] = str(exc)
                retired_target = False
                preserved_artifact = False
            if retired_target and preserved_artifact:
                recovery_evidence.update(
                    {
                        "retiredTargetConfirmed": True,
                        "replacementIdleTarget": {
                            "pid": evidence.pid,
                            "hwnd": evidence.hwnd,
                        },
                        "preservedIncompleteArtifact": {
                            "path": str(expected_path),
                            "sha256": _sha256_file(expected_path),
                        },
                    }
                )
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="FAILED",
                    result=merged_recovery_result(),
                    error_code="NATIVE_PROJECT_INCOMPLETE_ARTIFACT_PRESERVED",
                    error_detail=(
                        "The original XG5000 target ended without a durable build "
                        "checkpoint. Its unverified artifact was preserved unchanged, "
                        "and the selected replacement XG5000 window is exactly idle; "
                        "a fresh operation may use a new destination."
                    ),
                )
        if target_changed or path_changed:
            return self._repository.resolve_quarantine(
                operation_id,
                state="QUARANTINED",
                result=merged_recovery_result(),
                error_code="NATIVE_PROJECT_RECOVERY_IDENTITY_MISMATCH",
                error_detail=(
                    "Recovery PID/HWND/path did not exactly match the stored "
                    "native-project contract."
                ),
            )

        if _native_create_failed_before_confirmation(origin_error_detail):
            try:
                destination_empty = destination.is_dir() and not any(destination.iterdir())
                artifact_absent = not expected_path.exists()
            except OSError:
                destination_empty = False
                artifact_absent = False
            if destination_empty and artifact_absent and not has_checkpoint:
                recovery_evidence["preConfirmationFailure"] = True
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="FAILED",
                    result=merged_recovery_result(),
                    error_code="NATIVE_PROJECT_CONFIRMED_NOT_CREATED",
                    error_detail=(
                        "XG5000 rejected a New Project choice before confirmation; "
                        "the allocated destination remains empty."
                    ),
                )

        try:
            await self._verify_exact_instance(
                NativeProjectSpec(
                    operation_id=operation.operation_id,
                    project_name=str(request["projectName"]),
                    workspace_parent=Path(str(request["workspaceParent"])),
                    plc_series=str(request["plcSeries"]),
                    cpu_model=str(request["cpuModel"]),
                    program_name=str(request["programName"]),
                    language=str(request["language"]),
                    description=str(request["description"]),
                    xg5000_pid=expected_pid,
                    xg5000_hwnd=expected_hwnd,
                )
            )
        except (
            MCPToolError,
            NativeProjectPreconditionError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            recovery_evidence["instanceError"] = str(exc)
            return self._repository.resolve_quarantine(
                operation_id,
                state="QUARANTINED",
                result=merged_recovery_result(),
                error_code="NATIVE_PROJECT_RECOVERY_TARGET_UNVERIFIED",
                error_detail="The exact XG5000 PID/HWND could not be reverified.",
            )

        if expected_path.is_file() and not expected_path.is_symlink():
            actual_hash = _sha256_file(expected_path)
            recovery_evidence["observedProjectSha256"] = actual_hash
            if has_checkpoint and not exact_checkpoint_receipt:
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="QUARANTINED",
                    result=merged_recovery_result(),
                    error_code="NATIVE_PROJECT_RECOVERY_CHECKPOINT_RECEIPT_INVALID",
                    error_detail=(
                        "The durable native-create checkpoint no longer proves the "
                        "exact reviewed build; its weaker artifact identity cannot "
                        "authorize completion."
                    ),
                )
            if stored_project_hash is not None and actual_hash != stored_project_hash:
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="QUARANTINED",
                    result=merged_recovery_result(),
                    error_code="NATIVE_PROJECT_RECOVERY_STORED_HASH_MISMATCH",
                    error_detail=(
                        "The native artifact hash changed from the durable "
                        "post-create checkpoint."
                    ),
                )
            if (
                evidence.expected_project_sha256 is not None
                and actual_hash != evidence.expected_project_sha256.casefold()
            ):
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="QUARANTINED",
                    result=merged_recovery_result(),
                    error_code="NATIVE_PROJECT_RECOVERY_HASH_MISMATCH",
                    error_detail=(
                        "The native artifact hash did not match the explicit recovery evidence."
                    ),
                )
            try:
                attached = await self._gateway.call_read_tool(
                    "xg5000.attach",
                    {
                        "pid": expected_pid,
                        "hwnd": expected_hwnd,
                        "project_path": str(expected_path),
                        "project_hash": actual_hash,
                        "access_profile": "analysis",
                    },
                )
                session = (
                    attached.get("session")
                    if isinstance(attached, dict) and isinstance(attached.get("session"), dict)
                    else attached
                )
                if (
                    not isinstance(session, dict)
                    or session.get("pid") != expected_pid
                    or session.get("hwnd") != expected_hwnd
                    or _canonical_path(Path(str(session.get("project_path") or "")))
                    != _canonical_path(expected_path)
                    or str(session.get("project_hash") or "").casefold() != actual_hash
                    or not isinstance(session.get("metadata"), dict)
                    or session["metadata"].get("project_identity_verified_against_selected_pid")
                    is not True
                ):
                    raise NativeProjectEvidenceError(
                        "attach did not prove the exact project artifact"
                    )
                if not has_checkpoint and _native_create_failed_before_program_write(
                    origin_error_detail
                ):
                    recovery_evidence["session"] = session
                    recovery_evidence["confirmedIncompleteStage"] = (
                        "before_program_write"
                    )
                    recovery_evidence["programWritePerformed"] = False
                    return self._repository.resolve_quarantine(
                        operation_id,
                        state="FAILED",
                        result=merged_recovery_result(
                            observations={
                                "projectPath": str(expected_path),
                                "projectSha256": actual_hash,
                                "identityVerified": True,
                                "programWritePerformed": False,
                                "controllerConnected": False,
                                "plcWritePerformed": False,
                            }
                        ),
                        error_code="NATIVE_PROJECT_CONFIRMED_INCOMPLETE",
                        error_detail=(
                            "The exact empty project artifact is preserved, but "
                            "clipboard setup failed before any reviewed mnemonic "
                            "write. A new operation may use a fresh destination."
                        ),
                    )
                self._gateway.bind_agent_session(session)
                try:
                    catalog = await self._gateway.call_read_tool(
                        "project.list_programs",
                        {"name_filter": str(request["programName"])},
                    )
                finally:
                    self._gateway.clear_agent_session()
                programs = catalog.get("programs") if isinstance(catalog, dict) else None
                exact_programs = [
                    item
                    for item in programs or []
                    if isinstance(item, dict)
                    and item.get("name") == request["programName"]
                    and str(item.get("language") or "").casefold()
                    == str(request["language"]).casefold()
                ]
                if len(exact_programs) != 1:
                    raise NativeProjectEvidenceError(
                        "the requested program/language was not uniquely verified"
                    )
            except (
                MCPToolError,
                NativeProjectEvidenceError,
                OSError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                recovery_evidence["projectProbeError"] = str(exc)
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="QUARANTINED",
                    result=merged_recovery_result(),
                    error_code="NATIVE_PROJECT_RECOVERY_PROJECT_UNVERIFIED",
                    error_detail=(
                        "The artifact exists, but current XG5000 project/spec "
                        "identity was not proven."
                    ),
                )
            recovery_evidence["session"] = session
            recovery_evidence["program"] = exact_programs[0]
            recovered_identity = {
                "projectPath": str(expected_path),
                "projectSha256": actual_hash,
                "identityVerified": True,
                "controllerConnected": False,
                "plcWritePerformed": False,
            }
            if not exact_checkpoint_receipt:
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="QUARANTINED",
                    result=merged_recovery_result(observations=recovered_identity),
                    error_code="NATIVE_PROJECT_RECOVERY_BUILD_RECEIPT_REQUIRED",
                    error_detail=(
                        "The artifact and requested program identity were observed, but "
                        "no exact durable native-create checkpoint proves the reviewed "
                        "build receipt, source, rung set, and plan digest. Full build "
                        "readback, independent reopen, and XG-SIM recovery remain required."
                    ),
                )
            if requires_post_create_recovery:
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="QUARANTINED",
                    result=merged_recovery_result(recovered_identity),
                    error_code="NATIVE_PROJECT_POST_CREATE_RECOVERY_REQUIRED",
                    error_detail=(
                        "The source artifact still matches the exact reviewed build, "
                        "but capture, independent reopen, XG-SIM restoration, or the "
                        "final commit remains unresolved; artifact identity alone "
                        "cannot authorize completion."
                    ),
                )
            return self._repository.resolve_quarantine(
                operation_id,
                state="COMPLETED",
                result=merged_recovery_result(recovered_identity),
                error_code=None,
                error_detail=None,
            )

        # Absence alone is not enough. FAILED is safe only when the exact
        # selected XG5000 window proves that a different project is open and
        # the allocated destination is still empty.
        try:
            destination_empty = destination.is_dir() and not any(destination.iterdir())
            attach_arguments: dict[str, Any] = {
                "pid": expected_pid,
                "hwnd": expected_hwnd,
                "access_profile": "analysis",
            }
            if evidence.observed_project_path is not None:
                observed_hint = Path(evidence.observed_project_path).resolve()
                if (
                    observed_hint.is_symlink()
                    or not observed_hint.is_file()
                    or _canonical_path(observed_hint) == _canonical_path(expected_path)
                ):
                    raise ValueError(
                        "observed project path must be a different regular project file"
                    )
                observed_hint_hash = _sha256_file(observed_hint)
                attach_arguments.update(
                    {
                        "project_path": str(observed_hint),
                        "project_hash": observed_hint_hash,
                    }
                )
                recovery_evidence["suppliedObservedProject"] = {
                    "path": str(observed_hint),
                    "sha256": observed_hint_hash,
                }
            attached = await self._gateway.call_read_tool(
                "xg5000.attach",
                attach_arguments,
            )
            session = (
                attached.get("session")
                if isinstance(attached, dict) and isinstance(attached.get("session"), dict)
                else attached
            )
            observed_path = Path(
                str(session.get("project_path") or "") if isinstance(session, dict) else ""
            ).resolve()
            different_project = (
                isinstance(session, dict)
                and session.get("pid") == expected_pid
                and session.get("hwnd") == expected_hwnd
                and isinstance(session.get("metadata"), dict)
                and session["metadata"].get("project_identity_verified_against_selected_pid")
                is True
                and observed_path.is_file()
                and _canonical_path(observed_path) != _canonical_path(expected_path)
            )
        except (
            MCPToolError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            recovery_evidence["absenceProbeError"] = str(exc)
            destination_empty = False
            different_project = False
        if destination_empty and different_project:
            recovery_evidence["observedDifferentProject"] = str(observed_path)
            if has_checkpoint:
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="QUARANTINED",
                    result=merged_recovery_result(),
                    error_code="NATIVE_PROJECT_RECOVERY_CHECKPOINT_ARTIFACT_MISSING",
                    error_detail=(
                        "A durable checkpoint proves that native creation reached "
                        "post-create verification, so later artifact absence cannot "
                        "be reclassified as confirmed non-creation."
                    ),
                )
            return self._repository.resolve_quarantine(
                operation_id,
                state="FAILED",
                result=merged_recovery_result(),
                error_code="NATIVE_PROJECT_CONFIRMED_NOT_CREATED",
                error_detail=(
                    "The allocated destination is empty and the exact XG5000 "
                    "window proves a different project is open."
                ),
            )
        return self._repository.resolve_quarantine(
            operation_id,
            state="QUARANTINED",
            result=merged_recovery_result(),
            error_code="NATIVE_PROJECT_RECOVERY_OUTCOME_UNCERTAIN",
            error_detail=(
                "The current file/window evidence cannot safely prove creation "
                "or non-creation; the target remains locked."
            ),
        )

    @staticmethod
    def _canonicalize_local_request(spec: NativeProjectSpec) -> NativeProjectSpec:
        operation_id = _canonical_uuid(spec.operation_id, "operationId")
        raw_parent = Path(spec.workspace_parent).expanduser()
        if not raw_parent.is_absolute():
            raise NativeProjectPreconditionError("workspaceParent must be absolute")
        if raw_parent.is_symlink():
            raise NativeProjectPreconditionError("workspaceParent must not be a symlink")
        try:
            parent = raw_parent.resolve(strict=True)
        except OSError as exc:
            raise NativeProjectPreconditionError(
                "workspaceParent must be an existing absolute directory"
            ) from exc
        if not parent.is_dir():
            raise NativeProjectPreconditionError(
                "workspaceParent must be a real directory, not a file or symlink"
            )
        project_name = _validated_name("project_name", spec.project_name, maximum=80)
        program_name = _validated_name("program_name", spec.program_name, maximum=80)
        plc_series = _validated_text("plc_series", spec.plc_series, maximum=32)
        cpu_model = _validated_text("cpu_model", spec.cpu_model, maximum=64)
        language = _validated_text("language", spec.language, maximum=32)
        if not isinstance(spec.description, str) or "\x00" in spec.description:
            raise NativeProjectPreconditionError("description must be a string without NUL")
        description = spec.description.strip()
        if len(description) > 1024:
            raise NativeProjectPreconditionError("description is too long")
        if spec.xg5000_pid <= 0 or spec.xg5000_hwnd <= 0:
            raise NativeProjectPreconditionError(
                "xg5000Pid and xg5000Hwnd must be positive integers"
            )
        return NativeProjectSpec(
            operation_id=operation_id,
            project_name=project_name,
            workspace_parent=parent,
            plc_series=plc_series,
            cpu_model=cpu_model,
            program_name=program_name,
            language=language,
            description=description,
            xg5000_pid=spec.xg5000_pid,
            xg5000_hwnd=spec.xg5000_hwnd,
            planning_conversation_id=spec.planning_conversation_id,
            planning_message_id=spec.planning_message_id,
            planning_plan_digest=spec.planning_plan_digest,
            reviewed_section_ids=tuple(spec.reviewed_section_ids),
            reviewed_item_ids=tuple(spec.reviewed_item_ids),
            project_brief_digest=spec.project_brief_digest,
            attachment_revision_digest=spec.attachment_revision_digest,
            planning_artifact=(
                _json_load(_json_dump(spec.planning_artifact))
                if isinstance(spec.planning_artifact, dict)
                else None
            ),
        )

    async def _verify_exact_instance(self, spec: NativeProjectSpec) -> None:
        try:
            raw_instances = await self._gateway.call_read_tool(
                "xg5000.instances",
                {},
            )
        except MCPToolError as exc:
            raise NativeProjectPreconditionError(str(exc)) from exc
        if not isinstance(raw_instances, dict) or not isinstance(
            raw_instances.get("instances"),
            list,
        ):
            raise NativeProjectPreconditionError("MCP returned an invalid XG5000 instance list")
        matches = [
            item
            for item in raw_instances["instances"]
            if isinstance(item, dict)
            and item.get("pid") == spec.xg5000_pid
            and item.get("hwnd") == spec.xg5000_hwnd
        ]
        if len(matches) != 1:
            raise NativeProjectPreconditionError(
                "the exact xg5000Pid/xg5000Hwnd pair did not resolve to one instance"
            )
        instance = matches[0]
        if instance.get("visible") is not True:
            raise NativeProjectPreconditionError("the selected XG5000 main window is not visible")

    @staticmethod
    def _destination_directory(spec: NativeProjectSpec) -> Path:
        operation_suffix = uuid.UUID(spec.operation_id).hex[:12]
        return spec.workspace_parent / f"{spec.project_name}-{operation_suffix}"

    @staticmethod
    def _request_payload(
        spec: NativeProjectSpec,
        destination: Path,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operationId": spec.operation_id,
            "projectName": spec.project_name,
            "workspaceParent": str(spec.workspace_parent),
            "destinationDirectory": str(destination),
            "plcSeries": spec.plc_series,
            "cpuModel": spec.cpu_model,
            "programName": spec.program_name,
            "language": spec.language,
            "description": spec.description,
            "xg5000Pid": spec.xg5000_pid,
            "xg5000Hwnd": spec.xg5000_hwnd,
        }
        if spec.planning_conversation_id is not None:
            payload["reviewedPlan"] = _reviewed_plan_payload(spec)
            payload["buildBundle"] = _native_build_bundle(spec)
        return payload

    @staticmethod
    def _ensure_allocated_directory(
        destination: Path,
        *,
        allow_existing: bool,
    ) -> None:
        parent = destination.parent.resolve(strict=True)
        _require_within(destination, parent, "destination_directory")
        if destination.exists():
            if (
                not allow_existing
                or not destination.is_dir()
                or destination.is_symlink()
                or any(destination.iterdir())
            ):
                raise NativeProjectPreconditionError(
                    "the allocated native-project directory is not a unique empty directory"
                )
            return
        destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        if destination.is_symlink() or any(destination.iterdir()):
            raise NativeProjectPreconditionError(
                "the allocated native-project directory is not empty"
            )

    def _authorization(
        self,
        *,
        actor_id: str,
        plan_digest: str,
        build_bundle_id: str,
        allowed_operation: str = "project.create_native",
    ) -> dict[str, Any]:
        issued_at = _utc_now()
        claims = {
            "schema": NATIVE_PROJECT_AUTHORIZATION_SCHEMA,
            "authorization_id": str(uuid.uuid4()),
            "actor_id": actor_id,
            "plan_digest": plan_digest,
            "build_bundle_id": build_bundle_id,
            "capability": "workspace_write",
            "allowed_operation": allowed_operation,
            "issued_at": issued_at.isoformat(timespec="milliseconds"),
            "expires_at": (issued_at + timedelta(minutes=2)).isoformat(timespec="milliseconds"),
            "nonce": str(uuid.uuid4()),
        }
        return self._gateway.sign_mutation_authorization(claims)

    @staticmethod
    def _validate_creation_result(
        raw: Any,
        *,
        spec: NativeProjectSpec,
        destination: Path,
        plan_digest: str,
        authorization: dict[str, Any],
        build_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise NativeProjectEvidenceError(
                f"native project result is not an object: {type(raw).__name__}"
            )
        if (
            raw.get("ok") is not True
            or raw.get("verified") is not True
            or raw.get("operation") != "project.create_native"
            or raw.get("pid") != spec.xg5000_pid
            or raw.get("hwnd") != spec.xg5000_hwnd
        ):
            raise NativeProjectEvidenceError(
                "MCP did not verify the exact native project operation and XG5000 target"
            )
        data = raw.get("data")
        if not isinstance(data, dict):
            raise NativeProjectEvidenceError("native project evidence data is missing")
        expected_values = {
            "project_name": spec.project_name,
            "plc_series": spec.plc_series,
            "cpu_type": spec.cpu_model,
            "program_name": spec.program_name,
            "program_language": spec.language,
            "description": _native_bundle_description(build_bundle),
        }
        mismatches = {
            key: {"expected": expected, "observed": data.get(key)}
            for key, expected in expected_values.items()
            if data.get(key) != expected
        }
        if mismatches:
            raise NativeProjectEvidenceError(
                f"MCP returned substituted native-project fields: {sorted(mismatches)}"
            )
        dialog = data.get("dialog")
        dialog_fields = dialog.get("fields") if isinstance(dialog, dict) else None
        exact_dialog_values = {
            "project_name": spec.project_name,
            "file_directory": str(destination),
            "program_name": spec.program_name,
            "project_description": _native_bundle_description(build_bundle),
        }
        if (
            not isinstance(dialog, dict)
            or dialog.get("coordinates_used") is not False
            or not isinstance(dialog_fields, dict)
            or any(
                not isinstance(dialog_fields.get(field), dict)
                or dialog_fields[field].get("observed") != expected
                for field, expected in exact_dialog_values.items()
            )
            or any(
                not isinstance(dialog_fields.get(field), dict)
                or not isinstance(dialog_fields[field].get("observed"), str)
                or not dialog_fields[field]["observed"].strip()
                for field in ("plc_series", "cpu_type", "program_language")
            )
        ):
            raise NativeProjectEvidenceError(
                "native project dialog field readback is incomplete or substituted"
            )
        if (
            _canonical_path(Path(str(data.get("destination_directory") or "")))
            != _canonical_path(destination)
            or data.get("plan_digest") != plan_digest
            or data.get("controller_connected") is not False
            or data.get("plc_write_performed") is not False
        ):
            raise NativeProjectEvidenceError(
                "native project result violated destination, plan, or no-PLC guarantees"
            )
        approval = data.get("authorization")
        if (
            not isinstance(approval, dict)
            or approval.get("authorization_id") != authorization.get("authorization_id")
            or approval.get("nonce") != authorization.get("nonce")
            or approval.get("actor_id") != authorization.get("actor_id")
            or approval.get("plan_digest") != plan_digest
            or approval.get("build_bundle_id") != build_bundle.get("bundleId")
            or approval.get("signature_verified") is not True
            or approval.get("consumed") is not True
        ):
            raise NativeProjectEvidenceError(
                "MCP did not prove consumption of the exact Host authorization"
            )

        project_path = Path(str(data.get("project_path") or "")).resolve()
        _require_within(project_path, destination, "project_path")
        if (
            not project_path.is_file()
            or project_path.is_symlink()
            or project_path.suffix.casefold() != ".xgwx"
            or project_path.stem.casefold() != spec.project_name.casefold()
        ):
            raise NativeProjectEvidenceError(
                "the returned project path is not the requested native .xgwx artifact"
            )
        actual_size = project_path.stat().st_size
        actual_hash = _sha256_file(project_path)
        if (
            actual_size <= 0
            or data.get("project_size") != actual_size
            or data.get("project_sha256") != actual_hash
        ):
            raise NativeProjectEvidenceError(
                "native project file size or SHA-256 evidence does not match disk"
            )
        compiled_program = build_bundle.get("compiledProgram")
        program_apply = data.get("program_apply")
        if not isinstance(compiled_program, dict) or not isinstance(program_apply, dict):
            raise NativeProjectEvidenceError(
                "compiled program or native program-apply evidence is missing"
            )
        expected_instruction_readback = str(compiled_program.get("source") or "").splitlines()
        initial_readback = program_apply.get("initial_readback")
        insert_readback = program_apply.get("insert_readback")
        ld_roundtrip = program_apply.get("ld_roundtrip")
        program_check = program_apply.get("program_check")
        save = program_apply.get("save")
        initial_proof = (
            initial_readback.get("proof")
            if isinstance(initial_readback, dict)
            and isinstance(initial_readback.get("proof"), dict)
            else None
        )
        if (
            program_apply.get("status") != "APPLIED_VERIFIED"
            or program_apply.get("source_sha256") != compiled_program.get("sourceSha256")
            or program_apply.get("rung_ids") != compiled_program.get("rungIds")
            or program_apply.get("ladder_ir_sha256")
            != compiled_program.get("ladderIrSha256")
            or program_apply.get("exact_instruction_readback") != expected_instruction_readback
            or not isinstance(initial_readback, dict)
            or initial_readback.get("status") != "passed"
            or initial_readback.get("blankState") != "new_program_empty_grid"
            or initial_readback.get("il_readback_lines") != []
            or not isinstance(initial_proof, dict)
            or initial_proof.get("projectIdentityVerified") is not True
            or initial_proof.get("newProjectDialogProgramName") != spec.program_name
            or not isinstance(initial_proof.get("initialProjectSave"), dict)
            or not isinstance(insert_readback, dict)
            or insert_readback.get("status") != "passed"
            or insert_readback.get("before") != []
            or insert_readback.get("readback") != expected_instruction_readback
            or not isinstance(ld_roundtrip, dict)
            or ld_roundtrip.get("roundtrip_il") != expected_instruction_readback
            or ld_roundtrip.get("final_view") != "LD"
            or not isinstance(program_check, dict)
            or program_check.get("status") != "passed"
            or program_check.get("error_count") != 0
            or not isinstance(save, dict)
            or save.get("hash_after") != actual_hash
            or program_apply.get("saved_project_sha256") != actual_hash
        ):
            raise NativeProjectEvidenceError(
                "MCP did not prove exact program readback, Program Check, and save"
            )
        identity = data.get("identity")
        target = data.get("target")
        if (
            not isinstance(identity, dict)
            or identity.get("verified") is not True
            or _canonical_path(Path(str(identity.get("project_path") or "")))
            != _canonical_path(project_path)
            or not isinstance(target, dict)
            or target.get("owner_pid") != spec.xg5000_pid
            or target.get("project_open") is not False
        ):
            raise NativeProjectEvidenceError(
                "native project identity or idle-target evidence is incomplete"
            )
        expected_receipt = {
            "schemaVersion": NATIVE_BUILD_BUNDLE_RECEIPT_SCHEMA,
            "bundleId": build_bundle["bundleId"],
            "phase": NATIVE_BUILD_BUNDLE_PHASE,
            "status": "APPLIED_VERIFIED",
            "reviewedPlanDigest": build_bundle["reviewedPlan"]["planDigest"],
            "nativeReadback": {
                "projectName": spec.project_name,
                "projectPath": str(project_path),
                "projectSha256": actual_hash,
                "projectSize": actual_size,
                "destinationDirectory": str(destination),
                "plcSeries": spec.plc_series,
                "cpuModel": spec.cpu_model,
                "programName": spec.program_name,
                "language": spec.language,
                "description": _native_bundle_description(build_bundle),
                "identityVerified": True,
            },
            "programReadback": {
                "rungCount": len(compiled_program["rungIds"]),
                "rungIds": compiled_program["rungIds"],
                "ladderIrSha256": compiled_program["ladderIrSha256"],
                "sourceSha256": compiled_program["sourceSha256"],
                "exactInstructionReadback": expected_instruction_readback,
                "finalView": "LD",
                "programCheckPassed": True,
                "savedProjectSha256": actual_hash,
            },
        }
        receipt = data.get("build_bundle_receipt")
        if receipt != expected_receipt:
            raise NativeProjectEvidenceError(
                "MCP build-bundle receipt does not match the exact native readback"
            )
        return {
            "identityVerified": True,
            "operation": "project.create_native",
            "xg5000Pid": spec.xg5000_pid,
            "xg5000Hwnd": spec.xg5000_hwnd,
            "projectPath": str(project_path),
            "projectSha256": actual_hash,
            "projectSize": actual_size,
            "destinationDirectory": str(destination),
            "planDigest": plan_digest,
            "buildBundleId": build_bundle["bundleId"],
            "reviewedPlanDigest": build_bundle["reviewedPlan"]["planDigest"],
            "buildBundleReceipt": receipt,
            "controllerConnected": False,
            "plcWritePerformed": False,
            "identityEvidence": identity,
        }

    @staticmethod
    def _validate_resume_result(
        raw: Any,
        *,
        spec: NativeProjectSpec,
        destination: Path,
        expected_path: Path,
        plan_digest: str,
        authorization: dict[str, Any],
        build_bundle: dict[str, Any],
        prior_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Accept only a proof of the already-present reviewed source, never insertion."""

        if not isinstance(raw, dict) or (
            raw.get("ok") is not True
            or raw.get("verified") is not True
            or raw.get("operation") != "project.resume_native"
            or raw.get("pid") != spec.xg5000_pid
            or raw.get("hwnd") != spec.xg5000_hwnd
            or raw.get("plan_digest") != plan_digest
            or raw.get("controller_connected") is not False
            or raw.get("plc_write_performed") is not False
        ):
            raise NativeProjectEvidenceError("MCP did not verify the exact native resume")
        provenance = raw.get("resume_provenance")
        if not isinstance(provenance, dict) or (
            provenance.get("mode") != "observed_existing_project"
            or provenance.get("lifecycle_mode") != "resume_existing_verified_source"
            or provenance.get("no_new_dialog") is not True
            or provenance.get("no_il_insert") is not True
            or not isinstance(provenance.get("observed_program_id"), str)
            or not provenance["observed_program_id"]
            or provenance.get("observed_program_name") != spec.program_name
        ):
            raise NativeProjectEvidenceError("native resume did not prove no dialog or IL insertion")
        approval = raw.get("authorization")
        if (
            not isinstance(approval, dict)
            or approval.get("authorization_id") != authorization.get("authorization_id")
            or approval.get("nonce") != authorization.get("nonce")
            or approval.get("actor_id") != authorization.get("actor_id")
            or approval.get("plan_digest") != plan_digest
            or approval.get("build_bundle_id") != build_bundle.get("bundleId")
            or approval.get("signature_verified") is not True
            or approval.get("consumed") is not True
        ):
            raise NativeProjectEvidenceError("native resume authorization was not exactly consumed")
        try:
            project_path = Path(str(raw.get("project_path") or "")).resolve()
            actual_hash = _sha256_file(project_path)
            actual_size = project_path.stat().st_size
        except OSError as exc:
            raise NativeProjectEvidenceError("native resume project artifact is unreadable") from exc
        if (
            _canonical_path(project_path) != _canonical_path(expected_path)
            or not project_path.is_file()
            or project_path.is_symlink()
            or raw.get("project_sha256") != actual_hash
            or raw.get("project_size") != actual_size
            or actual_size <= 0
        ):
            raise NativeProjectEvidenceError("native resume project artifact differs from the stored path")
        compiled = build_bundle.get("compiledProgram")
        reviewed = build_bundle.get("reviewedPlan")
        program_apply = raw.get("program_apply")
        expected_lines = compiled.get("source", "").splitlines() if isinstance(compiled, dict) else []
        if not isinstance(compiled, dict) or not isinstance(reviewed, dict) or not isinstance(program_apply, dict):
            raise NativeProjectEvidenceError("native resume has no reviewed build or program proof")
        ld_roundtrip = program_apply.get("ld_roundtrip")
        program_check = program_apply.get("program_check")
        program_check_receipt = raw.get("program_check_receipt")
        program_check_data = (
            program_check_receipt.get("data")
            if isinstance(program_check_receipt, dict)
            else None
        )
        save = program_apply.get("save")
        if (
            program_apply.get("status") != "APPLIED_VERIFIED"
            or program_apply.get("source_sha256") != compiled.get("sourceSha256")
            or program_apply.get("rung_ids") != compiled.get("rungIds")
            or program_apply.get("ladder_ir_sha256") != compiled.get("ladderIrSha256")
            or program_apply.get("exact_instruction_readback") != expected_lines
            or not isinstance(ld_roundtrip, dict)
            or ld_roundtrip.get("roundtrip_il") != expected_lines
            or ld_roundtrip.get("final_view") != "LD"
            or not isinstance(program_check, dict)
            or program_check.get("status") != "passed"
            or program_check.get("error_count") != 0
            or not isinstance(program_check_receipt, dict)
            or program_check_receipt.get("ok") is not True
            or program_check_receipt.get("verified") is not True
            or program_check_receipt.get("operation") != "program.check_ld"
            or program_check_receipt.get("pid") != spec.xg5000_pid
            or program_check_receipt.get("hwnd") != spec.xg5000_hwnd
            or not isinstance(program_check_data, dict)
            or program_check_data.get("status") != "passed"
            or program_check_data.get("error_count") != 0
            or program_check != program_check_data
            or not isinstance(save, dict)
            or save.get("hash_after") != actual_hash
            or program_apply.get("saved_project_sha256") != actual_hash
        ):
            raise NativeProjectEvidenceError("native resume did not prove complete LD, Program Check, and save")
        receipt = raw.get("build_bundle_receipt")
        expected_receipt = {
            "schemaVersion": NATIVE_BUILD_BUNDLE_RECEIPT_SCHEMA,
            "bundleId": build_bundle.get("bundleId"),
            "phase": NATIVE_BUILD_BUNDLE_PHASE,
            "status": "APPLIED_VERIFIED",
            "reviewedPlanDigest": reviewed.get("planDigest"),
            "nativeReadback": {
                "projectName": spec.project_name,
                "projectPath": str(project_path),
                "projectSha256": actual_hash,
                "projectSize": actual_size,
                "destinationDirectory": str(destination),
                "plcSeries": spec.plc_series,
                "cpuModel": spec.cpu_model,
                "programName": spec.program_name,
                "language": spec.language,
                "description": _native_bundle_description(build_bundle),
                "identityVerified": True,
            },
            "programReadback": {
                "rungCount": len(compiled.get("rungIds", [])),
                "rungIds": compiled.get("rungIds"),
                "ladderIrSha256": compiled.get("ladderIrSha256"),
                "sourceSha256": compiled.get("sourceSha256"),
                "exactInstructionReadback": expected_lines,
                "finalView": "LD",
                "programCheckPassed": True,
                "savedProjectSha256": actual_hash,
            },
        }
        if receipt != expected_receipt:
            raise NativeProjectEvidenceError("native resume receipt does not match the reviewed build")
        return {
            **dict(prior_result),
            "identityVerified": True,
            "operation": "project.resume_native",
            "xg5000Pid": spec.xg5000_pid,
            "xg5000Hwnd": spec.xg5000_hwnd,
            "projectPath": str(project_path),
            "projectSha256": actual_hash,
            "projectSize": actual_size,
            "destinationDirectory": str(destination),
            "planDigest": plan_digest,
            "buildBundleId": build_bundle.get("bundleId"),
            "reviewedPlanDigest": reviewed.get("planDigest"),
            "buildBundleReceipt": receipt,
            "controllerConnected": False,
            "plcWritePerformed": False,
            "resumeProvenance": {
                "lifecycleMode": "resume_existing_verified_source",
                "mode": "observed_existing_project",
                "resumeAttempted": True,
                "noNewDialog": True,
                "noIlInsert": True,
                "mcp": provenance,
            },
        }

    async def _capture_workspace(
        self,
        project_path: str,
        project_hash: str,
        *,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            raw = await self._gateway.call_workspace_tool(
                "workspace.open_native",
                {
                    "source_path": project_path,
                    "expected_source_hash": project_hash,
                    "source_kind": "new_build",
                    "provenance": provenance,
                },
            )
            workspace = self._validate_workspace_capture(
                raw,
                project_path=Path(project_path),
                project_hash=project_hash,
                provenance=provenance,
            )
        except Exception as exc:  # noqa: BLE001 - capture is best-effort after proof
            return {
                "state": "capture_failed",
                "detail": str(exc),
            }
        return {
            "state": "captured_not_bound",
            "detail": (
                "The new source artifact was captured into a protected workspace, "
                "but its separate working copy is not the project currently open in XG5000."
            ),
            "workspace": workspace,
        }

    async def _verify_fresh_process_reopen(
        self,
        *,
        source_pid: int,
        registration: dict[str, Any],
        build_bundle: dict[str, Any],
        existing_session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workspace = registration.get("workspace")
        compiled = build_bundle.get("compiledProgram")
        reviewed = build_bundle.get("reviewedPlan")
        if (
            registration.get("state") != "captured_not_bound"
            or not isinstance(workspace, dict)
            or not isinstance(compiled, dict)
            or not isinstance(reviewed, dict)
        ):
            return {
                "state": "not_verified",
                "detail": "A verified protected workspace was unavailable for fresh-process reopen.",
            }
        working = workspace.get("working_copy")
        if not isinstance(working, dict):
            return {
                "state": "not_verified",
                "detail": "The protected workspace working-copy identity is missing.",
            }
        workspace_id = str(workspace.get("workspace_id") or "")
        project_path = str(working.get("path") or "")
        project_hash = str(working.get("current_sha256") or "").casefold()
        expected_source = str(compiled.get("source") or "")
        expected_source_sha256 = str(compiled.get("sourceSha256") or "").casefold()
        expected_rung_ids = compiled.get("rungIds")
        expected_ladder_ir_sha256 = str(
            compiled.get("ladderIrSha256") or ""
        ).casefold()
        if (
            not isinstance(expected_rung_ids, list)
            or not expected_rung_ids
            or any(not isinstance(rung_id, str) for rung_id in expected_rung_ids)
            or len(set(expected_rung_ids)) != len(expected_rung_ids)
            or re.fullmatch(r"[0-9a-f]{64}", expected_ladder_ir_sha256) is None
        ):
            return {
                "state": "not_verified",
                "detail": "Compiled ladder rung identity or IR digest is missing.",
            }
        bundle_id = str(build_bundle.get("bundleId") or "").casefold()
        reviewed_plan_digest = str(reviewed.get("planDigest") or "").casefold()
        program_name = str(compiled.get("programName") or "")
        launch: dict[str, Any] | None = None
        session: dict[str, Any] | None = None
        reopened_pid = 0
        reopened_hwnd = 0
        try:
            if existing_session is None:
                launch = await self._gateway.call_workspace_tool(
                    "xg5000.open_project",
                    {
                        "workspace_id": workspace_id,
                        "expected_working_hash": project_hash,
                        "independent_from_pid": source_pid,
                        "timeout_seconds": 60.0,
                    },
                )
                if not isinstance(launch, dict):
                    raise NativeProjectEvidenceError(
                        "fresh-process XG5000 launch result is not an object"
                    )
                instance = launch.get("instance")
                if (
                    launch.get("ok") is not True
                    or launch.get("verified") is not True
                    or launch.get("identity_assurance") != "controlled_launch_read_only"
                    or launch.get("independent_from_pid") != source_pid
                    or launch.get("process_is_independent") is not True
                    or launch.get("working_path") != project_path
                    or launch.get("working_hash") != project_hash
                    or not isinstance(instance, dict)
                ):
                    raise NativeProjectEvidenceError(
                        "MCP did not prove an independent fresh-process working-copy open"
                    )
                reopened_pid = int(instance["pid"])
                reopened_hwnd = int(instance["hwnd"])
                attached = await self._gateway.call_read_tool(
                    "xg5000.attach",
                    {
                        "pid": reopened_pid,
                        "hwnd": reopened_hwnd,
                        "project_path": project_path,
                        "project_hash": project_hash,
                        "workspace_id": workspace_id,
                        "access_profile": "analysis",
                    },
                )
                session = (
                    attached.get("session")
                    if isinstance(attached, dict) and isinstance(attached.get("session"), dict)
                    else attached
                )
                if not isinstance(session, dict):
                    raise NativeProjectEvidenceError(
                        "fresh-process XG5000 attach result has no session"
                    )
            else:
                launch = {"reused_existing_controlled_process": True}
                session = existing_session
                reopened_pid = int(session.get("pid") or 0)
                reopened_hwnd = int(session.get("hwnd") or 0)
            if reopened_pid <= 0 or reopened_hwnd <= 0 or reopened_pid == source_pid:
                raise NativeProjectEvidenceError(
                    "fresh-process XG5000 identity was not independent"
                )
            if not self._controlled_reopen_session_matches(
                session,
                pid=reopened_pid,
                hwnd=reopened_hwnd,
                project_path=project_path,
                project_hash=project_hash,
            ):
                raise NativeProjectEvidenceError(
                    "fresh-process attach did not preserve the controlled workspace identity"
                )
            self._gateway.bind_agent_session(session)
            verified = await self._gateway.call_workspace_tool(
                "project.verify_native_reopen",
                {
                    "session_id": session["session_id"],
                    "pid": reopened_pid,
                    "hwnd": reopened_hwnd,
                    "project_path": project_path,
                    "project_hash": project_hash,
                    "source_pid": source_pid,
                    "program_name": program_name,
                    "expected_source": expected_source,
                    "expected_source_sha256": expected_source_sha256,
                    "expected_rung_ids": expected_rung_ids,
                    "expected_ladder_ir_sha256": expected_ladder_ir_sha256,
                    "build_bundle_id": bundle_id,
                    "reviewed_plan_digest": reviewed_plan_digest,
                },
            )
            receipt = (
                verified.get("reopen_receipt")
                if isinstance(verified, dict) and isinstance(verified.get("reopen_receipt"), dict)
                else None
            )
            expected_readback = expected_source.splitlines()
            if (
                not isinstance(receipt, dict)
                or receipt.get("schemaVersion") != NATIVE_REOPEN_RECEIPT_SCHEMA
                or receipt.get("status") != "REOPEN_VERIFIED"
                or receipt.get("buildBundleId") != bundle_id
                or receipt.get("reviewedPlanDigest") != reviewed_plan_digest
                or receipt.get("sourceProcess") != {"pid": source_pid}
                or receipt.get("reopenedProcess")
                != {"pid": reopened_pid, "hwnd": reopened_hwnd, "independent": True}
                or receipt.get("projectReadback")
                != {
                    "projectPath": project_path,
                    "projectSha256": project_hash,
                    "projectUnchanged": True,
                    "identityAssurance": "controlled_launch_read_only",
                }
                or receipt.get("programReadback")
                != {
                    "programName": program_name,
                    "language": "LD",
                    "sourceSha256": expected_source_sha256,
                    "rungCount": len(expected_rung_ids),
                    "rungIds": expected_rung_ids,
                    "ladderIrSha256": expected_ladder_ir_sha256,
                    "exactInstructionReadback": expected_readback,
                    "finalView": "LD",
                    "programCheckPassed": True,
                }
                or receipt.get("controllerConnected") is not False
                or receipt.get("plcWritePerformed") is not False
            ):
                raise NativeProjectEvidenceError(
                    "fresh-process reopen receipt does not match the reviewed build bundle"
                )
            refreshed = await self._gateway.call_read_tool(
                "xg5000.session_status",
                {},
            )
            refreshed_session = (
                refreshed.get("session")
                if isinstance(refreshed, dict) and isinstance(refreshed.get("session"), dict)
                else refreshed
            )
            if not isinstance(refreshed_session, dict):
                refreshed_session = session
            self._gateway.bind_agent_session(refreshed_session)
            return {
                "state": "verified",
                "detail": (
                    "Protected working copy reopened in an independent XG5000 process "
                    "with exact mnemonic, LD, Program Check, and disk-hash readback."
                ),
                "receipt": receipt,
                "launch": launch,
                "workspace": workspace,
                "session": refreshed_session,
            }
        except (RuntimeError, KeyError, TypeError, ValueError, OSError) as exc:
            self._gateway.clear_agent_session()
            failed = {
                "state": "not_verified",
                "detail": str(exc),
                "workspace": workspace,
            }
            target = self._failed_reopen_target_evidence(
                session,
                pid=reopened_pid,
                hwnd=reopened_hwnd,
                project_path=project_path,
                project_hash=project_hash,
                launch=launch,
            )
            if target is not None:
                failed["reopenTarget"] = target
            return failed

    @staticmethod
    def _failed_reopen_target_evidence(
        session: object,
        *,
        pid: int,
        hwnd: int,
        project_path: str,
        project_hash: str,
        launch: object,
    ) -> dict[str, Any] | None:
        """Persist an observed independent target without claiming reopen verification."""

        if not isinstance(session, dict) or pid <= 0 or hwnd <= 0:
            return None
        metadata = session.get("metadata")
        if not isinstance(metadata, dict):
            return None
        process_create_time: object | None = None
        for proof_name in (
            "saved_build_reopen_resume",
            "controlled_open_read_proof",
            "project_identity_proof",
        ):
            proof = metadata.get(proof_name)
            if isinstance(proof, dict):
                process_create_time = proof.get("process_create_time")
                if isinstance(process_create_time, (int, float)) and not isinstance(
                    process_create_time, bool
                ) and float(process_create_time) > 0:
                    break
        if (
            not isinstance(process_create_time, (int, float))
            or isinstance(process_create_time, bool)
            or float(process_create_time) <= 0
            or session.get("pid") != pid
            or session.get("hwnd") != hwnd
            or _canonical_path(Path(str(session.get("project_path") or "")))
            != _canonical_path(Path(project_path))
            or str(session.get("project_hash") or "").casefold() != project_hash
        ):
            return None
        target = {
            "pid": pid,
            "hwnd": hwnd,
            "processCreateTime": float(process_create_time),
            "projectPath": project_path,
            "projectSha256": project_hash,
            "session": session,
        }
        if isinstance(launch, dict):
            target["launch"] = launch
        return target

    async def _verify_native_simulation(
        self,
        *,
        reopen_verification: dict[str, Any],
        build_bundle: dict[str, Any],
        trial_id: str,
    ) -> dict[str, Any]:
        """Observe the exact reopened build and return a safely restored trial receipt."""

        if reopen_verification.get("state") != "verified":
            return {
                "state": "not_verified",
                "detail": "Independent reopen must be verified before XG-SIM acceptance.",
            }
        session = reopen_verification.get("session")
        workspace = reopen_verification.get("workspace")
        reviewed = build_bundle.get("reviewedPlan")
        compiled = build_bundle.get("compiledProgram")
        engineering_plan = build_bundle.get("engineeringPlan")
        technical = build_bundle.get("technicalConfiguration")
        if not all(
            isinstance(value, dict)
            for value in (
                session,
                workspace,
                reviewed,
                compiled,
                engineering_plan,
                technical,
            )
        ):
            return {
                "state": "not_verified",
                "detail": "Reopened session or reviewed build bundle is incomplete.",
            }
        assert isinstance(session, dict)
        assert isinstance(workspace, dict)
        assert isinstance(reviewed, dict)
        assert isinstance(compiled, dict)
        assert isinstance(engineering_plan, dict)
        assert isinstance(technical, dict)
        project_path = str(session.get("project_path") or "")
        project_hash = str(session.get("project_hash") or "").casefold()
        bundle_id = str(build_bundle.get("bundleId") or "").casefold()
        reviewed_plan_digest = str(reviewed.get("planDigest") or "").casefold()
        try:
            raw = await self._gateway.call_workspace_tool(
                "project.verify_native_simulation",
                {
                    "session_id": session["session_id"],
                    "pid": int(session["pid"]),
                    "hwnd": int(session["hwnd"]),
                    "project_path": project_path,
                    "project_hash": project_hash,
                    "build_bundle": build_bundle,
                },
            )
            pre_dispatch_failure = _pre_dispatch_native_simulation_failure(
                raw,
                project_hash=project_hash,
            )
            if pre_dispatch_failure is not None:
                return {
                    "state": "not_verified",
                    "detail": (
                        "XG-SIM pre-dispatch read failed before any selected-XG simulator "
                        "command, program write, baseline capture, or CPU RUN. "
                        "The exact read-only pre-dispatch failure is retained for a normal "
                        "verification-tail retry."
                    ),
                    "failure": pre_dispatch_failure,
                    "session": session,
                }
            unresolved_failure = _unresolved_native_simulation_failure(
                raw,
                project_hash=project_hash,
            )
            if unresolved_failure is not None:
                return {
                    "state": "quarantined",
                    "detail": (
                        "XG-SIM runtime failure has retained phase evidence but does not "
                        "prove safe simulator restoration. Manual current-state recovery is "
                        "required before another simulation."
                    ),
                    "failure": unresolved_failure,
                    "session": session,
                    "trial": {
                        "schemaVersion": NATIVE_SIMULATION_TRIAL_SCHEMA,
                        "trialId": trial_id,
                        "state": "QUARANTINED",
                        "safeRestorationVerified": False,
                        "quarantinedAt": _utc_text(_utc_now()),
                        "decision": None,
                    },
                }
            preflight = raw.get("preflight") if isinstance(raw, dict) else None
            if (
                isinstance(preflight, dict)
                and preflight.get("state") in {"rejected", "not_verified"}
                and preflight.get("simulatorActionPerformed") is False
            ):
                return {
                    "state": "not_verified",
                    "detail": str(
                        preflight.get("detail")
                        or "XG-SIM acceptance profile is unsupported"
                    ),
                    "session": session,
                }
            receipt = (
                raw.get("simulation_receipt")
                if isinstance(raw, dict) and isinstance(raw.get("simulation_receipt"), dict)
                else None
            )
            if not isinstance(receipt, dict):
                raise NativeProjectEvidenceError("MCP did not return a native XG-SIM receipt")
            project_readback = receipt.get("projectReadback")
            simulation_preparation = receipt.get("simulationPreparation")
            program_binding = receipt.get("programBinding")
            simulator = receipt.get("simulator")
            scenarios = receipt.get("scenarioReceipt")
            boundary = receipt.get("inputWriteBoundary")
            if not all(
                isinstance(value, dict)
                for value in (
                    project_readback,
                    simulation_preparation,
                    program_binding,
                    simulator,
                    scenarios,
                    boundary,
                )
            ):
                raise NativeProjectEvidenceError("native XG-SIM receipt sections are incomplete")
            assert isinstance(project_readback, dict)
            assert isinstance(simulation_preparation, dict)
            assert isinstance(program_binding, dict)
            assert isinstance(simulator, dict)
            assert isinstance(scenarios, dict)
            assert isinstance(boundary, dict)
            coverage = scenarios.get("coverage")
            scenario_items = scenarios.get("scenarios")
            if not isinstance(coverage, dict) or not isinstance(scenario_items, list):
                raise NativeProjectEvidenceError("native XG-SIM scenario coverage is incomplete")
            compiled_scope = compiled.get("collectionScope")
            if not isinstance(compiled_scope, dict) or set(compiled_scope) != set(
                _NATIVE_COLLECTION_FIELDS
            ):
                raise NativeProjectEvidenceError(
                    "compiled native collection scope is unavailable for simulation"
                )
            expected_scope_statuses: dict[str, str] = {}
            for name in _NATIVE_COLLECTION_FIELDS:
                decision = compiled_scope.get(name)
                if (
                    not isinstance(decision, dict)
                    or decision.get("source") != "explicit"
                    or decision.get("status") not in {"confirmed", "not_applicable"}
                ):
                    raise NativeProjectEvidenceError(
                        f"compiled collectionScope.{name} is not explicit"
                    )
                expected_scope_statuses[name] = str(decision["status"])
            design = engineering_plan.get("design")
            if not isinstance(design, dict):
                raise NativeProjectEvidenceError("reviewed native design is missing")
            io_points = design.get("ioPoints")
            modes = design.get("operatingModes")
            networks = design.get("controlNetworks")
            timers = design.get("timers")
            sequences = design.get("sequences")
            alarms = design.get("alarms")
            recoveries = design.get("recoveryRules")
            safety_rules = design.get("safetyRules")
            ladder_rungs = design.get("ladderRungs")
            if not all(
                isinstance(value, list)
                for value in (
                    io_points,
                    modes,
                    networks,
                    timers,
                    sequences,
                    alarms,
                    recoveries,
                    safety_rules,
                    ladder_rungs,
                )
            ):
                raise NativeProjectEvidenceError(
                    "reviewed simulation signal or safety scope is invalid"
                )
            io_by_id = {
                str(item.get("signalId") or ""): item
                for item in io_points
                if isinstance(item, dict)
            }
            if "" in io_by_id or len(io_by_id) != len(io_points):
                raise NativeProjectEvidenceError("reviewed simulation I/O identities are invalid")
            ladder_ir = compiled.get("ladderIr")
            ladder_ir_sha256 = str(compiled.get("ladderIrSha256") or "")
            if (
                not networks
                or any(not isinstance(network, dict) for network in networks)
                or not isinstance(ladder_ir, dict)
                or set(ladder_ir) != {"schemaVersion", "rungs"}
                or ladder_ir.get("schemaVersion") != "controlstudio-ld-rung-ir/v1"
                or ladder_ir.get("rungs") != ladder_rungs
                or hashlib.sha256(
                    json.dumps(
                        ladder_ir,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                != ladder_ir_sha256
            ):
                raise NativeProjectEvidenceError(
                    "simulation ladder IR or control-network scope is invalid"
                )
            input_id_order = _native_simulation_stimulus_ids(
                io_points,
                networks,
                ladder_rungs,
            )
            input_ids = set(input_id_order)
            simulation_latch_id: str | None = None
            known_simulation_steps: list[dict[str, Any]] = []
            if len(alarms) == 1 and isinstance(alarms[0], dict):
                simulation_latch_id = str(alarms[0].get("latchSignalId") or "")
            if len(sequences) == 1 and isinstance(sequences[0], dict):
                fixed_steps = sequences[0].get("steps")
                if (
                    isinstance(fixed_steps, list)
                    and len(fixed_steps) == 2
                    and all(isinstance(step, dict) for step in fixed_steps)
                ):
                    known_simulation_steps = list(fixed_steps)

            def merge_ladder_states(*values: dict[str, int]) -> dict[str, int]:
                merged: dict[str, int] = {}
                for value in values:
                    for signal_id, requested in value.items():
                        previous = merged.get(signal_id)
                        if previous is not None and previous != requested:
                            raise NativeProjectEvidenceError(
                                "simulation ladder condition requires contradictory states"
                            )
                        merged[signal_id] = requested
                return merged

            def ladder_condition_state(
                value: dict[str, Any],
                *,
                desired: bool,
                fixed_contact_values: dict[tuple[str, str], int],
            ) -> dict[str, int]:
                kind = value.get("kind")
                if kind == "contact":
                    source_type = str(value.get("sourceType") or "")
                    source_id = str(value.get("sourceId") or "")
                    state = value.get("state")
                    if state not in {"on", "off"}:
                        raise NativeProjectEvidenceError(
                            "simulation OUT rung contact state is invalid"
                        )
                    requested = 1 if state == "on" else 0
                    if source_type == "signal" and source_id in input_ids:
                        return {
                            source_id: requested if desired else 1 - requested
                        }
                    if source_type == "timer":
                        raise NativeProjectEvidenceError(
                            "timer contact is not verified by the stable "
                            "network-ladder preflight profile"
                        )
                    fixed_value = fixed_contact_values.get(
                        (source_type, source_id)
                    )
                    if fixed_value is None or (fixed_value == requested) != desired:
                        raise NativeProjectEvidenceError(
                            "simulation OUT rung contains an incompatible computed contact"
                        )
                    return {}
                terms = value.get("terms")
                if (
                    kind not in {"all_of", "any_of"}
                    or not isinstance(terms, list)
                    or not 2 <= len(terms) <= 20
                    or any(not isinstance(term, dict) for term in terms)
                ):
                    raise NativeProjectEvidenceError(
                        "simulation selected OUT rung condition tree is invalid"
                    )
                conjunction = kind == "all_of"
                if desired == conjunction:
                    return merge_ladder_states(
                        *(
                            ladder_condition_state(
                                term,
                                desired=desired,
                                fixed_contact_values=fixed_contact_values,
                            )
                            for term in terms
                        )
                    )
                for selected_index, _selected in enumerate(terms):
                    try:
                        return merge_ladder_states(
                            *(
                                ladder_condition_state(
                                    term,
                                    desired=(
                                        desired
                                        if index == selected_index
                                        else not desired
                                    ),
                                    fixed_contact_values=fixed_contact_values,
                                )
                                for index, term in enumerate(terms)
                            )
                        )
                    except NativeProjectEvidenceError:
                        continue
                raise NativeProjectEvidenceError(
                    "simulation selected OUT rung has no driveable assignment"
                )

            network_bindings: list[dict[str, Any]] = []
            network_ids: list[str] = []
            output_ids: list[str] = []
            seen_network_ids: set[str] = set()
            seen_output_ids: set[str] = set()
            for network in networks:
                assert isinstance(network, dict)
                network_id = str(network.get("networkId") or "")
                output_id = str(network.get("outputSignalId") or "")
                output_point = io_by_id.get(output_id)
                if (
                    not network_id
                    or network_id in seen_network_ids
                    or not output_id
                    or output_id in seen_output_ids
                    or not isinstance(output_point, dict)
                    or output_point.get("kind")
                    not in {"digital_output", "internal_bit"}
                ):
                    raise NativeProjectEvidenceError(
                        "simulation control-network identity or output binding is invalid"
                    )
                matching_rungs = [
                    rung
                    for rung in ladder_rungs
                    if isinstance(rung, dict)
                    and isinstance(rung.get("action"), dict)
                    and rung["action"].get("instruction") == "OUT"
                    and rung["action"].get("targetType") == "signal"
                    and rung["action"].get("targetId") == output_id
                ]
                if len(matching_rungs) != 1:
                    raise NativeProjectEvidenceError(
                        f"simulation control network {network_id} does not have one "
                        "reviewed OUT rung"
                    )
                rung = matching_rungs[0]
                condition = rung.get("condition")
                if not isinstance(condition, dict):
                    raise NativeProjectEvidenceError(
                        f"simulation control network {network_id} condition is invalid"
                    )
                fixed_contact_values: dict[tuple[str, str], int] = {}
                fixed_contact_values[("signal", output_id)] = 0
                if simulation_latch_id is not None:
                    fixed_contact_values[("signal", simulation_latch_id)] = 0
                selected_step_id = network.get("stepId")
                step_context = {
                    str(step.get("stepId") or ""): (
                        1 if selected_step_id == step.get("stepId") else 0
                    )
                    for step in known_simulation_steps
                }
                fixed_contact_values.update(
                    {
                        ("step", step_id): value
                        for step_id, value in step_context.items()
                    }
                )
                true_state = ladder_condition_state(
                    condition,
                    desired=True,
                    fixed_contact_values=fixed_contact_values,
                )
                false_state = ladder_condition_state(
                    condition,
                    desired=False,
                    fixed_contact_values=fixed_contact_values,
                )
                output_feedback = (
                    output_id in _native_ladder_signal_contact_ids(condition)
                )
                if output_feedback:
                    permits = network.get("permitAll")
                    if isinstance(permits, list):
                        for permit in permits:
                            if (
                                isinstance(permit, dict)
                                and str(permit.get("signalId") or "") in input_ids
                                and permit.get("state") in {"on", "off"}
                            ):
                                false_state = {
                                    str(permit["signalId"]): (
                                        0 if permit["state"] == "on" else 1
                                    )
                                }
                                break
                transition_state: dict[str, int] = {}
                pre_transition_state: dict[str, int] = {}
                hold_state: dict[str, int] = {}
                release_step = (
                    known_simulation_steps[-1]
                    if known_simulation_steps
                    else None
                )
                if (
                    isinstance(release_step, dict)
                    and selected_step_id == release_step.get("stepId")
                ):
                    reviewed_timer = (
                        timers[0]
                        if len(timers) == 1 and isinstance(timers[0], dict)
                        else None
                    )
                    transition_conditions = (
                        reviewed_timer.get("enableAll")
                        if reviewed_timer is not None
                        else known_simulation_steps[0].get("completeAll")
                    )
                    if not isinstance(transition_conditions, list):
                        raise NativeProjectEvidenceError(
                            "terminal-step transition conditions are unavailable"
                        )
                    transition_trigger_state: dict[str, int] = {}
                    for item in transition_conditions:
                        if (
                            not isinstance(item, dict)
                            or item.get("signalId") not in input_ids
                            or item.get("state") not in {"on", "off"}
                        ):
                            raise NativeProjectEvidenceError(
                                "terminal-step transition is not driveable"
                            )
                        transition_trigger_state = merge_ladder_states(
                            transition_trigger_state,
                            {
                                str(item["signalId"]): (
                                    1 if item["state"] == "on" else 0
                                )
                            },
                        )
                    transition_state = dict(transition_trigger_state)
                    if reviewed_timer is not None and reviewed_timer.get("modeId") is not None:
                        reviewed_mode = (
                            modes[0]
                            if len(modes) == 1 and isinstance(modes[0], dict)
                            else None
                        )
                        activation = (
                            reviewed_mode.get("activation")
                            if reviewed_mode is not None
                            and reviewed_timer.get("modeId")
                            == reviewed_mode.get("modeId")
                            else None
                        )
                        if (
                            not isinstance(activation, dict)
                            or activation.get("signalId") not in input_ids
                            or activation.get("state") not in {"on", "off"}
                        ):
                            raise NativeProjectEvidenceError(
                                "terminal-step timer mode is not driveable"
                            )
                        transition_state = merge_ladder_states(
                            transition_state,
                            {
                                str(activation["signalId"]): (
                                    1 if activation["state"] == "on" else 0
                                )
                            },
                        )
                    start_conditions = known_simulation_steps and sequences[0].get(
                        "startAll"
                    )
                    if not isinstance(start_conditions, list):
                        raise NativeProjectEvidenceError(
                            "terminal-step start conditions are unavailable"
                        )
                    start_state: dict[str, int] = {}
                    for item in start_conditions:
                        if (
                            not isinstance(item, dict)
                            or item.get("signalId") not in input_ids
                            or item.get("state") not in {"on", "off"}
                        ):
                            raise NativeProjectEvidenceError(
                                "terminal-step start is not driveable"
                            )
                        start_state = merge_ladder_states(
                            start_state,
                            {
                                str(item["signalId"]): (
                                    1 if item["state"] == "on" else 0
                                )
                            },
                        )
                    for signal_id, requested in transition_trigger_state.items():
                        disabled = 1 - requested
                        if start_state.get(signal_id, disabled) == disabled:
                            pre_transition_state = {signal_id: disabled}
                            break
                    if not pre_transition_state:
                        raise NativeProjectEvidenceError(
                            "terminal-step transition cannot be held inactive while "
                            "the reviewed sequence start is asserted"
                        )
                    release_completion = release_step.get("completeAll")
                    if not isinstance(release_completion, list):
                        raise NativeProjectEvidenceError(
                            "terminal-step hold conditions are unavailable"
                        )
                    for item in release_completion:
                        if (
                            not isinstance(item, dict)
                            or item.get("signalId") not in input_ids
                            or item.get("state") not in {"on", "off"}
                        ):
                            raise NativeProjectEvidenceError(
                                "terminal-step hold is not driveable"
                            )
                        hold_state = merge_ladder_states(
                            hold_state,
                            {
                                str(item["signalId"]): (
                                    0 if item["state"] == "on" else 1
                                )
                            },
                        )
                    merge_ladder_states(
                        true_state,
                        transition_state,
                        hold_state,
                    )
                    merge_ladder_states(false_state, hold_state)
                network_bindings.append(
                    {
                        "network": network,
                        "networkId": network_id,
                        "outputSignalId": output_id,
                        "rung": rung,
                        "condition": condition,
                        "trueState": true_state,
                        "falseState": false_state,
                        "stepContext": step_context,
                        "transitionState": transition_state,
                        "preTransitionState": pre_transition_state,
                        "holdState": hold_state,
                    }
                )
                seen_network_ids.add(network_id)
                seen_output_ids.add(output_id)
                network_ids.append(network_id)
                output_ids.append(output_id)
            if (
                compiled.get("networkIds") != network_ids
                or compiled.get("outputSignalIds") != output_ids
            ):
                raise NativeProjectEvidenceError(
                    "compiled network/output order does not match the reviewed design"
                )
            expected_network_rungs = [
                {
                    "networkId": str(binding["networkId"]),
                    "outputSignalId": str(binding["outputSignalId"]),
                    "rungId": str(binding["rung"]["rungId"]),
                    "conditionKind": str(binding["condition"]["kind"]),
                    "status": "verified",
                }
                for binding in network_bindings
            ]

            def input_condition_matches(
                value: dict[str, Any],
                assignment: dict[str, int],
            ) -> bool:
                kind = value.get("kind")
                if kind == "contact":
                    source_id = str(value.get("sourceId") or "")
                    if (
                        value.get("sourceType") != "signal"
                        or source_id not in input_ids
                        or source_id not in assignment
                    ):
                        raise NativeProjectEvidenceError(
                            "MOV acceptance condition is not fully bound to reviewed "
                            "digital inputs"
                        )
                    expected = 1 if value.get("state") == "on" else 0
                    return assignment[source_id] == expected
                terms = value.get("terms")
                if (
                    kind not in {"all_of", "any_of"}
                    or not isinstance(terms, list)
                    or any(not isinstance(term, dict) for term in terms)
                ):
                    raise NativeProjectEvidenceError(
                        "MOV acceptance condition tree is invalid"
                    )
                matches = [
                    input_condition_matches(term, assignment) for term in terms
                ]
                return all(matches) if kind == "all_of" else any(matches)

            def condition_input_ids(value: dict[str, Any]) -> set[str]:
                if value.get("kind") == "contact":
                    source_id = str(value.get("sourceId") or "")
                    if value.get("sourceType") != "signal" or source_id not in input_ids:
                        raise NativeProjectEvidenceError(
                            "MOV acceptance condition is not a reviewed digital input"
                        )
                    return {source_id}
                terms = value.get("terms")
                if not isinstance(terms, list) or any(
                    not isinstance(term, dict) for term in terms
                ):
                    raise NativeProjectEvidenceError(
                        "MOV acceptance condition tree is invalid"
                    )
                return set().union(*(condition_input_ids(term) for term in terms))

            move_bindings: list[dict[str, Any]] = []
            move_by_destination: dict[str, list[dict[str, Any]]] = {}
            for rung in ladder_rungs:
                assert isinstance(rung, dict)
                action = rung.get("action")
                if (
                    not isinstance(action, dict)
                    or action.get("instruction") != "MOV"
                ):
                    continue
                condition = rung.get("condition")
                source_constant = action.get("sourceConstant")
                destination_address = action.get("destinationAddress")
                if (
                    set(action)
                    != {"instruction", "sourceConstant", "destinationAddress"}
                    or isinstance(source_constant, bool)
                    or not isinstance(source_constant, int)
                    or not -32_768 <= source_constant <= 32_767
                    or not isinstance(destination_address, str)
                    or re.fullmatch(r"D\d{5}", destination_address) is None
                    or not isinstance(condition, dict)
                ):
                    raise NativeProjectEvidenceError(
                        "reviewed MOV rung is invalid for simulation"
                    )
                binding = {
                    "rung": rung,
                    "rungId": str(rung.get("rungId") or ""),
                    "condition": condition,
                    "conditionKind": str(condition.get("kind") or ""),
                    "action": dict(action),
                }
                move_bindings.append(binding)
                move_by_destination.setdefault(destination_address, []).append(binding)
            for name, items in (
                ("operatingModes", modes),
                ("timers", timers),
                ("sequences", sequences),
                ("alarms", alarms),
                ("recoveryRules", recoveries),
            ):
                if len(items) > 1:
                    raise NativeProjectEvidenceError(
                        "simulation acceptance supports at most one confirmed "
                        f"{name} item"
                    )
            mode = modes[0] if modes and isinstance(modes[0], dict) else None
            sequence = (
                sequences[0]
                if sequences and isinstance(sequences[0], dict)
                else None
            )
            timer = timers[0] if timers and isinstance(timers[0], dict) else None
            alarm = alarms[0] if alarms and isinstance(alarms[0], dict) else None
            recovery = (
                recoveries[0]
                if recoveries and isinstance(recoveries[0], dict)
                else None
            )
            mode_output_ids = (
                [
                    str(binding["outputSignalId"])
                    for binding in network_bindings
                    if binding["network"].get("modeId") == mode.get("modeId")
                ]
                if mode is not None
                else []
            )
            sequence_steps = sequence.get("steps") if sequence is not None else None
            run_step_id = (
                str(sequence_steps[0].get("stepId") or "")
                if isinstance(sequence_steps, list)
                and sequence_steps
                and isinstance(sequence_steps[0], dict)
                else None
            )
            release_step_id = (
                str(sequence_steps[-1].get("stepId") or "")
                if isinstance(sequence_steps, list)
                and sequence_steps
                and isinstance(sequence_steps[-1], dict)
                else None
            )
            known_step_ids = (
                {
                    str(step.get("stepId") or "")
                    for step in sequence_steps
                    if isinstance(step, dict)
                }
                if isinstance(sequence_steps, list)
                else set()
            )
            sequence_output_ids = (
                [
                    str(binding["outputSignalId"])
                    for binding in network_bindings
                    if binding["network"].get("stepId") == run_step_id
                ]
                if run_step_id is not None
                else []
            )
            timer_output_ids = (
                list(sequence_output_ids)
                if timer is not None and timer.get("stepId") is not None
                else []
            )
            alarm_output_ids = (
                [str(item) for item in alarm.get("affectedOutputSignalIds", [])]
                if alarm is not None
                and isinstance(alarm.get("affectedOutputSignalIds"), list)
                else []
            )
            expected_alarm_ids = [str(alarm["alarmId"])] if alarm is not None else []
            expected_sequence_ids = (
                [str(sequence["sequenceId"])] if sequence is not None else []
            )
            optional_profile_valid = bool(
                all(
                    not items or selected is not None
                    for items, selected in (
                        (modes, mode),
                        (timers, timer),
                        (sequences, sequence),
                        (alarms, alarm),
                        (recoveries, recovery),
                    )
                )
                and (
                    all(
                        binding["network"].get("modeId") is None
                        for binding in network_bindings
                    )
                    if mode is None
                    else all(
                        binding["network"].get("modeId")
                        in {None, mode.get("modeId")}
                        for binding in network_bindings
                    )
                )
                and (
                    all(
                        binding["network"].get("stepId") is None
                        for binding in network_bindings
                    )
                    if sequence is None
                    else isinstance(sequence_steps, list)
                    and len(sequence_steps) == 2
                    and len(known_step_ids) == 2
                    and run_step_id
                    and release_step_id
                    and sequence.get("restartPolicy") == "require_start_release"
                    and all(
                        binding["network"].get("stepId") in {None, *known_step_ids}
                        for binding in network_bindings
                    )
                )
                and (
                    True
                    if timer is None
                    else (
                        timer.get("stepId") is None
                        if sequence is None
                        else timer.get("stepId") == run_step_id
                        and isinstance(sequence_steps, list)
                        and isinstance(sequence_steps[0], dict)
                        and {
                            (item.get("signalId"), item.get("state"))
                            for item in sequence_steps[0].get("completeAll", [])
                            if isinstance(item, dict)
                        }
                        == {(timer.get("timerId"), "on")}
                    )
                )
                and (
                    True
                    if alarm is None
                    else bool(alarm_output_ids)
                    and (
                        alarm.get("stepId") is None
                        if sequence is None
                        else alarm.get("stepId") == run_step_id
                    )
                )
                and (
                    recovery is not None
                    and recovery.get("resetAlarmIds") == expected_alarm_ids
                    and recovery.get("resetSequenceIds") == expected_sequence_ids
                    if alarm is not None or sequence is not None
                    else recovery is None
                )
            )
            if (
                not optional_profile_valid
                or (mode is not None and not mode_output_ids)
                or (sequence is not None and not sequence_output_ids)
                or any(output_id not in seen_output_ids for output_id in alarm_output_ids)
                or len(set(alarm_output_ids)) != len(alarm_output_ids)
            ):
                raise NativeProjectEvidenceError(
                    "simulation optional profile has no exact reviewed output subset"
                )
            plc_series = str(technical.get("plcSeries") or "").upper()

            def io_descriptor(signal_id: str) -> dict[str, Any]:
                point = io_by_id.get(signal_id)
                if not isinstance(point, dict):
                    raise NativeProjectEvidenceError(
                        f"simulation address map references unknown I/O {signal_id!r}"
                    )
                address = _canonical_native_io_address(
                    str(point.get("address") or ""),
                    plc_series,
                )
                if address is None:
                    raise NativeProjectEvidenceError(
                        f"simulation I/O address is invalid for {plc_series}: "
                        f"{str(point.get('address') or '')!r}"
                    )
                width = 4 if plc_series == "XGK" else 3 if plc_series == "XGB" else 0
                match = re.fullmatch(
                    rf"([PM])(\d{{{width}}})([0-9A-F])",
                    address,
                )
                if match is None:
                    raise NativeProjectEvidenceError(
                        f"simulation I/O address is invalid for {plc_series}: {address!r}"
                    )
                word = int(match.group(2), 10)
                word_bit = int(match.group(3), 16)
                return {
                    "identity": signal_id,
                    "address": address,
                    "area": match.group(1),
                    "byteOffset": word * 2 + word_bit // 8,
                    "bit": word_bit % 8,
                    "kind": str(point.get("kind") or ""),
                }

            expected_address_map: dict[str, Any] = {
                "inputs": [io_descriptor(signal_id) for signal_id in input_id_order],
                "outputs": [
                    io_descriptor(str(signal_id))
                    for signal_id in compiled.get("outputSignalIds", [])
                ],
                "dataWords": [
                    {
                        "address": address,
                        "area": "D",
                        "byteOffset": int(address[1:], 10) * 2,
                        "encoding": "little_endian_signed16",
                    }
                    for address in dict.fromkeys(
                        str(binding["action"]["destinationAddress"])
                        for binding in move_bindings
                    )
                ],
            }
            if alarms:
                alarm = alarms[0] if len(alarms) == 1 else None
                if not isinstance(alarm, dict):
                    raise NativeProjectEvidenceError(
                        "simulation acceptance profile has multiple alarms"
                    )
                expected_address_map["alarmLatch"] = io_descriptor(
                    str(alarm.get("latchSignalId") or "")
                )
            if timers:
                timer = timers[0] if len(timers) == 1 else None
                if not isinstance(timer, dict):
                    raise NativeProjectEvidenceError(
                        "simulation acceptance profile has multiple timers"
                    )
                timer_id = str(timer.get("timerId") or "")
                timer_address = str(timer.get("address") or "").upper()
                timer_match = re.fullmatch(r"T(\d{3,4})", timer_address)
                if timer_match is None:
                    raise NativeProjectEvidenceError("simulation timer address is invalid")
                timer_number = int(timer_match.group(1), 10)
                expected_address_map["timer"] = {
                    "identity": timer_id,
                    "address": timer_address,
                    "area": "T",
                    "byteOffset": timer_number // 8,
                    "bit": timer_number % 8,
                    "kind": "timer_done",
                }
            if sequences:
                sequence = sequences[0] if len(sequences) == 1 else None
                if not isinstance(sequence, dict):
                    raise NativeProjectEvidenceError(
                        "simulation acceptance profile has multiple sequences"
                    )
                expected_steps: list[dict[str, Any]] = []
                for step in sequence.get("steps", []):
                    if not isinstance(step, dict):
                        raise NativeProjectEvidenceError(
                            "simulation sequence step is invalid"
                        )
                    step_address = str(step.get("address") or "").upper()
                    step_match = re.fullmatch(
                        r"S(\d{2}|1[01]\d|12[0-7])\.(\d{2})",
                        step_address,
                    )
                    if step_match is None or int(step_match.group(2), 10) < 1:
                        raise NativeProjectEvidenceError(
                            "simulation sequence step address is invalid"
                        )
                    linear_bit = (
                        int(step_match.group(1), 10) * 100
                        + int(step_match.group(2), 10)
                        - 1
                    )
                    expected_steps.append(
                        {
                            "identity": str(step.get("stepId") or ""),
                            "address": step_address,
                            "area": "S",
                            "byteOffset": linear_bit // 8,
                            "bit": linear_bit % 8,
                            "kind": "sequence_step",
                        }
                    )
                expected_address_map["steps"] = expected_steps
            address_map_valid = scenarios.get("addressMap") == expected_address_map

            input_writes = scenarios.get("inputWrites")
            reviewed_input_locations = {
                signal_id: (
                    str(descriptor["area"]),
                    int(descriptor["byteOffset"]),
                )
                for signal_id, descriptor in (
                    (signal_id, io_descriptor(signal_id)) for signal_id in input_ids
                )
            }
            protected_locations = {
                (
                    str(descriptor["area"]),
                    int(descriptor["byteOffset"]),
                )
                for key, value in expected_address_map.items()
                if key != "inputs"
                for descriptor in (value if isinstance(value, list) else [value])
                if isinstance(descriptor, dict)
            }
            written_input_ids: set[str] = set()
            input_writes_valid = isinstance(input_writes, list) and bool(input_writes)
            if input_writes_valid:
                for event in input_writes:
                    records = event.get("bytes") if isinstance(event, dict) else None
                    if not isinstance(records, list) or not records:
                        input_writes_valid = False
                        break
                    for record in records:
                        requested = (
                            record.get("requestedSignals")
                            if isinstance(record, dict)
                            else None
                        )
                        if (
                            not isinstance(record, dict)
                            or not isinstance(requested, dict)
                            or not requested
                            or record.get("outputByteWritten") is not False
                            or record.get("readback") != record.get("target")
                        ):
                            input_writes_valid = False
                            break
                        location = (
                            str(record.get("area") or ""),
                            int(record.get("byteOffset", -1)),
                        )
                        if location in protected_locations:
                            input_writes_valid = False
                            break
                        for signal_id, value in requested.items():
                            if (
                                signal_id not in reviewed_input_locations
                                or type(value) is not int
                                or value not in {0, 1}
                                or reviewed_input_locations[signal_id] != location
                            ):
                                input_writes_valid = False
                                break
                            written_input_ids.add(signal_id)
                        if not input_writes_valid:
                            break
                    if not input_writes_valid:
                        break
            input_writes_valid = input_writes_valid and written_input_ids == input_ids

            baseline = receipt.get("baseline")
            restore = boundary.get("restore")
            baseline_bytes = (
                baseline.get("inputBytes") if isinstance(baseline, dict) else None
            )
            restored_bytes = (
                restore.get("finalInputBytes") if isinstance(restore, dict) else None
            )
            expected_input_byte_keys = {
                f"{area}:{offset}"
                for area, offset in reviewed_input_locations.values()
            }
            baseline_restore_bound = (
                isinstance(baseline_bytes, dict)
                and isinstance(restored_bytes, dict)
                and set(baseline_bytes) == expected_input_byte_keys
                and restored_bytes == baseline_bytes
                and all(
                    isinstance(value, str)
                    and re.fullmatch(r"[0-9A-F]{2}", value) is not None
                    for value in baseline_bytes.values()
                )
            )

            allowed_observation_ids = {
                *io_by_id,
                *(str(item) for item in compiled.get("timerIds", [])),
                *(str(item) for item in compiled.get("stepIds", [])),
            }

            def scenario_trace_valid(item: object) -> bool:
                if not isinstance(item, dict):
                    return False
                action = item.get("action")
                if isinstance(action, dict) and action.get("instruction") == "MOV":
                    observation = item.get("observation")
                    return (
                        isinstance(observation, dict)
                        and set(observation)
                        == {
                            "address",
                            "byteOffset",
                            "encoding",
                            "beforeValue",
                            "expectedValue",
                            "observedValue",
                            "matched",
                        }
                        and isinstance(observation.get("address"), str)
                        and type(observation.get("byteOffset")) is int
                        and observation.get("encoding")
                        == "little_endian_signed16"
                        and type(observation.get("beforeValue")) is int
                        and -32_768 <= observation["beforeValue"] <= 32_767
                        and type(observation.get("expectedValue")) is int
                        and type(observation.get("observedValue")) is int
                        and observation["beforeValue"] != observation["expectedValue"]
                        and observation["observedValue"] == observation["expectedValue"]
                        and observation.get("matched") is True
                    )
                single = item.get("observation")
                multiple = item.get("observations")
                traces = [single] if isinstance(single, dict) else multiple
                if (
                    not isinstance(traces, list)
                    or not traces
                    or any(not isinstance(trace, dict) for trace in traces)
                ):
                    return False
                for trace in traces:
                    signals = trace.get("signals")
                    if (
                        not isinstance(signals, dict)
                        or not signals
                        or any(
                            signal_id not in allowed_observation_ids
                            or type(value) is not int
                            or value not in {0, 1}
                            for signal_id, value in signals.items()
                        )
                    ):
                        return False
                return True

            scenario_traces_valid = all(
                scenario_trace_valid(item) for item in scenario_items
            )
            process_interlocks = [
                item
                for item in safety_rules
                if isinstance(item, dict) and item.get("classification") == "process_interlock"
            ]
            alarm_latch_id = (
                str(alarm.get("latchSignalId") or "")
                if alarm is not None
                else None
            )
            if any(
                not isinstance(item.get("permitWhen"), dict)
                or item["permitWhen"].get("signalId")
                not in {*input_ids, alarm_latch_id}
                for item in process_interlocks
            ):
                raise NativeProjectEvidenceError(
                    "simulation process interlock is not exactly driveable by a reviewed "
                    "simulation stimulus or exercised alarm latch"
                )
            process_interlock_ids = [str(item["ruleId"]) for item in process_interlocks]
            external_boundary_ids = [
                str(item["ruleId"])
                for item in safety_rules
                if isinstance(item, dict)
                and item.get("classification") == "external_safety_boundary"
            ]
            actual_move_rungs = coverage.get("moveRungs")
            move_coverage_valid = (
                isinstance(actual_move_rungs, list)
                and len(actual_move_rungs) == len(move_bindings)
            )
            if move_coverage_valid:
                for actual, binding in zip(
                    actual_move_rungs,
                    move_bindings,
                    strict=True,
                ):
                    assignment = (
                        actual.get("inputAssignment")
                        if isinstance(actual, dict)
                        else None
                    )
                    if (
                        not isinstance(actual, dict)
                        or set(actual)
                        != {
                            "rungId",
                            "conditionKind",
                            "action",
                            "inputAssignment",
                            "status",
                        }
                        or actual.get("rungId") != binding["rungId"]
                        or actual.get("conditionKind") != binding["conditionKind"]
                        or actual.get("action") != binding["action"]
                        or actual.get("status") != "verified"
                        or not isinstance(assignment, dict)
                        or not assignment
                        or any(
                            signal_id not in input_ids
                            or type(value) is not int
                            or value not in {0, 1}
                            for signal_id, value in assignment.items()
                        )
                    ):
                        move_coverage_valid = False
                        break
                    destination = str(binding["action"]["destinationAddress"])
                    try:
                        expected_assignment_ids = set().union(
                            *(
                                condition_input_ids(peer["condition"])
                                for peer in move_by_destination[destination]
                            )
                        )
                        active = [
                            peer
                            for peer in move_by_destination[destination]
                            if input_condition_matches(peer["condition"], assignment)
                        ]
                    except NativeProjectEvidenceError:
                        move_coverage_valid = False
                        break
                    if (
                        set(assignment) != expected_assignment_ids
                        or len(active) != 1
                        or active[0] is not binding
                    ):
                        move_coverage_valid = False
                        break
            expected_coverage = {
                "collectionScope": expected_scope_statuses,
                "inputSignalIds": sorted(input_ids),
                "modeIds": [str(item["modeId"]) for item in modes if isinstance(item, dict)],
                "networkIds": list(compiled.get("networkIds") or []),
                "outputSignalIds": list(compiled.get("outputSignalIds") or []),
                "networkRungs": expected_network_rungs,
                "timerIds": list(compiled.get("timerIds") or []),
                "sequenceIds": list(compiled.get("sequenceIds") or []),
                "sequenceRestartPolicies": dict(compiled.get("sequenceRestartPolicies") or {}),
                "stepIds": list(compiled.get("stepIds") or []),
                "alarmIds": list(compiled.get("alarmIds") or []),
                "recoveryIds": list(compiled.get("recoveryIds") or []),
                "processInterlockIds": process_interlock_ids,
                "documentedExternalSafetyBoundaryIds": external_boundary_ids,
            }
            coverage_without_moves = (
                {
                    key: value
                    for key, value in coverage.items()
                    if key != "moveRungs"
                }
                if isinstance(coverage, dict)
                else None
            )
            scenario_id_values = [
                str(item.get("scenarioId") or "")
                for item in scenario_items
                if isinstance(item, dict)
            ]
            scenario_ids_unique = (
                len(scenario_id_values) == len(scenario_items)
                and "" not in scenario_id_values
                and len(set(scenario_id_values)) == len(scenario_id_values)
            )
            scenario_by_id = {
                str(item["scenarioId"]): item
                for item in scenario_items
                if isinstance(item, dict) and item.get("status") == "passed"
            }
            scenario_ids = set(scenario_by_id)
            required_scenarios = {
                *(f"mode_removed:{item['modeId']}" for item in modes if isinstance(item, dict)),
                *(f"fault_recovery:{item}" for item in compiled.get("alarmIds", [])),
                *(f"network_ladder:{network_id}" for network_id in network_ids),
                *(
                    f"move_ladder:{binding['rungId']}"
                    for binding in move_bindings
                ),
                *(
                    f"process_interlock:{item['ruleId']}"
                    for item in process_interlocks
                ),
            }
            expected_scenario_ids = {"initial_normal_start", *required_scenarios}
            if timer is not None:
                expected_scenario_ids.add(
                    f"timer_rearm:{timer['timerId']}"
                    if sequence is not None
                    else f"timer_complete:{timer['timerId']}"
                )
            elif sequence is not None:
                expected_scenario_ids.add(
                    f"sequence_rearm:{sequence['sequenceId']}"
                )
            if sequence is not None and recovery is not None:
                expected_scenario_ids.add(
                    f"sequence_recovery:{recovery['recoveryId']}"
                )

            def scenario_output_binding_valid(
                item: object,
                expected_output_ids: list[str],
                expected_states: list[int],
            ) -> bool:
                if (
                    not isinstance(item, dict)
                    or item.get("outputSignalIds") != expected_output_ids
                ):
                    return False
                single = item.get("observation")
                multiple = item.get("observations")
                traces = [single] if isinstance(single, dict) else multiple
                if not isinstance(traces, list) or not traces:
                    return False
                observed: dict[str, list[int]] = {
                    output_id: [] for output_id in expected_output_ids
                }
                for trace in traces:
                    signals = trace.get("signals") if isinstance(trace, dict) else None
                    if not isinstance(signals, dict):
                        return False
                    trace_outputs = [
                        output_id for output_id in output_ids if output_id in signals
                    ]
                    if expected_output_ids:
                        if len(trace_outputs) != 1:
                            return False
                    elif trace_outputs:
                        return False
                    for output_id in trace_outputs:
                        if output_id not in observed:
                            return False
                        value = signals[output_id]
                        if type(value) is not int or value not in {0, 1}:
                            return False
                        observed[output_id].append(value)
                return all(
                    observed[output_id] == expected_states
                    for output_id in expected_output_ids
                )

            network_ladder_profile_valid = True
            for binding in network_bindings:
                network_id = str(binding["networkId"])
                output_id = str(binding["outputSignalId"])
                item = scenario_by_id.get(f"network_ladder:{network_id}")
                observations = item.get("observations") if isinstance(item, dict) else None
                expected_observation_states = [1, 0, 1]
                selected_step_id = binding["network"].get("stepId")
                observed_states = (
                    [
                        trace.get("signals", {}).get(output_id)
                        for trace in observations
                        if isinstance(trace, dict)
                    ]
                    if isinstance(observations, list)
                    else []
                )
                if not (
                    isinstance(item, dict)
                    and item.get("networkId") == network_id
                    and item.get("outputSignalId") == output_id
                    and item.get("rungId") == binding["rung"].get("rungId")
                    and item.get("conditionKind")
                    == binding["condition"].get("kind")
                    and item.get("assertions")
                    == {
                        "normalConditionEnergizedOutput": True,
                        "deniedConditionDeenergizedOutput": True,
                        "restoredConditionReenergizedOutput": True,
                    }
                    and item.get("inputAssignments")
                    == {
                        "normal": binding["trueState"],
                        "deny": binding["falseState"],
                        "restore": binding["trueState"],
                    }
                    and isinstance(observations, list)
                    and len(observations) == 3
                    and observed_states == expected_observation_states
                    and all(
                        isinstance(trace, dict)
                        and trace.get("signals")
                        == {
                            output_id: expected_state,
                            **(
                                {str(selected_step_id): 1}
                                if selected_step_id is not None
                                else {}
                            ),
                        }
                        for trace, expected_state in zip(
                            observations,
                            expected_observation_states,
                            strict=True,
                        )
                    )
                ):
                    network_ladder_profile_valid = False
                    break

            move_scenario_profile_valid = move_coverage_valid
            if move_scenario_profile_valid:
                assert isinstance(actual_move_rungs, list)
                for binding, move_coverage in zip(
                    move_bindings,
                    actual_move_rungs,
                    strict=True,
                ):
                    rung_id = str(binding["rungId"])
                    action = binding["action"]
                    destination = str(action["destinationAddress"])
                    expected_value = int(action["sourceConstant"])
                    item = scenario_by_id.get(f"move_ladder:{rung_id}")
                    if (
                        not isinstance(item, dict)
                        or set(item)
                        != {
                            "scenarioId",
                            "rungId",
                            "conditionKind",
                            "action",
                            "inputAssignment",
                            "status",
                            "assertions",
                            "observation",
                        }
                        or item.get("rungId") != rung_id
                        or item.get("conditionKind") != binding["conditionKind"]
                        or item.get("action") != action
                        or item.get("inputAssignment")
                        != move_coverage.get("inputAssignment")
                        or item.get("assertions")
                        != {
                            "singleWriterSelected": True,
                            "destinationMatchedExpectedValue": True,
                        }
                        or not isinstance(item.get("observation"), dict)
                        or item["observation"]
                        != {
                            "address": destination,
                            "byteOffset": int(destination[1:], 10) * 2,
                            "encoding": "little_endian_signed16",
                            "beforeValue": item["observation"].get("beforeValue"),
                            "expectedValue": expected_value,
                            "observedValue": expected_value,
                            "matched": True,
                        }
                        or type(item["observation"].get("beforeValue")) is not int
                        or item["observation"]["beforeValue"] == expected_value
                    ):
                        move_scenario_profile_valid = False
                        break

            optional_scenario_bindings_valid = True
            if mode is not None:
                optional_scenario_bindings_valid = scenario_output_binding_valid(
                    scenario_by_id.get(f"mode_removed:{mode['modeId']}"),
                    mode_output_ids,
                    [0],
                )
            if timer is not None:
                timer_scenario_id = (
                    f"timer_rearm:{timer['timerId']}"
                    if sequence is not None
                    else f"timer_complete:{timer['timerId']}"
                )
                optional_scenario_bindings_valid = (
                    optional_scenario_bindings_valid
                    and scenario_output_binding_valid(
                        scenario_by_id.get(timer_scenario_id),
                        timer_output_ids,
                        [0, 0, 0, 1] if sequence is not None else [],
                    )
                )
            elif sequence is not None:
                optional_scenario_bindings_valid = (
                    optional_scenario_bindings_valid
                    and scenario_output_binding_valid(
                        scenario_by_id.get(
                            f"sequence_rearm:{sequence['sequenceId']}"
                        ),
                        sequence_output_ids,
                        [0, 0],
                    )
                )
            if sequence is not None and recovery is not None:
                optional_scenario_bindings_valid = (
                    optional_scenario_bindings_valid
                    and scenario_output_binding_valid(
                        scenario_by_id.get(
                            f"sequence_recovery:{recovery['recoveryId']}"
                        ),
                        sequence_output_ids,
                        [0],
                    )
                )
            if alarm is not None:
                optional_scenario_bindings_valid = (
                    optional_scenario_bindings_valid
                    and scenario_output_binding_valid(
                        scenario_by_id.get(f"fault_recovery:{alarm['alarmId']}"),
                        alarm_output_ids,
                        [0, 0, 0],
                    )
                )
            for rule in process_interlocks:
                permit = rule.get("permitWhen")
                if not isinstance(permit, dict):
                    optional_scenario_bindings_valid = False
                    continue
                affected_output_ids = [
                    str(output_id)
                    for output_id in rule.get("affectedOutputSignalIds", [])
                ]
                optional_scenario_bindings_valid = (
                    optional_scenario_bindings_valid
                    and bool(affected_output_ids)
                    and len(set(affected_output_ids)) == len(affected_output_ids)
                    and all(
                        output_id in seen_output_ids
                        for output_id in affected_output_ids
                    )
                    and scenario_output_binding_valid(
                        scenario_by_id.get(
                            f"process_interlock:{rule['ruleId']}"
                        ),
                        affected_output_ids,
                        [0, 1],
                    )
                )
            timer_scenarios_complete = all(
                f"timer_rearm:{timer_id}" in scenario_ids
                or f"timer_complete:{timer_id}" in scenario_ids
                for timer_id in compiled.get("timerIds", [])
            )
            sequence_scenarios_complete = all(
                f"sequence_rearm:{sequence_id}" in scenario_ids
                or any(
                    scenario_id.startswith("timer_rearm:")
                    for scenario_id in scenario_ids
                )
                for sequence_id in compiled.get("sequenceIds", [])
            )
            recovery_scenarios_complete = all(
                f"sequence_recovery:{recovery_id}" in scenario_ids
                or any(
                    isinstance(item, dict)
                    and str(item.get("scenarioId") or "").startswith(
                        "fault_recovery:"
                    )
                    and item.get("recoveryId") == recovery_id
                    for item in scenario_items
                )
                for recovery_id in compiled.get("recoveryIds", [])
            )
            normal_scenarios = [
                item
                for item in scenario_items
                if isinstance(item, dict)
                and str(item.get("scenarioId") or "").endswith("_normal_start")
            ]
            normal_scenarios_bound = all(
                isinstance(item.get("outputSignalId"), str)
                and item["outputSignalId"] in seen_output_ids
                and isinstance(item.get("observation"), dict)
                and isinstance(item["observation"].get("signals"), dict)
                and item["observation"]["signals"].get(item["outputSignalId"])
                == 1
                for item in normal_scenarios
            )
            sequence_assertion_present = any(
                isinstance(item.get("assertions"), dict)
                and item["assertions"].get("runStepActive") is True
                for item in normal_scenarios
            )
            sequence_scope_confirmed = (
                expected_scope_statuses["sequences"] == "confirmed"
            )
            unexpected_na_scenario = any(
                (
                    expected_scope_statuses["operatingModes"] == "not_applicable"
                    and scenario_id.startswith("mode_removed:")
                )
                or (
                    expected_scope_statuses["timers"] == "not_applicable"
                    and scenario_id.startswith(("timer_rearm:", "timer_complete:"))
                )
                or (
                    expected_scope_statuses["alarms"] == "not_applicable"
                    and scenario_id.startswith("fault_recovery:")
                )
                or (
                    expected_scope_statuses["sequences"] == "not_applicable"
                    and scenario_id.startswith("sequence_rearm:")
                )
                or (
                    expected_scope_statuses["recoveryRules"] == "not_applicable"
                    and scenario_id.startswith(
                        ("sequence_recovery:", "fault_recovery:")
                    )
                )
                or (
                    expected_scope_statuses["safetyRules"] == "not_applicable"
                    and scenario_id.startswith("process_interlock:")
                )
                for scenario_id in scenario_ids
            )
            expected_instruction_readback = str(compiled.get("source") or "").splitlines()
            expected_post_write_readback = {
                "programName": compiled.get("programName"),
                "language": "LD",
                "sourceSha256": compiled.get("sourceSha256"),
                "rungCount": len(compiled.get("rungIds", [])),
                "rungIds": compiled.get("rungIds"),
                "ladderIrSha256": compiled.get("ladderIrSha256"),
                "exactInstructionReadback": expected_instruction_readback,
                "finalView": "LD",
                "programCheckPassed": True,
            }
            prepared_hash = str(project_readback.get("projectSha256") or "").casefold()
            hash_after_start = str(
                simulation_preparation.get("hashAfterSimulatorStart") or ""
            ).casefold()
            hash_after_write = str(
                simulation_preparation.get("hashAfterProgramWrite") or ""
            ).casefold()
            preparation_hashes_valid = all(
                re.fullmatch(r"[0-9a-f]{64}", value) is not None
                for value in (project_hash, hash_after_start, hash_after_write, prepared_hash)
            )
            hash_transition_observed = len(
                {project_hash, hash_after_start, hash_after_write}
            ) > 1

            workspace_id = str(workspace.get("workspace_id") or "")
            _canonical_uuid(workspace_id, "workspace_id")
            refreshed_workspace = await self._gateway.call_workspace_tool(
                "workspace.status",
                {"workspace_id": workspace_id},
            )
            if not isinstance(refreshed_workspace, dict):
                raise NativeProjectEvidenceError(
                    "MCP returned no workspace status after XG-SIM"
                )
            original_workspace_source = workspace.get("source")
            refreshed_source = refreshed_workspace.get("source")
            refreshed_working = refreshed_workspace.get("working_copy")
            workspace_transition_valid = (
                refreshed_workspace.get("ok") is True
                and refreshed_workspace.get("verified") is True
                and refreshed_workspace.get("workspace_id") == workspace_id
                and refreshed_workspace.get("revision_id") == workspace.get("revision_id")
                and isinstance(original_workspace_source, dict)
                and isinstance(refreshed_source, dict)
                and isinstance(refreshed_working, dict)
                and refreshed_source.get("sha256") == project_hash
                and refreshed_source.get("original_state") == "unchanged"
                and refreshed_source.get("original_current_sha256") == project_hash
                and _canonical_path(Path(str(refreshed_source.get("captured_path") or "")))
                == _canonical_path(
                    Path(str(original_workspace_source.get("captured_path") or ""))
                )
                and _canonical_path(Path(str(refreshed_working.get("path") or "")))
                == _canonical_path(Path(project_path))
                and refreshed_working.get("baseline_sha256") == project_hash
                and refreshed_working.get("current_sha256") == prepared_hash
                and refreshed_working.get("state")
                == ("clean" if prepared_hash == project_hash else "modified")
            )

            refreshed = await self._gateway.call_read_tool(
                "xg5000.session_status",
                {},
            )
            refreshed_session = (
                refreshed.get("session")
                if isinstance(refreshed, dict) and isinstance(refreshed.get("session"), dict)
                else refreshed
            )
            if not isinstance(refreshed_session, dict):
                raise NativeProjectEvidenceError(
                    "MCP returned no refreshed XG5000 session after XG-SIM"
                )
            refreshed_metadata = refreshed_session.get("metadata")
            session_transition_valid = (
                refreshed_session.get("session_id") == session.get("session_id")
                and refreshed_session.get("pid") == session.get("pid")
                and refreshed_session.get("hwnd") == session.get("hwnd")
                and _canonical_path(
                    Path(str(refreshed_session.get("project_path") or ""))
                )
                == _canonical_path(Path(project_path))
                and str(refreshed_session.get("project_hash") or "").casefold()
                == prepared_hash
                and isinstance(refreshed_metadata, dict)
                and refreshed_metadata.get("project_identity_assurance")
                == "controlled_launch_read_only"
            )
            if (
                receipt.get("schemaVersion") != NATIVE_SIMULATION_RECEIPT_SCHEMA
                or receipt.get("status") != "SIMULATION_VERIFIED"
                or receipt.get("buildBundleId") != bundle_id
                or receipt.get("reviewedPlanDigest") != reviewed_plan_digest
                or project_readback
                != {
                    "projectPath": project_path,
                    "projectSha256": prepared_hash,
                    "reopenedProjectSha256": project_hash,
                    "projectUnchangedDuringRuntime": True,
                    "identityAssurance": "controlled_launch_read_only",
                }
                or not preparation_hashes_valid
                or simulation_preparation
                != {
                    "hashBeforeSimulatorStart": project_hash,
                    "hashAfterSimulatorStart": hash_after_start,
                    "hashAfterProgramWrite": hash_after_write,
                    "hashTransitionObserved": hash_transition_observed,
                    "programReadback": expected_post_write_readback,
                }
                or hash_after_write != prepared_hash
                or not workspace_transition_valid
                or not session_transition_valid
                or program_binding
                != {
                    "programName": compiled.get("programName"),
                    "sourceSha256": compiled.get("sourceSha256"),
                    "rungCount": len(compiled.get("rungIds", [])),
                    "rungIds": compiled.get("rungIds"),
                    "ladderIrSha256": compiled.get("ladderIrSha256"),
                    "compiledProgramSchema": NATIVE_COMPILED_PROGRAM_SCHEMA,
                }
                or simulator.get("product") != "XG-SIM"
                or simulator.get("bridge") != "XimUtil.DeviceInterface"
                or simulator.get("programWritePerformed") is not True
                or simulator.get("cpuRunVerified") is not True
                or simulator.get("cpuStopped") is not True
                or not _native_simulator_evidence_verified(simulator)
                or scenarios.get("schemaVersion") != "controlstudio-native-simulation-scenarios/v3"
                or scenarios.get("status") != "SCENARIOS_VERIFIED"
                or scenarios.get("scenarioCount") != len(scenario_items)
                or not scenario_ids_unique
                or any(
                    not isinstance(item, dict)
                    or item.get("status") != "passed"
                    or not isinstance(item.get("assertions"), dict)
                    or not item["assertions"]
                    or any(value is not True for value in item["assertions"].values())
                    for item in scenario_items
                )
                or not any(value.endswith("_normal_start") for value in scenario_ids)
                or scenario_ids != expected_scenario_ids
                or not network_ladder_profile_valid
                or not move_coverage_valid
                or not move_scenario_profile_valid
                or not optional_scenario_bindings_valid
                or not timer_scenarios_complete
                or not sequence_scenarios_complete
                or not recovery_scenarios_complete
                or sequence_assertion_present != sequence_scope_confirmed
                or not normal_scenarios_bound
                or unexpected_na_scenario
                or coverage_without_moves != expected_coverage
                or not address_map_valid
                or not input_writes_valid
                or not baseline_restore_bound
                or not scenario_traces_valid
                or scenarios.get("outputBytesWritten") is not False
                or boundary.get("onlyReviewedSimulationStimuli") is not True
                or boundary.get("outputBytesWritten") is not False
                or boundary.get("baselineRestoredAfterCpuStop") is not True
                or not isinstance(boundary.get("restore"), dict)
                or boundary["restore"].get("attempted") is not True
                or boundary["restore"].get("matched") is not True
                or boundary["restore"].get("cpuStoppedBeforeRestore") is not True
                or receipt.get("controllerConnected") is not False
                or receipt.get("plcWritePerformed") is not False
            ):
                raise NativeProjectEvidenceError(
                    "native XG-SIM receipt does not cover the exact reviewed build"
                )
            self._gateway.bind_agent_session(refreshed_session)
            workspace.clear()
            workspace.update(refreshed_workspace)
            receipt_digest = _json_digest(receipt)
            verified_scope = [
                name
                for name, status in expected_scope_statuses.items()
                if status == "confirmed"
            ]
            external_boundary_detail = (
                " Confirmed external safety boundaries were documented but remained "
                "outside XG-SIM."
                if external_boundary_ids
                else ""
            )
            result = {
                "state": "verified",
                "detail": (
                    "Exact reviewed build passed its scope-bound XG-SIM scenarios for "
                    f"confirmed collections ({', '.join(verified_scope)}); N/A "
                    "collections were not exercised."
                    f"{external_boundary_detail} CPU was stopped and original input "
                    "bytes were restored. The receipt is awaiting an explicit accept "
                    "or revert decision."
                ),
                "receipt": receipt,
                "session": refreshed_session,
                "workspace": workspace,
                "trial": {
                    "schemaVersion": NATIVE_SIMULATION_TRIAL_SCHEMA,
                    "trialId": trial_id,
                    "state": "OBSERVING",
                    "receiptDigest": receipt_digest,
                    "observedAt": str(scenarios["verifiedAt"]),
                    "safeRestorationVerified": True,
                    "decision": None,
                },
            }
            return result
        except (RuntimeError, KeyError, TypeError, ValueError, OSError) as exc:
            return {
                "state": "quarantined",
                "detail": (
                    "XG-SIM observation did not return a complete safe-restoration receipt; "
                    f"manual simulator recovery is required before retry: {exc}"
                ),
                "session": session,
                "trial": {
                    "schemaVersion": NATIVE_SIMULATION_TRIAL_SCHEMA,
                    "trialId": trial_id,
                    "state": "QUARANTINED",
                    "safeRestorationVerified": False,
                    "quarantinedAt": _utc_text(_utc_now()),
                    "decision": None,
                },
            }

    @staticmethod
    def _validate_workspace_capture(
        raw: Any,
        *,
        project_path: Path,
        project_hash: str,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            not isinstance(raw, dict)
            or raw.get("ok") is not True
            or raw.get("verified") is not True
        ):
            raise NativeProjectEvidenceError("workspace capture was not verified")
        _canonical_uuid(str(raw.get("workspace_id") or ""), "workspace_id")
        _canonical_uuid(str(raw.get("revision_id") or ""), "revision_id")
        source = raw.get("source")
        working = raw.get("working_copy")
        if not isinstance(source, dict) or not isinstance(working, dict):
            raise NativeProjectEvidenceError("workspace artifact identity is incomplete")
        if (
            raw.get("source_kind") != "new_build"
            or raw.get("provenance") != provenance
            or provenance.get("saved_project_sha256") != project_hash
            or provenance.get("receipt_digest")
            != _json_digest(
                {key: value for key, value in provenance.items() if key != "receipt_digest"}
            )
            or _canonical_path(Path(str(source.get("original_path") or "")))
            != _canonical_path(project_path)
            or source.get("sha256") != project_hash
            or working.get("baseline_sha256") != project_hash
            or working.get("current_sha256") != project_hash
            or working.get("state") != "clean"
        ):
            raise NativeProjectEvidenceError(
                "workspace capture does not match the created native project"
            )
        for field, container in (
            ("captured_path", source),
            ("path", working),
        ):
            path = Path(str(container.get(field) or "")).resolve()
            if not path.is_file() or path.is_symlink() or _sha256_file(path) != project_hash:
                raise NativeProjectEvidenceError(
                    f"workspace {field} does not match the created project"
                )
        return raw


__all__ = [
    "NativeProjectConflict",
    "NativeProjectEvidenceError",
    "NativeProjectOperation",
    "NativeProjectPreconditionError",
    "NativeProjectRepository",
    "NativeProjectService",
    "NativeProjectSpec",
    "validated_new_build_workspace_provenance",
]
