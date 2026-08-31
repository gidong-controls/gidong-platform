from __future__ import annotations

import unittest

from controlstudio_host.execution_broker import (
    MCPExecutionBroker,
    MCPToolError,
    _canonical_digest,
)


def _analysis(*, include_binding: bool) -> dict[str, object]:
    binding = {
        "schema": "xg5000.project.field-io-binding/1.0",
        "device": "P0000",
        "direction": "input",
        "point_kind": "digital_input",
        "module": {"rack_base": 0, "slot": 0, "module_id": 1},
        "channel": {"index": 0, "status": "derived_from_verified_terminal_range"},
        "terminal_range": {"address_start": "P0000", "address_end": "P001F"},
        "proposal_scope": {"online_force_write_download": "unsupported"},
    }
    binding["binding_digest"] = _canonical_digest(binding)
    unsigned: dict[str, object] = {
        "schema": "xg5000.program.change-target-analysis/1.1",
        "target": {
            "program_id": "program-1",
            "program_name": "MainProgram",
            "value_token_offset": 1,
            "value": "P0000",
            "record_kind": "CONTACT_NO",
            "visual_row": 0,
            "column": 0,
            "network_id": "network-1",
            "network_digest": "a" * 64,
            "network_index": 0,
            "rung_id": "rung-1",
            "rung_role": "condition",
        },
        "current_semantics": {"status": "confirmed", "kind": "contact_operand"},
        "proposed_semantics": {"status": "confirmed", "kind": "contact_operand"},
        "proposed_value": "P0001",
        "project_binding": {"project_hash": "b" * 64},
        "hardware_context_binding": {
            "schema": "xg5000.project.hardware-context/1.0",
            "context_digest": "c" * 64,
        },
        "impact": {
            "primary_devices": ["P0000"],
            "physical_io_candidates": ["P0000"],
            "physical_output_candidates": [],
            "multiple_writer_devices": [],
            "physical_io_hardware_bindings": (
                [
                    {
                        "device": "P0000",
                        "binding_status": "bound",
                        "field_io_binding": binding,
                    }
                ]
                if include_binding
                else []
            ),
        },
        "blocking_reasons": [],
        "offline_candidate_ready": True,
        "runtime_deployment_ready": False,
        "runtime_deployment_blocking_reasons": [
            "online_runtime_deployment_unsupported"
        ],
    }
    return {**unsigned, "analysis_digest": _canonical_digest(unsigned)}


def _runtime_warning_analysis() -> dict[str, object]:
    analysis = _analysis(include_binding=False)
    analysis.pop("analysis_digest")
    analysis["offline_candidate_ready"] = True
    analysis["blocking_reasons"] = []
    analysis["runtime_deployment_blocking_reasons"] = [
        "online_runtime_deployment_unsupported",
        "cpu_model_unobserved",
        "device_not_observed:P0000",
        "io_module_rack_slot_channel_unresolved:P0000",
        "target_network_semantics_partial",
    ]
    impact = analysis["impact"]
    assert isinstance(impact, dict)
    impact["physical_io_hardware_bindings"] = [
        {
            "device": "P0000",
            "expected_direction": "input",
            "binding_status": "unresolved",
            "reason": "rack/slot/channel is not observed",
        }
    ]
    return {**analysis, "analysis_digest": _canonical_digest(analysis)}


def _internal_m_analysis(*, record_kind: str = "OUTPUT") -> dict[str, object]:
    analysis = _analysis(include_binding=False)
    analysis.pop("analysis_digest")
    target = analysis["target"]
    assert isinstance(target, dict)
    target.update(
        value="M0010", record_kind=record_kind,
        rung_role="output" if record_kind == "OUTPUT" else "condition",
    )
    semantic_kind = "coil_operand" if record_kind == "OUTPUT" else "contact_operand"
    analysis["current_semantics"] = {
        "kind": semantic_kind, "status": "confirmed", "device": "M0010",
    }
    analysis["proposed_semantics"] = {
        "kind": semantic_kind, "status": "confirmed", "device": "M0011",
    }
    analysis["proposed_value"] = "M0011"
    analysis["hardware_context_binding"] = None
    analysis["impact"] = {
        "primary_devices": ["M0010", "M0011"],
        "physical_io_candidates": [], "physical_output_candidates": [],
        "multiple_writer_devices": [], "physical_io_hardware_bindings": [],
    }
    return {**analysis, "analysis_digest": _canonical_digest(analysis)}


class FieldIoEvidenceProjectionTests(unittest.TestCase):
    def test_projects_internal_m_contact_and_coil_without_hardware(self) -> None:
        for record_kind in ("CONTACT_NO", "OUTPUT"):
            with self.subTest(record_kind=record_kind):
                receipt = MCPExecutionBroker._change_target_evidence_contract(
                    {"analysis": _internal_m_analysis(record_kind=record_kind)}
                )
                self.assertEqual(receipt["schema"], "controlstudio-change-target-evidence/v2")
                self.assertTrue(receipt["offlineCandidateReady"])
                self.assertIsNone(receipt["hardwareContextBinding"])
                self.assertEqual(receipt["physicalIoCandidates"], [])
                self.assertEqual(receipt["currentValue"], "M0010")
                self.assertEqual(receipt["proposedValue"], "M0011")
                self.assertFalse(receipt["runtimeDeploymentReady"])

    def test_internal_m_projection_still_accepts_existing_hardware_binding(self) -> None:
        analysis = _internal_m_analysis()
        analysis.pop("analysis_digest")
        analysis["hardware_context_binding"] = _analysis(include_binding=True)[
            "hardware_context_binding"
        ]
        analysis["analysis_digest"] = _canonical_digest(analysis)
        receipt = MCPExecutionBroker._change_target_evidence_contract({"analysis": analysis})
        self.assertEqual(receipt["hardwareContextBinding"]["context_digest"], "c" * 64)

    def test_hardware_free_m_exception_does_not_accept_unknown_or_timer_semantics(self) -> None:
        for semantics in ({}, {"kind": "timer_instruction", "status": "confirmed", "device": "M0010"}):
            with self.subTest(semantics=semantics):
                analysis = _internal_m_analysis()
                analysis.pop("analysis_digest")
                analysis["current_semantics"] = semantics
                analysis["analysis_digest"] = _canonical_digest(analysis)
                with self.assertRaisesRegex(MCPToolError, "required hardware context"):
                    MCPExecutionBroker._change_target_evidence_contract({"analysis": analysis})

    def test_physical_target_or_internal_target_with_physical_impact_still_needs_hardware(self) -> None:
        for internal_target in (False, True):
            with self.subTest(internal_target=internal_target):
                analysis = _analysis(include_binding=True)
                analysis.pop("analysis_digest")
                analysis["hardware_context_binding"] = None
                if internal_target:
                    internal = _internal_m_analysis()
                    for field in ("target", "current_semantics", "proposed_semantics", "proposed_value"):
                        analysis[field] = internal[field]
                analysis["analysis_digest"] = _canonical_digest(analysis)
                with self.assertRaisesRegex(MCPToolError, "required hardware context"):
                    MCPExecutionBroker._change_target_evidence_contract({"analysis": analysis})

    def test_projects_exact_typed_terminal_binding(self) -> None:
        receipt = MCPExecutionBroker._change_target_evidence_contract(
            {"analysis": _analysis(include_binding=True)}
        )

        self.assertEqual(receipt["physicalIoCandidates"], ["P0000"])
        self.assertEqual(receipt["physicalIoBindings"][0]["channel"]["index"], 0)
        self.assertEqual(
            receipt["physicalIoBindings"][0]["proposalScope"][
                "online_force_write_download"
            ],
            "unsupported",
        )

    def test_rejects_physical_candidate_without_terminal_binding(self) -> None:
        with self.assertRaises(MCPToolError):
            MCPExecutionBroker._change_target_evidence_contract(
                {"analysis": _analysis(include_binding=False)}
            )

    def test_accepts_candidate_with_explicit_runtime_blockers(self) -> None:
        receipt = MCPExecutionBroker._change_target_evidence_contract(
            {"analysis": _runtime_warning_analysis()}
        )
        self.assertEqual(receipt["schema"], "controlstudio-change-target-evidence/v2")
        self.assertTrue(receipt["offlineCandidateReady"])
        self.assertEqual(receipt["physicalIoCandidates"], ["P0000"])
        self.assertEqual(receipt["physicalIoBindings"], [])
        self.assertFalse(receipt["runtimeDeploymentReady"])
        self.assertIn(
            "device_not_observed:P0000",
            receipt["runtimeDeploymentBlockingReasons"],
        )

    def test_rejects_unresolved_candidate_without_device_specific_runtime_blocker(self) -> None:
        analysis = _runtime_warning_analysis()
        analysis.pop("analysis_digest")
        analysis["runtime_deployment_blocking_reasons"] = [
            "online_runtime_deployment_unsupported",
            "cpu_model_unobserved",
        ]
        analysis["analysis_digest"] = _canonical_digest(analysis)
        with self.assertRaisesRegex(MCPToolError, "explicit runtime blocker"):
            MCPExecutionBroker._change_target_evidence_contract({"analysis": analysis})

    def test_rejects_candidate_without_the_global_runtime_deployment_block(self) -> None:
        analysis = _runtime_warning_analysis()
        analysis.pop("analysis_digest")
        analysis["runtime_deployment_blocking_reasons"] = [
            "cpu_model_unobserved",
            "device_not_observed:P0000",
            "io_module_rack_slot_channel_unresolved:P0000",
            "target_network_semantics_partial",
        ]
        analysis["analysis_digest"] = _canonical_digest(analysis)
        with self.assertRaisesRegex(MCPToolError, "runtime deployment boundary"):
            MCPExecutionBroker._change_target_evidence_contract({"analysis": analysis})

    def test_rejects_analysis_or_field_binding_digest_tamper(self) -> None:
        analysis = _analysis(include_binding=True)
        analysis["analysis_digest"] = "0" * 64
        with self.assertRaisesRegex(MCPToolError, "receipt digest"):
            MCPExecutionBroker._change_target_evidence_contract({"analysis": analysis})

        analysis = _analysis(include_binding=True)
        analysis.pop("analysis_digest")
        impact = analysis["impact"]
        assert isinstance(impact, dict)
        entries = impact["physical_io_hardware_bindings"]
        assert isinstance(entries, list)
        binding = entries[0]["field_io_binding"]
        assert isinstance(binding, dict)
        binding["binding_digest"] = "0" * 64
        analysis["analysis_digest"] = _canonical_digest(analysis)
        with self.assertRaisesRegex(MCPToolError, "field I/O binding"):
            MCPExecutionBroker._change_target_evidence_contract({"analysis": analysis})
