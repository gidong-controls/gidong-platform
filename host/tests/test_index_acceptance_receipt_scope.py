import pytest

from controlstudio_host.native_projects import NativeProjectEvidenceError, _index_acceptance_scenario_ids


def design():
    return {"indexAcceptanceBindings": {"alarms": {
        "emergency": "estop_fault", "door": "guard_fault", "timeout": "late_fault",
    }}, "alarms": [{"alarmId": identity} for identity in ("estop_fault", "guard_fault", "late_fault")]}


def test_expected_trials_use_reviewed_arbitrary_alarm_ids():
    expected = _index_acceptance_scenario_ids(design())
    assert len(expected) == 8
    assert {value for value in expected if ":fault_recovery:" in value} == {
        f"acceptance:fault_recovery:{identity}" for identity in ("estop_fault", "guard_fault", "late_fault")
    }


@pytest.mark.parametrize("change", ["omitted", "duplicate", "unknown", "extra", "duplicate_reviewed"])
def test_partial_or_aliased_fault_coverage_is_rejected(change):
    value = design()
    roles = value["indexAcceptanceBindings"]["alarms"]
    if change == "omitted":
        del roles["door"]
    elif change == "duplicate":
        roles["door"] = roles["emergency"]
    elif change == "unknown":
        roles["door"] = "unreviewed"
    elif change == "extra":
        value["alarms"].append({"alarmId": "extra_fault"})
    else:
        value["alarms"].append(value["alarms"][0].copy())
    with pytest.raises(NativeProjectEvidenceError):
        _index_acceptance_scenario_ids(value)


def test_legacy_trial_names_are_unchanged():
    expected = _index_acceptance_scenario_ids({})
    assert len(expected) == 8
    assert "acceptance:fault_recovery:alarm_emergency" in expected
