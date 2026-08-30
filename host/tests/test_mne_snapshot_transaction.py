from __future__ import annotations

import unittest
from typing import Any

from controlstudio_host.execution_broker import MCPExecutionBroker
from controlstudio_host.mcp_client import MCPGateway

_WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
_REVISION_ID = "22222222-2222-4222-8222-222222222222"
_SESSION = {
    "session_id": "session-verified",
    "pid": 1234,
    "hwnd": 5678,
    "project_path": r"C:\\workspaces\\working\\machine.xgwx",
    "project_hash": "a" * 64,
}
_WORKSPACE = {
    "workspace_id": _WORKSPACE_ID,
    "revision_id": _REVISION_ID,
    "source_kind": "local_file",
    "provenance_receipt_digest": None,
    "source_sha256": "b" * 64,
    "working_path": r"C:\\workspaces\\working\\machine.xgwx",
    "working_sha256": "a" * 64,
}


class MneSnapshotTransactionTests(unittest.TestCase):
    def _bound_gateway(self) -> MCPGateway:
        gateway = MCPGateway(command="unused")
        gateway.bind_agent_session(
            {
                **_SESSION,
                **_WORKSPACE,
            }
        )
        return gateway

    def test_agent_snapshot_drops_private_mne_transaction_arguments(self) -> None:
        safe = self._bound_gateway()._safe_arguments(
            "project.snapshot",
            {
                "plan_kind": "mne_full_program_replacement",
                "workspace_id": "untrusted-workspace",
                "begin_change_transaction": False,
                "host_transaction_id": "33333333-3333-4333-8333-333333333333",
            },
        )

        self.assertNotIn("workspace_id", safe)
        self.assertNotIn("plan_kind", safe)
        self.assertNotIn("host_transaction_id", safe)
        self.assertIs(safe["begin_change_transaction"], False)
        for field, value in _SESSION.items():
            self.assertEqual(safe[field], value)

    def test_non_private_snapshot_remains_read_only(self) -> None:
        safe = self._bound_gateway()._safe_arguments(
            "project.snapshot",
            {
                "begin_change_transaction": True,
                "workspace_id": "untrusted-workspace",
            },
        )

        self.assertNotIn("workspace_id", safe)
        self.assertIs(safe["begin_change_transaction"], False)

    def test_agent_snapshot_schema_hides_private_transaction_controls(self) -> None:
        class _Gateway:
            @property
            def agent_session_identity_snapshot(self) -> dict[str, Any]:
                return {**_SESSION, **_WORKSPACE}

        schema = {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "pid": {"type": "integer"},
                "hwnd": {"type": "integer"},
                "project_path": {"type": "string"},
                "project_hash": {"type": "string"},
                "workspace_id": {"type": "string"},
                "begin_change_transaction": {"type": "boolean"},
                "plan_kind": {"type": "string"},
                "host_transaction_id": {"type": "string"},
            },
            "required": [
                "session_id",
                "pid",
                "hwnd",
                "project_path",
                "project_hash",
                "workspace_id",
                "begin_change_transaction",
                "plan_kind",
                "host_transaction_id",
            ],
        }

        public = MCPExecutionBroker(_Gateway())._public_schema("project.snapshot", schema)

        assert public is not None
        self.assertEqual(set(public["properties"]), set())
        self.assertEqual(public["required"], [])
