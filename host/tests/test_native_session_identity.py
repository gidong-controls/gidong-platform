from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from controlstudio_host.api_models import LocalWorkspaceV1
from controlstudio_host.app import (
    _record_native_handoff_after_freshness,
    _workspace_cli_failure_detail,
    _workspace_resume_identity,
)
from controlstudio_host.native_session_identity import (
    _workspace_digest,
    fresh_native_trial_identity,
    native_trial_session_identity,
)


def _digest(value: dict[str, object]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _workspace(root: Path) -> LocalWorkspaceV1:
    project_hash = "a" * 64
    provenance = {
        "schema": "controlstudio-new-build-workspace-provenance/v1",
        "native_project_id": "11111111-1111-4111-8111-111111111111",
        "reviewed_plan_digest": "b" * 64,
        "build_bundle_id": "c" * 64,
        "build_receipt_digest": "d" * 64,
        "saved_project_sha256": project_hash,
    }
    provenance["receipt_digest"] = _digest(provenance)
    return LocalWorkspaceV1.model_validate(
        {
            "workspace_id": "22222222-2222-4222-8222-222222222222",
            "revision_id": "33333333-3333-4333-8333-333333333333",
            "source_kind": "new_build",
            "vendor": "ls",
            "engineering_system": "XG5000",
            "created_at": datetime(2026, 8, 16, tzinfo=timezone.utc),
            "source": {
                "original_path": str(root / "built.xgwx"),
                "captured_path": str(root / "captured.xgwx"),
                "sha256": project_hash,
                "original_state": "unchanged",
                "original_current_sha256": project_hash,
            },
            "working_copy": {
                "path": str(root / "working.xgwx"),
                "baseline_sha256": project_hash,
                "current_sha256": project_hash,
                "state": "clean",
            },
            "provenance": provenance,
        }
    )


def _session(workspace: LocalWorkspaceV1) -> dict[str, object]:
    path = str(Path(workspace.working_copy.path).resolve())
    project_hash = workspace.working_copy.current_sha256
    created = 1234.5
    proof = {
        "method": "same_rot_object_project_tree_and_path_v1",
        "same_active_object": True,
        "stable_repeat": True,
        "pid": 41,
        "main_hwnd": 42,
        "project_tree_hwnd": 43,
        "project_path": path,
        "project_hash": project_hash,
        "process_create_time": created,
        "version": "4.82.1",
    }
    resume = {
        "native_project_id": "11111111-1111-4111-8111-111111111111",
        "trial_id": "44444444-4444-4444-8444-444444444444",
        "receipt_digest": "e" * 64,
        "source_kind": "new_build",
        "provenance_receipt_digest": workspace.provenance.receipt_digest,
        "workspace_id": workspace.workspace_id,
        "revision_id": workspace.revision_id,
        "pid": 41,
        "main_hwnd": 42,
        "project_path": path,
        "project_hash": project_hash,
        "process_create_time": created,
        "authorization_id": "55555555-5555-4555-8555-555555555555",
    }
    return {
        "session_id": "session-1",
        "pid": 41,
        "hwnd": 42,
        "project_path": path,
        "project_hash": project_hash,
        "metadata": {
            "title": "working.xgwx - XG5000",
            "project_identity_source": "host_authorized_native_trial_resume",
            "project_identity_verified_against_selected_pid": True,
            "project_identity_read_only_bound_to_selected_pid": True,
            "project_identity_assurance": "controlled_launch_read_only",
            "project_identity_proof": proof,
            "workspace_identity": {
                "workspace_id": workspace.workspace_id,
                "project_path": os.path.normcase(path),
                "project_hash": project_hash,
                "source_sha256": workspace.source.sha256,
                "project_identity_digest": _workspace_digest(workspace),
            },
            "native_trial_resume": resume,
        },
    }


class NativeSessionIdentityTests(unittest.TestCase):
    def test_workspace_cli_failure_detail_is_bounded_and_rejects_raw_mcp_text(self) -> None:
        safe = Exception(
            "xg5000.open_project failed: workspace_cli_failure("
            "kind=WorkspaceError,message=the command-line project argument did not bind)"
        )
        self.assertEqual(
            _workspace_cli_failure_detail(safe),
            "the verified workspace could not be reopened in XG5000 before acceptance: "
            "WorkspaceError: the command-line project argument did not bind",
        )
        self.assertEqual(
            _workspace_cli_failure_detail(Exception("token=must-not-echo")),
            "the verified workspace could not be reopened in XG5000 before acceptance",
        )

    def test_reuse_rejects_actual_project_or_signed_workspace_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-session-identity-") as raw:
            workspace = _workspace(Path(raw))
            session = _session(workspace)
            self.assertIsNotNone(native_trial_session_identity(session, workspace))

            for path, value in (
                (("project_identity_proof", "project_hash"), "f" * 64),
                (("workspace_identity", "project_identity_digest"), "f" * 64),
                (("native_trial_resume", "project_path"), str(Path(raw) / "other.xgwx")),
            ):
                tampered = copy.deepcopy(session)
                tampered["metadata"][path[0]][path[1]] = value
                with self.subTest(path=path):
                    self.assertIsNone(native_trial_session_identity(tampered, workspace))

    def test_same_workspace_ids_do_not_hide_working_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-workspace-drift-") as raw:
            workspace = _workspace(Path(raw))
            fresh = workspace.model_copy(deep=True)
            fresh.working_copy.current_sha256 = "f" * 64
            fresh.working_copy.state = "modified"

            self.assertEqual(fresh.workspace_id, workspace.workspace_id)
            self.assertEqual(fresh.revision_id, workspace.revision_id)
            self.assertNotEqual(
                _workspace_resume_identity(fresh),
                _workspace_resume_identity(workspace),
            )


class NativeHandoffFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_workspace_drift_prevents_durable_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-handoff-freshness-") as raw:
            workspace = _workspace(Path(raw))
            session = _session(workspace)
            drifted = workspace.model_dump(mode="json")
            drifted["working_copy"]["current_sha256"] = "f" * 64
            drifted["working_copy"]["state"] = "modified"
            calls: list[str] = []

            class FakeMCP:
                async def call_workspace_tool(self, name, arguments):
                    calls.append(name)
                    return copy.deepcopy(drifted)

                async def call_read_tool(self, name, arguments):
                    calls.append(name)
                    return copy.deepcopy(session)

            writes = 0

            def write():
                nonlocal writes
                writes += 1
                return object(), True

            with self.assertRaisesRegex(ValueError, "identity drifted"):
                await _record_native_handoff_after_freshness(
                    fresh_native_trial_identity(FakeMCP(), workspace, session),
                    write,
                )

            self.assertEqual(calls, ["workspace.status", "xg5000.session_status"])
            self.assertEqual(writes, 0)


if __name__ == "__main__":
    unittest.main()
