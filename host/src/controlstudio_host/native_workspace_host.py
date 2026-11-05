"""Run-scoped native LD authority; no sidecar filesystem or GUI identity."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
import uuid
import zlib
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from controlstudio_contracts import AgentToolDefinitionV1
from controlstudio_contracts.native_failure import validate_native_failure
from controlstudio_contracts.native_ld_commands import (
    native_ld_optional_fields,
    native_ld_required_fields,
    native_ld_save_request,
)
from controlstudio_contracts.native_simulator_addresses import (
    resolved_simulator_devices,
    simulator_bit_key,
)

from .agent_protocol import (
    ValidatedNativeCheckpoint,
    _without_host_binding_admission,
    is_native_failure_field,
    native_checkpoint_boundary,
    native_checkpoint_objective,
    scrub_native_tool_result,
    validated_native_checkpoint,
    validated_native_checkpoint_snapshot,
)
from .mcp_client import MCPToolError, MCPToolPreflightError, MCPToolRejectedError

_ACTION_FIELDS = {**native_ld_required_fields(), 'observe': set(), 'activate': set(), 'activate_check_result': {"result_ref", "row_index"}, 'read_find_results': {"program_name"}, 'find_ld_device': {"program_name", "query"}, 'find_ld_devices': {"program_name", "queries"}, 'respond_owned_ui': {"ui_ref", "response"}, 'inspect_rung_comment': {"program_name", "payload_sha256", "visual_row"}, 'set_rung_comment': {"comment_ref", "comment"}, 'inspect_variable_comment': {"program_name", "visual_row", "column", "expected_element"}, 'set_variable_comment': {"variable_ref", "comment"}, 'inspect_abandoned_operation': set(), 'settle_abandoned_operation': {"recovery_ref"}}


def _operation_quarantine_projection(error: dict[str, Any]) -> dict[str, Any] | None:
    if (not isinstance(error, dict) or error.get("code") != "XG5000_OPERATION_QUARANTINED"
            or error.get("type") != "OperationQuarantinedError"):
        return None
    recovery = error.get("operationRecovery")
    if (not isinstance(recovery, dict) or recovery.get("state") != "quarantined"
            or any(recovery.get(key) is not True for key in
                   ("quarantined", "inspection_required", "old_outcome_unknown"))
            or recovery.get("automatic_replay") is not False
            or type(recovery.get("input_state_unknown")) is not bool):
        return None
    unknown = recovery["input_state_unknown"]
    if ((not unknown and recovery.get("action_attempted") is not False)
            or (unknown and "action_attempted" in recovery)):
        return None
    return {
        "input_state_unknown": unknown, "automatic_replay": False,
        **({"action_attempted": False} if not unknown else {}),
        "operation_recovery": {"state": "quarantined", "quarantined": True,
                               "inspection_required": True, "old_outcome_unknown": True},
    }


def _valid_instruction_move_arguments(arguments: dict[str, Any]) -> bool:
    def triple(value: Any) -> bool:
        return (isinstance(value, dict) and set(value) == {"opcode", "coordinate", "value"}
                and isinstance(value["opcode"], str) and bool(value["opcode"])
                and type(value["coordinate"]) is int and value["coordinate"] >= 0
                and isinstance(value["value"], str))

    expected, destination = arguments["expected_element"], arguments["destination"]
    if (type(arguments["visual_row"]) is not int or arguments["visual_row"] < 0
            or type(arguments["column"]) is not int or arguments["column"] < 0
            or not triple(expected) or expected["opcode"] != "33" or not expected["value"]
            or not isinstance(destination, dict)
            or set(destination) != {"visual_row", "row_header", "records"}
            or type(destination["visual_row"]) is not int or destination["visual_row"] < 0
            or not isinstance(destination["records"], list)
            or not all(triple(record) for record in destination["records"])):
        return False
    header = destination["row_header"]
    # Keep the actual producer's additional header metadata for native equality.
    return (isinstance(header, dict) and header.get("opcode") == "67"
            and type(header.get("coordinate")) is int and header["coordinate"] >= 0
            and isinstance(header.get("value"), str))


def _valid_row_terminal_arguments(arguments: dict[str, Any]) -> bool:
    row = arguments["expected_row"]
    if not isinstance(row, dict) or set(row) != {"row_header", "records"}:
        return False
    # The same literal instruction/header/record shapes are validated for both
    # operations; native placement and route proof remain the CLI's responsibility.
    return _valid_instruction_move_arguments({
        "visual_row": arguments["visual_row"], "column": arguments["column"],
        "expected_element": arguments["expected_element"],
        "destination": {"visual_row": arguments["visual_row"], **row},
    })


def _valid_row_deletion_arguments(arguments: dict[str, Any]) -> bool:
    first, last = arguments["visual_row_start"], arguments["visual_row_end"]
    rows = arguments["expected_rows"]
    if (type(first) is not int or type(last) is not int or first < 0 or last < first
            or not isinstance(rows, list) or len(rows) != last - first + 1):
        return False
    for offset, row in enumerate(rows):
        if (not isinstance(row, dict) or set(row) != {"visual_row", "row_header", "records"}
                or type(row["visual_row"]) is not int or row["visual_row"] != first + offset
                or not isinstance(row["records"], list)):
            return False
        header = row["row_header"]
        if (not isinstance(header, dict) or set(header) != {"opcode", "coordinate", "value"}
                or not isinstance(header["opcode"], str) or not header["opcode"]
                or type(header["coordinate"]) is not int or header["coordinate"] < 0
                or not isinstance(header["value"], str)):
            return False
        for record in row["records"]:
            if (not isinstance(record, dict) or set(record) != {"opcode", "coordinate", "column", "value"}
                    or not isinstance(record["opcode"], str) or not record["opcode"]
                    or type(record["coordinate"]) is not int or record["coordinate"] < 0
                    or type(record["column"]) is not int or record["column"] < 0
                    or not isinstance(record["value"], str)):
                return False
    return True


_SAVED_SECTION_TAGS = ("Program", "RungTable", "GlobalVariables", "VariableComment", "Symbols")
_SAVED_SECTION_MEANING = (
    "Each fingerprint covers one serialized XML subtree at a sibling-indexed path. "
    "Counts are observed XML tags; declared_count is the stored Count attribute."
)


def _saved_xml_sections(root: ElementTree.Element) -> dict[str, Any]:
    categories = {tag: {"count": 0, "items": []} for tag in _SAVED_SECTION_TAGS}

    def walk(node: ElementTree.Element, identity: str, program_name: str | None) -> None:
        tag = node.tag.rsplit("}", 1)[-1]
        name = node.get("Name") or node.get("InstanceName")
        if tag == "Program":
            name = str(node.text or name or "").strip()
            program_name = name or None
        if tag in categories:
            item = {"identity": identity,
                    "sha256": hashlib.sha256(ElementTree.tostring(node, encoding="utf-8")).hexdigest()}
            if name:
                item["name"] = name
            if program_name and tag != "Program":
                item["program_name"] = program_name
            if "Count" in node.attrib:
                # A serialized attribute, not a count inferred by decoding its payload.
                item["declared_count"] = node.attrib["Count"]
            categories[tag]["items"].append(item)
        positions: dict[str, int] = {}
        for child in node:
            child_tag = child.tag.rsplit("}", 1)[-1]
            index = positions.get(child_tag, 0)
            positions[child_tag] = index + 1
            walk(child, f"{identity}/{child_tag}[{index}]", program_name)

    walk(root, "/" + root.tag.rsplit("}", 1)[-1], None)
    for category in categories.values():
        category["count"] = len(category["items"])
    return {"status": "observed", "source": "same_hashed_saved_project_xml",
            "fingerprint_kind": "sha256_elementtree_utf8_subtree", "meaning": _SAVED_SECTION_MEANING,
            "categories": categories}


def _saved_source_comparison(current: dict[str, Any], captured_source: dict[str, Any]) -> dict[str, Any]:
    comparison: dict[str, Any] = {"baseline_kind": "workspace_captured_source", "status": "unavailable",
                                  "meaning": _SAVED_SECTION_MEANING}
    path, expected = captured_source.get("captured_path"), captured_source.get("sha256")
    if (not isinstance(path, str) or not path or not isinstance(expected, str)
            or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected.casefold())):
        comparison["error"] = {"kind": "captured_source_identity_unavailable"}
        return comparison
    try:
        baseline = saved_project_observation(Path(path))
    except Exception as exc:  # Observation failure does not invalidate the current read.
        comparison["error"] = {"kind": "captured_source_read_unavailable", "exception_kind": type(exc).__name__}
        return comparison
    comparison["baseline"] = {key: baseline[key] for key in
                              ("exists", "sha256", "size", "stable_during_read") if key in baseline}
    if not baseline.get("exists"):
        comparison["error"] = {"kind": "captured_source_missing"}
    elif baseline.get("sha256", "").casefold() != expected.casefold():
        comparison["error"] = {"kind": "captured_source_hash_mismatch", "expected_sha256": expected}
    elif baseline.get("stable_during_read") is not True:
        comparison["error"] = {"kind": "captured_source_unstable"}
    elif baseline.get("saved_sections", {}).get("status") != "observed":
        comparison["error"] = {"kind": "captured_source_sections_unavailable",
                               "observation_error": baseline.get("saved_sections", {}).get("error")}
    elif current.get("saved_sections", {}).get("status") != "observed":
        comparison["error"] = {"kind": "current_saved_sections_unavailable",
                               "observation_error": current.get("saved_sections", {}).get("error")}
    else:
        categories = {}
        for tag in _SAVED_SECTION_TAGS:
            before = baseline["saved_sections"]["categories"][tag]
            after = current["saved_sections"]["categories"][tag]
            old = {item["identity"]: item for item in before["items"]}
            new = {item["identity"]: item for item in after["items"]}
            items = []
            for identity in sorted(old.keys() | new.keys()):
                left, right = old.get(identity), new.get(identity)
                status = ("missing_baseline" if left is None else "missing_current" if right is None
                          else "unchanged" if left["sha256"] == right["sha256"] else "changed")
                observed = right if right is not None else left
                items.append({"identity": identity, "status": status,
                              **{key: observed[key] for key in ("name", "program_name", "declared_count")
                                 if key in observed}})
            categories[tag] = {"baseline_count": before["count"], "current_count": after["count"], "items": items}
        comparison.update(status="compared", categories=categories)
    return comparison


def saved_project_observation(path: Path, *, captured_source: dict[str, Any] | None = None,
                              include_source_snapshot: bool = False) -> dict[str, Any]:
    """Read/hash the same saved bytes, allowing the IDE's Windows write sharing."""
    if not path.exists():
        result = {"exists": False, "read_only": True,
                  "saved_sections": {"status": "unavailable", "error": {"kind": "saved_file_missing"}}}
        if captured_source is not None:
            result["source_comparison"] = _saved_source_comparison(result, captured_source)
        return result
    if os.name == "nt":
        import msvcrt

        import win32file
        handle = win32file.CreateFile(str(path), win32file.GENERIC_READ,
                                     win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                                     None, win32file.OPEN_EXISTING, 0, None)
        native_handle = handle.Detach()
        try:
            descriptor = msvcrt.open_osfhandle(native_handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            win32file.CloseHandle(native_handle)
            raise
        stream = os.fdopen(descriptor, "rb")
    else:
        stream = path.open("rb")
    with stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        payload = bytearray() if before.st_size <= 256 * 1024 * 1024 else None
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            if payload is not None:
                payload.extend(block)
                if len(payload) > 256 * 1024 * 1024:
                    payload = None
        after = os.fstat(stream.fileno())
    result = {"exists": True, "size": after.st_size, "mtime_ns": after.st_mtime_ns,
              "sha256": digest.hexdigest(), "stable_during_read":
              (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
              "read_only": True}
    try:
        if payload is None:
            raise ValueError("Saved XML exceeds read bound")
        offset = payload.find(b"\x1f\x8b\x08", 0, min(len(payload), 4096))
        if offset < 0:
            raise ValueError("Saved XML member absent")
        decoder = zlib.decompressobj(31)
        xml = decoder.decompress(payload[offset:], 32 * 1024 * 1024 + 1)
        if len(xml) > 32 * 1024 * 1024 or decoder.unconsumed_tail or not decoder.eof:
            raise ValueError("Saved XML incomplete or too large")
        xml += decoder.flush()
        if len(xml) > 32 * 1024 * 1024:
            raise ValueError("Unsupported saved XML")
        from controlstudio_contracts.native_simulator_saved_source import parse_project_xml
        root = parse_project_xml(
            xml, error_type=ValueError, declaration_error_message="Unsupported saved XML"
        )
        local = lambda node: node.tag.rsplit("}", 1)[-1]
        if local(root) != "Project":
            raise ValueError("Saved XML root differs")
        result["saved_sections"] = (_saved_xml_sections(root) if result["stable_during_read"] else
                                    {"status": "unavailable", "error": {"kind": "saved_file_unstable"}})
        names = []
        for configuration in root.iter():
            if local(configuration) != "Configuration":
                continue
            for program in configuration.iter():
                if local(program) != "Program":
                    continue
                routines = {local(candidate) for body in program if local(body) == "Body"
                            for candidate in body.iter() if local(candidate).endswith("Routine")}
                if routines != {"LDRoutine"}:
                    continue
                name = str(program.text or program.get("Name") or program.get("InstanceName") or "").strip()
                if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
                    raise ValueError("Invalid saved program identity")
                names.append(name)
        if not names or len(names) > 2000 or len({name.casefold() for name in names}) != len(names):
            raise ValueError("Saved LD names absent or ambiguous")
        result.update(program_names=sorted(names, key=str.casefold),
                      program_names_source="saved_project_xml_same_hashed_bytes", live_editor_catalog=False)
        if include_source_snapshot or result['stable_during_read']:
            from controlstudio_contracts.native_simulator_saved_source import (
                saved_project_xml_snapshot,
            )
            try:
                source_snapshot = saved_project_xml_snapshot(root, sha256=result['sha256'],
                    size=result['size'], xml_sha256=hashlib.sha256(xml).hexdigest())
            except Exception:
                if include_source_snapshot:
                    raise
            else:
                if result['stable_during_read']:
                    result['source_sha256'] = source_snapshot['source_sha256']
                if include_source_snapshot:
                    result['_source_snapshot'] = source_snapshot
    except Exception as exc:  # noqa: BLE001 - preserve classified saved-catalog unavailability
        result["program_names_error"] = {"kind": "saved_program_catalog_unavailable",
                                         "exception_kind": type(exc).__name__}
        result.setdefault("saved_sections", {"status": "unavailable", "error": {
            "kind": "saved_xml_sections_unavailable", "exception_kind": type(exc).__name__}})
    if captured_source is not None:
        result["source_comparison"] = _saved_source_comparison(result, captured_source)
    return result


def _native_creation_parameter_origin(project_path, before, captured_source, binding):
    """Read only the Host-bound baseline and its native creation provenance."""
    from controlstudio_contracts.native_simulator_saved_source import (
        native_creation_parameter_origin_from_snapshots,
    )
    try:
        if not isinstance(captured_source,dict):
            return None
        path=Path(project_path).resolve();workspace=path.parent.parent
        captured=Path(captured_source['captured_path']).resolve()
        manifest=json.loads((workspace/'workspace.json').read_text(encoding='utf-8'))
        source,working=manifest['source'],manifest['working_copy']
        source_relative=Path(source['captured_relative_path']);working_relative=Path(working['relative_path'])
        if (workspace.name!=binding['workspace_id'] or manifest.get('workspace_id')!=binding['workspace_id']
                or manifest.get('revision_id')!=binding['revision_id']
                or source_relative.is_absolute() or working_relative.is_absolute()
                or (workspace/source_relative).resolve()!=captured
                or (workspace/working_relative).resolve()!=path
                or not captured.is_relative_to(workspace/'source')
                or not path.is_relative_to(workspace/'working') or captured==path
                or source['sha256']!=captured_source['sha256']):
            return None
        observed=saved_project_observation(captured,include_source_snapshot=True)
        if (observed.get('stable_during_read') is not True or observed.get('sha256')!=captured_source['sha256']
                or not isinstance(observed.get('_source_snapshot'),dict)):
            return None
        return native_creation_parameter_origin_from_snapshots(before,observed['_source_snapshot'],manifest)
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        return None


def native_default_load_chain_verified(raw, origin, before, after, binding) -> bool:
    """Validate the two existing Write receipts against the owner's saved reads."""
    from controlstudio_contracts.native_simulator_saved_source import (
        compare_simulator_save,
        native_default_parameter_change,
        native_write_completion_verified,
    )
    try:
        chain = raw['native_saved_version_load']
        first = chain['first_completed_write']
        stopped = chain['stopped_before_second_write']
        mode = stopped['postcondition']['ui_state']
        transition = first['saved_version_transition']
        final = raw['saved_version_transition']
        material = compare_simulator_save(before, after)
        defaults = native_default_parameter_change(origin, before, after, material)
        return bool(defaults is not None and chain['native_default_parameters'] == defaults
            and chain['requested_sha256'] == raw['requested_sha256'] == before['sha256']
            and first['schema'] == 'controlstudio-simulator-completed-write/v1'
            and first['binding'] == binding and first['requested_sha256'] == before['sha256']
            and isinstance(first['attempt_id'], str) and re.fullmatch('[0-9a-f]{32}', first['attempt_id'])
            and isinstance(raw['load_attempt_id'], str) and re.fullmatch('[0-9a-f]{32}', raw['load_attempt_id'])
            and first['attempt_id'] != raw['load_attempt_id']
            and stopped['operation'] == 'stop' and stopped['ok'] is True and stopped['verified'] is True
            and stopped['input_state_unknown'] is False and stopped['postcondition']['main_enabled'] is True
            and mode['verified'] is True and mode['classification'] == 'STOP'
            and mode['pid'] == binding['pid'] and mode['hwnd'] == binding['hwnd']
            and abs(mode['process_create_time'] - binding['expected_process_create_time']) < 0.001
            and native_write_completion_verified(first['gui']) and native_write_completion_verified(raw['gui'])
            and transition['verified'] is False and transition['before']['sha256'] == before['sha256']
            and transition['before']['source_sha256'] == before['source_sha256']
            and [change for change in transition['changed_fields'] if change['allowed'] is False]
                == [change for change in material['changed_fields'] if change['allowed'] is False]
            and first['written_saved_project']['stable_during_read'] is True
            and first['written_saved_project']['sha256'] == transition['after']['sha256']
                == raw['final_load_requested_sha256'] == final['before']['sha256']
            and first['written_saved_project']['size'] == transition['after']['size'] == final['before']['size']
            and transition['after']['source_sha256'] == final['before']['source_sha256']
                == final['after']['source_sha256'] == final['source_sha256']
                == raw['loaded_source_sha256'] == after['source_sha256']
            and final['verified'] is True
            and final['after']['sha256'] == raw['loaded_sha256'] == after['sha256']
            and final['after']['size'] == after['size'])
    except (ValueError, TypeError, KeyError, RecursionError):
        return False


def native_recovery_target_retired(target: dict[str, Any], created: float) -> bool:
    """Read original process lifetime and main retirement; no input or kill.

    An unavailable query is not death. PID reuse is a different lifetime, not
    permission to close the replacement process. All handles are released.
    """
    import ctypes
    import math
    from ctypes import wintypes
    if os.name != 'nt' or type(created) not in {int,float} or not math.isfinite(created) or created <= 0:
        raise MCPToolError('Native recovery lifetime inspection is unavailable')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    user=ctypes.WinDLL('user32',use_last_error=True)
    open_process=kernel.OpenProcess
    open_process.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];open_process.restype=wintypes.HANDLE
    close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
    wait=kernel.WaitForSingleObject;wait.argtypes=[wintypes.HANDLE,wintypes.DWORD];wait.restype=wintypes.DWORD
    times=kernel.GetProcessTimes
    times.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4;times.restype=wintypes.BOOL
    is_window=user.IsWindow;is_window.argtypes=[wintypes.HWND];is_window.restype=wintypes.BOOL
    window_pid=user.GetWindowThreadProcessId
    window_pid.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)];window_pid.restype=wintypes.DWORD
    ctypes.set_last_error(0)
    handle=open_process(0x00101000,False,target['pid'])
    reused=False
    if not handle:
        if ctypes.get_last_error()!=87:
            raise MCPToolError('Native recovery process query did not prove retirement')
        exited=True
    else:
        try:
            values=[wintypes.FILETIME() for _ in range(4)]
            if not times(handle,*(ctypes.byref(v) for v in values)):
                raise MCPToolError('Native recovery process lifetime could not be read')
            ticks=(int(values[0].dwHighDateTime)<<32)|int(values[0].dwLowDateTime)
            reused=abs(ticks/10000000-11644473600-created)>=0.001
            waited=wait(handle,0)
            if waited not in {0,258}:
                raise MCPToolError('Native recovery process state is unknown')
            exited=waited==0 or reused
        finally:close(handle)
    owner=wintypes.DWORD()
    main_present=bool(is_window(target['hwnd']))
    if main_present:
        if not window_pid(target['hwnd'],ctypes.byref(owner)):
            raise MCPToolError('Native recovery main ownership is unknown')
    return bool(exited and (not main_present or owner.value!=target['pid'] or reused))


def _native_admission_snapshot(owner: Any) -> dict[str, Any] | None:
    """Snapshot existing Host admission, not a fresh native-liveness claim."""
    try:
        return {'target':deepcopy(owner.target),'current':deepcopy(owner.current_target()),
            'expected_created':owner.expected_process_create_time,
            'current_created':owner.current_process_create_time() if owner.current_process_create_time is not None else None,
            'recovery_only':getattr(owner,'recovery_only',True),'recovery_pending':getattr(owner,'recovery_pending',True)}
    except Exception:
        return None


def native_admission_generation(target: Any, process_create_time: Any) -> str | None:
    """현재 Host 대상의 admission 식별자. 저장 버전/물리 종료/지속 생존 증명은 별개다."""
    fields = ('workspace_id', 'revision_id', 'session_id', 'pid', 'hwnd', 'project_path')
    if (not isinstance(target, dict)
            or any(not isinstance(target.get(key), str) or not target[key].strip()
                   for key in ('workspace_id', 'revision_id', 'session_id', 'project_path'))
            or any(type(target.get(key)) is not int or target[key] <= 0 for key in ('pid', 'hwnd'))
            or type(process_create_time) not in {int, float}
            or not math.isfinite(process_create_time) or process_create_time <= 0):
        return None
    try:
        identity = {**{key: target[key] for key in fields},
            'project_path': os.path.normcase(str(Path(target['project_path']).resolve())),
            'process_create_time': float(process_create_time)}
        return 'host_verified_native_binding/v2:' + hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None



def _known_scenario_native_prefix(result, saved_sha256):
    """Known native prefix and unexecuted suffix; never a functional success proof."""
    if (not isinstance(saved_sha256,str) or re.fullmatch('[0-9a-f]{64}',saved_sha256) is None
            or not isinstance(result,dict) or result.get('action')!='execute_simulation_scenario'
            or result.get('ok') is not False or result.get('input_state_unknown') is not False):
        return False
    def conflict(value):
        if isinstance(value,dict):
            return (any(key in value and not isinstance(value[key],str)
                        for key in ('status','operation_outcome','execution_status'))
                or any(key in value and value[key] is not False for key in
                        ('input_state_unknown','input_queue_state_unknown','quarantined',
                         'inspection_required','old_outcome_unknown'))
                or any(value.get(key) for key in ('error','failure','nativeFailure','recovery_ref'))
                or value.get('status') in ('failed','unknown','pending','dispatched','admitted')
                or value.get('operation_outcome') in ('failed','unknown','pending')
                or value.get('execution_status') in ('failed','unknown','pending','dispatched')
                or any(conflict(item) for item in value.values()))
        return isinstance(value,list) and any(conflict(item) for item in value)
    if conflict(result):
        return False
    batch=result.get('batch')
    if not isinstance(batch,dict):
        return False
    children=batch.get('children');prefix=batch.get('completed_prefix_count')
    if (not isinstance(children,list) or not children or type(prefix) is not int
            or not 0<prefix<=len(children) or batch.get('loaded_sha256')!=saved_sha256
            or batch.get('cancelled') is not False or batch.get('expectations_matched') is not False
            or type(batch.get('not_started_count')) is not int
            or batch['not_started_count']!=len(children)-prefix
            or batch.get('all_native_steps_completed') is not (prefix==len(children))
            or batch.get('all_completed') is not (prefix==len(children))):
        return False
    if batch.get('stop_reason') is None:
        if prefix!=len(children) or batch.get('stop_index') is not None:
            return False
    elif (not isinstance(batch['stop_reason'],str) or not batch['stop_reason']
            or type(batch.get('stop_index')) is not int or batch['stop_index']!=prefix-1):
        return False
    mismatched=False
    for index,child in enumerate(children):
        if (not isinstance(child,dict) or type(child.get('index')) is not int or child['index']!=index
                or child.get('action') not in ('read_devices','write_devices','read_words','wait_devices')):
            return False
        if index>=prefix:
            if (child.get('status')!='not_started' or child.get('native_execution_completed',False) is not False
                    or any(key in child for key in ('result','expectation','started_at_ns','finished_at_ns'))):
                return False
            continue
        native_result=child.get('result');verdict=child.get('expectation')
        if (child.get('status')!='completed' or child.get('native_execution_completed') is not True
                or not isinstance(native_result,dict) or native_result.get('ok') is not True
                or native_result.get('loaded_sha256')!=saved_sha256
                or not isinstance(native_result.get('saved_project'),dict)
                or native_result['saved_project'].get('sha256')!=saved_sha256
                or native_result['saved_project'].get('stable_during_read') is not True
                or not isinstance(verdict,dict) or verdict.get('native_samples_complete') is not True
                or verdict.get('matched') is not None and type(verdict['matched']) is not bool):
            return False
        mismatched=mismatched or verdict.get('matched') is False
    return mismatched

def _preserved_native_admission(result: Any, before: Any, after: Any, tool_call_id: str | None) -> dict[str, Any] | None:
    """Preserve prior admission only; operation outcome and target readiness stay separate."""
    fields=('workspace_id','revision_id','session_id','pid','hwnd','project_path')
    if (not isinstance(result,dict) or not (result.get('ok') is False or result.get('error') or result.get('failure'))
            or not isinstance(tool_call_id,str) or not tool_call_id.strip()
            or not isinstance(before,dict) or not isinstance(after,dict)
            or before.get('target')!=after.get('target')
            or before.get('expected_created')!=after.get('expected_created')):
        return None
    target=before.get('target')
    created=before.get('expected_created')
    if (not isinstance(target,dict)
            or any(not isinstance(target.get(k),str) or not target[k].strip()
                   for k in ('workspace_id','revision_id','session_id','project_path'))
            or any(type(target.get(k)) is not int or target[k]<=0 for k in ('pid','hwnd'))
            or type(created) not in {int,float} or not math.isfinite(created) or created<=0):
        return None
    for snapshot in (before,after):
        current=snapshot.get('current')
        observed_created=snapshot.get('current_created')
        if (snapshot.get('recovery_only') is not False or snapshot.get('recovery_pending') is not False
                or not isinstance(current,dict)
                or any(type(current.get(k)) is not type(target[k]) or current.get(k)!=target[k] for k in fields)
                or type(observed_created) not in {int,float} or not math.isfinite(observed_created)
                or observed_created!=created):
            return None
    if any(before['current'].get(k)!=after['current'].get(k) for k in ('project_hash','working_sha256')):
        return None

    # Assessment failure does not revoke an unchanged Host admission. This is
    # issued after the broker strips all externally supplied admission proof.
    saved_sha256=before['current'].get('working_sha256')
    batch=result.get('batch')
    assessment_stop=(isinstance(batch,dict) and
        (batch.get('stop_reason')=='model_expectation_mismatch'
         or batch.get('stop_reason') is None and batch.get('all_native_steps_completed') is True))
    if assessment_stop and _known_scenario_native_prefix(result,saved_sha256):
        generation=native_admission_generation(target,created)
        if generation is not None:
            return {'schema_version':'1','provenance':'host_expected_native_admission/v1',
                'admitted_generation':generation,'binding_preserved':True,
                'scope':'known_native_execution_assessment_only','tool_call_id':tool_call_id,
                'saved_sha256':saved_sha256}

    evidence=[result,result.get('receipt')]
    typed_receipts=[]
    for key in ('error','failure'):
        failure=result.get(key)
        if isinstance(failure,dict) and isinstance(failure.get('nativeFailure'),dict):
            failure=failure['nativeFailure']
        if isinstance(failure,dict) and 'schema' in failure:
            try:
                checked=validate_native_failure(failure)
            except (TypeError,ValueError):
                continue
            receipt=checked['details'].get('receipt')
            evidence.append(receipt)
            if isinstance(receipt,dict):typed_receipts.append(receipt)
    no_input=any(isinstance(item,dict) and item.get('action_attempted') is False
        and item.get('input_state_unknown') is False for item in evidence)
    # Existing legacy editor receipts omit the optional uncertainty field.
    # Their exact native target and zero-input witness remain sufficient.
    for receipt in typed_receipts:
        environment=receipt.get('input_environment')
        observed=environment.get('target') if isinstance(environment,dict) else None
        count=receipt.get('input_events_inserted')
        if (receipt.get('action_attempted') is False and receipt.get('ld_edit_verified') is False
                and 'input_events_inserted' in receipt and (count is None or type(count) is int and count==0)
                and type(receipt.get('action_target_hwnd')) is int and receipt['action_target_hwnd']==target['hwnd']
                and type(receipt.get('action_target_main_hwnd')) is int and receipt['action_target_main_hwnd']==target['hwnd']
                and isinstance(observed,dict) and type(observed.get('pid')) is int and observed['pid']==target['pid']):
            no_input=True
    conflict=False
    def inspect(value):
        nonlocal conflict
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {'action_attempted','input_state_unknown','input_queue_state_unknown',
                           'quarantined','inspection_required','old_outcome_unknown'} and item is not False:
                    conflict=True
                if key in {'input_events_inserted','command_events_inserted','sent_events'} and not (item is None or type(item) is int and item==0):
                    conflict=True
                if key=='ld_edit_verified' and item is not False:
                    conflict=True
                inspect(item)
        elif isinstance(value,list):
            for item in value:inspect(item)
    inspect(result)
    if not no_input or conflict:
        return None
    sha=target.get('working_sha256')
    if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):
        return None
    generation=native_admission_generation(target, created)
    if generation is None:
        return None
    return {'schema_version':'1','provenance':'host_expected_native_admission/v1',
        'admitted_generation':generation,'binding_preserved':True,
        'scope':'known_no_input_operation_rejection_only','tool_call_id':tool_call_id}


def _declaration_public(value: Any) -> Any:
    """Preserve public diagnostics; remove only private authority/transport fields."""
    private = {'authority','declarationauthority','declarationbinding','nonce','marker',
        'rowtoken','programbinding','baselinecsv','artifactdir','declarationartifactdir','lifetimemarker',
        'recoverytoken','targetprocesstoken','ownerprocesstoken','lifetimemarker',
        'destination','sourcepath','csvpath','filepath','directory'}
    paths=set()
    def collect(item):
        if isinstance(item,dict):
            for key,child in item.items():
                normalized=re.sub('[^a-z0-9]','',key.casefold())
                if isinstance(child,str) and (normalized in private or normalized.endswith('path')):
                    paths.add(child)
                collect(child)
        elif isinstance(item,list):
            for child in item:collect(child)
    collect(value)
    def public(item):
        if isinstance(item,dict):
            result={}
            for key,child in item.items():
                normalized=re.sub('[^a-z0-9]','',key.casefold())
                if normalized in private:continue
                result[key]=public(child)
            # Recurse first so the shared typed-failure bypass sees sanitized
            # details, while all unrelated diagnostic fields remain intact.
            return scrub_native_tool_result(result)
        if isinstance(item,list):return [public(child) for child in item]
        if isinstance(item,str):
            for path in paths:
                if path:item=item.replace(path,'[private authority redacted]')
        return item
    return public(value)


def _rung_public(value: Any) -> Any:
    """Strip the private native proof before the shared typed-failure bypass."""
    private={'authority','rungauthority','rungbinding','baseline','expectedrow','rungexpectedrow',
             'dialog','fields','marker','freshsource','selectedrow','payload','payloadbytes','rungcommenttext',
             'decoded','rawtokens','unparsedtokens'}
    secrets=set()
    def collect(item):
        if isinstance(item,dict):
            for key,child in item.items():
                normalized=re.sub('[^a-z0-9]','',key.casefold())
                if isinstance(child,str) and (normalized.endswith('path') or normalized in {'directory','nonce','recoverytoken'}):
                    if child:secrets.add(child)
                if normalized=='marker' and isinstance(child,dict) and isinstance(child.get('name'),str):secrets.add(child['name'])
                collect(child)
        elif isinstance(item,(list,tuple)):
            for child in item:collect(child)
    collect(value)
    def clean(item):
        if isinstance(item,dict):
            return {key:clean(child) for key,child in item.items()
                    if re.sub('[^a-z0-9]','',key.casefold()) not in private}
        if isinstance(item,(list,tuple)):return [clean(child) for child in item]
        if isinstance(item,str):
            for secret in secrets:item=item.replace(secret,'[private authority redacted]')
        return item
    return _declaration_public(clean(value))


def _rung_unknown(value: Any) -> bool:
    return (isinstance(value,dict) and (value.get('input_state_unknown') is True
        or value.get('input_queue_state_unknown') is True or any(_rung_unknown(item) for item in value.values()))
        or isinstance(value,(list,tuple)) and any(_rung_unknown(item) for item in value))


def _validated_simulation_scenario(arguments: dict[str,Any]) -> list[dict[str,Any]]:
    initialize=arguments.get('initialize')
    initialization_valid=(isinstance(initialize,dict) and set(initialize)=={'saved_sha256'}
        and isinstance(initialize['saved_sha256'],str)
        and re.fullmatch('[0-9a-f]{64}',initialize['saved_sha256']) is not None)
    loaded_valid=(isinstance(arguments.get('loaded_sha256'),str)
        and re.fullmatch('[0-9a-f]{64}',arguments['loaded_sha256']) is not None)
    if (not {'steps','stop_on_mismatch'}<=set(arguments)
            or not set(arguments)<={'loaded_sha256','initialize','steps','stop_on_mismatch','run_before','stop_after','end_after'}
            or ('initialize' in arguments)==('loaded_sha256' in arguments)
            or not (initialization_valid if 'initialize' in arguments else loaded_valid)
            or any(type(arguments[key]) is not bool for key in ('run_before','stop_after','end_after') if key in arguments)
            or type(arguments['stop_on_mismatch']) is not bool
            or not isinstance(arguments['steps'],list) or not arguments['steps']):
        raise MCPToolError('Scenario requires exactly one observed loaded SHA or initialize.saved_sha256, ordered steps and mismatch choice')
    for step_index, step in enumerate(arguments['steps']):
        if (not isinstance(step,dict) or not {'case_label','action','devices','expected'}<=set(step)
                or not set(step)<={'case_label','action','devices','expected','samples','interval_ms','readback_devices','readback_delay_ms'}
                or not isinstance(step['case_label'],str) or not step['case_label']
                or step['action'] not in {'read_devices','write_devices','read_words','wait_devices'}
                or not isinstance(step['devices'],list) or not 1<=len(step['devices'])<=16
                or not isinstance(step['expected'],list)):
            raise MCPToolError('Scenario steps permit labeled existing bit read/write or read-only D/C/T/F word actions')
        if step['action']=='wait_devices' and (not step['expected']
                or len(json.dumps(step,ensure_ascii=True,separators=(',',':')).encode('utf8'))>120000):
            raise MCPToolError('wait_devices requires nonempty exact boolean expectations and a bounded step')
        write=step['action']=='write_devices'
        word=step['action']=='read_words'
        extra=step.get('readback_devices',[])
        if 'readback_devices' in step and (not write or not isinstance(extra,list) or not 1<=len(extra)<=16):
            raise MCPToolError('readback_devices requires a write step and 1 to 16 read-only M/P references')
        if 'readback_delay_ms' in step and (not write or 'readback_devices' not in step
                or type(step['readback_delay_ms']) is not int or not 0<=step['readback_delay_ms']<=1000):
            raise MCPToolPreflightError('SIMULATOR_READBACK_DELAY_INVALID',
                'readback_delay_ms requires action=write_devices, readback_devices, and an integer from 0 to 1000.',
                details={'invalid_fields': ['readback_delay_ms'],
                    'missing_fields': ['readback_devices'] if 'readback_devices' not in step else [],
                    'required_action': 'write_devices', 'minimum': 0, 'maximum': 1000,
                    'step_index': step_index})
        def key(item, expected=False):
            if word:
                if (not isinstance(item,dict) or set(item)!=({'area','word','value'} if expected else {'area','word'})
                        or item.get('area') not in ('D','C','T','F') or type(item.get('word')) is not int or item['word']<0
                        or expected and (type(item.get('value')) is not int or not 0<=item['value']<=65535)):
                    raise ValueError('Invalid D/C/T/F word reference')
                return (item['area'],item['word'])
            return simulator_bit_key(item,value=write or expected)
        try:
            requested=[key(item) for item in step['devices']]
            expected=[key(item,True) for item in step['expected']]
            extra_keys=[simulator_bit_key(item,value=False) for item in extra]
            if (len(set(requested))!=len(requested) or len(set(extra_keys))!=len(extra_keys)
                    or len(set(expected))!=len(expected) or not set(expected)<=set(requested+extra_keys)):
                raise ValueError('Distinct exact references required')
        except ValueError as error:
            raise MCPToolError('Scenario requires distinct exact native or absolute bit/boolean or D/C/T/F word/unsigned16 references') from error
        samples,interval=step.get('samples',1),step.get('interval_ms',0)
        if (type(samples) is not int or not 1<=samples<=60 or type(interval) is not int
                or not 0<=interval<=5000 or samples*interval>60000
                or write and ('samples' in step or 'interval_ms' in step)):
            raise MCPToolError(f'Scenario step {step_index}: samples must be 1..60, interval_ms 0..5000, samples * interval_ms <= 60000; write_devices must omit samples and interval_ms')
    return deepcopy(arguments['steps'])


def _valid_sample_acquisition(value):
    if not isinstance(value, dict) or not {'started_at', 'completed_at', 'duration_ms'} <= set(value) or not set(value) <= {'started_at', 'completed_at', 'duration_ms', 'device_windows'}:
        return False
    def finite(item):
        return type(item) in (int, float) and math.isfinite(item)
    if not all(finite(value[key]) for key in ('started_at', 'completed_at', 'duration_ms')) or value['duration_ms'] < 0:
        return False
    windows = value.get('device_windows', [])
    return isinstance(windows, list) and all(isinstance(item, dict)
        and set(item) == {'area', 'word', 'started_at', 'completed_at', 'duration_ms'}
        and item['area'] in ('D', 'C', 'T', 'F') and type(item['word']) is int and item['word'] >= 0
        and all(finite(item[key]) for key in ('started_at', 'completed_at', 'duration_ms'))
        and item['duration_ms'] >= 0 for item in windows)


def _write_readback_observation(step: dict[str,Any], result: dict[str,Any], saved_sha256: str):
    """Validate two projections of one response; no address arithmetic or I/O."""
    written=resolved_simulator_devices(step['devices'],result.get('address_resolution'),saved_sha256,write=True)
    extra=resolved_simulator_devices(step['readback_devices'],result.get('readback_address_resolution'),saved_sha256,write=False)
    memory=result.get('memory')
    if (not isinstance(memory,dict) or memory.get('write_acknowledged') is not True
            or memory.get('readback_matched') is not True or memory.get('requested')!=written):
        raise ValueError('Written-input acknowledgement and readback proof are required')
    def sample(value, references):
        if (not isinstance(value,dict) or not {'devices','observed_at','transport','atomic_snapshot'} <= set(value)
                or not set(value) <= {'devices','observed_at','transport','atomic_snapshot','acquisition'}
                or 'acquisition' in value and not _valid_sample_acquisition(value['acquisition'])
                or type(value['observed_at']) not in (int,float) or not math.isfinite(value['observed_at'])
                or value['transport']!='XGT_FENET_SIMULATOR' or value['atomic_snapshot'] is not False
                or not isinstance(value['devices'],list) or len(value['devices'])!=len(references)):
            raise ValueError('Readback projection is incomplete')
        for actual,reference in zip(value['devices'],references):
            if simulator_bit_key(actual,value=True)!=(reference['area'],reference['bit']):
                raise ValueError('Readback projection identity/order differs from its request')
        return value
    readback=sample(memory.get('readback'),written)
    observed=sample(memory.get('observed_readback'),extra)
    if 'readback_delay_ms' in step:
        delay=step['readback_delay_ms'];elapsed=memory.get('readback_elapsed_ms')
        if (type(delay) is not int or not 0<=delay<=1000
                or type(memory.get('readback_delay_ms')) is not int or memory['readback_delay_ms']!=delay
                or type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<delay):
            raise ValueError('Post-write readback delay receipt differs from its request')
    ack=memory.get('write_acknowledged_at')
    if (type(ack) not in (int,float) or not math.isfinite(ack)
            or readback['devices']!=written
            or any(readback.get(field)!=observed.get(field) for field in ('observed_at','transport','atomic_snapshot','acquisition'))):
        raise ValueError('Readback projections do not share one observation or written-input proof')
    union,values,indexed=[],[],{}
    for reference,actual in zip([*written,*extra],[*readback['devices'],*observed['devices']]):
        identity=reference['area'],reference['bit']
        if identity in indexed:
            if indexed[identity]['value'] is not actual['value']:
                raise ValueError('Overlapping readback values disagree')
        else:
            indexed[identity]=actual
            union.append(reference)
            values.append(actual)
    if len(union)>16:
        raise ValueError('Readback union exceeds one existing frame')
    mapped={simulator_bit_key(original,value=True):normalized for original,normalized in zip(step['devices'],written)}
    mapped.update({simulator_bit_key(original,value=False):normalized for original,normalized in zip(step['readback_devices'],extra)})
    return union,mapped,{**readback,'devices':values}


def _simulation_expectations(step: dict[str,Any], result: dict[str,Any]) -> dict[str,Any]:
    if step['action']=='write_devices':
        memory=result.get('memory',{})
        samples=[memory.get('readback')]
        complete=memory.get('write_acknowledged') is True and memory.get('readback_matched') is True
    else:
        samples=result.get('trace')
        complete=isinstance(samples,list) and (1<=len(samples)<=step.get('samples',1)
            if step['action']=='wait_devices' else len(samples)==step.get('samples',1))
    word=step['action']=='read_words'
    unit='word' if word else 'bit'
    try:
        requested = step['devices'] if word else resolved_simulator_devices(
            step['devices'], result.get('address_resolution'), result.get('loaded_sha256'),
            write=step['action']=='write_devices')
        mapped = {simulator_bit_key(original,value=step['action']=='write_devices'): normalized
            for original,normalized in zip(step['devices'],requested)} if not word else {}
        if step['action']=='write_devices' and 'readback_devices' in step:
            requested,mapped,combined=_write_readback_observation(step,result,result.get('loaded_sha256'))
            samples=[combined]
        expected = step['expected'] if word else [
            {**mapped[simulator_bit_key(item,value=True)], 'value':item['value']}
            for item in step['expected']]
        if 'readback_devices' in step and len({(item['area'],item[unit]) for item in expected})!=len(expected):
            raise ValueError('Expectations must be distinct after readback resolution')
    except (ValueError,KeyError):
        return {'native_samples_complete':False,'expected':deepcopy(step['expected']),
            'comparisons':[],'matched':None,'address_resolution_valid':False}
    wanted={(item['area'],item[unit]) for item in requested}
    actual=[]
    for sample in samples if isinstance(samples,list) else []:
        values=sample.get('devices') if isinstance(sample,dict) else None
        if isinstance(sample,dict) and 'acquisition' in sample and not _valid_sample_acquisition(sample['acquisition']):
            complete=False
        def valid(item):
            if not isinstance(item,dict) or set(item)!={'area',unit,'value'}:
                return False
            if word:
                return (item['area'] in ('D','C','T','F') and type(item['word']) is int and item['word']>=0
                    and type(item['value']) is int and 0<=item['value']<=65535)
            return (item['area'] in ('M','P') and type(item['bit']) is int
                and 0<=item['bit']<=2097151 and type(item['value']) is bool)
        if (not isinstance(values,list) or any(not valid(item) for item in values)
                or len(values)!=len(wanted) or {(item['area'],item[unit]) for item in values}!=wanted):
            complete=False;continue
        actual.append({(item['area'],item[unit]):item['value'] for item in values})
    if not actual:complete=False
    waiting=step['action']=='wait_devices'
    if waiting:
        matches=[all(sample[(item['area'],item[unit])] is item['value'] for item in expected) for sample in actual]
        complete=bool(complete and expected and len(actual)==len(samples) and not any(matches[:-1])
            and (matches[-1] or len(actual)==step.get('samples',1)))
    comparisons=[{'area':item['area'],unit:item[unit],'expected':item['value'],
        'observed_values':[sample[(item['area'],item[unit])] for sample in actual],
        'matched':complete and all((sample[(item['area'],item[unit])]==item['value'] if word
            else sample[(item['area'],item[unit])] is item['value']) for sample in (actual[-1:] if waiting else actual))} for item in expected]
    return {'native_samples_complete':bool(complete),'expected':deepcopy(step['expected']),'comparisons':comparisons,
        **({'address_resolution':deepcopy(result['address_resolution'])} if 'address_resolution' in result else {}),
        'meaning':('The first sample matching all exact expectations opens this gate; earlier samples remain observations.' if waiting
            else 'Each model expectation applies to every returned sample of this step; no PLC behavior inferred beyond these observations.'),
        'matched':all(item['matched'] for item in comparisons) if expected and complete else None}

def _simulation_write_group_end(steps, start):
    end=start
    while end<len(steps) and steps[end]['action'] in {'write_devices','wait_devices','read_devices','read_words'}:
        end+=1
    return end


def _simulation_lifecycle_plan(steps, lifecycle):
    from .native_simulator_sequence_control import MAX_PROGRESS_BYTES
    phase = lambda action: {'case_label':'lifecycle.'+action,'action':action,'devices':[],'expected':[]}
    plans=[];start=0
    while start<len(steps):
        end=_simulation_write_group_end(steps,start)
        if end==start:
            return None
        plan=([phase('run')] if start==0 and 'run' in lifecycle else [])+deepcopy(steps[start:end])
        if end==len(steps):
            plan += [phase(action) for action in ('stop','end') if action in lifecycle]
        encoded = len(json.dumps(plan,ensure_ascii=True,separators=(',',':')).encode('utf8'))
        # Reserve the existing proof bound for per-sample devices, comparisons,
        # repeated framing and native receipts before dispatching any input.
        proof_reserve = 4 * encoded + sum(4096 + step.get('samples', 1) *
            (512 + 256 * len(step.get('devices', []))) for step in plan)
        if encoded>120000 or proof_reserve >= MAX_PROGRESS_BYTES:
            raise MCPToolError('Scenario exceeds the bounded single-sequence request')
        plans.append((start,plan));start=end
    return plans


class NativeWorkspaceExecution:
    def __init__(self, *, gateway: Any, target: dict[str, Any],
                 current_target: Callable[[], dict[str, Any] | None],
                 record_raw: Callable[[str, dict[str, Any], Any], None],
                 refresh_saved: Callable[[dict[str, Any]], None], program_name: str | None = None,
                 captured_source: dict[str, Any] | None = None, allow_simulator: bool = False,
                 expected_process_create_time: float | None = None, recover_workspace: Callable | None = None,
                 current_process_create_time: Callable[[], float | None] | None = None,
                 pending_recovery_owned_ui: Callable | None = None,
                 wait_workspace_recovery: Callable | None = None,
                 program_open_binding: dict[str, Any] | None = None,
                 record_process_diagnostic: Callable | None = None,
                 current_recovery_admission: Callable | None = None):
        self.gateway, self.target = gateway, deepcopy(target)
        self.current_target, self.record_raw, self.refresh_saved = current_target, record_raw, refresh_saved
        self.program_name = program_name
        self.captured_source = deepcopy(captured_source)
        self.owned_ui: dict[str, dict[str, Any]] = {}
        self.variable_comments: dict[str, dict[str, Any]] = {}
        self.declaration_tables: dict[str, dict[str, Any]] = {}
        self.rung_sources: dict[str, dict[str, Any]] = {}
        self.rung_comments: dict[str, dict[str, Any]] = {}
        self.rung_generation = uuid.uuid4().hex
        self.declaration_generation = uuid.uuid4().hex
        self.check_results: dict[str, dict[str, Any]] = {}
        self.operation_recoveries: dict[str, dict[str, Any]] = {}
        self.allow_simulator = allow_simulator
        self.expected_process_create_time = expected_process_create_time
        self.current_process_create_time = current_process_create_time
        self.program_open_binding = deepcopy(program_open_binding)
        self.recover_workspace = recover_workspace
        self.current_recovery_admission = current_recovery_admission
        self.pending_recovery_owned_ui = pending_recovery_owned_ui
        self.wait_workspace_recovery = wait_workspace_recovery
        self.recovery_pending = False
        self.recovery_only = False
        self._execution_lock = asyncio.Lock()
        self._record_process_diagnostic = record_process_diagnostic
        self._vendor_process_diagnostics = None
        self._diagnostic_call_context = {}
        self._simulator_end_precontrol_source = None

    def tool_catalog(self) -> list[AgentToolDefinitionV1]:
        catalog = [AgentToolDefinitionV1(tool_name=name, description=description,
                    parameters_json_schema=schema) for name, description, schema in (
            ("native_editor", "Execute a scoped native LD primitive using the Host-bound working editor.",
             {"type": "object", "properties": {"action": {"type": "string", "enum": list(_ACTION_FIELDS)}},
              "required": ["action"], "additionalProperties": True}),
            ("read_saved_symbols", "Read paged symbol names, devices, types and comments from the Host-bound saved working file through the existing structure index. No UI input, export or edit authority; saved bytes only, with coverage and partial semantics.",
             {"type": "object", "properties": {"offset": {"type": "integer", "minimum": 0},
              "limit": {"type": "integer", "minimum": 1, "maximum": 2000}}, "additionalProperties": False}),
            ("read_saved_project", "Observe the bound saved working file summary and verified initial saved symbol/comment page from the same SHA, with coverage and limitations. Saved bytes only, no path, UI input or edit authority. Larger tables use existing read_saved_symbols pages and retained result readers.",
             {"type": "object", "properties": {}, "additionalProperties": False}),
            ("inspect_declaration_comments", "Read the current global/direct declaration table through actual native CSV export and native LD preservation proof. No LD element ref. Returns a one-use complete table ref for typed creation and row refs for unambiguous existing comment edits; navigation/export is input, not a project edit or save. A historical unknown export/navigation remains unknown; it does not itself prohibit a new current-bound inspection. Fresh target/lifetime/source/native-state checks still apply; current pending/quarantine/owned UI requires its actual observation/recovery contract. A current-call unknown stops further input without replay or automatic cancellation.",
             {"type":"object","properties":{},"additionalProperties":False}),
            ("set_declaration_comments", "Create typed declarations with explicit {symbol,data_type,address,comment}, or update comments with {row_ref,comment}, using one freshly inspected table_ref. Creation requires absent names and unoccupied devices; existing declarations cannot be renamed or retyped. One-use native CSV import, full table re-export and LD preservation; no save. Never resend an unknown operation; inspect current state for a new request.",
             {"type":"object","additionalProperties":False,"properties":{
                 "table_ref":{"type":"string","minLength":1},"entries":{"type":"array","minItems":1,"items":{
                     "anyOf":[{"type":"object","additionalProperties":False,"properties":{"row_ref":{"type":"string","minLength":1},
                         "comment":{"type":"string","maxLength":512,"pattern":"^[^\\u0000\\r\\n\\t]*$"}},"required":["row_ref","comment"]},
                         {"type":"object","additionalProperties":False,"properties":{
                             "symbol":{"type":"string","minLength":1,"maxLength":80},
                             "data_type":{"type":"string","minLength":1,"maxLength":80},
                             "address":{"type":"string","minLength":1,"maxLength":80},
                             "comment":{"type":"string","maxLength":512,"pattern":"^[^\\u0000\\r\\n\\t]*$"}},
                          "required":["symbol","data_type","address","comment"]}]}}},"required":["table_ref","entries"]}),
            ("read_variable_comments", "Read the bound global/direct variable-comment table through verified native CSV export; UI input, no project edit/save or model-selected destination.",
             {"type": "object", "properties": {}, "additionalProperties": False}),
            ("inspect_variable_table", "Inspect current bound global variable-table UI metadata without UI navigation, export, import, edit or save. Not a complete declaration read or edit authority.",
             {"type": "object", "properties": {}, "additionalProperties": False}),
            ("open_variable_table", "For the current user table-opening request, freshly validate the bound target/pane, restore only a proved hidden Project pane with a fresh unique supported exact button Invoke, and reobserve pane/grid before navigation. Skip the Invoke if already visible. Historical unknown raw inputs remain unknown and are never replayed; current-call unknown stops further input. Preserve no-input refusal and pane-restoration/table-open facts separately. UI metadata only; no export/import, project edit/save or declaration-edit authority.",
             {"type": "object", "properties": {}, "additionalProperties": False}),
            ("save_project", "Dispatch Ctrl+S to the bound working editor, then return a separately observed saved-project snapshot.",
             {"type": "object", "properties": {}, "additionalProperties": False}),
            ("initialize_ld_program", "Insert only the first model-selected NO/NC contact into a verified newly created empty program. Requires fresh creation, saved-file and native empty-slot proof; never replay an unknown input.",
             {"type": "object", "additionalProperties": False,
              "properties": {"program_name": {"type": "string", "minLength": 1, "maxLength": 80},
                  "element_kind": {"enum": ["NO", "NC"]}, "operand": {"type": "string", "minLength": 1, "maxLength": 80},
                  "symbol": {"type": "string", "maxLength": 80}},
              "required": ["program_name", "element_kind", "operand"]}),
        )]
        if self.recover_workspace is not None:
            catalog.append(AgentToolDefinitionV1(tool_name='recover_workspace',
                description='Save and normally reopen only this source-preserving working workspace under Host control. Never discard edits, kill processes or replay unknown close/open. A pending modal needs fresh observe and an explicit owned UI response. For a pending operation on the same physical generation, further recover calls only observe the original outcome without replaying unknown input. If Host has independently rebound this workspace to a different physical generation, an explicit recover request may select a new operation only after Host freshly verifies the current frame/lifetime and saved identity. The prior operation and its unknown outcomes remain historical, without settlement or replay. A generation token or new session alone proves neither freshness nor permission; this guidance does not settle or authorize retry of an unknown simulator Start. New binding requires fresh program/LD observation and a new simulator load.',
                parameters_json_schema={'type':'object','properties':{},'additionalProperties':False}))
        if self.allow_simulator:
            catalog.append(AgentToolDefinitionV1(tool_name="simulator",
                description='Operate only the bound local XG-SIM. Load binds the actual saved SHA; run and memory require that version. Input ACKs and runtime traces are distinct. Optional readback_devices is only for write_devices: read-only M/P signals observed in the same existing ACK-following read response, separately returned as observed_readback. readback_matched still tests written inputs only. Extra observations do not prove a completed PLC scan, atomic snapshot, stable values or functional success. Optional readback_delay_ms (integer 0..1000, default0) requires readback_devices and delays the existing single read after ACK within the same absolute operation budget; it adds no read and proves no PLC scan or functional timing.',
                parameters_json_schema={'type': 'object', 'additionalProperties': False, 'properties': {'action': {'type': 'string', 'enum': ['start', 'load', 'run', 'stop', 'end', 'observe', 'read_devices', 'write_devices', 'read_words']}, 'devices': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': {'oneOf': [{'type': 'object', 'additionalProperties': False, 'properties': {'address': {'type': 'string', 'pattern': '^[MP][0-9]{3,4}[0-9A-F]$', 'description': 'Exact native LD bit address in the explicitly selected notation; conversion is not CPU-family verification.'}, 'value': {'type': 'boolean'}, 'notation': {'type': 'string', 'enum': ['xgk_compact_bit'], 'description': 'Explicitly select decimal word plus final hexadecimal bit notation. This does not infer or verify CPU compatibility.'}}, 'required': ['address', 'notation']}, {'type': 'object', 'additionalProperties': False, 'properties': {'area': {'enum': ['M', 'P']}, 'bit': {'type': 'integer', 'minimum': 0, 'maximum': 2097151}, 'value': {'type': 'boolean'}}, 'required': ['area', 'bit']}, {'type': 'object', 'additionalProperties': False, 'properties': {'area': {'type': 'string', 'enum': ['D', 'C', 'T', 'F']}, 'word': {'type': 'integer', 'minimum': 0, 'description': 'D/C/T/F word index. read_words returns exact 2-byte WORD data as unsigned 16-bit integers (0..65535); no signed/type, CPU support or counter-behavior inference; no word write.'}}, 'required': ['area', 'word']}]}}, 'readback_delay_ms': {'type': 'integer', 'minimum': 0, 'maximum': 1000, 'description': 'Only write_devices with readback_devices: model-selected delay after ACK before its existing single union read. Omitted/default0 preserves immediate observation; no PLC scan or timing-success guarantee.'}, 'readback_devices': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'description': 'Only write_devices: read-only M/P targets included in its existing single readback response. No value field, extra read, scan completion or atomic snapshot guarantee.', 'items': {'oneOf': [{'type': 'object', 'additionalProperties': False, 'properties': {'address': {'type': 'string', 'pattern': '^[MP][0-9]{3,4}[0-9A-F]$'}, 'notation': {'type': 'string', 'enum': ['xgk_compact_bit']}}, 'required': ['address', 'notation']}, {'type': 'object', 'additionalProperties': False, 'properties': {'area': {'type': 'string', 'enum': ['M', 'P']}, 'bit': {'type': 'integer', 'minimum': 0, 'maximum': 2097151}}, 'required': ['area', 'bit']}]}}, 'samples': {'type': 'integer', 'minimum': 1, 'maximum': 60}, 'interval_ms': {'type': 'integer', 'minimum': 0, 'maximum': 5000}}, 'required': ['action']}))
            catalog[-1].parameters_json_schema['properties']['devices']['items']['oneOf'].append({'type': 'object', 'additionalProperties': False, 'properties': {'area': {'const': 'T'}, 'bit': {'type': 'integer', 'minimum': 0, 'maximum': 2097151}}, 'required': ['area', 'bit'], 'description': 'Standalone read_devices only: observed T contact bit index, not timer current value.'})
            catalog.append(AgentToolDefinitionV1(tool_name='execute_simulation_scenario',
                description='Execute model-authored ordered read_devices/write_devices/read_words scenario steps on the already loaded/running bound local simulator, without a model round trip per step. Supply the observed loaded SHA, case labels, distinct exact native M/P address or existing absolute bit devices with boolean expectations or read-only D/C/T/F words with exact unsigned16 integer expectations (empty means no assertion). read_devices/read_words expectations apply to every returned sample; writes require their native readback. Explicit wait_devices requires nonempty exact M/P boolean expectations: observe immediately, then up to samples with interval_ms waits between reads; only the first sample matching all expectations opens the gate. Earlier unmatched samples are retained. Exhaustion is a known mismatch and always stops the suffix, including when stop_on_mismatch is false. Bit reads, writes and waits share one bounded CLI/MSAA lifetime and absolute budget, split before the planned 60 fresh-read or byte limit. No scan-completion, stable-state or timing-success inference. Choose stop_on_mismatch explicitly for other steps. Existing binding, saved version, native target and write/readback admission run afresh for each step. Optional run_before, stop_after and end_after compose existing verified mode operations around the scenario. OFF signals remain explicit scenario steps. No implicit preparation, start, load or save. Explicit initialize starts and loads the bound saved version before the scenario. Native failure, unknown, changed binding/version or cancellation stops new steps; retain actual completed prefix and not_started suffix. A known expectation mismatch is distinct from native execution failure. Never replay an unknown step or whole batch. Optional readback_devices is only for write_devices: read-only M/P signals observed in the same existing ACK-following read response, separately returned as observed_readback. readback_matched still tests written inputs only. Extra observations do not prove a completed PLC scan, atomic snapshot, stable values or functional success. Optional readback_delay_ms (integer 0..1000, default0) requires readback_devices and delays the existing single read after ACK within the same absolute operation budget; it adds no read and proves no PLC scan or functional timing.',
                parameters_json_schema={'type': 'object', 'additionalProperties': False, 'properties': {'loaded_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}, 'stop_on_mismatch': {'type': 'boolean'}, 'run_before': {'type': 'boolean', 'description': 'Run and verify the loaded simulator before the scenario.'}, 'stop_after': {'type': 'boolean', 'description': 'Stop after the ordered scenario completes without cancellation or native failure.'}, 'end_after': {'type': 'boolean', 'description': 'End XG-SIM after completed scenario and any selected STOP. Input/output OFF belongs in the model-authored steps.'}, 'steps': {'type': 'array', 'minItems': 1, 'items': {'type': 'object', 'additionalProperties': False, 'properties': {'case_label': {'type': 'string', 'minLength': 1}, 'action': {'type': 'string', 'enum': ['read_devices', 'write_devices', 'read_words', 'wait_devices']}, 'devices': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': {'oneOf': [{'type': 'object', 'additionalProperties': False, 'properties': {'address': {'type': 'string', 'pattern': '^[MP][0-9]{3,4}[0-9A-F]$', 'description': 'Exact native LD bit address in the explicitly selected notation; conversion is not CPU-family verification.'}, 'value': {'type': 'boolean'}, 'notation': {'type': 'string', 'enum': ['xgk_compact_bit'], 'description': 'Explicitly select decimal word plus final hexadecimal bit notation. This does not infer or verify CPU compatibility.'}}, 'required': ['address', 'notation']}, {'type': 'object', 'additionalProperties': False, 'properties': {'area': {'enum': ['M', 'P']}, 'bit': {'type': 'integer', 'minimum': 0, 'maximum': 2097151}, 'value': {'type': 'boolean'}}, 'required': ['area', 'bit']}, {'type': 'object', 'additionalProperties': False, 'properties': {'area': {'type': 'string', 'enum': ['D', 'C', 'T', 'F']}, 'word': {'type': 'integer', 'minimum': 0, 'description': 'D/C/T/F word index. read_words returns exact 2-byte WORD data as unsigned 16-bit integers (0..65535); no signed/type, CPU support or counter-behavior inference; no word write.'}}, 'required': ['area', 'word']}]}}, 'expected': {'type': 'array', 'items': {'oneOf': [{'type': 'object', 'additionalProperties': False, 'properties': {'address': {'type': 'string', 'pattern': '^[MP][0-9]{3,4}[0-9A-F]$', 'description': 'Exact native LD bit address in the explicitly selected notation; conversion is not CPU-family verification.'}, 'value': {'type': 'boolean'}, 'notation': {'type': 'string', 'enum': ['xgk_compact_bit'], 'description': 'Explicitly select decimal word plus final hexadecimal bit notation. This does not infer or verify CPU compatibility.'}}, 'required': ['address', 'notation', 'value']}, {'type': 'object', 'additionalProperties': False, 'properties': {'area': {'enum': ['M', 'P']}, 'bit': {'type': 'integer', 'minimum': 0, 'maximum': 2097151}, 'value': {'type': 'boolean'}}, 'required': ['area', 'bit', 'value']}, {'type': 'object', 'additionalProperties': False, 'properties': {'area': {'type': 'string', 'enum': ['D', 'C', 'T', 'F']}, 'word': {'type': 'integer', 'minimum': 0, 'description': 'D/C/T/F word index. read_words returns exact 2-byte WORD data as unsigned 16-bit integers (0..65535); no signed/type, CPU support or counter-behavior inference; no word write.'}, 'value': {'type': 'integer', 'minimum': 0, 'maximum': 65535, 'description': 'Exact unsigned16 expectation; boolean is not an integer word value.'}}, 'required': ['area', 'word', 'value']}]}}, 'readback_delay_ms': {'type': 'integer', 'minimum': 0, 'maximum': 1000, 'description': 'Only write_devices with readback_devices: model-selected delay after ACK before its existing single union read. Omitted/default0 preserves immediate observation; no PLC scan or timing-success guarantee.'}, 'readback_devices': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'description': 'Only write_devices: read-only M/P targets included in its existing single readback response. No value field, extra read, scan completion or atomic snapshot guarantee.', 'items': {'oneOf': [{'type': 'object', 'additionalProperties': False, 'properties': {'address': {'type': 'string', 'pattern': '^[MP][0-9]{3,4}[0-9A-F]$'}, 'notation': {'type': 'string', 'enum': ['xgk_compact_bit']}}, 'required': ['address', 'notation']}, {'type': 'object', 'additionalProperties': False, 'properties': {'area': {'type': 'string', 'enum': ['M', 'P']}, 'bit': {'type': 'integer', 'minimum': 0, 'maximum': 2097151}}, 'required': ['area', 'bit']}]}}, 'samples': {'type': 'integer', 'minimum': 1, 'maximum': 60}, 'interval_ms': {'type': 'integer', 'minimum': 0, 'maximum': 5000}}, 'required': ['case_label', 'action', 'devices', 'expected']}}}, 'required': ['loaded_sha256', 'stop_on_mismatch', 'steps']}))
            scenario_schema=catalog[-1].parameters_json_schema
            scenario_schema['properties']['initialize']={'type':'object','additionalProperties':False,
                'properties':{'saved_sha256':{'type':'string','pattern':'^[0-9a-f]{64}$'}},
                'required':['saved_sha256'],'description':'Start and load this exact stable saved version before the scenario; use instead of loaded_sha256. Actual load may return an accepted native saved-version SHA.'}
            scenario_schema['required'].remove('loaded_sha256')
            scenario_schema['oneOf']=[{'required':['loaded_sha256']},{'required':['initialize']}]
        return catalog

    def _check_target(self) -> None:
        current = self.current_target()
        fields = ("workspace_id", "revision_id", "session_id", "pid", "hwnd", "project_path")
        if current is None or any(current.get(key) != self.target.get(key) for key in fields):
            self.rung_sources.clear();self.rung_comments.clear()
            raise MCPToolError("Native run target changed")

    def check_checkpoint_target(self) -> None:
        # Persist prior results while a proved recovery retires its old target.
        # This grants no native/UI capability to any other tool.
        if self.recovery_pending:
            current=self.current_target()
            if current is None or all(current.get(k)==self.target.get(k) for k in
                    ('workspace_id','revision_id','session_id','pid','hwnd','project_path')):
                return
        self._check_target()

    async def execute_with_diagnostic_context(self, name, arguments, context):
        return await self.execute(name, arguments, _diagnostic_context=context)

    async def execute(self, name: str, arguments: dict[str, Any], *, _diagnostic_context=None) -> dict[str, Any]:
        async with self._execution_lock:
            self._diagnostic_call_context = deepcopy(_diagnostic_context or {})
            try:
                # Join already started managed input before cancellation releases
                # this owner. No second request can overtake its recovery stages.
                if (name in {'recover_workspace','set_declaration_comments'}
                        or self.recovery_only and name=='native_editor' and arguments.get('action')=='respond_owned_ui'):
                    prior_ui = (deepcopy(self.owned_ui.get(arguments.get('ui_ref')))
                        if name == 'native_editor' and arguments.get('action') == 'respond_owned_ui' else None)
                    async def joined_prefix():
                        result = await self._execute(name, arguments)
                        if (self.wait_workspace_recovery is not None and self.recovery_pending
                                and name=='native_editor' and arguments.get('action')=='respond_owned_ui'):
                            # The actual response, post-input validation, durable
                            # unknown barrier and typed handoff form one prefix.
                            result = await self.wait_workspace_recovery(self, result,
                                prior_ui_identity=prior_ui,defer_only=True)
                        return result
                    result = await native_checkpoint_boundary(joined_prefix())
                    if (self.wait_workspace_recovery is not None and self.recovery_pending
                            and name == 'recover_workspace'):
                        # Only the idle read-only wait is outside the join.
                        result = await self.wait_workspace_recovery(self, result)
                    return result
                return await self._execute(name, arguments)
            finally:
                self._diagnostic_call_context = {}

    def _retain_simulator_end_precontrol_source(self, arguments, raw, saved):
        """Retain only this owner's recorded no-control End navigation failure."""
        from controlstudio_contracts.native_operation_continuation import (
            operation_constraints_digest,
        )
        if arguments.get('action') == 'observe':
            return
        self._simulator_end_precontrol_source = None
        navigation = raw.get('navigation_input') if isinstance(raw, dict) else None
        reported = raw.get('saved_project') if isinstance(raw, dict) else None
        context = self._diagnostic_call_context
        def worker_running(value):
            return (isinstance(value, dict) and (value.get('worker_running') is True
                    or any(worker_running(item) for item in value.values()))
                or isinstance(value, list) and any(worker_running(item) for item in value))
        if (arguments.get('action') != 'end' or not isinstance(raw, dict)
                or raw.get('action') != 'end' or raw.get('ok') is not False
                or worker_running(raw)
                or raw.get('input_state_unknown') is not True
                or raw.get('simulator_control_action_attempted') is not False
                or not isinstance(navigation, dict) or navigation.get('attempted') is not True
                or navigation.get('control_action_attempted') is not False
                or (navigation.get('outcome') in {'opened', 'not_delivered'}
                    and (navigation.get('outcome') != 'opened'
                         or navigation.get('close_outcome') in {'closed', 'already_closed'}))
                or not isinstance(reported, dict) or reported.get('stable_during_read') is not True
                or saved.get('stable_during_read') is not True
                or reported.get('sha256') != saved.get('sha256')
                or raw.get('loaded_sha256') != saved.get('sha256')
                or self.current_recovery_admission is None
                or any(type(context.get(key)) is not str or not context[key].strip()
                       for key in ('conversation_id', 'run_id', 'request_id', 'call_id'))):
            return
        generation = self.current_recovery_admission(self)
        if generation is not None:
            self._simulator_end_precontrol_source = {
                'source_call_id': context['call_id'],
                'source_raw_receipt_sha256': operation_constraints_digest(raw),
                'admitted_generation': generation, 'saved_sha256': saved['sha256']}

    def _current_operation_continuation(self, arguments, raw, saved):
        # This public observation is scoped by the actual broker callback and
        # existing Host admission, not a discovered tool or a historical receipt.
        from controlstudio_contracts.native_operation_continuation import (
            SCHEMA,
            operation_constraints_digest,
            validate_current_operation_continuation,
        )
        constraints = raw.get('current_constraints') if isinstance(raw,dict) else None
        context = self._diagnostic_call_context
        reported = raw.get('saved_project') if isinstance(raw,dict) else None
        source = getattr(self, '_simulator_end_precontrol_source', None)
        if (isinstance(source, dict) and saved.get('stable_during_read') is True
                and (source.get('saved_sha256') != saved.get('sha256')
                    or isinstance(constraints, dict) and constraints.get('journal_state') != 'loaded')):
            self._simulator_end_precontrol_source = source = None
        current_loaded = (isinstance(source, dict) and isinstance(constraints, dict)
            and constraints.get('journal_state') == 'loaded'
            and raw.get('loaded_sha256') == saved.get('sha256')
            and source.get('saved_sha256') == saved.get('sha256'))
        if (arguments != {'action':'observe'} or not isinstance(raw,dict)
                or raw.get('ok') is not True or raw.get('action')!='observe'
                or raw.get('error') or raw.get('failure') or raw.get('input_state_unknown') is True
                or not isinstance(constraints,dict)
                or constraints.get('schema')!='xg5000-simulator-current-constraints/v1'
                or constraints.get('journal_scope')!='record_for_current_binding'
                or constraints.get('evidence_scope')!='current_binding_journal_snapshot'
                or not (current_loaded
                    or constraints.get('start_behavior')=='blocked_by_current_journal_unknown'
                    and type(constraints.get('start_journal_blocker')) is str
                    and bool(constraints['start_journal_blocker']))
                or constraints.get('snapshot_resolves_historical_outcomes') is not False
                or constraints.get('fresh_frame_saved_binding_revalidation_required') is not True
                or raw.get('load_state')!=constraints.get('journal_state')
                or not isinstance(reported,dict) or reported.get('stable_during_read') is not True
                or reported.get('sha256')!=saved.get('sha256') or saved.get('stable_during_read') is not True
                or self.recover_workspace is None or self.current_recovery_admission is None
                or any(type(context.get(key)) is not str or not context[key].strip()
                       for key in ('conversation_id','run_id','request_id','call_id'))):
            return None
        generation = self.current_recovery_admission(self)
        if generation is None:
            return None
        if current_loaded and source['admitted_generation'] != generation:
            self._simulator_end_precontrol_source = None
            return None
        route = 'recover_workspace'
        if current_loaded:
            # The original UI input stays unknown. This describes a newly
            # selected End route; ordinary exact-frame/lifetime/saved/menu
            # admission still runs and no pending completion input is cleared.
            constraints['operation_local_continuation'] = {
                'explicit_action': 'end', 'route_tool_name': 'simulator',
                'navigation_outcome_unknown': True,
                'previous_end_simulator_control_dispatched': False,
                'current_journal_state': 'loaded',
                'fresh_native_preflight_required': True,
                'original_outcome_preserved': True,
                'source_call_id': source['source_call_id'],
                'source_raw_receipt_sha256': source['source_raw_receipt_sha256']}
            route = 'simulator'
        if (constraints.get('different_saved_version_load_available') is True
                and constraints.get('load_requires_stopped_idle') is True
                and constraints.get('load_requires_explicit_request') is True):
            route = 'simulator'
        reconciliation = raw.get('end_reconciliation')
        if (constraints.get('journal_state') == 'end_outcome_unknown'
                and isinstance(reconciliation, dict)
                and reconciliation.get('explicit_action') == 'end'
                and reconciliation.get('purpose') == 'verify_current_session_inactive'
                and reconciliation.get('simulator_control_replay') is False):
            # Preserve the CLI's operation-local observation route. This issues
            # no control permission and cannot settle the prior End outcome.
            route = 'simulator'
        return validate_current_operation_continuation({
            'schema':SCHEMA,'provenance':'host_current_operation_continuation/v1',
            'source_tool_name':'simulator','source_call_id':context['call_id'],
            'host_request_id':context['request_id'],'constraints_schema':constraints['schema'],
            'constraints_sha256':operation_constraints_digest(constraints),
            'admitted_generation':generation,'saved_sha256':saved['sha256'],
            'route_tool_name':route,'native_input_authority':False,
            'original_outcomes_preserved':True})

    async def _observe_vendor_process_start(self, saved_sha256):
        if self._record_process_diagnostic is None:
            return
        try:
            from .native_vendor_process_diagnostics import VendorProcessDiagnostics
            if self._vendor_process_diagnostics is None:
                self._vendor_process_diagnostics = VendorProcessDiagnostics(self._record_process_diagnostic)
            await self._vendor_process_diagnostics.note_start({**self._diagnostic_call_context,
                'workspace_id':self.target['workspace_id'],'revision_id':self.target['revision_id'],
                'target_pid':self.target['pid'],'target_process_create_time':self.expected_process_create_time,
                'saved_sha256':saved_sha256})
        except asyncio.CancelledError:
            raise
        except Exception:
            # A read-only diagnostic never substitutes for native action outcome.
            pass

    async def close_vendor_process_diagnostics(self):
        if self._vendor_process_diagnostics is not None:
            await native_checkpoint_boundary(self._vendor_process_diagnostics.close())

    def _retain_rung_source(self, request: dict[str,Any], raw: dict[str,Any]) -> None:
        # Private current-run source ownership. Historical public projections are
        # not complete native rows and never create a new comment capability.
        action=request.get('action')
        if raw.get('ok') is False or raw.get('error') or _rung_unknown(raw):
            self.rung_sources.clear();self.rung_comments.clear();return
        ld=raw.get('ld');read=raw.get('read_receipt')
        if action=='read_ld' and isinstance(ld,dict) and isinstance(read,dict):
            decoded=ld.get('decoded');program=ld.get('program')
            rows=decoded.get('rows') if isinstance(decoded,dict) else None
            if (ld.get('operation')=='program.read_ld' and ld.get('ok') is True
                    and ld.get('verified') is True and ld.get('stable_repeat') is True
                    and ld.get('clipboard_format')=='XG5000LADDER'
                    and re.fullmatch('[0-9a-f]{64}',str(ld.get('payload_sha256'))) is not None
                    and read.get('completed') is True and not _rung_unknown(read)
                    and isinstance(program,dict) and program.get('name')==request.get('program_name')
                    and isinstance(rows,(list,tuple)) and rows
                    and all(isinstance(row,dict) and type(row.get('visual_row')) is int for row in rows)
                    and [row['visual_row'] for row in rows]==sorted({row['visual_row'] for row in rows})):
                saved=saved_project_observation(Path(self.target['project_path']))
                if saved.get('stable_during_read') is True and saved.get('sha256'):
                    canonical=json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
                    old=self.rung_sources.get(program['name'])
                    source={'payload_sha256':ld['payload_sha256'],
                            'decoded_rows_sha256':hashlib.sha256(canonical.encode('utf8')).hexdigest()}
                    if old is None or old['source']!=source:self.rung_comments.clear()
                    self.rung_sources[program['name']]={'rows':json.loads(canonical),'source':source,
                        'target':deepcopy(self.target),'generation':self.rung_generation,'saved_sha256':saved['sha256']}
                    return
            self.rung_sources.clear();self.rung_comments.clear()
        elif action not in {'observe','activate','read_check','program_check','read_find_results','find_ld_device',
                'find_ld_devices','read_ld_rows','read_selection'}:
            # A new navigation/input can retire the inspected modal or shift its
            # historical row. Require a new full read instead of guessing lineage.
            self.rung_sources.clear();self.rung_comments.clear()

    async def _rung_comment(self, action: str, arguments: dict[str,Any]) -> dict[str,Any]:
        def refused(kind):
            return {'ok':False,'action':action,'error':{'kind':kind},'action_attempted':False,
                    'input_state_unknown':False,'automatic_replay':False}
        self._check_target()
        if (self.expected_process_create_time is None or self.current_process_create_time is not None
                and self.current_process_create_time()!=self.expected_process_create_time):
            self.rung_comments.clear();self.rung_sources.clear();return refused('rung_comment_binding_unavailable')
        saved=await asyncio.to_thread(saved_project_observation,Path(self.target['project_path']))
        self._check_target()
        if saved.get('stable_during_read') is not True:return refused('rung_comment_saved_version_unavailable')
        request={'action':action,'pid':self.target['pid'],'hwnd':self.target['hwnd']}
        static={'pid':self.target['pid'],'hwnd':self.target['hwnd'],
                'process_create_time':self.expected_process_create_time,'project_path':self.target['project_path'],
                'project_sha256':saved.get('sha256')}
        if action=='inspect_rung_comment':
            if (set(arguments)!={'program_name','payload_sha256','visual_row'}
                    or type(arguments['visual_row']) is not int or arguments['visual_row']<0
                    or not isinstance(arguments['program_name'],str)
                    or self.program_name is not None and arguments['program_name']!=self.program_name):
                return refused('invalid_rung_comment_reference')
            source=self.rung_sources.get(arguments['program_name'])
            if (source is None or source['generation']!=self.rung_generation or source['target']!=self.target
                    or source['source']['payload_sha256']!=arguments['payload_sha256']
                    or source['saved_sha256']!=saved.get('sha256')):
                return refused('rung_comment_requires_fresh_full_ld_source')
            rows=[row for row in source['rows'] if row['visual_row']==arguments['visual_row']]
            if len(rows)!=1:return refused('rung_comment_row_unavailable')
            self.rung_comments.clear()
            request.update(rung_binding={**static,'program_name':arguments['program_name']},
                rung_source=deepcopy(source['source']),rung_expected_row=deepcopy(rows[0]))
        else:
            if (set(arguments)!={'comment_ref','comment'} or not isinstance(arguments.get('comment_ref'),str)
                    or not isinstance(arguments.get('comment'),str) or '\0' in arguments['comment']):
                return refused('invalid_rung_comment_arguments')
            try:arguments['comment'].encode('utf-16-le')
            except UnicodeEncodeError:return refused('invalid_rung_comment_arguments')
            proof=self.rung_comments.pop(arguments['comment_ref'],None)
            if (proof is None or proof['target']!=self.target or proof['generation']!=self.rung_generation
                    or any(proof['authority']['binding'].get(key)!=value for key,value in static.items())):
                return refused('rung_comment_reference_unavailable')
            request.update(rung_authority=proof['authority'],rung_comment_text=arguments['comment'])
        try:raw=await self.gateway.call_workspace_tool('xg5000.editor',request)
        except MCPToolRejectedError as error:
            envelope=error.mcp_error_payload or {};self.record_raw('native_editor',request,envelope)
            self.rung_comments.clear();self.rung_sources.clear()
            typed=envelope.get('error',{});native=validate_native_failure(typed['nativeFailure']) if 'nativeFailure' in typed else None
            details=native['details'] if native is not None else {}
            flags=[details.get('input_state_unknown')]
            if isinstance(details.get('receipt'),dict):flags.append(details['receipt'].get('input_state_unknown'))
            known=[flag for flag in flags if type(flag) is bool]
            output={**_rung_public(details),'ok':False,'action':action,
                'error':{'kind':'native_rung_comment_rejected',**({'code':typed['code']} if 'code' in typed else {}),
                    **({'nativeFailure':_rung_public(native)} if native is not None else {})},
                'input_state_unknown':True if True in known else False if known else True,'automatic_replay':False}
            quarantine=_operation_quarantine_projection(typed)
            if quarantine is not None:output.update(quarantine)
            return output
        except BaseException:
            self.rung_comments.clear();self.rung_sources.clear();raise
        self.record_raw('native_editor',request,raw);self._check_target()
        body=raw.get('rung_comment') if isinstance(raw,dict) else None
        public=_rung_public(raw)
        if action=='inspect_rung_comment':
            authority=body.get('authority') if isinstance(body,dict) else None
            valid=(raw.get('ok') is True and isinstance(body,dict) and body.get('phase')=='inspected'
                and body.get('input_state_unknown') is False and not _rung_unknown(raw)
                and isinstance(body.get('comment'),str) and isinstance(authority,dict)
                and authority.get('schema')=='xg5000.rung-comment-authority/v1'
                and set(authority)=={'schema','binding','source','expected_row','baseline','dialog','fields','marker'}
                and isinstance(authority.get('binding'),dict)
                and all(authority['binding'].get(key)==value for key,value in request['rung_binding'].items())
                and set(authority['binding'])==set(request['rung_binding'])|{'program_id','mdi_hwnd','editor_hwnd'}
                and authority.get('source')==request['rung_source']
                and authority.get('expected_row')==request['rung_expected_row'])
            if valid:
                ref='rc:'+uuid.uuid4().hex
                self.rung_comments[ref]={'authority':authority,'generation':self.rung_generation,'target':deepcopy(self.target)}
                public['rung_comment']['comment_ref']=ref
        else:
            valid=(raw.get('ok') is True and isinstance(body,dict) and body.get('phase')=='verified'
                and body.get('comment_verified') is True and body.get('native_edit_completed') is True
                and body.get('input_state_unknown') is False and not _rung_unknown(raw)
                and isinstance(body.get('native_ld_preservation'),dict)
                and body['native_ld_preservation'].get('verified') is True)
            self.rung_sources.clear();self.rung_comments.clear()
            if valid:
                program = proof['authority']['binding']['program_name']
                self._retain_rung_source({'action': 'read_ld', 'program_name': program}, raw)
                if program in self.rung_sources:
                    public.update(ld=raw['ld'], read_receipt=raw['read_receipt'])
        if not valid:
            self.rung_sources.clear();self.rung_comments.clear()
            return {**public,'ok':False,'action':action,'error':{'kind':'native_rung_comment_unverified'},
                    'input_state_unknown':True,'automatic_replay':False}
        return {**public,'ok':True,'action':action,'saved':False,'automatic_replay':False,
                'runtime_behavior_verified':False,'input_state_unknown':False}

    async def _simulation_write_group(self, steps, sha256, stop_on_mismatch):
        """One internal CLI invocation; cancellation joins only its admitted step."""
        from .native_simulator_sequence_control import SimulatorSequenceControl
        self._simulator_end_precontrol_source = None
        children=[{'index':i,'case_label':step['case_label'],'action':step['action'],'status':'not_started',
                   'expected':deepcopy(step['expected'])} for i,step in enumerate(steps)]
        fallback={'ok':False,'action':'write_sequence','batch':{'children':children,'completed_prefix_count':0,
            'cancelled':False,'stop_index':0,'stop_reason':'sequence_admission_failed'},
            'automatic_replay':False,'runtime_behavior_verified':False}
        def admission_failure(kind, stage, error=None):
            result=deepcopy(fallback)
            result.update(action_attempted=False,input_state_unknown=False,
                error={'kind':kind,'stage':stage,'action_attempted':False,'input_state_unknown':False})
            if error is not None:
                result['error']['exception_kind']=type(error).__name__
            return result
        stage='host_target_validation'
        try:
            self._check_target()
            stage='host_process_lifetime'
            if self.current_process_create_time is not None and self.current_process_create_time()!=self.expected_process_create_time:
                return admission_failure('simulator_sequence_lifetime_changed',stage)
            stage='host_saved_observation'
            saved=await asyncio.to_thread(saved_project_observation,Path(self.target['project_path']))
            if saved.get('stable_during_read') is not True:
                return admission_failure('simulator_sequence_saved_unstable',stage)
            if saved.get('sha256')!=sha256:
                return admission_failure('simulator_sequence_saved_version_changed',stage)
        except asyncio.CancelledError:
            result=admission_failure('simulator_sequence_cancelled_before_dispatch',stage)
            result['batch'].update(cancelled=True,stop_reason='cancelled_before_step');return result
        except Exception as error:
            return admission_failure('simulator_sequence_admission_failed',stage,error)
        binding={key:self.target[key] for key in ('pid','hwnd','project_path')}
        binding.update(project_hash=sha256,expected_process_create_time=self.expected_process_create_time)
        control=None;cancelled=False;signal_confirmed=True;failed=False;raw=None;body=None;launched=False;rejected=False
        terminal_proven=False
        async def terminal_read(method, *values):
            nonlocal cancelled, signal_confirmed
            read_task=asyncio.create_task(asyncio.to_thread(method,*values))
            while not read_task.done():
                try:await asyncio.shield(read_task)
                except asyncio.CancelledError:
                    cancelled=True;signal_confirmed=control.revoke() and signal_confirmed
                except Exception:break
            return read_task.result()
        try:
            stage='host_control_creation'
            control=SimulatorSequenceControl(binding,steps)
            stage='host_control_request'
            request={**binding,'workspace_id':self.target['workspace_id'],'revision_id':self.target['revision_id'],
                'action':'write_sequence','sequence_steps':deepcopy(steps),'stop_on_mismatch':stop_on_mismatch,
                'sequence_control':control.control}
            task=asyncio.create_task(self.gateway.call_workspace_tool('workspace.simulator',request));launched=True
            try:
                raw=await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled=True;signal_confirmed=control.revoke()
                # The CLI checks this opaque signal at each step admission. Join
                # its one already-admitted write/readback, not a fresh suffix.
                while not task.done():
                    try:await asyncio.shield(task)
                    except asyncio.CancelledError:continue
                    except BaseException:break
                try:raw=task.result()
                except BaseException:failed=True
            except MCPToolRejectedError:
                failed=True;rejected=True
            except Exception:
                failed=True
            signal_confirmed=control.revoke() and signal_confirmed
            try:
                if raw is not None:
                    try:body=await terminal_read(control.terminal_result,raw)
                    except Exception:body=await terminal_read(control.terminal_progress)
                else:
                    body=await terminal_read(control.terminal_progress)
                terminal_proven=body.pop('_sequence_terminal_proven',False) if isinstance(body,dict) else False
                if rejected and terminal_proven:failed=False
            except Exception:
                body=None
            if not isinstance(body,dict):
                body=deepcopy(fallback)
                child=body['batch']['children'][0]
                child.update(status='unknown',result={'ok':False,'action':steps[0]['action'],'input_state_unknown':True,
                    'automatic_replay':False,'error':{'kind':'simulator_sequence_result_unconfirmed'}})
                body['batch']['stop_reason']='sequence_result_unconfirmed'
                body['input_state_unknown']=True
            actual=body.get('batch',{}).get('children')
            prefix=body.get('batch',{}).get('completed_prefix_count')
            valid=(isinstance(actual,list) and len(actual)==len(steps) and type(prefix) is int and 0<=prefix<=len(steps))
            if valid:
                for index,(child,step) in enumerate(zip(actual,steps)):
                    if (not isinstance(child,dict) or child.get('index')!=index or child.get('case_label')!=step['case_label']
                            or child.get('action')!=step['action'] or child.get('expected')!=step['expected']
                            or child.get('status') not in {'not_started','admitted','completed','failed','unknown'}
                            or index<prefix and child.get('status')!='completed'
                            or index>=prefix and child.get('status')=='completed'
                            or index>prefix and child.get('status')!='not_started'):
                        valid=False;break
            if not valid:
                control.terminal_verified=False  # Preserve invalid receipt ownership evidence.
                body=deepcopy(fallback)
                body['batch']['children'][0].update(status='unknown',result={'ok':False,'action':steps[0]['action'],
                    'input_state_unknown':True,'automatic_replay':False,'error':{'kind':'simulator_sequence_receipt_invalid'}})
                body['batch']['stop_reason']='sequence_receipt_invalid';body['input_state_unknown']=True
            if failed or not signal_confirmed:
                body['ok']=False;body['error']={'kind':'simulator_sequence_transport_failed' if failed else 'simulator_sequence_cancel_unconfirmed'}
                batch=body['batch'];prefix=batch['completed_prefix_count']
                if prefix<len(steps) and not terminal_proven:
                    active=batch['children'][prefix]
                    active['status']='unknown'
                    receipt=active.setdefault('result',{'action':steps[prefix]['action']})
                    receipt.update(ok=False,input_state_unknown=True,automatic_replay=False)
                    batch.update(stop_index=prefix,stop_reason='sequence_transport_unknown')
                    body['input_state_unknown']=True
            if cancelled:body['batch']['cancelled']=True;body['ok']=False
            for child,step in zip(body['batch']['children'],steps):
                if child.get('status')=='admitted':
                    child.update(status='unknown',native_execution_completed=False)
                    child.setdefault('result',{}).update(ok=False,input_state_unknown=True,automatic_replay=False)
                if isinstance(child.get('result'),dict):
                    self.record_raw('simulator',{**binding,**{key:deepcopy(value) for key,value in step.items()
                        if key=='action' or step['action'] not in {'run','stop','end'}
                        and key in {'devices','samples','interval_ms','readback_devices','readback_delay_ms'}}},child['result'])
                    if terminal_proven:
                        self._retain_simulator_end_precontrol_source(step, child['result'], saved)
                    else:
                        self._simulator_end_precontrol_source = None
            try:self._check_target()
            except MCPToolError:
                body.update(ok=False,error={'kind':'simulator_sequence_host_binding_changed'})
            body['input_state_unknown']=any(child.get('result',{}).get('input_state_unknown') is True
                for child in body['batch']['children'])
            return body
        except Exception as error:
            if control is not None:
                control.revoke()
                if body is not None:control.terminal_verified=False  # Required Host recording failed: retain evidence.
            if not launched:
                return admission_failure('simulator_sequence_control_unavailable',stage,error)
            if isinstance(body,dict):
                body.update(ok=False,error={'kind':'simulator_sequence_host_recording_failed'})
                return body
            if launched:
                fallback['batch']['children'][0].update(status='unknown',result={'ok':False,'action':steps[0]['action'],
                    'input_state_unknown':True,'automatic_replay':False})
                fallback['input_state_unknown']=True
                fallback['batch']['stop_reason']='sequence_result_unconfirmed'
            return fallback
        finally:
            if control is not None:
                if not launched:control.terminal_verified=True
                try:control.close()
                except Exception:pass  # Keep unconfirmed private evidence; never infer successful cleanup.

    async def _execute_simulation_scenario(self, arguments: dict[str,Any]) -> dict[str,Any]:
        steps=_validated_simulation_scenario(arguments)
        if not self.allow_simulator or self.expected_process_create_time is None:
            return {'ok':False,'error':{'kind':'simulator_scenario_authority_unavailable'},
                'action_attempted':False,'input_state_unknown':False,'automatic_replay':False}
        loaded_sha256=arguments.get('loaded_sha256')
        children=[{'index':i,'case_label':step['case_label'],'action':step['action'],
            'status':'not_started','expected':deepcopy(step['expected'])} for i,step in enumerate(steps)]
        batch={'loaded_sha256':loaded_sha256,'children':children,'completed_prefix_count':0,
            'cancelled':False,'stop_index':None,'stop_reason':None}
        def unknown(value):
            return (isinstance(value,dict) and (value.get('input_state_unknown') is True
                or any(unknown(item) for item in value.values()))) or (isinstance(value,list) and any(unknown(item) for item in value))
        lifecycle={action:{'status':'not_started'} for action,flag in
            (('run','run_before'),('stop','stop_after'),('end','end_after')) if arguments.get(flag) is True}
        # Validate the single bounded sequence before Start/load or any input.
        planned_sequence = _simulation_lifecycle_plan(steps,lifecycle)
        if 'initialize' in arguments:
            batch['requested_saved_sha256']=arguments['initialize']['saved_sha256']
            lifecycle={'start':{'status':'not_started'},'load':{'status':'not_started'},**lifecycle}
        async def lifecycle_action(action, expected_sha256=None):
            entry=lifecycle[action]
            entry.update(status='dispatched',started_at_ns=time.time_ns())
            task=asyncio.create_task(self._execute('simulator',{'action':action},
                expected_simulator_sha256=expected_sha256 if expected_sha256 is not None else loaded_sha256))
            try:
                result=await asyncio.shield(task)
            except asyncio.CancelledError:
                batch.update(cancelled=True,stop_reason='cancelled_during_'+action)
                while not task.done():
                    try:await asyncio.shield(task)
                    except asyncio.CancelledError:continue
                    except BaseException:break
                try:result=task.result()
                except BaseException:result={'ok':False,'input_state_unknown':True,
                    'error':{'kind':'scenario_lifecycle_outcome_unknown'}}
            except Exception as error:
                result={'ok':False,'input_state_unknown':True,
                    'error':{'kind':'scenario_lifecycle_failed_without_receipt','exception_kind':type(error).__name__}}
            current_unknown=isinstance(result,dict) and result.get('input_state_unknown') is True
            complete=(isinstance(result,dict) and result.get('ok') is True
                and not result.get('error') and not result.get('failure') and not current_unknown)
            entry.update(status='unknown' if current_unknown else 'completed' if complete else 'failed',
                finished_at_ns=time.time_ns(),result=result)
            if not complete and batch['stop_reason'] is None:batch['stop_reason']='lifecycle_'+action+'_failed'
            return complete and not batch['cancelled']
        initialization_ready=True
        if 'initialize' in arguments:
            initial_sha256=arguments['initialize']['saved_sha256']
            initialization_ready=False
            try:
                self._check_target()
                if self.current_process_create_time is not None and self.current_process_create_time()!=self.expected_process_create_time:
                    raise MCPToolError('Scenario process lifetime changed before initialization')
                saved=await asyncio.to_thread(saved_project_observation,Path(self.target['project_path']))
                if saved.get('stable_during_read') is not True or saved.get('sha256')!=initial_sha256:
                    raise MCPToolError('Scenario initialization saved version changed')
                initialization_ready=True
            except asyncio.CancelledError:
                batch.update(cancelled=True,stop_reason='cancelled_before_initialization')
            except Exception as error:
                batch.update(stop_reason='initialization_saved_version_unverified',
                    error={'kind':'simulator_scenario_initialization_rejected','message':str(error)})
            if initialization_ready:
                initialization_ready=await lifecycle_action('start',initial_sha256)
            if initialization_ready:
                initialization_ready=await lifecycle_action('load',initial_sha256)
            if initialization_ready:
                loaded=lifecycle['load']['result']
                reported=loaded.get('saved_project')
                accepted=loaded.get('loaded_sha256')
                if (loaded.get('action')=='load' and loaded.get('load_state')=='loaded'
                        and loaded.get('requested_sha256')==initial_sha256
                        and isinstance(accepted,str) and re.fullmatch('[0-9a-f]{64}',accepted)
                        and isinstance(reported,dict) and reported.get('stable_during_read') is True
                        and reported.get('sha256')==accepted):
                    loaded_sha256=accepted;batch['loaded_sha256']=accepted
                else:
                    initialization_ready=False
                    batch.update(stop_reason='initialization_load_version_unverified',
                        error={'kind':'simulator_scenario_initialized_load_unverified'})
        plans=planned_sequence if initialization_ready else None
        sequence_used=plans is not None
        if sequence_used:
            for case_start,plan in plans:
                grouped=await self._simulation_write_group(plan,loaded_sha256,arguments['stop_on_mismatch'])
                group_batch=grouped.get('batch',{})
                if grouped.get('error'):batch['error']=deepcopy(grouped['error'])
                case_index=case_start
                for child,step in zip(group_batch.get('children',[]),plan):
                    if step['action'] in lifecycle:
                        lifecycle[step['action']]=deepcopy({key:value for key,value in child.items()
                            if key in {'status','started_at_ns','finished_at_ns','result'}})
                        if child.get('status') in {'failed','unknown'}:
                            batch['stop_reason']='lifecycle_'+step['action']+'_failed'
                        continue
                    entry=children[case_index]
                    entry.update(deepcopy(child));entry['index']=case_index
                    if child.get('status')=='completed':
                        result=entry.get('result',{})
                        native_ok=(result.get('ok') is True and not result.get('error') and not result.get('failure')
                            and not unknown(result) and result.get('loaded_sha256')==loaded_sha256
                            and result.get('saved_project',{}).get('sha256')==loaded_sha256
                            and result.get('saved_project',{}).get('stable_during_read') is True)
                        verdict=_simulation_expectations(steps[case_index],result) if native_ok else None
                        native_ok=native_ok and verdict['native_samples_complete'] is True
                        entry.update(native_execution_completed=bool(native_ok),status='completed' if native_ok else 'failed')
                        if verdict is not None:entry['expectation']=verdict
                        if native_ok and batch['completed_prefix_count']==case_index:batch['completed_prefix_count']+=1
                    if entry.get('status') in {'failed','unknown'} and batch['stop_index'] is None:
                        batch.update(stop_index=case_index,stop_reason=group_batch.get('stop_reason') or 'native_failure')
                    case_index+=1
                if group_batch.get('cancelled') is True:
                    batch.update(cancelled=True,stop_reason=group_batch.get('stop_reason') or 'cancelled_during_step')
                elif batch['stop_reason'] is None:
                    batch['stop_reason']=group_batch.get('stop_reason')
                stopped=group_batch.get('stop_index')
                if (batch['stop_index'] is None and type(stopped) is int and 0<=stopped<len(plan)
                        and plan[stopped]['action'] not in lifecycle):
                    batch['stop_index']=case_start+sum(step['action'] not in lifecycle for step in plan[:stopped])
                if (batch['cancelled'] or batch['stop_reason'] is not None or batch.get('error')
                        or any(child['status']!='completed' for child in children[case_start:case_index])):
                    break
            ready=False
        else:
            ready=initialization_ready and (await lifecycle_action('run') if 'run' in lifecycle else True)
        handled_until=0
        for index,step in enumerate(steps if ready else []):
            if index<handled_until:continue
            end=_simulation_write_group_end(steps,index)
            if end-index>=2 or step['action']=='wait_devices':
                # No new user schema or hidden retry. Each bounded group owns one
                # reader and absolute native deadline; public indices stay global.
                grouped=await self._simulation_write_group(steps[index:end],loaded_sha256,arguments['stop_on_mismatch'])
                group_batch=grouped.get('batch',{})
                if grouped.get('error'):batch['error']=deepcopy(grouped['error'])
                for offset,child in enumerate(group_batch.get('children',[])):
                    entry=children[index+offset]
                    entry.update(deepcopy(child));entry['index']=index+offset
                    result=entry.get('result')
                    current_unknown=unknown(result)
                    native_ok=(isinstance(result,dict) and result.get('ok') is True
                        and not result.get('error') and not result.get('failure') and not current_unknown
                        and result.get('loaded_sha256')==loaded_sha256
                        and result.get('saved_project',{}).get('sha256')==loaded_sha256
                        and result.get('saved_project',{}).get('stable_during_read') is True)
                    if entry.get('status')!='not_started':
                        verdict=_simulation_expectations(steps[index+offset],result) if native_ok else None
                        native_ok=native_ok and verdict['native_samples_complete'] is True
                        entry['native_execution_completed']=bool(native_ok)
                        entry['status']='unknown' if current_unknown else 'completed' if native_ok else 'failed'
                        if verdict is not None:entry['expectation']=verdict
                        if native_ok and batch['completed_prefix_count']==index+offset:batch['completed_prefix_count']+=1
                handled_until=end
                if group_batch.get('cancelled') is True:
                    stopped=group_batch.get('stop_index')
                    batch.update(cancelled=True,stop_index=index+(stopped if type(stopped) is int else
                        group_batch.get('completed_prefix_count',0)),stop_reason='cancelled_during_step');break
                if (grouped.get('error') or group_batch.get('stop_reason') is not None
                        or any(child['status']!='completed' for child in children[index:end])):
                    stopped=group_batch.get('stop_index')
                    batch.update(stop_index=index+stopped if type(stopped) is int and 0<=stopped<end-index else
                        min(end-1,batch['completed_prefix_count']),stop_reason=group_batch.get('stop_reason') or 'native_failure');break
                continue
            try:
                # No cached admission: every step then enters the same normal
                # simulator route with fresh binding/version/native checks.
                self._check_target()
                if self.current_process_create_time is not None and self.current_process_create_time()!=self.expected_process_create_time:
                    raise MCPToolError('Scenario process lifetime changed')
                saved=await asyncio.to_thread(saved_project_observation,Path(self.target['project_path']))
                if saved.get('stable_during_read') is not True or saved.get('sha256')!=loaded_sha256:
                    batch.update(stop_index=index,stop_reason='saved_version_changed');break
            except asyncio.CancelledError:
                batch.update(cancelled=True,stop_index=index,stop_reason='cancelled_before_step');break
            except MCPToolError:
                batch.update(stop_index=index,stop_reason='binding_changed');break
            except Exception:
                batch.update(stop_index=index,stop_reason='binding_observation_failed');break
            entry=children[index];entry.update(status='dispatched',started_at_ns=time.time_ns())
            request={key:deepcopy(value) for key,value in step.items() if key in {'action','devices','samples','interval_ms','readback_devices','readback_delay_ms'}}
            task=asyncio.create_task(self._execute('simulator',request,
                expected_simulator_sha256=loaded_sha256))
            try:
                result=await asyncio.shield(task)
            except asyncio.CancelledError:
                # Join only the already-dispatched step. The remaining suffix
                # is never run after cancellation, even if this step succeeds.
                batch.update(cancelled=True,stop_index=index,stop_reason='cancelled_during_step')
                while not task.done():
                    try:await asyncio.shield(task)
                    except asyncio.CancelledError:continue
                    except BaseException:break
                try:result=task.result()
                except BaseException:result={'ok':False,'input_state_unknown':True,'automatic_replay':False,
                    'error':{'kind':'scenario_step_outcome_unknown'}}
            except Exception:
                result={'ok':False,'input_state_unknown':True,'automatic_replay':False,
                    'error':{'kind':'scenario_step_failed_without_receipt'}}
            entry.update(finished_at_ns=time.time_ns(),result=result)
            current_unknown=unknown(result)
            native_ok=(isinstance(result,dict) and result.get('ok') is True
                and not result.get('error') and not result.get('failure') and not current_unknown
                and result.get('loaded_sha256')==loaded_sha256
                and result.get('saved_project',{}).get('sha256')==loaded_sha256
                and result.get('saved_project',{}).get('stable_during_read') is True)
            verdict=_simulation_expectations(step,result) if native_ok else None
            native_ok=native_ok and verdict['native_samples_complete'] is True
            entry['native_execution_completed']=bool(native_ok)
            entry['status']='unknown' if current_unknown else 'completed' if native_ok else 'failed'
            if verdict is not None:entry['expectation']=verdict
            if native_ok:batch['completed_prefix_count']+=1
            if batch['cancelled']:break
            if not native_ok:
                batch.update(stop_index=index,stop_reason='native_unknown' if current_unknown else 'native_failure');break
            if verdict['matched'] is False and arguments['stop_on_mismatch']:
                batch.update(stop_index=index,stop_reason='model_expectation_mismatch');break
        batch['not_started_count']=sum(child['status']=='not_started' for child in children)
        batch['all_native_steps_completed']=batch['completed_prefix_count']==len(steps)
        # Shared owner completion classification; assertions remain separate.
        batch['all_completed']=batch['all_native_steps_completed']
        matched=[child['expectation']['matched'] for child in children if 'expectation' in child and child['expectation']['matched'] is not None]
        batch['expectations_matched']=all(matched) if matched else None
        if (batch['all_native_steps_completed'] and not batch['cancelled']
                and batch['stop_reason'] is None and not batch.get('error') and not sequence_used):
            for action in ('stop','end'):
                if action in lifecycle and not await lifecycle_action(action):break
        output={'ok':batch['all_native_steps_completed'] and batch['expectations_matched'] is not False
                and not batch['cancelled'] and batch['stop_reason'] is None and not batch.get('error'),
            'action':'execute_simulation_scenario','batch':batch,'automatic_replay':False,
            'input_state_unknown':(any(unknown(child.get('result')) for child in children)
                or any(entry['status']=='unknown' for entry in lifecycle.values())),
            'runtime_behavior_verified':False,
            'meaning':'Ordered actual simulator bit read/write and D/C/T/F word read actions with model-supplied assertions only. Native completion, assertion outcomes and whole-program behavior are distinct.'}
        if batch.get('error'):
            output['error']=deepcopy(batch['error'])
        if lifecycle:
            output['lifecycle']=lifecycle
        self.record_raw('execute_simulation_scenario',arguments,output)
        return output

    async def _declaration_comments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        def refused(kind):
            return {'ok':False,'error':{'kind':kind},'action_attempted':False,
                    'input_state_unknown':False,'automatic_replay':False}
        if self.expected_process_create_time is None:
            return refused('declaration_binding_unavailable')
        if name == 'inspect_declaration_comments':
            if arguments:return refused('invalid_declaration_arguments')
        else:
            if (set(arguments) != {'table_ref','entries'} or not isinstance(arguments.get('table_ref'),str)
                    or not isinstance(arguments.get('entries'),list) or not arguments['entries']):
                return refused('invalid_declaration_arguments')
            entries=arguments['entries']
            if any(not isinstance(item,dict) or set(item) not in ({'row_ref','comment'},{'symbol','data_type','address','comment'})
                    or not isinstance(item['comment'],str) or len(item['comment'])>512
                    or any(char in item['comment'] for char in '\0\r\n\t')
                    or ('row_ref' in item and (not isinstance(item['row_ref'],str) or not item['row_ref']))
                    or ('row_ref' not in item and any(not isinstance(item[k],str) or not item[k]
                        or item[k]!=item[k].strip() or len(item[k])>80 or any(ord(c)<32 for c in item[k])
                        for k in ('symbol','data_type','address'))) for item in entries):
                return refused('invalid_declaration_arguments')
            updates=[item for item in entries if 'row_ref' in item]
            if len({item['row_ref'] for item in updates})!=len(updates):
                return refused('duplicate_declaration_row')
        self._check_target()
        if self.current_process_create_time is not None and self.current_process_create_time()!=self.expected_process_create_time:
            return refused('declaration_lifetime_changed')
        saved=await asyncio.to_thread(saved_project_observation,Path(self.target['project_path']))
        self._check_target()
        if saved.get('stable_during_read') is not True or not re.fullmatch('[0-9a-f]{64}',saved.get('sha256','')):
            return refused('declaration_saved_binding_unavailable')
        binding={'project_path':self.target['project_path'],'project_sha256':saved['sha256'],
                 'process_create_time':self.expected_process_create_time}
        request={'action':name,'pid':self.target['pid'],'hwnd':self.target['hwnd']}
        if name == 'inspect_declaration_comments':
            request['declaration_binding']=binding
        else:
            proof=self.declaration_tables.get(arguments['table_ref'])
            if (proof is None or proof['generation']!=self.declaration_generation
                    or proof['target']!=self.target or proof['authority']['binding']!=binding):
                return refused('declaration_table_reference_unavailable')
            if any(item['row_ref'] not in proof['rows'] for item in entries if 'row_ref' in item):
                return refused('declaration_row_reference_unavailable')
            # Consume before transport: failed/unknown dispatch never grants replay.
            self.declaration_tables.pop(arguments['table_ref'])
            request['declaration_authority']=deepcopy(proof['authority'])
            expected_rows=deepcopy(proof['cells'])
            for item in entries:
                if 'row_ref' in item:expected_rows[proof['indices'][item['row_ref']]][5]=item['comment']
                else:
                    insertion=len(expected_rows)
                    while insertion and not any(expected_rows[insertion-1][k] for k in (0,1,2,5)):insertion-=1
                    expected_rows.insert(insertion,[item['symbol'],item['data_type'],item['address'],'0','0',item['comment']])
            request['declaration_entries']=[{'row_token':proof['rows'][item['row_ref']],
                                           'comment':item['comment']} if 'row_ref' in item else deepcopy(item) for item in entries]
        try:
            raw=await self.gateway.call_workspace_tool('xg5000.editor',request)
        except MCPToolRejectedError as exc:
            envelope=exc.mcp_error_payload or {}
            self.record_raw(name,request,envelope)
            error=envelope.get('error',{})
            native=validate_native_failure(error['nativeFailure']) if 'nativeFailure' in error else None
            details=native['details'] if native is not None else {}
            facts=_declaration_public(details)
            public_native=_declaration_public(native) if native is not None else None
            if public_native is not None and 'declaration_comments' in facts:
                # Keep table evidence once at its immutable tool-result path;
                # the original envelope remains in the raw receipt above.
                public_native['details'].pop('declaration_comments',None)
            quarantine=_operation_quarantine_projection(error)
            if quarantine is not None:
                return {**facts,'ok':False,'action':name,**quarantine,
                    'error':{'kind':'operation_quarantined','code':error['code'],
                        **({'nativeFailure':public_native} if native is not None else {})}}
            receipt=facts.get('receipt',{})
            unknown=facts.get('input_state_unknown') is True or receipt.get('input_state_unknown',True)
            return {'ok':False,'action':name,'error':{'kind':'declaration_comments_rejected',
                **({'code':native['kind'],'nativeFailure':public_native} if native is not None else {})},
                **facts,'input_state_unknown':unknown,'automatic_replay':False}
        except Exception as exc:
            # No structured native receipt was received; never infer no input
            # or return an exception representation containing private requests.
            self.record_raw(name,request,{'transport_failure_kind':type(exc).__name__})
            return {'ok':False,'action':name,'error':{'kind':'declaration_transport_failure',
                'type':type(exc).__name__},'input_state_unknown':True,'automatic_replay':False}
        self.record_raw(name,request,raw)
        self._check_target()
        after=await asyncio.to_thread(saved_project_observation,Path(self.target['project_path']))
        self._check_target()
        if (after.get('stable_during_read') is not True or after.get('sha256')!=saved['sha256']
                or self.current_process_create_time is not None and self.current_process_create_time()!=self.expected_process_create_time):
            raise MCPToolError('Declaration working-file or process binding changed after native observation')
        body=raw.get('declaration_comments') if isinstance(raw,dict) else None
        if (raw.get('ok') is not True or raw.get('action')!=name or not isinstance(body,dict)
                or body.get('input_state_unknown') is not False or raw.get('input_state_unknown') is True
                or body.get('native_ld_preservation',{}).get('verified') is not True):
            return {**_declaration_public(raw),'ok':False,'action':name,
                'error':{'kind':'declaration_comments_unverified'},
                'input_state_unknown':True,'automatic_replay':False}
        public={'ok':True,'action':name,**_declaration_public(raw),
                'input_state_unknown':False,'automatic_replay':False,
                'source':{'saved_sha256':saved['sha256'],'stable_before_and_after':True,
                    'current_native_csv':True,'unsaved_native_table_observed':True},'project_saved':False}
        if name == 'inspect_declaration_comments':
            authority=body.get('authority');rows=body.get('rows');columns=body.get('columns')
            if (body.get('supported') is not True or not isinstance(authority,dict)
                    or set(authority)!={'schema','pid','main_hwnd','binding','baseline_csv','baseline_sha256','ld_fingerprints','marker','nonce'}
                    or authority.get('schema')!='xg5000.declaration-comment-authority/v1'
                    or authority.get('pid')!=self.target['pid'] or authority.get('main_hwnd')!=self.target['hwnd']
                    or authority.get('binding')!=binding or not isinstance(authority.get('nonce'),str)
                    or re.fullmatch('[0-9a-f]{32}',authority['nonce']) is None
                    or not isinstance(authority.get('baseline_csv'),str) or not Path(authority['baseline_csv']).is_absolute()
                    or not isinstance(authority.get('marker'),dict) or set(authority['marker'])!={'name','value'}
                    or not isinstance(authority['marker']['name'],str)
                    or re.fullmatch(r'ControlStudio\.DeclarationComments\.[0-9a-f]{32}',authority['marker']['name']) is None
                    or type(authority['marker']['value']) is not int or authority['marker']['value']<=0
                    or not isinstance(authority.get('ld_fingerprints'),dict) or not authority['ld_fingerprints']
                    or columns!=['변수','타입','디바이스','사용 유무','HMI','설명문']
                    or not isinstance(rows,list) or body.get('row_count')!=len(rows)
                    or not isinstance(body.get('source_csv'),dict)
                    or body['source_csv'].get('sha256')!=authority.get('baseline_sha256')
                    or body['source_csv'].get('encoding')!='cp949'
                    or body['source_csv'].get('format')!='xg5000-global-variable-table-csv/native-observed-v1'
                    or body['source_csv'].get('row_count')!=len(rows)
                    or re.fullmatch('[0-9a-f]{64}',authority.get('baseline_sha256','')) is None
                    or any(not isinstance(row,dict) or row.get('index')!=i
                        or not isinstance(row.get('cells'),list) or len(row['cells'])!=6
                        or any(not isinstance(cell,str) for cell in row['cells'])
                        or type(row.get('editable_identity_unique')) is not bool
                        or (row['editable_identity_unique'] and (not isinstance(row.get('row_token'),str)
                            or re.fullmatch('[0-9a-f]{64}',row['row_token']) is None))
                        or (not row['editable_identity_unique'] and row.get('row_token') is not None)
                        for i,row in enumerate(rows))):
                raise MCPToolError('Declaration CSV authority differs from this bound native inspection')
            tokens=[row['row_token'] for row in rows if row['editable_identity_unique']]
            if len(tokens)!=len(set(tokens)):
                raise MCPToolError('Declaration CSV row authority is ambiguous')
            table_ref='declaration-table:'+uuid.uuid4().hex;private_rows={};public_rows=[]
            for row in rows:
                ref='declaration-row:'+uuid.uuid4().hex if row['editable_identity_unique'] else None
                if ref is not None:private_rows[ref]=row['row_token']
                public_rows.append({'index':row['index'],'cells':list(row['cells']),
                    'row_ref':ref,'editable_identity_unique':row['editable_identity_unique']})
            self.declaration_tables[table_ref]={'generation':self.declaration_generation,
                'target':deepcopy(self.target),'authority':deepcopy(authority),'rows':private_rows,
                'cells':[list(row['cells']) for row in rows],
                'indices':{row['row_ref']:row['index'] for row in public_rows if row['row_ref'] is not None}}
            public['declaration_comments']={**_declaration_public(body),'table_ref':table_ref,
                'columns':list(columns),'rows':public_rows,
                'coverage':{'row_count':len(rows),'returned_count':len(rows),'complete_native_csv':True,
                    'editable_row_count':len(private_rows),'scope':'global_direct_native_csv'},
                'source_csv':{key:body['source_csv'][key] for key in ('format','encoding','sha256','byte_size','row_count') if key in body['source_csv']}}
        else:
            table=body.get('table_readback')
            observed_rows=table.get('rows') if isinstance(table,dict) else None
            created={item['symbol'] for item in entries if 'row_ref' not in item}
            rows_match=isinstance(observed_rows,list) and len(observed_rows)==len(expected_rows)
            if rows_match:
                intended_created={row[0]:row for row in expected_rows if row[0] in created}
                preserved=[row for row in expected_rows if row[0] not in created]
                observed_preserved=[];seen=set()
                for observed_row in observed_rows:
                    if (not isinstance(observed_row,list) or len(observed_row)!=6
                            or any(not isinstance(cell,str) for cell in observed_row)):
                        rows_match=False;break
                    if observed_row[0] in created:
                        if observed_row[0] in seen or observed_row[0] not in intended_created:
                            rows_match=False;break
                        seen.add(observed_row[0]);expected_row=intended_created[observed_row[0]]
                        if observed_row[3] not in ('0','1') or observed_row[:3]+observed_row[4:]!=expected_row[:3]+expected_row[4:]:
                            rows_match=False;break
                    else:observed_preserved.append(observed_row)
                trailing=[]
                for row in reversed(expected_rows):
                    if any(row[k] for k in (0,1,2,5)):break
                    trailing.insert(0,row)
                rows_match=(rows_match and seen==created and observed_preserved==preserved
                    and (not trailing or observed_rows[-len(trailing):]==trailing))
            if (body.get('comment_verified') is not True or body.get('project_saved') is not False
                    or body.get('working_file_sha256')!=saved['sha256'] or body.get('working_file_unchanged') is not True
                    or not isinstance(table,dict) or table.get('all_non_target_cells_and_row_order_preserved') is not True
                    or table.get('columns')!=['변수','타입','디바이스','사용 유무','HMI','설명문']
                    or table.get('encoding')!='cp949' or not rows_match
                    or not isinstance(table.get('rows'),list) or table.get('row_count')!=len(table['rows'])
                    or any(not isinstance(row,list) or len(row)!=6 or any(not isinstance(cell,str) for cell in row) for row in table['rows'])):
                raise MCPToolError('Declaration batch lacks complete native re-export/preservation proof')
            public['declaration_comments']={**_declaration_public(body),'entries':deepcopy(arguments['entries']),
                'table_readback':{key:deepcopy(table[key]) for key in ('columns','rows','row_count','sha256','encoding','all_non_target_cells_and_row_order_preserved') if key in table}}
        return public

    async def _read_saved_project(self, arguments: dict[str,Any]) -> dict[str,Any]:
        if arguments:
            raise MCPToolError("Saved observation has no model-selected path")
        self._check_target()
        raw=await asyncio.to_thread(saved_project_observation,Path(self.target["project_path"]),
            captured_source=self.captured_source)
        self._check_target()
        output={**raw,"workspace_id":self.target["workspace_id"],"revision_id":self.target["revision_id"],
            "read_only":True,"action_attempted":False,"input_state_unknown":False}
        if raw.get("stable_during_read") is True:
            self.refresh_saved(raw)
        # Reuse the workspace hardware reader's bounded, digest-validated
        # timer parameters. This second read is usable only for the exact
        # saved version and unchanged Host binding observed above.
        output['timer_configuration'] = {'status': 'unavailable', 'areas': [], 'blockers': []}
        if raw.get('stable_during_read') is True and raw.get('sha256'):
            identity = deepcopy(self.current_target())
            try:
                if not isinstance(identity, dict) or identity.get('working_sha256') != raw['sha256']:
                    raise MCPToolError('Saved timer source binding changed')
                context = await self.gateway.execute_agent_tool('workspace.hardware_context', {},
                    session_identity=identity)
                from .execution_broker import MCPExecutionBroker
                verified = MCPExecutionBroker._workspace_hardware_context_evidence_contract(
                    context, expected_session_identity=identity)
                if (self.current_target() != identity or verified.get('workingSha256') != raw['sha256']):
                    raise MCPToolError('Saved timer source changed during observation')
                blockers = [item for item in verified['blockers'] if item.startswith('timer_area_')]
                status = ('ambiguous' if 'timer_area_ranges_overlap' in blockers else
                          'observed' if verified['timerAreas'] else 'unavailable')
                output['timer_configuration'] = {'status': status,
                    'source_sha256': raw['sha256'], 'areas': verified['timerAreas'], 'blockers': blockers}
            except Exception:
                # Timer metadata is optional; never turn an unavailable source
                # into a default timebase or a failed saved-file observation.
                pass
            self._check_target()
        # Reuse the existing index reader and default first500 page. Its own
        # binding/schema/digest/stability validation remains authoritative.
        if raw.get("stable_during_read") is not True or not raw.get("sha256"):
            output["saved_symbols_read"]={"status":"not_attempted","same_saved_sha256":False,
                "error":{"kind":"saved_project_summary_not_stable"}}
        else:
            try:
                symbols=await self._read_saved_symbols({})
            except Exception as error:
                self._check_target()
                output.update(ok=False,saved_symbols_read={"status":"failed","same_saved_sha256":False,
                    "error":{"kind":"saved_symbols_read_failed","exception_kind":type(error).__name__}})
            else:
                source=symbols.get("source",{})
                same=(source.get("sha256")==raw["sha256"] and source.get("size")==raw.get("size")
                    and source.get("stable_during_read") is True)
                if not same:
                    # Keep the actual summary, but never attach rows from a
                    # different saved version or silently re-read/retry it.
                    output.update(ok=False,saved_symbols_read={"status":"version_mismatch","same_saved_sha256":False,
                        "error":{"kind":"saved_project_symbols_version_mismatch"}})
                else:
                    coverage=symbols["symbols"]["coverage"]
                    output.update(ok=True,saved_symbols=symbols,saved_symbols_read={
                        "status":"partial" if symbols["status"]=="partial" or coverage["truncated"] else "complete",
                        "same_saved_sha256":True,"source_sha256":raw["sha256"],
                        "unsaved_ui_observed":False,"declaration_edit_authority":False})
                    if isinstance(symbols.get('simulator_source_comparison'), dict):
                        output['simulator_source_comparison'] = deepcopy(symbols['simulator_source_comparison'])
        self.record_raw("read_saved_project",arguments,output)
        return output

    async def _read_saved_symbols(self, arguments: dict[str, Any]) -> dict[str, Any]:
        # Only the current Host-bound working copy is selected; the model supplies a page.
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 500)
        if (set(arguments) - {"offset", "limit"} or type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= 2000):
            raise MCPToolError("Saved symbols require a bounded offset/limit page only")
        self._check_target()
        started = time.time_ns()
        path = Path(self.target["project_path"])
        before = await asyncio.to_thread(saved_project_observation, path)
        self._check_target()
        if before.get("stable_during_read") is not True or not before.get("sha256"):
            raise MCPToolError("Saved symbols require a stable bound saved file")
        request = {"workspace_id": self.target["workspace_id"], "revision_id": self.target["revision_id"],
                   "offset": offset, "limit": limit}
        raw = await self.gateway.call_workspace_tool("workspace.structure_index", request)
        self.record_raw("read_saved_symbols", request, raw)
        self._check_target()
        after = await asyncio.to_thread(saved_project_observation, path)
        self._check_target()
        index = raw.get("structure_index") if isinstance(raw, dict) else None
        binding = raw.get("workspace_binding") if isinstance(raw, dict) else None
        project = index.get("project_binding") if isinstance(index, dict) else None
        source = project.get("source_receipt") if isinstance(project, dict) else None
        table = index.get("sections", {}).get("symbols") if isinstance(index, dict) else None
        coverage = table.get("coverage") if isinstance(table, dict) else None
        items = table.get("items") if isinstance(table, dict) else None
        def digest(value):
            return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        def same_path(value):
            return isinstance(value, str) and os.path.normcase(str(Path(value).resolve())) == os.path.normcase(str(path.resolve()))
        if (not isinstance(raw, dict) or raw.get("operation") != "workspace.structure_index"
                or raw.get("schema_version") != "1.0"
                or raw.get("schema") != "xg5000.workspace-structure-index/1.0"
                or not isinstance(binding, dict)
                or any(binding.get(key) != self.target[key] for key in ("workspace_id", "revision_id"))
                or not same_path(binding.get("working_path")) or binding.get("working_sha256") != before["sha256"]
                or not isinstance(index, dict) or index.get("schema") != "xg5000.project.structure-index/1.0"
                or index.get("read_only") is not True or index.get("status") not in {"complete", "partial"}
                or not isinstance(index.get("limitations"), list)
                or not isinstance(project, dict) or not same_path(project.get("project_path"))
                or project.get("project_sha256") != before["sha256"]
                or not isinstance(source, dict) or source.get("observed") is not True
                or source.get("project_sha256") != before["sha256"] or source.get("project_size") != before.get("size")
                or after.get("stable_during_read") is not True
                or any(after.get(key) != before.get(key) for key in ("sha256", "size", "mtime_ns"))
                # _call_tool adds schema_version after the workspace producer signs its payload.
                or raw.get("receipt_digest") != digest({k: v for k, v in raw.items() if k not in {"operation", "receipt_digest", "schema_version"}})
                or index.get("index_digest") != digest({k: v for k, v in index.items() if k != "index_digest"})
                or not isinstance(items, list) or not isinstance(coverage, dict)
                or any(type(coverage.get(k)) is not int for k in ("count", "total", "returned", "offset", "limit"))
                or coverage["count"] != coverage["total"] or coverage["total"] < 0
                or coverage["offset"] != offset or coverage["limit"] != limit
                or coverage["returned"] != len(items) or len(items) > limit
                or len(items) != min(limit, max(0, coverage["total"] - offset))
                or coverage.get("truncated") is not (offset > 0 or offset + len(items) < coverage["total"])
                or coverage.get("next_offset") != (offset + len(items) if offset + len(items) < coverage["total"] else None)
                or any(not isinstance(item, dict) or any(not isinstance(item.get(k), str)
                       for k in ("symbol", "data_type", "comment")) or not isinstance(item.get("device"), dict) for item in items)):
            raise MCPToolError("Saved symbol index differs from the stable Host-bound saved file or requested page")
        return {"ok": True, "read_only": True, "action_attempted": False, "input_state_unknown": False,
                "workspace_id": self.target["workspace_id"], "revision_id": self.target["revision_id"],
                "source_kind": "saved_working_project_structure_index", "source": {
                    **{k: before[k] for k in ("sha256", "size", "mtime_ns", "stable_during_read")},
                    "read_started_unix_ns": started, "read_completed_unix_ns": time.time_ns(),
                    "xml_sha256": source.get("xml_sha256"), "index_digest": index["index_digest"],
                    "receipt_digest": raw["receipt_digest"]},
                "symbols": deepcopy(table), "status": index["status"], "limitations": deepcopy(index["limitations"]),
                **({'simulator_source_comparison': deepcopy(raw['simulator_source_comparison'])}
                   if isinstance(raw.get('simulator_source_comparison'), dict)
                   and raw['simulator_source_comparison'].get('current_saved', {}).get('sha256') == before['sha256'] else {}),
                "unsaved_ui_observed": False, "declaration_edit_authority": False,
                "meaning": "Saved bytes only. Coverage counts decoded records, not proof of complete vendor semantics or current unsaved UI declarations. No UI row ref or edit authority is issued."}

    async def _execute(self, name: str, arguments: dict[str, Any], *,
                       expected_simulator_sha256: str | None = None,
                       persist_observation: bool = True) -> dict[str, Any]:
        if (type(persist_observation) is not bool or not persist_observation
                and not (name=='native_editor' and arguments=={'action':'observe'}
                         and self.recovery_only and self.recovery_pending)):
            raise MCPToolError('Only an owned pending-close read may defer its successful observation record')
        if name == 'recover_workspace':
            if arguments or self.recover_workspace is None:
                raise MCPToolError('Workspace recovery is outside this run capability')
            result = await self.recover_workspace(self)
            if result.get('rebound') is True:
                self.recovery_pending=False;self.recovery_only=False
                self.owned_ui.clear();self.variable_comments.clear()
                self.declaration_tables.clear();self.declaration_generation=uuid.uuid4().hex
                self.rung_sources.clear();self.rung_comments.clear();self.rung_generation=uuid.uuid4().hex
                self.check_results.clear();self.operation_recoveries.clear()
                self.program_name=None
            self.record_raw(name, {}, result)
            return scrub_native_tool_result(result)
        pending_ui=self.recovery_only and name=='native_editor' and arguments.get('action') in {'observe','respond_owned_ui'}
        pending_ui_identity=None
        if self.recovery_only and not pending_ui:
            raise MCPToolError('Only preserved recovery is available before a fresh binding')
        if pending_ui:
            if self.pending_recovery_owned_ui is None:
                raise MCPToolError('Original pending close owned-UI scope is unavailable')
            if arguments.get('action')=='respond_owned_ui' and arguments.get('response')!='cancel':
                raise MCPToolError('Original pending close permits observed modal Cancel only')
            pending_ui_identity=await self.pending_recovery_owned_ui(self)
            if arguments.get('action')=='observe':self.owned_ui.clear()
        else:
            self._check_target()
        recorded_name = name
        mcp_tool = "xg5000.editor"
        if name == "initialize_ld_program":
            if (not {"program_name", "element_kind", "operand"} <= set(arguments)
                    or not set(arguments) <= {"program_name", "element_kind", "operand", "symbol"}
                    or arguments.get("element_kind") not in {"NO", "NC"}):
                raise MCPToolError("Initial LD contact arguments are invalid")
            arguments = {**arguments, "action": "write_ld_element", "visual_row": 0, "cell": 0, "expect_empty": True}
            name = "native_editor"
        if name == "simulator":
            if arguments.get('action') != 'observe':
                self._simulator_end_precontrol_source = None
            if not self.allow_simulator or self.expected_process_create_time is None:
                raise MCPToolError("This run has no local simulator authority")
            if (not isinstance(arguments, dict) or "action" not in arguments
                    or not set(arguments) <= {"action", "devices", "samples", "interval_ms", "readback_devices", "readback_delay_ms"}):
                raise MCPToolError("Simulator arguments cannot select a target")
            if 'readback_delay_ms' in arguments and (arguments['action']!='write_devices'
                    or 'readback_devices' not in arguments or type(arguments['readback_delay_ms']) is not int
                    or not 0<=arguments['readback_delay_ms']<=1000):
                raise MCPToolPreflightError('SIMULATOR_READBACK_DELAY_INVALID',
                    'readback_delay_ms requires action=write_devices, readback_devices, and an integer from 0 to 1000.',
                    details={'invalid_fields': ['readback_delay_ms'],
                        'missing_fields': ['readback_devices'] if 'readback_devices' not in arguments else [],
                        'required_action': 'write_devices', 'minimum': 0, 'maximum': 1000})
            if 'readback_devices' in arguments:
                if (arguments['action']!='write_devices' or not isinstance(arguments['readback_devices'],list)
                        or not 1<=len(arguments['readback_devices'])<=16):
                    raise MCPToolError('readback_devices requires write_devices and 1 to 16 read-only M/P references')
                try:
                    keys=[simulator_bit_key(item,value=False) for item in arguments['readback_devices']]
                    if len(set(keys))!=len(keys):raise ValueError('Duplicate readback reference')
                except ValueError as error:
                    raise MCPToolError('readback_devices requires distinct exact read-only M/P references') from error
            if arguments['action'] in {'read_devices', 'write_devices'}:
                try:
                    devices = arguments.get('devices')
                    if not isinstance(devices, list) or not 1 <= len(devices) <= 16:
                        raise ValueError('Invalid simulator devices')
                    keys = [simulator_bit_key(item, value=arguments['action'] == 'write_devices',
                        allow_timer=arguments['action'] == 'read_devices' and expected_simulator_sha256 is None)
                        for item in devices]
                    if len(set(keys)) != len(keys):
                        raise ValueError('Duplicate simulator devices')
                except ValueError as error:
                    raise MCPToolError('Invalid standalone simulator bit references') from error
            saved = saved_project_observation(Path(self.target["project_path"]),
                **({'include_source_snapshot': True} if arguments['action'] == 'load' else {}))
            load_before = saved.pop('_source_snapshot', None)
            load_origin = None
            if arguments['action'] == 'load' and isinstance(load_before, dict):
                load_origin = _native_creation_parameter_origin(self.target['project_path'],load_before,
                    self.captured_source,self.target)
            if saved.get("stable_during_read") is not True or not saved.get("sha256"):
                return {"ok": False, "error": {"kind": "simulator_saved_version_unavailable"}, "action_attempted": False}
            if expected_simulator_sha256 is not None and (arguments["action"] not in {"start","load","read_devices","write_devices","read_words","run","stop","end"}
                    or saved["sha256"] != expected_simulator_sha256):
                return {"ok":False,"error":{"kind":"simulator_scenario_saved_version_changed"},
                    "action_attempted":False,"input_state_unknown":False,"automatic_replay":False}
            request = {**arguments, **{key: self.target[key] for key in
                ("workspace_id", "revision_id", "pid", "hwnd", "project_path")},
                "project_hash": expected_simulator_sha256 if expected_simulator_sha256 is not None else saved["sha256"],
                "expected_process_create_time": self.expected_process_create_time}
            if arguments['action']=='start':
                await self._observe_vendor_process_start(saved['sha256'])
                self._check_target()
            try:
                raw = await self.gateway.call_workspace_tool("workspace.simulator", request)
            except MCPToolRejectedError as exc:
                self.record_raw(name, request, exc.mcp_error_payload)
                error = (exc.mcp_error_payload or {}).get("error", {})
                quarantine = _operation_quarantine_projection(error)
                if "nativeFailure" in error:
                    # A rejected command has no successful simulator receipt.
                    # Preserve its evidence without inferring no input occurred.
                    raw = {"ok": False, "action": arguments["action"],
                           "input_state_unknown": True, "automatic_replay": False,
                           "error": validate_native_failure(error["nativeFailure"])}
                elif quarantine is None:
                    raise
                else:
                    raw = {"ok": False, "action": arguments["action"], **quarantine,
                           "error": {"kind": "operation_quarantined", "code": error["code"]}}
                    self.operation_recoveries.clear()
                    if raw["input_state_unknown"]:
                        self.owned_ui.clear()
                        self.variable_comments.clear()
            else:
                self.record_raw(name, request, raw)
            try:
                self._check_target()
                if (arguments['action'] in {'read_devices','write_devices'} and isinstance(raw,dict)
                        and raw.get('ok') is True):
                    try:
                        resolved_simulator_devices(arguments.get('devices'), raw.get('address_resolution'),
                            saved['sha256'], write=arguments['action']=='write_devices',
                            allow_timer=arguments['action']=='read_devices' and expected_simulator_sha256 is None)
                    except ValueError as error:
                        raise MCPToolError('Simulator native address resolution differs from its exact request') from error
                if (arguments['action']=='write_devices' and 'readback_devices' in arguments
                        and isinstance(raw,dict) and raw.get('ok') is True):
                    try:
                        _write_readback_observation(arguments,raw,saved['sha256'])
                    except ValueError:
                        # Keep completed write facts; an invalid read response is never replayable.
                        raw={**raw,'ok':False,'input_state_unknown':True,'automatic_replay':False,
                             'error':{'kind':'simulator_readback_observation_invalid'}}
                reconciled_load = (arguments["action"] == "observe" and isinstance(raw, dict)
                    and raw.get("action") == "observe" and raw.get("load_reconciled_from_completed_write") is True
                    and raw.get("read_only") is True and raw.get("action_attempted") is False)
                if ((arguments["action"] == "load" or reconciled_load) and isinstance(raw, dict)
                        and raw.get("ok") is True and raw.get("input_state_unknown") is not True
                        and not raw.get("error") and not raw.get("failure") and raw.get("load_state") == "loaded"
                        and raw.get("action") == arguments["action"] and isinstance(raw.get("gui"), dict)
                        and raw["gui"].get("ok") is True and raw["gui"].get("verified") is True):
                    reported = raw.get("saved_project")
                    loaded = raw.get("loaded_sha256")
                    native_saved_version = isinstance(raw.get('native_saved_version_load'), dict)
                    if (not isinstance(reported, dict) or reported.get("stable_during_read") is not True
                            or reported.get("sha256") != loaded or raw.get("requested_sha256") != saved["sha256"]):
                        raise MCPToolError("Simulator load saved receipt differs from its request")
                    if raw.get("load_reconciled_from_completed_write") is True:
                        original = raw.get("completed_write_requested_sha256")
                        transition = raw.get("saved_version_transition")
                        before = transition.get("before") if isinstance(transition, dict) else None
                        after = transition.get("after") if isinstance(transition, dict) else None
                        if (not isinstance(original, str) or not re.fullmatch(r"[0-9a-f]{64}", original)
                                or not isinstance(transition, dict) or transition.get("verified") is not True
                                or (transition.get("metadata_only") is not True
                                    and (not isinstance(before, dict) or not isinstance(after, dict)
                                        or before.get("sha256") != after.get("sha256") or before.get("size") != after.get("size")))
                                or not isinstance(before, dict) or not isinstance(after, dict)
                                or before.get("sha256") != original or after.get("sha256") != loaded
                                or not isinstance(before.get("source_sha256"), str)
                                or not re.fullmatch(r"[0-9a-f]{64}", before["source_sha256"])
                                or before["source_sha256"] != after.get("source_sha256")
                                or before["source_sha256"] != raw.get("loaded_source_sha256")):
                            raise MCPToolError("Simulator completed Write transition was not verified")
                    if loaded != saved["sha256"] and not native_saved_version:
                        transition = raw.get("saved_version_transition")
                        before = transition.get("before") if isinstance(transition, dict) else None
                        after = transition.get("after") if isinstance(transition, dict) else None
                        if (not isinstance(transition, dict) or transition.get("verified") is not True
                                or transition.get("metadata_only") is not True
                                or not isinstance(before, dict) or not isinstance(after, dict)
                                or before.get("sha256") != saved["sha256"] or after.get("sha256") != loaded
                                or not before.get("source_sha256")
                                or before["source_sha256"] != after.get("source_sha256")):
                            raise MCPToolError("Simulator load saved transition was not verified")
                    observed = saved_project_observation(Path(self.target["project_path"]),
                        **({'include_source_snapshot': True} if native_saved_version else {}))
                    load_after = observed.pop('_source_snapshot', None)
                    self._check_target()
                    if (observed.get("stable_during_read") is not True
                            or observed.get("sha256") != loaded or observed.get("size") != reported.get("size")):
                        raise MCPToolError("Simulator load saved version was not stably observed")
                    if native_saved_version and not native_default_load_chain_verified(raw, load_origin,
                            load_before, load_after, {**{key: self.target[key] for key in ('pid', 'hwnd', 'project_path')},
                                'expected_process_create_time': self.expected_process_create_time}):
                        raise MCPToolError('Simulator native saved-version load chain was not verified')
                    # Reuse the normal saved-file lineage, workspace and broker update.
                    self.refresh_saved(observed)
                source_error = raw.get("error") if isinstance(raw, dict) else None
                simulator_failure = (validate_native_failure(source_error)
                                     if isinstance(source_error, dict) and "schema" in source_error else None)
                self._retain_simulator_end_precontrol_source(arguments, raw, saved)
                continuation = self._current_operation_continuation(arguments, raw, saved)
                public = scrub_native_tool_result(raw)
                public.pop('current_operation_continuation', None)
                if continuation is not None:
                    public['current_operation_continuation'] = continuation
                if isinstance(public.get("error"), dict):
                    error = public["error"]
                    public["error"] = {"kind": error.get("kind", "simulator_failed"),
                                       **({"code": error["code"]} if error.get("code") ==
                                          "XG5000_OPERATION_QUARANTINED" else {}),
                                       **({"nativeFailure": simulator_failure}
                                          if simulator_failure is not None else {})}
                return public
            except Exception as error:
                return {'ok':False,'action':arguments['action'],'automatic_replay':False,
                    'input_state_unknown':raw.get('input_state_unknown',True) if isinstance(raw,dict) else True,
                    'error':{'kind':'simulator_result_processing_failed','exception_kind':type(error).__name__},
                    'native_receipt':scrub_native_tool_result(raw)}
        if name == 'execute_simulation_scenario':
            return await self._execute_simulation_scenario(arguments)
        if name in {"inspect_declaration_comments", "set_declaration_comments"}:
            return await self._declaration_comments(name, arguments)
        if name == "read_saved_symbols":
            return await self._read_saved_symbols(arguments)
        if name == "read_saved_project":
            return await self._read_saved_project(arguments)
        if name in {"inspect_variable_table", "open_variable_table"}:
            if arguments or self.expected_process_create_time is None:
                raise MCPToolError("Variable table UI observation requires this run's exact lifetime and no arguments")
            saved = saved_project_observation(Path(self.target["project_path"]))
            current = self.current_target()
            if (saved.get("stable_during_read") is not True or not isinstance(current, dict)
                    or saved.get("sha256") != current.get("project_hash")
                    or self.current_process_create_time is None
                    or self.current_process_create_time() != self.expected_process_create_time):
                raise MCPToolError("Variable table UI observation requires a stable bound saved file")
            mcp_tool = ("project.inspect_symbols_ui" if name == "inspect_variable_table"
                        else "project.open_symbols_ui")
            request = {"action": name, "session_id": self.target["session_id"],
                       "workspace_id": self.target["workspace_id"], "revision_id": self.target["revision_id"],
                       "pid": self.target["pid"], "hwnd": self.target["hwnd"],
                       "project_path": self.target["project_path"], "project_hash": saved["sha256"],
                       "expected_process_create_time": self.expected_process_create_time}
        elif name == "read_variable_comments":
            if arguments or self.expected_process_create_time is None:
                raise MCPToolError("Variable table export requires this run's exact binding and no arguments")
            saved = saved_project_observation(Path(self.target["project_path"]))
            current = self.current_target()
            if (saved.get("stable_during_read") is not True or not isinstance(current, dict)
                    or saved.get("sha256") != current.get("project_hash")
                    or self.current_process_create_time is None
                    or self.current_process_create_time() != self.expected_process_create_time):
                raise MCPToolError("Variable table export requires a stable bound saved file")
            mcp_tool = "project.export_symbols"
            request = {"action": "read_variable_comments", "session_id": self.target["session_id"],
                       "workspace_id": self.target["workspace_id"], "revision_id": self.target["revision_id"],
                       "project_path": self.target["project_path"], "project_hash": saved["sha256"],
                       "expected_process_create_time": self.expected_process_create_time}
        elif name == "save_project":
            if arguments:
                raise MCPToolError("Save has no model-selected key or window")
            request = native_ld_save_request()
        elif name == "native_editor":
            action = arguments.get("action")
            if not isinstance(action, str) or action not in _ACTION_FIELDS:
                raise MCPToolError("Native action is outside this run capability")
            required = {"action"} | _ACTION_FIELDS[action]
            optional = native_ld_optional_fields(action) | (
                {"search_kind"} if action == "find_ld_device" else
                {"search_kind", "search_scope"} if action == "find_ld_devices" else set())
            if not required <= set(arguments) or not set(arguments) <= required | optional:
                return {"error": {"kind": "invalid_arguments", "action_attempted": False,
                                  "input_state_unknown": False,
                                  "missing_fields": sorted(required - set(arguments)),
                                  "unexpected_fields": sorted(set(arguments) - required - optional)},
                        "input_state_unknown": False}
            if action in {"inspect_rung_comment", "set_rung_comment"}:
                return await self._rung_comment(action, {key:value for key,value in arguments.items() if key != "action"})
            if action == "activate_check_result":
                ref, row_index = arguments["result_ref"], arguments["row_index"]
                if (not isinstance(ref, str) or not ref or type(row_index) is not int or row_index < 0):
                    return {"ok": False, "error": {"kind": "invalid_arguments"},
                            "action_attempted": False, "input_state_unknown": False}
                proof = self.check_results.pop(ref, None)
                if proof is None or row_index >= proof["table"]["row_count"]:
                    return {"ok": False, "error": {"kind": "check_result_reference_unavailable",
                            "requires_fresh_read": True}, "action_attempted": False, "input_state_unknown": False}
            if action == "write_ld_element" and "expect_empty" in arguments and type(arguments["expect_empty"]) is not bool:
                return {"error": {"kind": "invalid_arguments", "action_attempted": False}}
            if action == "edit_ld_instruction" and "expected_element" in arguments:
                expected = arguments['expected_element']
                if (not isinstance(expected, dict) or set(expected) != {'opcode', 'coordinate', 'value'}
                        or expected.get('opcode') != '33' or not isinstance(expected.get('value'), str)
                        or type(expected.get('coordinate')) is not int
                        or type(arguments.get('visual_row')) is not int or type(arguments.get('column')) is not int
                        or expected['coordinate'] != arguments['visual_row'] * 1024 + arguments['column']):
                    return {'error': {'kind': 'invalid_arguments', 'action_attempted': False},
                            'input_state_unknown': False}
            if action == "settle_abandoned_operation":
                ref = arguments["recovery_ref"]
                if not isinstance(ref, str):
                    return {"ok": False, "error": {"kind": "invalid_arguments"}, "action_attempted": False}
                proof = self.operation_recoveries.pop(ref, None)
                if proof is None or time.monotonic() >= proof["expires_at"]:
                    return {"ok": False, "error": {"kind": "operation_recovery_reference_expired",
                            "requires_fresh_inspection": True}, "action_attempted": False}
            if action == "inspect_variable_comment":
                expected = arguments["expected_element"]
                if (type(arguments["visual_row"]) is not int or arguments["visual_row"] < 0
                        or type(arguments["column"]) is not int or arguments["column"] < 0
                        or not isinstance(expected, dict) or set(expected) != {"opcode", "coordinate", "value"}
                        or not isinstance(expected["opcode"], str) or not expected["opcode"]
                        or type(expected["coordinate"]) is not int or expected["coordinate"] < 0
                        or not isinstance(expected["value"], str) or not expected["value"]):
                    return {"ok": False, "error": {"kind": "invalid_arguments"}, "action_attempted": False}
            if action == "set_variable_comment":
                ref, comment = arguments["variable_ref"], arguments["comment"]
                if (not isinstance(ref, str) or not isinstance(comment, str) or len(comment) > 512
                        or any(char in comment for char in "\0\r\n\t")):
                    return {"ok": False, "error": {"kind": "invalid_arguments"}, "action_attempted": False}
                try:
                    comment.encode("utf-16-le")
                except UnicodeEncodeError:
                    return {"ok": False, "error": {"kind": "invalid_arguments"}, "action_attempted": False}
                if ref not in self.variable_comments:
                    return {"ok": False, "error": {"kind": "variable_comment_reference_expired",
                            "requires_fresh_inspection": True}, "action_attempted": False}
            if action == "delete_ld_rows" and not _valid_row_deletion_arguments(arguments):
                return {"error": {"kind": "invalid_arguments", "action_attempted": False}}
            if action == "move_instruction" and not _valid_instruction_move_arguments(arguments):
                return {"error": {"kind": "invalid_arguments", "action_attempted": False},
                        "input_state_unknown": False}
            if action == "connect_row_terminal" and not _valid_row_terminal_arguments(arguments):
                return {"error": {"kind": "invalid_arguments", "action_attempted": False,
                                  "input_state_unknown": False}, "input_state_unknown": False}
            if action == "find_ld_devices":
                queries = arguments["queries"]
                if (not isinstance(queries, list) or not queries
                        or any(not isinstance(query, str) or not 1 <= len(query) <= 128 for query in queries)
                        or arguments.get("search_kind", "device") not in ("device", "string")
                        or arguments.get("search_scope", "current_window") not in ("current_window", "project")):
                    return {"error": {"kind": "invalid_arguments", "action_attempted": False}}
            if action in {"insert_ld_row_before", "insert_ld_row_after", "insert_ld_instruction", "delete_ld_element", "insert_series_contact_before"}:
                invalid = type(arguments["visual_row"]) is not int or arguments["visual_row"] < 0
                if action in {"insert_ld_row_before", "insert_ld_row_after", "delete_ld_element", "insert_series_contact_before"}:
                    invalid |= type(arguments["column"]) is not int or arguments["column"] < 0
                    if "expected_element" in arguments:
                        expected = arguments["expected_element"]
                        invalid |= (not isinstance(expected, dict) or set(expected) != {"opcode", "coordinate", "value"}
                                    or not isinstance(expected["opcode"], str) or not expected["opcode"]
                                    or not isinstance(expected["value"], str)
                                    or type(expected["coordinate"]) is not int or expected["coordinate"] < 0)
                    if action == "insert_series_contact_before":
                        invalid |= (arguments["contact_kind"] not in ("NO", "NC")
                                    or not isinstance(arguments["operand"], str) or not arguments["operand"])
                else:
                    cell, text = arguments["cell"], arguments["text"]
                    invalid |= (not (type(cell) is int and 0 <= cell <= 31 or cell == "end")
                                or not isinstance(text, str) or not 1 <= len(text) <= 512
                                or any(character in text for character in "\0\r\n\t"))
                    if isinstance(text, str):
                        try:
                            text.encode("utf-16-le")
                        except UnicodeEncodeError:
                            invalid = True
                if invalid:
                    return {"error": {"kind": "invalid_arguments", "action_attempted": False}}
            request = deepcopy(arguments)
            if action in {"inspect_abandoned_operation", "settle_abandoned_operation"}:
                mcp_tool = "xg5000." + action
                request = {"action": action, "workspace_id": self.target["workspace_id"],
                           "revision_id": self.target["revision_id"]}
                if action == "inspect_abandoned_operation":
                    self.operation_recoveries.clear()
                else:
                    request["recovery_token"] = proof["recovery_token"]
            if action == "activate_check_result":
                # The one-use Host source is consumed before dispatch, including lost transport.
                request = {"action": action, "program_name": proof["program_name"],
                           "row_index": arguments["row_index"], "expected_check_table": deepcopy(proof["table"])}
            if action == "set_variable_comment":
                # Consume before crossing the execution boundary.  A transport
                # failure must never grant a second input with this reference.
                proof = self.variable_comments.pop(request.pop("variable_ref"))
                request["authority"] = proof
            if action == "respond_owned_ui":
                if not isinstance(request["ui_ref"], str):
                    raise MCPToolError("UI reference must be text")
                ui = self.owned_ui.get(request["ui_ref"])
                if ui is None or request["response"] not in ui["allowed_responses"]:
                    raise MCPToolError("UI response lacks an actual owned capability")
                if pending_ui:
                    if ui.get('ui_kind')!='modal' or ui.get('pending_recovery_identity')!=pending_ui_identity:
                        raise MCPToolError('Owned Cancel belongs to another pending close')
                    # Consume before dispatch, including a transport failure or
                    # cancellation. Only a fresh observation can issue a new ref.
                    self.owned_ui.pop(request['ui_ref'])
                request = ({"action": "close_owned_window"} if request["response"] == "close"
                           else {"action": "respond_owned_modal", "response": request["response"],
                                 "expected_modal": deepcopy(ui["expected_modal"])})
            if "program_name" in request:
                program = request["program_name"]
                if not isinstance(program, str) or not program.strip() or len(program) > 80:
                    raise MCPToolError("Native program name is invalid")
                if action != "open_program" and self.program_name is not None and program != self.program_name:
                    raise MCPToolError("Native primitive differs from the observed active program")
        else:
            raise MCPToolError("Unknown native run callback")
        request.update(pid=self.target["pid"], hwnd=(ui["hwnd"] if name == "native_editor"
                       and arguments.get("action") == "respond_owned_ui" else self.target["hwnd"]))
        if name == "native_editor" and request.get("action") == "open_program" and self.program_open_binding is not None:
            request["program_open_binding"] = deepcopy(self.program_open_binding)
        if name == "native_editor" and request.get("action") in {"inspect_plc_properties", "configure_plc_cpu"}:
            saved = saved_project_observation(Path(self.target["project_path"]))
            current = self.current_target()
            if (self.expected_process_create_time is None or self.current_process_create_time is None
                    or self.current_process_create_time() != self.expected_process_create_time
                    or not isinstance(current, dict) or saved.get("stable_during_read") is not True
                    or saved.get("sha256") != current.get("project_hash")):
                raise MCPToolError("PLC properties requires the stable bound project and process lifetime")
            request.update(project_path=self.target["project_path"], project_hash=saved["sha256"],
                           expected_process_create_time=self.expected_process_create_time)
        if recorded_name == "initialize_ld_program":
            request["require_created_empty"] = True
        declared_writer = (request.get("action") == "apply_ld_edits"
            and isinstance(request.get("entries"), list) and len(request["entries"]) == 1
            and isinstance(request["entries"][0], dict)
            and request["entries"][0].get("action") == "write_ld_program")
        if request.get("action") in {"read_ld", "read_ld_rows", "read_selection", "write_ld_element"} or declared_writer:
            saved = saved_project_observation(Path(self.target["project_path"]))
            if saved.get("stable_during_read") is True and saved.get("sha256"):
                request["created_empty_authority"] = {"project_path": self.target["project_path"], "project_hash": saved["sha256"]}
                if self.expected_process_create_time is not None:
                    current = self.current_target()
                    created = self.current_process_create_time() if self.current_process_create_time is not None else None
                    if (not isinstance(current, dict)
                            or any(current.get(key) != self.target.get(key) for key in
                                   ("workspace_id", "revision_id", "session_id", "pid", "hwnd", "project_path"))
                            or created != self.expected_process_create_time):
                        blocked = {"ok": False, "action": request["action"],
                            "action_attempted": False, "input_state_unknown": False, "automatic_replay": False,
                            "error": {"kind": "native_created_empty_binding_changed",
                                "action_attempted": False, "input_state_unknown": False, "automatic_replay": False,
                                "message": "Initial empty observation current binding changed before native dispatch."}}
                        self.record_raw(recorded_name, request, blocked)
                        return blocked
                    if current.get("project_hash") != saved["sha256"] and request.get("require_created_empty") is True:
                        return {"ok": False, "action_attempted": False, "input_state_unknown": False,
                            "automatic_replay": False, "error": {
                                "kind": "native_saved_binding_stale", "action_attempted": False,
                                "input_state_unknown": False, "automatic_replay": False,
                                "bound_saved_sha256": current.get("project_hash"),
                                "observed_saved_sha256": saved["sha256"],
                                "next_action": "read_saved_project",
                                "message": "The stable saved file differs from the Host-bound saved version. "
                                    "read_saved_project can independently observe and refresh the saved binding; "
                                    "then obtain a fresh LD observation before choosing further work. "
                                    "This rejection dispatched no native action and does not verify a SIM load "
                                    "or resolve any earlier unknown input."}}
                    if current.get("project_hash") != saved["sha256"]:
                        # A stale saved baseline cannot prove the clipboard-free initial
                        # slot. Ordinary actions retain their native capture preconditions.
                        request.pop("created_empty_authority", None)
                    else:
                        request["created_empty_authority"]["current_binding"] = {
                            **{key: current[key] for key in ("workspace_id", "revision_id", "session_id", "pid", "hwnd")},
                            "process_create_time": created}
        native = None
        try:
            raw = await self.gateway.call_workspace_tool(mcp_tool,
                {key: value for key, value in request.items() if key != "action"}
                if mcp_tool != "xg5000.editor" else request)
        except MCPToolRejectedError as exc:
            self.record_raw(recorded_name, request, exc.mcp_error_payload)
            envelope = exc.mcp_error_payload or {}
            error = envelope.get("error", {})
            message = error.get("message")
            native = (validate_native_failure(error["nativeFailure"])
                      if "nativeFailure" in error else None)
            details = native["details"] if native is not None else None
            if native is not None:
                # A CLI/MCP field with this name never provides Host authority.
                details=_without_host_binding_admission(details)
                native['details']=details
            # Compatibility for previously released MCPs only.  New failures
            # have one structured contract, never diagnostic JSON in prose.
            if native is None and isinstance(message, str) and message.startswith("standalone XG5000 CLI 'editor' failed: workspace_cli_failure(") and message.endswith(")"):
                offset = message.find(",details=")
                while offset >= 0:
                    # Try whole suffixes, not a substring inside user LD text.
                    body = message[offset + len(",details="):]
                    offset = message.find(",details=", offset + 1)
                    try:
                        details = json.loads(body[:-1])
                    except ValueError:
                        continue
                    if isinstance(details, dict):
                        break
            # Diagnostics are data, not fresh observation/capability authority.
            # Legacy projections retain their previous compatibility behavior.
            raw = {**(details if native is None and isinstance(details, dict) else {}),
                   "ok": False, "action": request["action"],
                   "error": {"kind": "native_editor_rejected", "type": error.get("type"),
                             **({"code": error["code"]} if "code" in error else {}),
                             **({"nativeFailure": native} if native is not None else {})}}
            if name in {"read_variable_comments", "open_variable_table"}:
                # Export/open use native UI input.  Absent explicit diagnostics is
                # uncertainty, never evidence of a safe automatic replay.
                receipt = details.get("receipt") if isinstance(details, dict) else None
                flags = ([details.get("input_state_unknown")] if isinstance(details, dict) else [])
                if isinstance(receipt, dict):
                    flags.append(receipt.get("input_state_unknown"))
                known_flags = [value for value in flags if type(value) is bool]
                unknown = True if True in known_flags else False if known_flags else True
                raw.update(input_state_unknown=unknown, automatic_replay=False)
                if isinstance(receipt, dict) and type(receipt.get("action_attempted")) is bool:
                    raw["action_attempted"] = receipt["action_attempted"]
            quarantine = _operation_quarantine_projection(error)
            if quarantine is not None:
                raw.update(quarantine)
                self.operation_recoveries.clear()
                if raw["input_state_unknown"]:
                    self.owned_ui.clear()
                    self.variable_comments.clear()
        else:
            if (name == 'save_project' and isinstance(raw, dict) and raw.get('ok') is True
                    and not raw.get('error') and not raw.get('failure')
                    and raw.get('input_state_unknown') is not True):
                try:
                    saved_observation = await self._read_saved_project({})
                except Exception as error:
                    self._check_target()
                    saved_observation = {'ok': False, 'read_only': True, 'action_attempted': False,
                        'input_state_unknown': False, 'error': {'kind': 'saved_project_read_failed',
                            'exception_kind': type(error).__name__}}
                raw = {**raw, 'saved_project': saved_observation}
            if (persist_observation or not isinstance(raw,dict) or raw.get('ok') is not True
                    or raw.get('error') or raw.get('failure') or raw.get('input_state_unknown') is True):
                self.record_raw(recorded_name, request, raw)
            raw=_without_host_binding_admission(raw)
            entries = arguments.get('entries') if name == 'native_editor' and arguments.get('action') == 'apply_ld_edits' else None
            batch = raw.get('batch') if isinstance(raw, dict) else None
            completed = batch.get('entries') if isinstance(batch, dict) else None
            if (isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], dict)
                    and entries[0].get('action') == 'write_ld_program'
                    and entries[0].get('check_and_save') is True
                    and isinstance(batch, dict) and batch.get('all_completed') is True
                    and isinstance(completed, list) and len(completed) == 1 and isinstance(completed[0], dict)
                    and completed[0].get('status') == 'completed'):
                child = completed[0].get('result')
                check = child.get('check') if isinstance(child, dict) else None
                save = child.get('save_input') if isinstance(child, dict) else None
                if (isinstance(child, dict) and child.get('ok') is True
                        and child.get('declaration_matches') is True
                        and isinstance(check, dict) and check.get('status') == 'passed' and check.get('has_errors') is False
                        and isinstance(save, dict) and save.get('completed') is True
                        and save.get('input_attempted') is True and save.get('input_state_unknown') is False):
                    try:
                        saved_observation = await self._read_saved_project({})
                    except Exception as error:
                        self._check_target()
                        saved_observation = {'ok': False, 'read_only': True, 'action_attempted': False,
                            'input_state_unknown': False, 'error': {'kind': 'saved_project_read_failed',
                                'exception_kind': type(error).__name__}}
                    raw = {**raw, 'saved_project': saved_observation}
        if pending_ui:
            if arguments.get('action')=='observe' and await self.pending_recovery_owned_ui(self)!=pending_ui_identity:
                self.owned_ui.clear()
                raise MCPToolError('Original pending close changed during owned-UI observation')
        else:
            self._check_target()
        if not isinstance(raw, dict):
            raise MCPToolError("Native backend returned a non-object")
        raw = {key: value for key, value in raw.items() if key not in {
            'current_workspace_recovery', 'current_workspace_continuation',
            'native_binding_generation', 'rebound', 'pending_owned_ui'}}
        if name in {"inspect_variable_table", "open_variable_table", "read_variable_comments"} and raw.get("ok") is True:
            current = self.current_target()
            if (not isinstance(current, dict) or current.get("project_hash") != saved["sha256"]
                    or self.current_process_create_time is None
                    or self.current_process_create_time() != self.expected_process_create_time):
                raise MCPToolError("Variable table current binding changed during observation")
        if name in {"inspect_variable_table", "open_variable_table"} and raw.get("ok") is True:
            bindings = [raw.get("target_binding_before"), raw.get("target_binding_after")]
            opening = name == "open_variable_table"
            navigation = raw.get("navigation")
            if (type(raw.get("verified")) is not bool or raw.get("read_only") is not True
                    or raw.get("operation") != mcp_tool
                    or raw.get("pid") != self.target["pid"] or raw.get("hwnd") != self.target["hwnd"]
                    or raw.get("session_id") != self.target["session_id"]
                    or raw.get("project_path") != self.target["project_path"]
                    or raw.get("project_unchanged") is not True
                    or any(raw.get(key) != saved["sha256"] for key in (
                        "project_sha256_before", "project_sha256_after", "project_hash_before", "project_hash_after"))
                    or any(not isinstance(item, dict) or item.get("pid") != self.target["pid"]
                           or item.get("hwnd") != self.target["hwnd"]
                           or item.get("process_create_time") != self.expected_process_create_time for item in bindings)
                    or not isinstance(raw.get("observation"), dict)
                    or (opening and (not isinstance(navigation, dict)
                                     or navigation.get("verified") is not raw["verified"]))
                    or (not opening and raw["verified"] is not True)
                    or raw.get("error") or raw.get("failure") or raw.get("input_state_unknown") is True):
                raise MCPToolError("Variable table UI observation differs from this bound inspection")
            raw = {**raw, "ui_observation_scope": "bounded_native_variable_table_ui_metadata",
                   "declared_rows_complete": False, "declaration_edit_authority": False,
                   "automatic_replay": False}
        if name == "read_variable_comments" and raw.get("ok") is True:
            exported = raw.get("export"); table = exported.get("csv") if isinstance(exported, dict) else None
            rows = table.get("rows") if isinstance(table, dict) else None
            bindings = [raw.get("target_binding_before"), raw.get("target_binding_after")]
            if (raw.get("verified") is not True or raw.get("read_only") is not True
                    or raw.get("operation") != "project.export_symbols"
                    or raw.get("session_id") != self.target["session_id"]
                    or raw.get("project_path") != self.target["project_path"]
                    or raw.get("project_unchanged") is not True
                    or any(raw.get(key) != saved["sha256"] for key in (
                        "project_sha256_before", "project_sha256_after", "project_hash_before", "project_hash_after"))
                    or any(not isinstance(item, dict) or item.get("pid") != self.target["pid"]
                        or item.get("hwnd") != self.target["hwnd"]
                        or item.get("process_create_time") != self.expected_process_create_time for item in bindings)
                    or not isinstance(exported, dict) or exported.get("verified") is not True
                    or exported.get("scope") != "global_direct_variables"
                    or not isinstance(table, dict) or table.get("format") != "xg5000-global-variable-table-csv/native-observed-v1"
                    or table.get("format_observed_native") is not True
                    or table.get("columns") != ["변수", "타입", "디바이스", "사용 유무", "HMI", "설명문"]
                    or not isinstance(table.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", table["sha256"]) is None
                    or type(table.get("byte_size")) is not int or table["byte_size"] <= 0
                    or not isinstance(rows, list) or not rows or len(rows) > 100000
                    or type(table.get("row_count")) is not int or table["row_count"] != len(rows)
                    or any(not isinstance(row, list) or len(row) != 6
                           or any(not isinstance(cell, str) for cell in row) for row in rows)
                    or raw.get("error") or raw.get("failure") or raw.get("input_state_unknown") is True):
                raise MCPToolError("Variable table export did not retain complete bound CSV proof")
        if name == "native_editor" and arguments.get("action") in {
                "inspect_abandoned_operation", "settle_abandoned_operation"}:
            recovery = raw.get("operation_recovery")
            authority = raw.get("authority")
            if (arguments["action"] == "inspect_abandoned_operation"
                    and raw.get("ok") is True and isinstance(recovery, dict)
                    and recovery.get("recovery_available") is True):
                saved = saved_project_observation(Path(self.target["project_path"]))
                if (not isinstance(authority, dict)
                        or authority.get("pid") != self.target["pid"]
                        or authority.get("main_hwnd") != self.target["hwnd"]
                        or authority.get("workspace_id") != self.target["workspace_id"]
                        or authority.get("revision_id") != self.target["revision_id"]
                        or (self.expected_process_create_time is not None
                            and authority.get("process_create_time") != self.expected_process_create_time)
                        or saved.get("stable_during_read") is not True
                        or authority.get("working_sha256") != saved.get("sha256")
                        or recovery.get("saved_sha256") != saved.get("sha256")
                        or recovery.get("target_verified") is not True
                        or not isinstance(authority.get("recovery_token"), str)
                        or not 1 <= len(authority["recovery_token"]) <= 200):
                    raise MCPToolError("Operation recovery proof differs from this bound inspection")
                ref = "recovery:" + uuid.uuid4().hex
                self.operation_recoveries[ref] = {
                    "recovery_token": authority["recovery_token"], "expires_at": time.monotonic() + 300.0}
                raw = deepcopy(raw)
                raw["operation_recovery"]["recovery_ref"] = ref
            if isinstance(recovery, dict) and recovery.get("settled") is True:
                self.operation_recoveries.clear()
                self.variable_comments.clear()
                self.owned_ui.clear()

            def public_recovery(value: Any) -> Any:
                if isinstance(value, dict):
                    return {key: (validate_native_failure(item) if is_native_failure_field(key, item) else public_recovery(item)) for key, item in value.items()
                            if key not in {"authority", "recovery_token", "target_process_token",
                                           "owner_process_token", "lifetime_marker"}}
                if isinstance(value, list):
                    return [public_recovery(item) for item in value]
                return value
            raw = public_recovery(raw)
        if name == "native_editor" and arguments.get("action") in {"read_check", "program_check"}:
            self.check_results.clear()
            check = raw.get("check")
            if (raw.get("ok") is True and isinstance(check, dict) and check.get("read_complete") is True
                    and check.get("program", {}).get("name") == arguments["program_name"]):
                raw = deepcopy(raw)
                for table in raw["check"].get("tables", []):
                    identity = table.get("identity", {})
                    if (table.get("read_complete") is True and table.get("identity_verified") is True
                            and identity.get("pid") == self.target["pid"]
                            and identity.get("main_hwnd") == self.target["hwnd"]
                            and (self.expected_process_create_time is None
                                 or identity.get("process_create_time") == self.expected_process_create_time)
                            and type(table.get("row_count")) is int
                            and table["row_count"] == len(table.get("rows", []))
                            and type(table.get("column_count")) is int
                            and table["column_count"] > 0
                            and table["column_count"] == len(table.get("columns", []))
                            and all(row.get("index") == index and row.get("read_complete") is True
                                    and len(row.get("cells", [])) == table["column_count"]
                                    for index, row in enumerate(table["rows"]))):
                        ref = "check-result:" + uuid.uuid4().hex
                        self.check_results[ref] = {"table": deepcopy(table),
                            "program_name": arguments["program_name"]}
                        table["result_ref"] = ref
        if name == "native_editor" and arguments.get("action") == "inspect_variable_comment":
            variable = raw.get("variable_comment")
            proof = variable.get("authority") if isinstance(variable, dict) else None
            if (raw.get("ok") is True and isinstance(variable, dict) and variable.get("supported") is True
                    and isinstance(proof, dict) and proof.get("pid") == self.target["pid"]
                    and proof.get("main_hwnd") == self.target["hwnd"]
                    and proof.get("program_name") == arguments["program_name"]
                    and proof.get("expected_element") == arguments["expected_element"]
                    and (self.expected_process_create_time is None or
                         proof.get("process_create_time") == self.expected_process_create_time)
                    and isinstance(proof.get("fields"), dict)
                    and all(isinstance(proof["fields"].get(field), dict)
                            and proof["fields"][field].get("text") == variable.get(field)
                            for field in ("device", "symbol", "comment"))):
                ref = "variable:" + uuid.uuid4().hex
                self.variable_comments[ref] = deepcopy(proof)
                raw = deepcopy(raw)
                raw["variable_comment"]["variable_ref"] = ref
            elif raw.get("ok") is True and proof is not None:
                raise MCPToolError("Variable comment proof differs from this bound inspection")
        if name == "native_editor" and arguments.get("action") in {"inspect_variable_comment", "set_variable_comment"}:
            # Keep the exact backend receipt in record_raw; opaque model refs
            # never expose a native dialog/control capability, including errors.
            def public_comment(value: Any) -> Any:
                if isinstance(value, dict):
                    return {key: (validate_native_failure(item) if is_native_failure_field(key, item) else public_comment(item)) for key, item in value.items()
                            if key not in {"authority", "observed_modal", "lifetime_marker"}}
                if isinstance(value, list):
                    return [public_comment(item) for item in value]
                return value
            raw = public_comment(raw)
        self._retain_rung_source(request, raw)
        scene = raw.get("observation", {})
        if pending_ui:
            if (arguments.get('action')!='observe' or raw.get('ok') is not True
                    or raw.get('error') or raw.get('failure')):
                scene={}
            elif (not isinstance(scene,dict) or scene.get('pid')!=self.target['pid']
                    or scene.get('main_hwnd')!=self.target['hwnd']
                    or scene.get('process_create_time',self.expected_process_create_time)!=self.expected_process_create_time):
                raise MCPToolError('Pending close observation lacks exact current frame/lifetime')
        if isinstance(scene, dict):
            if scene.get("pid", self.target["pid"]) != self.target["pid"] or scene.get("main_hwnd", self.target["hwnd"]) != self.target["hwnd"]:
                raise MCPToolError("Native result target differs")
            # Missing collections are unobserved, not evidence of absence.
            # Each actual collection updates only its own capability ledger.
            modals = scene.get("owned_modals")
            windows = scene.get("owned_windows")
            modals = modals if isinstance(modals, list) else None
            windows = windows if isinstance(windows, list) else None
            modal_handles = ({item.get("hwnd") for item in modals if isinstance(item, dict)
                              and type(item.get("hwnd")) is int}
                             if modals is not None else
                             {item["hwnd"] for item in self.owned_ui.values() if item["ui_kind"] == "modal"})
            for kind, collection in (("modal", modals), ("modeless", windows)):
                if collection is not None:
                    self.owned_ui = {ref: item for ref, item in self.owned_ui.items()
                                     if item["ui_kind"] != kind}
            excluded = {self.target["hwnd"], scene.get("active_mdi_hwnd"),
                        *scene.get("mdi_client_hwnds", [])}
            capabilities = {}
            owner_windows = {item.get("hwnd"): item for item in windows or [] if isinstance(item, dict)
                             and type(item.get("hwnd")) is int}
            for kind, collection in (("modeless", windows), ("modal", modals)):
                for original in collection or []:
                    if not isinstance(original, dict):
                        continue
                    window = original
                    handle = window.get("hwnd")
                    if type(handle) is not int or handle <= 0:
                        continue
                    if kind == "modeless" and handle in modal_handles:
                        continue
                    if kind == "modal" and "root_owner_hwnd" not in window:
                        # Modal rows omit owner fields. Join only an actual
                        # same-scene window identity, never a stored capability.
                        owner = owner_windows.get(handle)
                        if not isinstance(owner, dict) or any(owner.get(key) != window.get(key)
                                for key in ("pid", "title", "class_name")):
                            continue
                        window = {**owner, **window}
                    class_name = str(window.get("class_name", "")).casefold()
                    if (handle in excluded
                            or window.get("pid") != self.target["pid"]
                            or window.get("root_owner_hwnd") != self.target["hwnd"]
                            or window.get("visible") is False
                            or window.get("surface_kind") == "native_menu"
                            or class_name.startswith("bcgpshadow")
                            or class_name in {"combolbox", "#32768"}):
                        continue
                    if kind == "modeless" and class_name == "#32770":
                        continue
                    responses = (["accept", "cancel"] if kind == "modal" else ["close"])
                    if pending_ui:
                        controls=window.get('controls')
                        cancel=[item for item in controls if isinstance(item,dict)
                            and type(item.get('control_id')) is int and item['control_id']==2
                            and str(item.get('class_name','')).casefold()=='button'
                            and item.get('enabled') is True and item.get('visible') is True] if isinstance(controls,list) else []
                        responses=['cancel'] if kind=='modal' and len(cancel)==1 else []
                    if not window.get("enabled"):
                        responses = []
                    ref = "ui:" + hashlib.sha256(json.dumps(
                        [self.target["session_id"], handle, window.get("title"), window.get("class_name"), kind],
                        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:24]
                    if pending_ui:ref='ui:'+uuid.uuid4().hex
                    capability = {"ui_ref": ref, "ui_kind": kind, "allowed_responses": responses}
                    capabilities[handle] = capability
                    if responses:
                        self.owned_ui[ref] = {"hwnd": handle, **capability}
                        if pending_ui:self.owned_ui[ref]['pending_recovery_identity']=deepcopy(pending_ui_identity)
                        if kind == "modal":
                            self.owned_ui[ref]["expected_modal"] = {
                                "main_hwnd": self.target["hwnd"], "owner_hwnd": window.get("owner_hwnd"),
                                "process_create_time": self.expected_process_create_time,
                                "title": window.get("title"), "class_name": window.get("class_name")}
            # Work on a copy: the actual private backend journal stays exact.
            raw = deepcopy(raw)
            for collection in ("owned_modals", "owned_windows"):
                actual = raw.get("observation", {}).get(collection)
                for item in actual if isinstance(actual, list) else []:
                    if isinstance(item, dict) and item.get("hwnd") in capabilities:
                        item.update(capabilities[item["hwnd"]])
        if name == "native_editor" and arguments.get("action") == "respond_owned_ui":
            raw = {**raw, "owned_ui_response": {
                "ui_ref": arguments["ui_ref"], "ui_kind": ui["ui_kind"],
                "requested_response": arguments["response"]}}
        opened = raw.get("program_activation", {})
        if (request["action"] == "open_program" and isinstance(opened, dict)
                and opened.get("exact_program_active") is True and opened.get("verified") is True
                and opened.get("program_name") == request["program_name"]):
            self.program_name = request["program_name"]
        return scrub_native_tool_result(raw)


def persist_native_checkpoint(store: Any, key: str, binding: dict[str, Any],
                              checkpoint: dict[str, Any], digest: str, *,
                              source_run_id: str | None = None) -> None:
    immutable_snapshot = type(checkpoint) is ValidatedNativeCheckpoint
    if immutable_snapshot:
        if checkpoint.digest != digest:
            raise MCPToolError("Native immutable checkpoint digest mismatched")
    else:
        checkpoint, digest = validated_native_checkpoint(checkpoint, digest)
    previous = store.get_native_checkpoint_prefix_state(key, binding)
    if previous is not None and previous.get("binding") == binding:
        old = previous["checkpoint"]["public_state"]
        public = checkpoint["public_state"]
        prefix_matches = (checkpoint.completed_prefix_matches(old) if immutable_snapshot else
            old["model_prompt"] == public["model_prompt"]
            and public["completed_pairs"][:len(old["completed_pairs"])] == old["completed_pairs"]
            and public["records"][:len(old["records"])] == old["records"])
        if not prefix_matches:
            raise MCPToolError("Native completed prefix regressed or changed")
    retained = {"binding": binding, "checkpoint": checkpoint, "digest": digest}
    if source_run_id is not None:
        retained["source_run_id"] = source_run_id
    if immutable_snapshot:
        store.set_native_checkpoint_snapshot(key, binding, checkpoint, source_run_id=source_run_id)
    else:
        store.set_host_state(key, retained)


def persist_native_checkpoint_with_index(store: Any, key: str, index_key: str, binding: dict[str, Any],
                                         checkpoint: dict[str, Any], digest: str, *,
                                         source_run_id: str) -> None:
    """One joined worker preserves full validation, prefix and store ordering."""
    persist_native_checkpoint(store, key, binding, checkpoint, digest, source_run_id=source_run_id)
    store.set_host_state(index_key, {"key": key})


def native_checkpoint_matches_request(checkpoint: dict[str, Any], prompt: str, binding: dict[str, Any]) -> bool:
    try:
        original = json.loads(checkpoint["public_state"]["model_prompt"])
        return (isinstance(original, dict) and original.get("request") == prompt
                and isinstance(original.get("binding"), dict)
                and all(original["binding"].get(key) == binding[key] for key in ("workspace_id", "revision_id")))
    except (KeyError, TypeError, ValueError):
        return False


def native_followup_checkpoint(previous: dict[str, Any], prompt: str, binding: dict[str, Any]) -> dict[str, Any]:
    """Carry actual completed pairs into a new objective without replaying them."""
    checkpoint = validated_native_checkpoint_snapshot(previous["checkpoint"], previous["digest"])
    if previous.get("binding") != binding:
        raise MCPToolError("Native follow-up target differs")
    original = json.loads(checkpoint["public_state"]["model_prompt"])
    if not isinstance(original, dict) or not isinstance(original.get("request"), str):
        raise MCPToolError("Native prior request is unavailable")
    continued = {
        "event": "request_continued", "previous_request": original["request"], "request": prompt,
        "source_run_id": previous.get("source_run_id"),
        "meaning": "Previous results are historical facts. Do not replay completed or uncertain input; follow this new request.",
    }
    model_prompt = json.dumps({
        "request": prompt, "binding": {**binding, "program_name": original.get("binding", {}).get("program_name")},
        "previous_request": original["request"],
        "continuation": (
            "Retained tool results belong to previous requests, not fresh execution. "
            "Do not replay completed input or an unknown outcome. A failure explicitly reporting "
            "creation_input_attempted:false and input_state_unknown:false did not create a project; "
            "a new user-requested preparation attempt after revalidation is permitted. "
            "Report a retry only when a new actual preparation receipt exists."
        ),
    }, ensure_ascii=False)
    return native_checkpoint_objective(checkpoint, model_prompt, continued)


def latest_native_checkpoint_summary(store: Any, conversation_id: str) -> dict[str, Any] | None:
    """Display-only metadata; not checkpoint integrity, goal, or resume authority."""
    summary = store.get_native_checkpoint_display_summary(conversation_id)
    if summary is None:
        return None
    source = store.get_run(summary['source_run_id'])
    if source is None or source.conversation_id != conversation_id:
        return None
    return summary


def latest_native_checkpoint(store: Any, conversation_id: str) -> dict[str, Any] | None:
    index = store.get_host_state(f"native_ld_checkpoint_latest:{conversation_id}")
    if not isinstance(index, dict) or not isinstance(index.get("key"), str):
        return None
    read_checkpoint = getattr(store, "get_native_checkpoint_state", store.get_host_state)
    retained = read_checkpoint(index["key"])
    if not isinstance(retained, dict):
        return None
    # Reuse only the internal immutable validation proof; raw/legacy stores still validate here.
    retained["checkpoint"] = validated_native_checkpoint_snapshot(retained["checkpoint"], retained["digest"])
    source = store.get_run(retained.get("source_run_id", ""))
    if source is None or source.conversation_id != conversation_id:
        return None
    return retained
