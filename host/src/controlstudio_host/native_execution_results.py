"""Small public projections of actual native receipts, never private payloads."""

from __future__ import annotations

from typing import Any

from controlstudio_contracts.native_failure import (
    NativeFailureProtocolError,
    validate_native_failure,
)
from controlstudio_contracts.native_ld_commands import (
    NativeInputEffect,
    native_operation_input_effect,
)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _activity_presentation(tool_name: str, action: str) -> tuple[str, str]:
    """Display categories only: never infer input, success or completion state."""
    labels = {
        "execute_simulation_scenario": "시뮬레이터 시나리오 실행",
        "save_project": "프로젝트 저장 입력", "read_saved_project": "저장 파일 확인",
        "read_variable_comments": "전역 변수·설명 표 읽기",
        "read_ld": "LD 읽기", "read_ld_rows": "LD 범위 읽기", "read_selection": "LD 선택 읽기",
        "open_program": "프로그램 열기", "apply_ld_edits": "LD 변경 묶음 실행",
        "observe": "편집 상태 확인", "program_check": "프로그램 검사 열기",
        "read_check": "프로그램 검사 결과 읽기", "respond_owned_ui": "편집 창 응답",
        "prepare_project": "새 프로젝트·LD 프로그램 준비",
        "inspect_variable_comment": "변수 설명 읽기", "set_variable_comment": "변수 설명 수정·재확인",
        "inspect_abandoned_operation": "중단된 편집 작업 상태 확인",
        "settle_abandoned_operation": "중단된 편집 작업 잠금 복구",
    }
    if tool_name == "simulator":
        labels = {
            "start": "XG-SIM 시작", "load": "XG-SIM 저장본 적재",
            "run": "XG-SIM RUN 명령", "stop": "XG-SIM 정지", "end": "XG-SIM 종료",
            "observe": "XG-SIM 상태 확인", "read_devices": "XG-SIM 디바이스 관측",
            "write_devices": "XG-SIM 입력·읽기 확인",
        }
    if tool_name != "simulator":
        labels.update({
            "read_saved_symbols": "저장된 변수 읽기",
            "inspect_declaration_comments": "변수 선언·설명 읽기",
            "set_declaration_comments": "변수 선언 설명 수정",
            "inspect_variable_table": "변수 표 상태 확인",
            "open_variable_table": "변수 표 열기",
            "inspect_rung_comment": "렁 설명 읽기", "set_rung_comment": "렁 설명 수정",
            "set_variable_comments": "변수 설명 묶음 수정",
            "read_find_results": "검색 결과 읽기", "find_ld_device": "LD 디바이스 검색",
            "find_ld_devices": "LD 디바이스 묶음 검색",
            "select_ld": "LD 요소 선택", "position_ld_cell": "LD 셀 이동", "goto_step": "LD 위치 이동",
            "activate_check_result": "검사 결과 위치 열기", "activate_find_result": "검색 결과 위치 열기",
            "recover_workspace": "작업 공간 복구", "inspect_project_preparation": "프로젝트 준비 상태 확인",
            "cancel_project_preparation": "프로젝트 준비 취소", "initialize_ld_program": "첫 LD 접점 작성",
        })
    else:
        labels["read_words"] = "XG-SIM 워드 관측"
    reads = {"read_saved_project", "read_saved_symbols", "read_variable_comments",
        "inspect_declaration_comments", "inspect_variable_table", "inspect_variable_comment", "inspect_rung_comment",
        "read_ld", "read_ld_rows", "read_selection", "read_check", "read_find_results", "observe",
        "find_ld_device", "find_ld_devices", "inspect_abandoned_operation", "inspect_project_preparation"}
    edits = {"write_ld_element", "insert_contact", "insert_parallel_contact", "insert_series_contact_before",
        "edit_ld_instruction", "insert_ld_instruction", "insert_ld_row_before", "insert_ld_row_after",
        "delete_ld_element", "delete_ld_rows", "move_instruction", "connect_row_terminal",
        "set_ld_vertical_wire", "set_ld_horizontal_wire", "apply_ld_edits", "initialize_ld_program"}
    comments = {"set_declaration_comments", "set_variable_comment", "set_variable_comments", "set_rung_comment"}
    if tool_name in {"simulator", "execute_simulation_scenario"}:
        return labels.get(action, "XG-SIM 동작"), "시뮬레이터"
    if action in reads:
        return labels.get(action, "자료 읽기"), "저장 확인" if action == "read_saved_project" else "읽기·관측"
    if action in edits:
        return labels.get(action, "LD 편집 실행"), "LD 편집"
    if action in comments:
        return labels[action], "설명 수정"
    return labels.get(action, "요청 동작"), "native 실행"


def native_result_event(tool_name: str, arguments: dict[str, Any], result: Any) -> dict[str, Any]:
    """Project allowlisted facts; input completion and saved bytes stay separate."""
    raw = _object(result)
    action = arguments.get("action") if tool_name in {"native_editor", "simulator"} else tool_name
    if not isinstance(action, str) or len(action) > 80 or not all(c.isalnum() or c == "_" for c in action):
        action = "native_action"
    summary: dict[str, Any] = {"schemaVersion": "1", "action": action}
    text: list[str] = []
    batch = _object(raw.get("batch"))
    failure = _object(raw.get("failure")) or _object(raw.get("error")) or _object(raw.get("admission_failure"))
    details = _object(failure.get("details"))
    whole_batch_unknown = (raw.get("batch_result") == "unknown" or details.get("batch_result") == "unknown"
                           or raw.get("completed_prefix_known") is False or details.get("completed_prefix_known") is False)
    recovery = _object(raw.get("operation_recovery"))
    if (failure.get("code") == "XG5000_OPERATION_QUARANTINED"
            and recovery.get("inspection_required") is True):
        summary.update(state="unknown" if raw.get("input_state_unknown") is True else "failed",
                       quarantined=True, inspectionRequired=True, oldOutcomeUnknown=True)
        if raw.get("action_attempted") is False:
            summary["actionAttempted"] = False
        text.append("중단된 작업의 상태를 확인한 뒤 이어서 진행합니다")
    elif tool_name == "execute_simulation_scenario" and batch:
        # Scenarios are ordered memory observations, not LD edit entries.
        children=batch.get("children")
        children=children if isinstance(children,list) else []
        prefix=_count(batch.get("completed_prefix_count"))
        if prefix is not None and prefix>len(children):prefix=None
        unknown_indices=[child["index"] for child in children if isinstance(child,dict)
            and child.get("status")=="unknown" and _count(child.get("index")) is not None]
        failed_indices=[child["index"] for child in children if isinstance(child,dict)
            and child.get("status")=="failed" and _count(child.get("index")) is not None]
        mismatch_indices=[child["index"] for child in children if isinstance(child,dict)
            and _object(child.get("expectation")).get("matched") is False and _count(child.get("index")) is not None]
        native_complete=(batch.get("all_native_steps_completed") is True and prefix==len(children)
            and not failed_indices and not unknown_indices)
        cancelled=batch.get("cancelled") is True
        summary.update(totalSteps=len(children),completedPrefixKnown=prefix is not None,
            nativeExecutionCompleted=native_complete,failedStepIndices=failed_indices,
            unknownStepIndices=unknown_indices,expectationMismatchIndices=mismatch_indices,
            notStartedCount=sum(isinstance(child,dict) and child.get("status")=="not_started" for child in children),
            cancelled=cancelled,runtimeBehaviorVerified=False)
        if prefix is not None:summary["completedPrefixCount"]=prefix
        matched=batch.get("expectations_matched")
        if matched is None or type(matched) is bool:summary["executedExpectationsMatched"]=matched
        summary["state"]=("unknown" if raw.get("input_state_unknown") is True or unknown_indices or prefix is None else
            "failed" if mismatch_indices else "completed" if native_complete and not cancelled and raw.get("ok") is True else
            "partial" if prefix or cancelled else "failed")
        text.append(f"시나리오 {len(children)}단계 중 {prefix if prefix is not None else '미확인'}단계 실행 완료")
        if mismatch_indices:text.append(f"기대값 불일치 {len(mismatch_indices)}단계")
        if unknown_indices or raw.get("input_state_unknown") is True:text.append("현재 실행 결과 미확인 · 같은 입력을 자동으로 반복하지 않습니다")
        if summary["notStartedCount"]:text.append(f"미시작 {summary['notStartedCount']}단계")
        if cancelled:text.append("취소 요청 뒤 진행 중이던 단계만 정리했습니다")
    elif whole_batch_unknown:
        summary.update(state="unknown", completedPrefixKnown=False)
        text.append("변경 묶음의 완료 범위를 확인하지 못했습니다. 실제 LD를 확인한 뒤 이어서 진행해야 합니다")
    elif batch:
        entries = batch.get("entries")
        entries = entries if isinstance(entries, list) else []
        prefix = _count(batch.get("completed_prefix_count"))
        if prefix is not None and prefix > len(entries):
            prefix = None
        summary.update(
            completedPrefixKnown=prefix is not None,
            totalEdits=len(entries),
            failedIndices=[row["index"] for row in entries if isinstance(row, dict)
                           and row.get("status") == "failed" and _count(row.get("index")) is not None],
            unknownIndices=[row["index"] for row in entries if isinstance(row, dict)
                            and row.get("status") == "unknown" and _count(row.get("index")) is not None],
            notStartedCount=sum(isinstance(row, dict) and row.get("status") == "not_started" for row in entries),
        )
        if prefix is not None:
            summary["completedPrefixCount"] = prefix
            text.append(f"변경 {len(entries)}건 중 {prefix}건 입력 완료")
        if summary["failedIndices"]:
            text.append(f"실패 {len(summary['failedIndices'])}건")
        if summary["unknownIndices"]:
            text.append(f"입력 결과 미확인 {len(summary['unknownIndices'])}건")
        if summary["notStartedCount"]:
            text.append(f"미시작 {summary['notStartedCount']}건")
        summary["state"] = ("unknown" if summary["unknownIndices"] or prefix is None else
                            "completed" if batch.get("all_completed") is True else "partial")
    elif tool_name == "simulator":
        summary["state"] = ("unknown" if raw.get("input_state_unknown") is True else
                            "failed" if raw.get("ok") is False or failure else "observed")
        summary["simulatorAction"] = action
        gui = _object(raw.get("gui"))
        for value, public_name in ((raw.get("load_state"), "loadState"), (gui.get("status"), "guiStatus")):
            if isinstance(value, str) and 0 < len(value) <= 80 and all(c.isalnum() or c == "_" for c in value):
                summary[public_name] = value
        for field, public_name in (("initial_load_pending", "initialLoadPending"),
                                   ("mode_input_dispatched", "modeInputDispatched"),
                                   ("read_only", "readOnly"),
                                   ("already_stopped", "alreadyStopped"),
                                   ("cleanup_disconnected_session", "cleanupDisconnectedSession"),
                                   ("end_reconciled_read_only", "endReconciledReadOnly"),
                                   ("ended", "ended"),
                                   ("runtime_behavior_verified", "runtimeBehaviorVerified")):
            if type(raw.get(field)) is bool:
                summary[public_name] = raw[field]
        target = _object(raw.get("target_status"))
        if target.get("classification") in {"SIMULATOR_TARGET", "NOT_SIMULATOR_TARGET"} and type(target.get("verified")) is bool:
            summary["simulatorTarget"] = {"classification": target["classification"], "verified": target["verified"]}
            for field, public_name in (("connected", "connected"),
                                       ("simulator_mode_verified", "simulatorModeVerified"),
                                       ("disconnected_mode_verified", "disconnectedModeVerified")):
                if type(target.get(field)) is bool:
                    summary["simulatorTarget"][public_name] = target[field]
        for field, public_name in (("loaded_sha256", "loadedSha256"),):
            digest = raw.get(field)
            if isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
                summary[public_name] = digest
        saved = _object(raw.get("saved_project"))
        digest = saved.get("sha256")
        if isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
            summary["savedSha256"] = digest
        if summary.get("loadedSha256") and summary.get("savedSha256"):
            summary["loadedVersionMatchesSaved"] = summary["loadedSha256"] == summary["savedSha256"]
        trace = raw.get("trace")
        if isinstance(trace, list):
            summary["sampleCount"] = len(trace)
        memory = _object(raw.get("memory"))
        if type(memory.get("readback_matched")) is bool:
            summary["inputReadbackMatched"] = memory["readback_matched"]
        if summary["state"] == "unknown":
            text.append("XG-SIM 입력 결과가 불명확합니다. 같은 입력을 자동으로 반복하지 않습니다")
        elif summary["state"] == "failed":
            text.append("XG-SIM 동작을 완료하지 못했습니다")
        elif action == "start" and (summary.get("initialLoadPending") is True
                or summary.get("loadState") == "awaiting_initial_load" or summary.get("guiStatus") == "write_dialog_ready"):
            text.append("XG-SIM 초기 적재 창을 열었습니다. 저장본 적재는 아직 완료되지 않았습니다")
        elif action == "load":
            text.append("저장본 버전을 XG-SIM에 적재했습니다" if summary.get("loadedVersionMatchesSaved") is True else
                        "XG-SIM 적재 결과를 받았습니다. 저장본 버전 일치는 미확인입니다")
        elif action == "run":
            text.append("XG-SIM RUN 명령을 전달했습니다. 실제 동작은 입력·출력 관측으로 확인합니다")
        elif action == "read_devices":
            text.append(f"XG-SIM 실제 디바이스 관측 {summary.get('sampleCount', 0)}회")
        elif action == "write_devices":
            text.append("XG-SIM 입력 후 실제 값을 다시 읽어 일치를 확인했습니다" if summary.get("inputReadbackMatched") is True else
                        "XG-SIM 입력 결과를 받았습니다. 읽기 일치는 미확인입니다")
        elif action == "stop" and summary.get("alreadyStopped") is True and summary.get("readOnly") is True:
            text.append("이미 정지한 XG-SIM의 실제 상태를 확인했습니다. 정지 입력은 보내지 않았습니다")
        elif action == "end" and summary.get("ended") is True:
            text.append("현재 XG-SIM 연결 해제를 읽어 종료 상태를 확인했습니다. 종료 입력은 보내지 않았습니다" if summary.get("endReconciledReadOnly") is True and summary.get("readOnly") is True else
                        "끊긴 XG-SIM 세션을 종료하고 연결 해제를 확인했습니다" if summary.get("cleanupDisconnectedSession") is True else
                        "XG-SIM을 종료하고 연결 해제를 확인했습니다")
        elif target.get("disconnected_mode_verified") is True and target.get("connected") is False:
            text.append("현재 XG-SIM 모드의 연결 끊김을 확인했습니다")
        elif target.get("verified") is False:
            text.append("현재 창의 XG-SIM 연결을 확인하지 못했습니다")
        else:
            text.append("XG-SIM 실제 상태를 확인했습니다")
    elif tool_name == "native_editor" and action in {"inspect_abandoned_operation", "settle_abandoned_operation"}:
        recovery = _object(raw.get("operation_recovery"))
        summary["state"] = "failed" if raw.get("ok") is False or failure else "completed" if recovery.get("settled") is True else "observed"
        for field, public_name in (("quarantined", "quarantined"), ("recovery_available", "recoveryAvailable"),
                                   ("settled", "settled"), ("old_outcome_unknown", "oldOutcomeUnknown"),
                                   ("target_verified", "targetVerified")):
            if type(recovery.get(field)) is bool:
                summary[public_name] = recovery[field]
        if summary["state"] == "failed":
            text.append("중단된 편집 작업의 안전한 복구 조건을 확인하지 못했습니다")
        elif recovery.get("settled") is True:
            text.append("이전 작업의 불명확한 결과를 보존하고 편집 잠금을 복구했습니다. 편집 입력은 보내지 않았습니다")
        elif recovery.get("recovery_available") is True:
            text.append("남은 실행 프로세스와 현재 저장본을 확인했습니다. 잠금 복구가 가능합니다")
        elif recovery.get("quarantined") is True:
            text.append("편집 잠금이 유지되고 있습니다")
        else:
            text.append("중단된 편집 작업 잠금이 없습니다")
    elif tool_name == "native_editor" and action in {"inspect_variable_comment", "set_variable_comment"}:
        variable = _object(raw.get("variable_comment")) or _object(details.get("variable_comment"))
        receipt = _object(raw.get("receipt")) or _object(details.get("receipt"))
        unknown = (raw.get("input_state_unknown") is True or details.get("input_state_unknown") is True
                   or receipt.get("input_state_unknown") is True or variable.get("input_state_unknown") is True)
        summary["state"] = ("unknown" if unknown else "failed" if raw.get("ok") is False or failure else
                            "completed" if variable.get("comment_verified") is True else "observed")
        for field, public_name in (("supported", "commentSupported"), ("text_read", "commentTextRead"),
                                   ("comment_verified", "commentVerified")):
            if type(variable.get(field)) is bool:
                summary[public_name] = variable[field]
        if unknown:
            text.append("설명 입력 결과가 불명확합니다. 실제 설명을 다시 확인해야 합니다")
        elif summary["state"] == "failed":
            text.append("변수 설명 동작을 완료하지 못했습니다")
        elif variable.get("comment_verified") is True:
            text.append("변수 설명을 수정하고 실제 편집창에서 다시 읽어 확인했습니다")
        elif variable.get("supported") is False:
            text.append("실제 변수 편집창을 관측했으나 지원하는 입력 필드와 일치하지 않습니다")
        else:
            text.append("실제 변수 이름과 설명을 읽었습니다")
    elif tool_name == "prepare_project" and raw.get("ok") is True:
        summary["state"] = "completed"
        name = raw.get("program_name")
        if isinstance(name, str):
            summary["programName"] = name[:80]
        saved = _object(raw.get("saved_project"))
        summary["savedFileObserved"] = saved.get("exists") is True and saved.get("stable_during_read") is True
        text.append("새 프로젝트와 첫 LD 프로그램을 준비했습니다" if raw.get("already_prepared") is not True else
                    "이미 준비된 프로젝트를 유지합니다. 생성 입력을 다시 실행하지 않았습니다")
        if summary["savedFileObserved"]:
            text.append("저장 파일 확인 완료")
    elif tool_name == "read_saved_project":
        saved = {key: raw[key] for key in ("exists", "stable_during_read") if type(raw.get(key)) is bool}
        if _count(raw.get("size")) is not None:
            saved["size"] = raw["size"]
        digest = raw.get("sha256")
        if isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
            saved["sha256"] = digest
        summary["savedFile"] = saved
        summary["state"] = "observed" if raw.get("exists") is True else "unavailable"
        if raw.get("exists") is True:
            text.append("저장된 파일을 읽었습니다" if raw.get("stable_during_read") is True else
                        "저장 파일을 읽었으나 읽기 중 안정성이 확인되지 않았습니다")
        else:
            text.append("저장 파일이 없습니다")
    else:
        for key in ("action_attempted", "ld_edit_verified"):
            if type(raw.get(key)) is bool:
                summary[key] = raw[key]
        if _count(raw.get("input_events_inserted")) is not None:
            summary["input_events_inserted"] = raw["input_events_inserted"]
        if raw.get("input_state_unknown") is True or details.get("input_state_unknown") is True:
            summary["state"] = "unknown"
            text.append("입력 결과가 불명확합니다. 현재 상태 확인이 필요합니다")
        elif raw.get("ok") is False or failure:
            summary["state"] = "failed"
            text.append("요청한 동작을 완료하지 못했습니다")
        else:
            summary["state"] = "completed" if raw.get("ok") is True else "observed"
            if tool_name == "save_project":
                text.append("저장 명령을 보냈습니다. 저장 파일 확인 결과는 별도로 표시합니다")
            else:
                text.append("실행 결과를 받았습니다")
        # Only the classified code is public. Native exception messages may
        # contain paths, handles, field values or transport credentials.
        code = failure.get("kind") or failure.get("code")
        if isinstance(code, str) and code and all(c.isalnum() or c in "_-" for c in code):
            summary["errorKind"] = code[:120]
            text.append(code[:120])
    # Display guidance only from the versioned failure contract, never an
    # arbitrary diagnostic/code string or an inferred input/completion outcome.
    if failure and raw.get("ok") is not True:
        native_failure = failure.get("nativeFailure")
        if native_failure is None and "schema" in failure:
            native_failure = failure
        if native_failure is not None:
            try:
                checked_failure = validate_native_failure(native_failure)
            except NativeFailureProtocolError:
                pass
            else:
                if checked_failure.get("code", checked_failure.get("kind")) == "XG5000_WINDOW_NOT_FOREGROUND":
                    text.append("XG5000 창을 앞으로 가져와 주세요. 창 전환 후 현재 상태 확인이 필요합니다.")
    capture = _object(raw.get("ld"))
    if capture:
        observed = {key: capture[key] for key in ("verified", "stable_repeat") if type(capture.get(key)) is bool}
        decoded = _object(capture.get("decoded"))
        rows = decoded.get("rows")
        if isinstance(rows, list):
            observed["rowCount"] = len(rows)
        program = _object(capture.get("program"))
        name = program.get("name")
        if isinstance(name, str):
            observed["programName"] = name[:80]
        summary["capture"] = observed
        text.append("실제 LD 관측 포함")
    after = _object(raw.get("post_batch_capture"))
    if after.get("status") in {"not_started", "completed", "failed"}:
        summary["postCaptureState"] = after["status"]
        if after["status"] == "failed":
            text.append("후속 LD 관측 실패")
    activity_label, activity_role = _activity_presentation(tool_name, action)
    return {"activity_label": activity_label, "activity_role": activity_role,
            "activity_result": " · ".join(text), "native_result": summary}


def native_interrupted_event(tool_name: str, arguments: dict[str, Any], kind: str) -> dict[str, Any]:
    """A missing native reply is not proof that mutating input failed to occur."""
    # Local saved-file reads retain their separate no-native path. Native
    # operations share the same audited effect resolver as the Agent owner.
    may_have_input = (tool_name != "read_saved_project" and
        native_operation_input_effect(tool_name, arguments) is not NativeInputEffect.NO_NATIVE_INPUT)
    raw = {"ok": False, "input_state_unknown": may_have_input,
           "error": {"kind": kind}}
    if tool_name == "native_editor" and arguments.get("action") == "apply_ld_edits":
        raw["completed_prefix_known"] = False
    return native_result_event(tool_name, arguments, raw)
