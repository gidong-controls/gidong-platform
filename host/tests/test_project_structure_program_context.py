from __future__ import annotations

import unittest
from unittest.mock import patch

from controlstudio_host.agent_client import _structural_tool_result_payload
from controlstudio_host.structural_evidence import (
    _validate_structure_item,
)


def _ld_program_code_unit(*, canonical_program_id: str = "ld:55e388e7aea7a8ce") -> dict[str, object]:
    return {
        "code_unit_id": "xg5000:code-unit:fixture",
        "configuration_id": "xg5000:configuration:fixture",
        "order": 1,
        "task_program_order": 1,
        "order_basis": "configuration_xml_document_order",
        "name": "MainProgram",
        "code_unit_type": "Program",
        "kind_code": None,
        "language": "LD",
        "language_status": "observed_routine_tag",
        "routine_tags": ["LD"],
        "vendor_object_id": None,
        "version": "",
        "instance_name": "",
        "comment": "",
        "task_assignment": {
            "task_name": "Scan Program",
            "task_id": "xg5000:task:fixture",
            "status": "observed",
        },
        "source_path": "/Project[1]/CodeUnit[1]",
        "body": {
            "status": "unobserved",
            "container_count": 0,
            "decoded_container_count": 0,
            "container_payload_digests": [],
            "marker_string_count": 0,
            "application_token_count": 0,
            "device_reference_count": 0,
            "raw_body_included": False,
        },
        "canonical_program_id": canonical_program_id,
    }


class ProjectStructureProgramContextTests(unittest.TestCase):
    def test_exact_ld_program_id_is_bound_to_the_code_unit(self) -> None:
        fact = _validate_structure_item("code_units", _ld_program_code_unit())

        assert fact is not None
        self.assertIn(
            {"name": "canonicalProgramId", "value": "ld:55e388e7aea7a8ce"},
            fact["attributes"],
        )

    def test_rejects_noncanonical_ld_program_id(self) -> None:
        with self.assertRaisesRegex(Exception, "canonical LD program id"):
            _validate_structure_item(
                "code_units",
                _ld_program_code_unit(canonical_program_id="MainProgram"),
            )

    def test_agent_tool_return_preserves_canonical_program_id_in_normal_receipt(self) -> None:
        fact = _validate_structure_item("code_units", _ld_program_code_unit())
        assert fact is not None
        with patch(
            "controlstudio_host.agent_client._structural_evidence_projection",
            return_value=(
                {
                    "evidenceId": "00000000-0000-4000-8000-000000000701",
                    "toolName": "project.structure_index",
                    "receipt": {
                        "schema": "controlstudio-structural-analysis-evidence/v1",
                        "receiptDigest": "a" * 64,
                        "structureFacts": [fact],
                    },
                },
                {"receiptDigest": "a" * 64, "structureFacts": [fact]},
            ),
        ):
            payload = _structural_tool_result_payload(
                request_id="structure-request",
                tool_alias="inspect_project_structure",
                evidence_ref={"ignored": True},
                raw_result={},
            )

        self.assertNotIn("modelContext", payload["result"])
        self.assertIn(
            {"name": "canonicalProgramId", "value": "ld:55e388e7aea7a8ce"},
            payload["result"]["_controlstudio_evidence"]["receipt"]["structureFacts"][0]["attributes"],
        )


if __name__ == "__main__":
    unittest.main()
