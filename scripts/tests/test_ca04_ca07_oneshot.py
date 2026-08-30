from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_SCRIPT = Path(__file__).resolve().parents[1] / "ca04_ca07_oneshot.py"
_SPEC = importlib.util.spec_from_file_location("ca04_ca07_oneshot", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
one_shot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(one_shot)


class Ca04Ca07OneShotTests(unittest.TestCase):
    def _terminal_transaction(self) -> dict:
        digest = "a" * 64
        return {
            "transaction_id": "transaction-123",
            "state": "COMPLETED",
            "plan_digest": digest,
            "target_identity": {
                "workspace_id": "fresh-workspace",
                "engineering_process_id": 101,
                "engineering_window_handle": 202,
                "project_path": r"C:\\workspace\\working.xgwx",
                "artifact_hash": "b" * 64,
            },
            "plan": {},
            "result": {
                "apply": {"apply": {"payload_sha256_before": "d" * 64, "payload_sha256_after": "f" * 64}},
                "verification": {
                    "save": {"project_hash": "e" * 64},
                    "validation": {"program_check": {"ok": True, "verified": True}},
                },
            },
        }

    def _fresh(self) -> dict:
        return {
            "ok": True, "verified": True,
            "operation": "project.fresh_reopen_ld_approval_readback",
            "approval_binding": {"approval_id": "transaction-123", "approval_plan_digest": "a" * 64, "result_digest": one_shot._canonical_digest(self._terminal_transaction()["result"])},
            "fresh_reopen": {"source_pid": 101, "source_hwnd": 202, "pid": 303, "hwnd": 404, "workspace_id": "fresh-workspace", "workspace_root": r"C:\\workspace-root", "project_path": r"C:\\workspace\\working.xgwx", "project_sha256": "e" * 64},
            "program_readback": {"program_id": one_shot.PROGRAM_ID, "program_name": one_shot.PROGRAM_NAME, "ld_payload_sha256": "f" * 64},
            "program_check": {"ok": True, "verified": True},
        }

    def test_contract_names_only_disposable_source_and_forbidden_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = one_shot.execution_contract(root / "backup.xgwx", root / "artifacts")
        self.assertTrue(contract["source"]["confirmed_disposable"])
        self.assertEqual(contract["source"]["sha256"], one_shot.BACKUP_SHA256)
        self.assertIn("afbf96ab-dca8-4a56-b7a5-894520da1b17", contract["forbidden_workspace_ids"])
        self.assertIn("d160dcac-643d-4087-b5aa-f119695e446f", contract["forbidden_workspace_ids"])

    def test_dry_run_writes_contract_without_host_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = root / "backup.xgwx"
            backup.write_bytes(b"reviewed backup")
            original = one_shot.BACKUP_SHA256
            try:
                one_shot.BACKUP_SHA256 = hashlib.sha256(backup.read_bytes()).hexdigest()
                code = one_shot.main(["--backup", str(backup), "--artifacts", str(root / "out")])
                self.assertEqual(code, 0)
                self.assertTrue((root / "out" / "execution-contract.json").is_file())
                self.assertTrue((root / "out" / "last-run.json").is_file())
            finally:
                one_shot.BACKUP_SHA256 = original

    def test_live_phase_requires_explicit_disposable_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = root / "backup.xgwx"
            backup.write_bytes(b"reviewed backup")
            original = one_shot.BACKUP_SHA256
            try:
                one_shot.BACKUP_SHA256 = hashlib.sha256(backup.read_bytes()).hexdigest()
                self.assertEqual(one_shot.main(["--backup", str(backup), "--prepare"]), 2)
            finally:
                one_shot.BACKUP_SHA256 = original

    def test_completed_ld_transaction_builds_strict_live_receipt(self) -> None:
        receipt = one_shot.build_live_receipt(self._terminal_transaction(), self._fresh(), "1" * 64)
        self.assertEqual(receipt["schema"], one_shot.LIVE_RECEIPT_SCHEMA)
        self.assertEqual(receipt["gate_id"], "CA-04-CA-07")
        self.assertEqual(receipt["evidence"]["program_check"], {"status": "PASS", "passed": True})
        self.assertEqual(receipt["evidence"]["readback"]["kind"], "LD")
        self.assertNotIn("before_mne_sha256", receipt["evidence"]["readback"])

    def test_receipt_rejects_nonfresh_reopen(self) -> None:
        transaction = self._terminal_transaction()
        fresh = self._fresh()
        fresh["fresh_reopen"]["pid"] = 101
        with self.assertRaisesRegex(one_shot.OrchestrationError, "PID/HWND"):
            one_shot.build_live_receipt(transaction, fresh, "1" * 64)

    def test_fresh_reopen_cli_is_bound_to_exact_ld_result_without_mne_arguments(self) -> None:
        transaction = self._terminal_transaction()
        binding = {
            "workspace": {
                "workspace_id": "fresh-workspace", "workspace_root": r"C:\\workspace-root",
                "working_path": r"C:\\workspace\\working.xgwx",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            cli = Path(temporary) / "controlstudio-xg5000.exe"
            cli.write_bytes(b"placeholder")
            response = {"ok": True, "result": self._fresh()}
            completed = SimpleNamespace(returncode=0, stdout=json.dumps(response))
            with patch.object(one_shot.subprocess, "run", return_value=completed) as run:
                result = one_shot.run_ld_fresh_reopen(cli, transaction, binding, timeout_seconds=120)
        self.assertEqual(result, self._fresh())
        argv = run.call_args.args[0]
        self.assertIn("fresh-reopen-ld-approval-readback", argv)
        self.assertIn("--expected-ld-payload-sha256", argv)
        self.assertIn("--approval-plan-digest", argv)
        self.assertIn("--workspace-root", argv)
        self.assertEqual(argv[argv.index("--workspace-root") + 1], r"C:\\workspace-root")
        self.assertNotIn("--expected-mne-payload-sha256", argv)

    def test_fresh_reopen_rejects_profile_workspace_root_divergence(self) -> None:
        fresh = self._fresh()
        binding = {"workspace": {"workspace_id": "fresh-workspace", "workspace_root": r"C:\\workspace-root"}}
        fresh["fresh_reopen"]["workspace_root"] = r"C:\\other-profile-root"
        with self.assertRaisesRegex(one_shot.OrchestrationError, "workspace root"):
            one_shot.build_live_receipt(
                self._terminal_transaction(), fresh, "1" * 64, binding
            )

    def test_pending_proposal_must_be_the_reviewed_ld_operand_plan(self) -> None:
        plan = {
            "target": {"programId": one_shot.PROGRAM_ID, "tokenOffset": one_shot.LD_VALUE_TOKEN_OFFSET},
            "current": {"value": "P0020"},
            "proposed": {"value": "P0021"},
        }
        one_shot._require_exact_ld_plan(plan)
        plan["proposed"] = {"value": "P0022"}
        with self.assertRaisesRegex(one_shot.OrchestrationError, "P0020-to-P0021"):
            one_shot._require_exact_ld_plan(plan)

    def test_typed_host_request_uses_offline_candidate_scope_and_evidence_derived_offset(self) -> None:
        client = one_shot.HostClient("http://example.invalid", "unused")
        calls: list[tuple[str, dict]] = []

        def request(path: str, **kwargs: object) -> dict:
            calls.append((path, kwargs.get("body", {})))
            if path == "/api/v1/conversations":
                return {"conversationId": "conversation-1"}
            return {"runId": "run-1"}

        client._request = request  # type: ignore[method-assign]
        client.start_exact_proposal()
        body = calls[-1][1]
        self.assertEqual(body["changeRequestScope"], "offline_candidate_revision")
        self.assertEqual(body["verificationScope"], "offline_native_reopen")
        self.assertEqual(body["offlineOperandMission"], one_shot.exact_ld_operand_mission_seed())
        self.assertNotIn("tokenOffset", body["offlineOperandMission"])

    def test_execution_binding_rejects_swapped_workspace_or_transaction(self) -> None:
        transaction = self._terminal_transaction()
        transaction["target_identity"].update({
            "revision_id": "revision-1",
            "source_artifact_hash": one_shot.BACKUP_SHA256,
        })
        transaction["plan"] = {
            "target": {"programId": one_shot.PROGRAM_ID, "tokenOffset": one_shot.LD_VALUE_TOKEN_OFFSET},
            "current": {"value": "P0020"},
            "proposed": {"value": "P0021"},
        }
        binding = {
            "schema": one_shot.BINDING_SCHEMA,
            "source": {"path": r"C:\\source.xgwx", "sha256": one_shot.BACKUP_SHA256},
            "workspace": {
                "workspace_id": "fresh-workspace", "revision_id": "revision-1",
                "workspace_root": r"C:\\workspace-root",
                "working_path": r"C:\\workspace\\working.xgwx", "working_sha256": "b" * 64,
            },
            "transaction_id": "transaction-123", "plan_digest": "a" * 64,
        }
        one_shot._validate_execution_binding(binding, transaction, "transaction-123", "a" * 64)
        transaction["target_identity"]["workspace_id"] = "other-workspace"
        with self.assertRaisesRegex(one_shot.OrchestrationError, "persisted disposable proposal binding"):
            one_shot._validate_execution_binding(binding, transaction, "transaction-123", "a" * 64)

    def test_prepare_rejects_missing_or_weak_read_ready_session(self) -> None:
        client = one_shot.HostClient("http://example.invalid", "unused")
        opened = {
            "workspace": {
                "workspaceId": "fresh-workspace", "revisionId": "revision-1",
                "source": {"path": r"C:\\source.xgwx", "sha256": one_shot.BACKUP_SHA256},
                "workingCopy": {
                    "path": r"C:\\workspace\\working.xgwx",
                    "baselineSha256": one_shot.BACKUP_SHA256,
                    "currentSha256": "a" * 64,
                },
            },
            "launch": {
                "verified": True, "workspaceId": "fresh-workspace", "windowDetected": True,
                "projectIdentityVerifiedAgainstSelectedPid": True,
                "projectIdentityReadOnlyBoundToSelectedPid": True,
                "identityAssurance": "strong_readback",
                "workingPath": r"C:\\workspace\\working.xgwx", "workingHash": "a" * 64,
                "instance": {"pid": 101, "hwnd": 202},
            },
            "session": {
                "sessionId": "session-1", "pid": 101, "hwnd": 202,
                "projectPath": r"C:\\workspace\\working.xgwx", "projectHash": "a" * 64,
                "metadata": {"project_identity_verified_against_selected_pid": True},
            },
        }
        client._request = lambda *_args, **_kwargs: opened  # type: ignore[method-assign]
        self.assertEqual(client.prepare_workspace(Path(r"C:\\source.xgwx")), opened)
        opened["session"] = None
        with self.assertRaisesRegex(one_shot.OrchestrationError, "read-ready"):
            client.prepare_workspace(Path(r"C:\\source.xgwx"))

    def test_approval_wait_requires_mission_terminal_and_unchanged_binding(self) -> None:
        transaction = self._terminal_transaction()
        transaction["target_identity"].update({
            "revision_id": "revision-1", "source_artifact_hash": one_shot.BACKUP_SHA256,
        })
        transaction["plan"] = {
            "target": {"programId": one_shot.PROGRAM_ID, "tokenOffset": one_shot.LD_VALUE_TOKEN_OFFSET},
            "current": {"value": "P0020"}, "proposed": {"value": "P0021"},
        }
        binding = {
            "schema": one_shot.BINDING_SCHEMA,
            "source": {"path": r"C:\\source.xgwx", "sha256": one_shot.BACKUP_SHA256},
            "workspace": {"workspace_id": "fresh-workspace", "revision_id": "revision-1", "workspace_root": r"C:\\workspace-root", "working_path": r"C:\\workspace\\working.xgwx", "working_sha256": "b" * 64},
            "transaction_id": "transaction-123", "plan_digest": "a" * 64,
        }
        client = one_shot.HostClient("http://example.invalid", "unused")
        states = iter(["active", "waiting_for_approval"])

        def request(path: str, **_kwargs: object) -> dict:
            if path.startswith("/api/v1/conversations/"):
                return {"engineeringMission": {
                    "snapshot": {"status": next(states)},
                    "changeTransactionId": "transaction-123", "updatedByRunId": "run-1",
                }}
            return transaction

        client._request = request  # type: ignore[method-assign]
        with patch.object(one_shot.time, "sleep") as sleep:
            client.wait_for_approval_wait("conversation-1", "run-1", binding, 5)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
