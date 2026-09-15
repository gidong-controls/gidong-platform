from controlstudio_host.planning_drafts import _design_execution_observations


def test_reset_order_observation_tracks_order_and_intervening_writers():
    contact = {"kind": "contact", "source_type": "signal", "source_id": "fault", "state": "on"}
    reset = {"rung_id": "clear_fault", "action": {"instruction": "RST", "target_type": "signal", "target_id": "fault"}}
    sequence = {"rung_id": "clear_steps", "condition": contact,
                "action": {"instruction": "SET", "target_type": "sequence_reset", "target_id": "cycle"}}
    def observe(rungs):
        return _design_execution_observations({"ladder_rungs": rungs})["potentialResetOrderDependencies"]
    assert observe([reset, sequence]) == [{"signalId": "fault", "earlierResetRungId": "clear_fault", "laterSequenceResetRungId": "clear_steps"}]
    assert observe([sequence, reset]) == []
    writer = {"rung_id": "new_value", "action": {"instruction": "OUT", "target_type": "signal", "target_id": "fault"}}
    assert observe([reset, writer, sequence]) == []
    alternative = {**sequence, "condition": {"kind": "any_of", "terms": [contact, {**contact, "source_id": "other"}]}}
    assert observe([reset, alternative]) == []
