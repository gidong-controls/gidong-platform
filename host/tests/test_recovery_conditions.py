from copy import deepcopy

from controlstudio_host.recovery_conditions import blocked_recovery_conditions


def contact(identity, state="on"):
    return {"kind": "contact", "sourceType": "signal", "sourceId": identity, "state": state}


def fixture():
    return {
        "ladderRungs": [
            {"rungId": "stopped", "action": {"instruction": "OUT", "targetType": "signal", "targetId": "stopped"},
             "condition": {"kind": "all_of", "terms": [contact("running", "off"), contact("aggregate", "off")]}},
            {"rungId": "aggregate", "action": {"instruction": "OUT", "targetType": "signal", "targetId": "aggregate"},
             "condition": {"kind": "any_of", "terms": [contact("alarm_a"), contact("alarm_b")]}},
        ],
        "recoveryRules": [{"recoveryId": "reset_a", "whenAll": [
            {"signalId": "stopped", "state": "on"}, {"signalId": "alarm_a", "state": "on"},
            {"signalId": "reset_button", "state": "on"},
        ]}],
    }


def test_reports_actual_dependency_chain_without_mutating_design():
    design = fixture()
    before = deepcopy(design)
    result = blocked_recovery_conditions(design)
    assert result[0]["conflictingSignals"] == ["alarm_a"]
    assert result[0]["dependencyRungIds"] == ["aggregate", "stopped"]
    assert design == before


def test_stopped_state_independent_of_alarm_allows_recovery():
    design = fixture()
    design["ladderRungs"][0]["condition"]["terms"].pop()
    assert blocked_recovery_conditions(design) == []


def test_alternative_valid_branch_is_not_treated_as_mandatory():
    design = fixture()
    design["ladderRungs"][0]["condition"]["kind"] = "any_of"
    assert blocked_recovery_conditions(design) == []


def test_multiple_writers_are_not_assumed_to_be_combinational_equations():
    design = fixture()
    design["ladderRungs"].append(deepcopy(design["ladderRungs"][0]))
    assert blocked_recovery_conditions(design) == []


def test_cyclic_dependency_terminates_without_inventing_a_contradiction():
    design = fixture()
    design["ladderRungs"][0]["condition"] = contact("stopped")
    assert blocked_recovery_conditions(design) == []
