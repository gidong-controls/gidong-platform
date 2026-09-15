from controlstudio_host.api_models import ProjectBriefV1
from controlstudio_host.planning_drafts import (
    _requirement_diagnostics, _ladder_edit_impact, _design_execution_observations,
)


def test_stopped_mode_does_not_duplicate_auto_activation():
    brief = ProjectBriefV1.model_validate({
        "project_name": "mode", "machine_purpose": "내부 신호 모의 운전",
        "sequence": "모드 선택", "hardware_and_io": "XGK-CPUE 내부 M",
        "safety_and_interlocks": "자동 수동 동시 활성화 금지",
        "operating_modes": "자동 수동 정지", "performance_and_acceptance": "상호 배타",
    })
    contact = lambda name: {"kind": "contact", "sourceType": "signal", "sourceId": name, "state": "on"}
    design = {key: [] for key in ("ioPoints", "operatingModes", "controlNetworks", "timers",
                                  "sequences", "alarms", "safetyRules", "recoveryRules", "ladderRungs")}
    design["operatingModes"] = [
        {"modeId": name, "name": name, "activation": {"signalId": signal, "state": state}}
        for name, signal, state in [("auto", "auto_select", "on"),
                                    ("manual", "manual_select", "on"), ("stopped", "auto_select", "off")]]
    rung = {"rungId": "test", "condition": contact("auto_select"),
            "action": {"instruction": "OUT", "targetType": "signal", "targetId": "running"}}
    design["ladderRungs"] = [rung]
    errors = lambda: [d for d in _requirement_diagnostics(design, brief)
                     if d["code"] == "mutually_exclusive_modes_required_together"]
    assert not errors()
    rung["condition"] = {"kind": "all_of", "terms": [contact("auto_select"), contact("manual_select")]}
    assert len(errors()) == 1


def test_manual_move_button_is_an_operator_command_not_mode_selection():
    brief = ProjectBriefV1.model_validate({
        "project_name": "manual", "machine_purpose": "수동 이동",
        "sequence": "수동 버튼 한 번에 1회 이동", "hardware_and_io": "내부 M",
        "safety_and_interlocks": "정지", "operating_modes": "수동",
        "performance_and_acceptance": "버튼을 놓아야 다시 이동",
    })
    design = {key: [] for key in ("ioPoints", "operatingModes", "controlNetworks", "timers",
                                  "sequences", "alarms", "safetyRules", "recoveryRules", "ladderRungs")}
    design["ioPoints"] = [{"signalId": name, "kind": "digital_input"}
                          for name in ("start_btn", "manual_move_btn", "manual_select")]
    design["operatingModes"] = [{"modeId": "manual", "name": "수동",
                                  "activation": {"signalId": "manual_select", "state": "on"}}]
    sequence = {"sequenceId": "manual", "modeId": "manual", "restartPolicy": "require_start_release",
                "startAll": [{"signalId": "manual_move_btn", "state": "on"}], "steps": []}
    design["sequences"] = [sequence]
    errors = lambda: [d for d in _requirement_diagnostics(design, brief)
                      if d["code"] == "manual_sequence_missing_operator_start"]
    assert not errors()
    sequence["startAll"] = [{"signalId": "manual_select", "state": "on"}]
    assert len(errors()) == 1
    sequence["steps"] = [{"stepId": "idle", "completeAll": [
        {"signalId": "manual_move_btn", "state": "on"}]}]
    assert not errors()  # Selecting manual may enter a non-actuating waiting step.
    design["ladderRungs"] = [{"rungId": "unsafe_idle", "condition": {
        "kind": "contact", "sourceType": "step", "sourceId": "idle", "state": "on"},
        "action": {"instruction": "OUT", "targetType": "signal", "targetId": "motion"}}]
    assert len(errors()) == 1  # Waiting must not itself energize a signal.


def test_execution_observations_find_nested_internal_reads_but_not_inputs():
    design = {
        "io_points": [{"signal_id": name, "kind": kind} for name, kind in
                      [("button", "digital_input"), ("running", "internal_bit"),
                       ("alarm", "internal_bit"), ("unused", "internal_bit")]],
        "ladder_rungs": [{
            "rung_id": "reader", "condition": {"kind": "all_of", "terms": [
                {"kind": "contact", "source_type": "signal", "source_id": name, "state": "on"}
                for name in ("button", "running", "alarm")]},
            "action": {"instruction": "OUT", "target_type": "signal", "target_id": "alarm"},
        }],
    }
    assert _design_execution_observations(design)["unwrittenInternalSignals"] == [
        {"signalId": "running", "readerRungIds": ["reader"]}]
    design["ladder_rungs"].append({"rung_id": "producer", "condition": {},
        "action": {"instruction": "OUT", "target_type": "signal", "target_id": "running"}})
    assert not _design_execution_observations(design)["unwrittenInternalSignals"]
    assert not _design_execution_observations({})["unwrittenInternalSignals"]


def test_ladder_edit_impact_reports_comments_separately_from_execution():
    first = {"rung_id": "first", "condition": {"state": "on"}, "description": "old"}
    second = {"rung_id": "second", "condition": {"state": "off"}, "description": "same"}
    before = [first, second]
    comments = _ladder_edit_impact(before, [{**first, "description": "new"}, second])
    assert comments["executableChanged"] is False
    assert comments["changedFieldsByRung"] == {"first": ["description"]}
    assert _ladder_edit_impact(before, [{**first, "condition": {"state": "off"}}, second])["executableChanged"]
    assert _ladder_edit_impact(before, [second, first])["executableChanged"]
    assert _ladder_edit_impact(before, [first])["executableChanged"]


def test_recovery_distinguishes_inputs_latches_and_trip_reset():
    brief = ProjectBriefV1.model_validate({
        "project_name": "복구", "machine_purpose": "내부 신호 모의 운전",
        "sequence": "복구 후 재시작", "hardware_and_io": "XGK-CPUE 내부 M",
        "safety_and_interlocks": "원인 해소 후 복구", "operating_modes": "단일 모드",
        "performance_and_acceptance": "복구 조건 확인", "attachment_source_ids": [],
    })
    def contact(signal, state):
        return {"kind": "contact", "sourceType": "signal", "sourceId": signal, "state": state}

    terms = [contact("emergency_stop", "on"), contact("alarm_estop_latch", "on")]
    design = {
        "ioPoints": [
            {"signalId": "emergency_stop", "kind": "digital_input", "normalState": "on"},
            {"signalId": "door_open", "kind": "digital_input", "normalState": "off"},
            {"signalId": "alarm_estop_latch", "kind": "internal_bit", "normalState": "off"},
        ],
        "operatingModes": [], "controlNetworks": [], "timers": [], "sequences": [],
        "alarms": [], "safetyRules": [],
        "recoveryRules": [{"recoveryId": "recover", "resetSequenceIds": ["seq"],
                           "resetAlarmIds": [], "whenAll": [
                               {"signalId": "emergency_stop", "state": "on"},
                               {"signalId": "alarm_estop_latch", "state": "on"}]}],
        "ladderRungs": [{"rungId": "reset", "condition": {"kind": "all_of", "terms": terms},
                         "action": {"instruction": "SET", "targetType": "sequence_reset", "targetId": "seq"}}],
    }
    def recovery_errors():
        return [d for d in _requirement_diagnostics(design, brief)
                if d["code"] == "recovery_requires_active_hazard"]

    assert not recovery_errors()  # An active alarm latch is not an active NC input.
    terms.append(contact("door_open", "on"))
    assert len(recovery_errors()) == 1
    terms.pop()
    design["ladderRungs"][0]["condition"] = contact("emergency_stop", "off")
    assert not recovery_errors()  # A trip may legitimately reset the sequence.
