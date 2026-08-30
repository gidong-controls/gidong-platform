from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from controlstudio_host.api_models import (
    LocalWorkspaceV1,
    ReconcileChangeTransactionRequestV1,
)
from controlstudio_host.app import _relaunch_unavailable_change_recovery_target
from controlstudio_host.change_execution import ChangeExecutionPreconditionError
from controlstudio_host.mcp_client import MCPToolError
from controlstudio_host.models import (
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
)


def _workspace(root: Path) -> LocalWorkspaceV1:
    project = root / "working" / "machine.xgwx"
    source = root / "source" / "machine.xgwx"
    captured = root / "captured" / "machine.xgwx"
    for path in (project, source, captured):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"workspace")
    return LocalWorkspaceV1.model_validate(
        {
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "revision_id": "22222222-2222-4222-8222-222222222222",
            "source_kind": "local_file",
            "vendor": "ls",
            "engineering_system": "XG5000",
            "created_at": "2026-08-26T00:00:00+00:00",
            "source": {
                "original_path": str(source.resolve()),
                "captured_path": str(captured.resolve()),
                "sha256": "a" * 64,
                "original_state": "unchanged",
                "original_current_sha256": "a" * 64,
            },
            "working_copy": {
                "path": str(project.resolve()),
                "baseline_sha256": "b" * 64,
                "current_sha256": "b" * 64,
                "state": "clean",
            },
        }
    )


def _transaction(workspace: LocalWorkspaceV1) -> ChangeTransaction:
    now = datetime.now(timezone.utc)
    return ChangeTransaction(
        id="33333333-3333-4333-8333-333333333333",
        operation_id="recovery-relaunch-test",
        state=ChangeTransactionState.QUARANTINED,
        target=ChangeTargetIdentity(
            workspace_id=workspace.workspace_id,
            revision_id=workspace.revision_id,
            artifact_hash=workspace.working_copy.current_sha256,
            source_artifact_hash=workspace.source.sha256,
            project_path=workspace.working_copy.path,
            vendor="ls",
            engineering_system="XG5000",
            engineering_session_id="journaled-session",
            engineering_process_id=1234,
            engineering_window_handle=5678,
        ),
        mutation_scope=f"workspace:{workspace.workspace_id}",
        risk_level=ChangeRiskLevel.HIGH,
        intent="Recover the quarantined offline change",
        plan={},
        plan_digest="c" * 64,
        requested_by_actor_id="test",
        approval_expires_at=now,
        result={},
        created_at=now,
        updated_at=now,
    )


class _RecoveryRelaunchMCP:
    def __init__(self, workspace: LocalWorkspaceV1) -> None:
        self.workspace = workspace
        self.instances: list[dict[str, Any]] = []
        self.receipt: dict[str, Any] = {
            "ok": True,
            "operation": "xg5000.open_project",
            "verified": True,
            "workspace_id": workspace.workspace_id,
            "revision_id": workspace.revision_id,
            "working_path": workspace.working_copy.path,
            "working_hash": workspace.working_copy.current_sha256,
            "launch_pid": 4321,
            "window_detected": True,
            "project_identity_verified_against_selected_pid": False,
            "project_identity_read_only_bound_to_selected_pid": True,
            "identity_assurance": "controlled_launch_read_only",
            "instance": {"pid": 4321, "hwnd": 8765},
            "observed_instances": [{"pid": 4321, "hwnd": 8765}],
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.open_project_error: MCPToolError | None = None

    async def call_read_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name != "xg5000.instances":
            raise AssertionError(f"unexpected read tool: {name}")
        return {"instances": list(self.instances)}

    async def call_workspace_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name != "xg5000.open_project":
            raise AssertionError(f"unexpected workspace tool: {name}")
        if self.open_project_error is not None:
            raise self.open_project_error
        return dict(self.receipt)


class ChangeRecoveryRelaunchTests(unittest.IsolatedAsyncioTestCase):
    def test_relaunch_contract_defaults_to_disabled(self) -> None:
        default = ReconcileChangeTransactionRequestV1(
            expected_plan_digest="c" * 64,
            pid=1234,
            hwnd=5678,
        )
        enabled = ReconcileChangeTransactionRequestV1.model_validate(
            {
                "expectedPlanDigest": "c" * 64,
                "pid": 1234,
                "hwnd": 5678,
                "relaunchIfUnavailable": True,
            }
        )

        self.assertFalse(default.relaunch_if_unavailable)
        self.assertTrue(enabled.relaunch_if_unavailable)

    async def test_relaunches_only_absent_journaled_target_without_attach(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovery-relaunch-") as raw:
            workspace = _workspace(Path(raw))
            transaction = _transaction(workspace)
            mcp = _RecoveryRelaunchMCP(workspace)

            target = await _relaunch_unavailable_change_recovery_target(
                mcp,  # type: ignore[arg-type]
                transaction,
                workspace,
                requested_pid=1234,
                requested_hwnd=5678,
            )

            self.assertEqual((target.pid, target.hwnd), (4321, 8765))
            self.assertEqual(
                mcp.calls,
                [
                    ("xg5000.instances", {}),
                    (
                        "xg5000.open_project",
                        {
                            "workspace_id": workspace.workspace_id,
                            "expected_working_hash": workspace.working_copy.current_sha256,
                            "timeout_seconds": 120.0,
                        },
                    ),
                ],
            )
            self.assertNotIn("xg5000.attach", [name for name, _ in mcp.calls])

    async def test_relaunch_rejects_a_nonjournaled_request_before_inspection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovery-relaunch-") as raw:
            workspace = _workspace(Path(raw))
            mcp = _RecoveryRelaunchMCP(workspace)

            with self.assertRaisesRegex(ChangeExecutionPreconditionError, "journaled PID"):
                await _relaunch_unavailable_change_recovery_target(
                    mcp,  # type: ignore[arg-type]
                    _transaction(workspace),
                    workspace,
                    requested_pid=9999,
                    requested_hwnd=5678,
                )

            self.assertEqual(mcp.calls, [])

    async def test_relaunch_rejects_any_existing_xg_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovery-relaunch-") as raw:
            workspace = _workspace(Path(raw))
            mcp = _RecoveryRelaunchMCP(workspace)
            mcp.instances = [{"pid": 9000, "hwnd": 9001}]

            with self.assertRaisesRegex(ChangeExecutionPreconditionError, "different XG5000"):
                await _relaunch_unavailable_change_recovery_target(
                    mcp,  # type: ignore[arg-type]
                    _transaction(workspace),
                    workspace,
                    requested_pid=1234,
                    requested_hwnd=5678,
                )

            self.assertEqual(mcp.calls, [("xg5000.instances", {})])

    async def test_relaunch_rejects_an_incomplete_launch_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovery-relaunch-") as raw:
            workspace = _workspace(Path(raw))
            mcp = _RecoveryRelaunchMCP(workspace)
            mcp.receipt["working_hash"] = "d" * 64

            with self.assertRaisesRegex(ChangeExecutionPreconditionError, "receipt"):
                await _relaunch_unavailable_change_recovery_target(
                    mcp,  # type: ignore[arg-type]
                    _transaction(workspace),
                    workspace,
                    requested_pid=1234,
                    requested_hwnd=5678,
                )

            self.assertEqual(
                [name for name, _ in mcp.calls],
                ["xg5000.instances", "xg5000.open_project"],
            )

    async def test_relaunch_preserves_the_mcp_project_open_failure_detail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovery-relaunch-") as raw:
            workspace = _workspace(Path(raw))
            mcp = _RecoveryRelaunchMCP(workspace)
            failure = (
                "xg5000.open_project was rejected by MCP: "
                "XG5000 could not open the verified working copy"
            )
            mcp.open_project_error = MCPToolError(failure)

            with self.assertRaises(ChangeExecutionPreconditionError) as raised:
                await _relaunch_unavailable_change_recovery_target(
                    mcp,  # type: ignore[arg-type]
                    _transaction(workspace),
                    workspace,
                    requested_pid=1234,
                    requested_hwnd=5678,
                )

            self.assertEqual(str(raised.exception), failure)
            self.assertIs(raised.exception.__cause__, mcp.open_project_error)
            self.assertEqual(
                [name for name, _ in mcp.calls],
                ["xg5000.instances", "xg5000.open_project"],
            )

    async def test_relaunch_rejects_a_reused_journaled_pid_or_hwnd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovery-relaunch-") as raw:
            workspace = _workspace(Path(raw))
            mcp = _RecoveryRelaunchMCP(workspace)
            mcp.receipt["launch_pid"] = 1234
            mcp.receipt["instance"] = {"pid": 1234, "hwnd": 8765}
            mcp.receipt["observed_instances"] = [{"pid": 1234, "hwnd": 8765}]

            with self.assertRaisesRegex(ChangeExecutionPreconditionError, "receipt"):
                await _relaunch_unavailable_change_recovery_target(
                    mcp,  # type: ignore[arg-type]
                    _transaction(workspace),
                    workspace,
                    requested_pid=1234,
                    requested_hwnd=5678,
                )


if __name__ == "__main__":
    unittest.main()
