from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from typing import Any

from controlstudio_host.execution_broker import (
    MCPExecutionBroker,
    MCPToolError,
    _canonical_digest,
)
from controlstudio_host.mcp_client import MCPGateway

_SESSION_ID = "11111111-1111-4111-8111-111111111111"
_OPERATION_ID = "22222222-2222-4222-8222-222222222222"
_PROJECT_PATH = r"C:\verified\working-copy.xgwx"
_PROJECT_HASH = "a" * 64
_SECTION_NAMES = (
    "configurations",
    "tasks",
    "code_units",
    "symbols",
    "devices",
    "call_edges",
    "parameters",
    "modules",
    "communications",
    "opaque_containers",
)


def _identity() -> dict[str, Any]:
    return {
        "session_id": _SESSION_ID,
        "pid": 1234,
        "hwnd": 5678,
        "project_path": _PROJECT_PATH,
        "project_hash": _PROJECT_HASH,
    }


def _identity_with_workspace() -> dict[str, Any]:
    return {
        **_identity(),
        "workspace_id": "33333333-3333-4333-8333-333333333333",
        "revision_id": "44444444-4444-4444-8444-444444444444",
        "source_kind": "local_file",
        "provenance_receipt_digest": None,
        "source_sha256": _PROJECT_HASH,
        "working_path": _PROJECT_PATH,
        "working_sha256": _PROJECT_HASH,
    }


def _empty_section() -> dict[str, Any]:
    return {
        "coverage": {
            "count": 0,
            "total": 0,
            "returned": 0,
            "offset": 0,
            "limit": 2_000,
            "truncated": False,
            "next_offset": None,
        },
        "items": [],
    }


def _structure_index() -> dict[str, Any]:
    unsigned: dict[str, Any] = {
        "schema": "xg5000.project.structure-index/1.0",
        "status": "complete",
        "read_only": True,
        "project_binding": {
            "session_id": _SESSION_ID,
            "pid": 1234,
            "hwnd": 5678,
            "project_path": _PROJECT_PATH,
            "project_sha256": _PROJECT_HASH,
            "project_digest": {
                "algorithm": "sha256",
                "value": _PROJECT_HASH,
                "scope": "exact_bound_xgwx_file",
            },
            "source_receipt": {
                "kind": "xgwx_project_xml",
                "observed": True,
                "project_sha256": _PROJECT_HASH,
                "project_size": 100,
                "xml_sha256": "b" * 64,
                "xml_size": 50,
                "gzip_offset": 0,
                "gzip_member_size": 80,
                "trailing_size": 20,
            },
        },
        "project": {
            "name": "Fixture",
            "vendor_guid": None,
            "version": "",
            "file_version": "",
            "comment": "",
            "configuration_count": 0,
        },
        "sections": {name: _empty_section() for name in _SECTION_NAMES},
        "coverage": {
            "offset": 0,
            "limit": 2_000,
            "maximum_limit": 2_000,
            "output_truncated": False,
            "code_body_semantics": "not_present",
            "task_order_basis": "configuration_xml_document_order",
            "raw_xml_included": False,
            "raw_binary_included": False,
            "secret_values_included": False,
            "secret_attribute_count_redacted": 0,
        },
        "limitations": [],
    }
    return {**unsigned, "index_digest": _canonical_digest(unsigned)}


def _envelope() -> dict[str, Any]:
    index = _structure_index()
    return {
        "session_id": _SESSION_ID,
        "operation": "project.structure_index",
        "ok": True,
        "verified": True,
        "structure_index": index,
        "index_digest": index["index_digest"],
        "receipt_path": r"C:\artifacts\project-structure-index.json",
        "project_hash_before": _PROJECT_HASH,
        "project_hash_after": _PROJECT_HASH,
        "project_unchanged": True,
        "operation_id": _OPERATION_ID,
    }


class ProjectStructureIndexEnvelopeTests(unittest.TestCase):
    def test_mcp_decode_preserves_the_outer_operation_envelope(self) -> None:
        envelope = _envelope()
        result = SimpleNamespace(
            is_error=False,
            structured_content={"status": "ok", "data": envelope},
            data=None,
            content=[],
        )

        decoded = object.__new__(MCPGateway)._decode_tool_result(
            "project.structure_index",
            result,
        )

        self.assertIs(decoded, envelope)

    def test_projects_only_the_validated_inner_structure_index(self) -> None:
        receipt = MCPExecutionBroker._evidence_contract(
            "project.structure_index",
            _envelope(),
            expected_session_identity=_identity(),
        )

        self.assertIsNotNone(receipt)
        assert receipt is not None
        self.assertEqual(receipt["toolName"], "project.structure_index")
        self.assertEqual(receipt["projectSnapshot"]["indexDigest"], _structure_index()["index_digest"])

    def test_rejects_the_inner_index_without_its_mcp_envelope(self) -> None:
        with self.assertRaisesRegex(MCPToolError, "envelope shape"):
            MCPExecutionBroker._evidence_contract(
                "project.structure_index",
                _structure_index(),
                expected_session_identity=_identity(),
            )

    def test_accepts_the_exact_session_and_workspace_identity_union(self) -> None:
        receipt = MCPExecutionBroker._evidence_contract(
            "project.structure_index",
            _envelope(),
            expected_session_identity=_identity_with_workspace(),
        )

        self.assertIsNotNone(receipt)
        assert receipt is not None
        self.assertEqual(receipt["toolName"], "project.structure_index")

    def test_rejects_partial_workspace_identity_and_unknown_extra_fields(self) -> None:
        partial_workspace = _identity_with_workspace()
        partial_workspace.pop("revision_id")
        unknown_extra = {**_identity_with_workspace(), "unexpected": "value"}
        for label, identity in (
            ("partial workspace", partial_workspace),
            ("unknown extra", unknown_extra),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                MCPToolError, "envelope shape"
            ):
                MCPExecutionBroker._evidence_contract(
                    "project.structure_index",
                    _envelope(),
                    expected_session_identity=identity,
                )

    def test_rejects_outer_success_identity_and_digest_tampering(self) -> None:
        mutations = {
            "extra field": lambda value: value.update({"schema_version": "1.0"}),
            "non-boolean ok": lambda value: value.update({"ok": 1}),
            "unverified": lambda value: value.update({"verified": False}),
            "changed project": lambda value: value.update({"project_unchanged": False}),
            "wrong session": lambda value: value.update({"session_id": "other"}),
            "wrong before hash": lambda value: value.update({"project_hash_before": "c" * 64}),
            "wrong after hash": lambda value: value.update({"project_hash_after": "c" * 64}),
            "wrong outer digest": lambda value: value.update({"index_digest": "c" * 64}),
            "invalid operation id": lambda value: value.update({"operation_id": "not-a-uuid"}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                envelope = _envelope()
                mutate(envelope)
                with self.assertRaises(MCPToolError):
                    MCPExecutionBroker._evidence_contract(
                        "project.structure_index",
                        envelope,
                        expected_session_identity=_identity(),
                    )

    def test_rejects_inner_digest_and_session_binding_tampering(self) -> None:
        envelope = _envelope()
        index = copy.deepcopy(envelope["structure_index"])
        index["project"]["name"] = "Tampered"
        envelope["structure_index"] = index
        with self.assertRaisesRegex(MCPToolError, "digest"):
            MCPExecutionBroker._evidence_contract(
                "project.structure_index",
                envelope,
                expected_session_identity=_identity(),
            )

        envelope = _envelope()
        index = copy.deepcopy(envelope["structure_index"])
        index.pop("index_digest")
        index["project_binding"]["pid"] = 9999
        index["index_digest"] = _canonical_digest(index)
        envelope["structure_index"] = index
        envelope["index_digest"] = index["index_digest"]
        with self.assertRaisesRegex(MCPToolError, "active Host session"):
            MCPExecutionBroker._evidence_contract(
                "project.structure_index",
                envelope,
                expected_session_identity=_identity(),
            )


if __name__ == "__main__":
    unittest.main()
