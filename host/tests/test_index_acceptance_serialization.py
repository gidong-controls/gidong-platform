from controlstudio_host.api_models import (
    ProjectPlanIndexAcceptanceBindingsV1,
    ProjectPlanNativeDesignV1,
)
from controlstudio_host.planning_drafts import ProjectDesignDraftService


def test_absent_binding_does_not_change_legacy_serialized_design():
    # Empty design isolates wire compatibility; this is not executable PLC proof.
    design = ProjectPlanNativeDesignV1(
        io_points=[], operating_modes=[], control_networks=[], safety_rules=[],
    )
    assert "indexAcceptanceBindings" not in design.model_dump(mode="json", by_alias=True)
    assert "index_acceptance_bindings" not in design.model_dump(mode="json")


def test_binding_is_preserved_in_reviewed_design_and_round_trip():
    binding = ProjectPlanIndexAcceptanceBindingsV1(
        signals={"manual_start": "manual_button"},
        modes={"manual": "manual_mode"}, alarms={"timeout": "late"}, timer_id="watch",
    )
    design = ProjectPlanNativeDesignV1(
        io_points=[], operating_modes=[], control_networks=[], safety_rules=[],
        index_acceptance_bindings=binding,
    )
    wire = design.model_dump(mode="json", by_alias=True)
    assert wire["indexAcceptanceBindings"]["timerId"] == "watch"
    assert ProjectPlanNativeDesignV1.model_validate(wire).index_acceptance_bindings == binding


def test_binding_edit_preserves_existing_design_entities():
    document = {"native_design": {"io_points": [{"signal_id": "existing"}]}}
    binding = {"signals": {"manual_start": "existing"}, "modes": {}, "alarms": {}, "timer_id": "timer"}
    edited = ProjectDesignDraftService._apply_changes(document, [{
        "operation": "replace_section", "section": "index_acceptance_bindings", "value": binding,
    }])
    assert edited["native_design"]["index_acceptance_bindings"]["signals"] == binding["signals"]
    assert edited["native_design"]["io_points"] == document["native_design"]["io_points"]
    assert "index_acceptance_bindings" not in document["native_design"]
