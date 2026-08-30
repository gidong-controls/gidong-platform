from __future__ import annotations

import unittest

from controlstudio_host.execution_broker import MCPExecutionBroker
from controlstudio_host.mcp_client import MCPToolError


class ProgramReadLdEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_hash = "a" * 64
        self.identity = {
            "session_id": "session-1",
            "pid": 321,
            "hwnd": 654,
            "project_path": r"C:\work\project.xgwx",
            "project_hash": self.project_hash,
        }
        self.result = {
            "operation": "program.read_ld",
            "ok": True,
            "verified": True,
            "stable_repeat": True,
            "project_unchanged": True,
            "project_hash_before": self.project_hash,
            "project_hash_after": self.project_hash,
            "project_sha256_before": self.project_hash,
            "project_sha256_after": self.project_hash,
            "payload_sha256": "b" * 64,
            "decoded_program_sha256": "c" * 64,
            "program": {
                "program_id": "ld:0123456789abcdef",
                "name": "MainProgram",
            },
        }

    def test_receipt_binds_exact_session_project_program_and_payload(self) -> None:
        receipt = MCPExecutionBroker._evidence_contract(
            "program.read_ld",
            self.result,
            expected_session_identity=self.identity,
        )

        self.assertEqual(receipt["schema"], "controlstudio-program-read-ld-evidence/v1")
        self.assertEqual(receipt["pid"], 321)
        self.assertEqual(receipt["hwnd"], 654)
        self.assertEqual(receipt["projectPath"], r"C:\work\project.xgwx")
        self.assertEqual(receipt["projectSha256"], self.project_hash)
        self.assertEqual(receipt["programId"], "ld:0123456789abcdef")
        self.assertEqual(receipt["programName"], "MainProgram")
        self.assertEqual(receipt["payloadSha256"], "b" * 64)
        self.assertEqual(len(receipt["receiptDigest"]), 64)

    def test_receipt_rejects_project_hash_drift(self) -> None:
        self.result["project_hash_after"] = "d" * 64

        with self.assertRaises(MCPToolError):
            MCPExecutionBroker._evidence_contract(
                "program.read_ld",
                self.result,
                expected_session_identity=self.identity,
            )

    def test_receipt_rejects_missing_pid_or_hwnd(self) -> None:
        identity = dict(self.identity)
        identity.pop("hwnd")

        with self.assertRaises(MCPToolError):
            MCPExecutionBroker._evidence_contract(
                "program.read_ld",
                self.result,
                expected_session_identity=identity,
            )


if __name__ == "__main__":
    unittest.main()
