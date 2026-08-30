from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from controlstudio_host.api_models import (
    LocalWorkspaceV1,
    ReconcileChangeTransactionRequestV1,
)
from controlstudio_host.app import (
    _event_frame_v1,
    _relaunch_unavailable_change_recovery_target,
    _validated_verified_quarantined_backup_restore,
    create_app,
)
from controlstudio_host.change_execution import ChangeExecutionPreconditionError
from controlstudio_host.config import HostConfig
from controlstudio_host.mcp_client import MCPStatus, MCPToolError
from controlstudio_host.models import (
    ApprovalDecision,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
)
from controlstudio_host.store import HostStore


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


class _RestoreThenRelaunchFailureMCP:
    """MCP seam for the one Host route that must retain a completed restore."""

    def __init__(self, *, native_transaction_id: str, workspace_id: str) -> None:
        self.status = MCPStatus(status="ready")
        self.native_transaction_id = native_transaction_id
        self.workspace_id = workspace_id
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> MCPStatus:
        return self.status

    async def stop(self) -> None:
        return None

    def mne_capability_status(self) -> str:
        return "missing"

    def bind_agent_workspace(self, _workspace: dict[str, Any]) -> None:
        return None

    def clear_agent_session(self) -> None:
        return None

    def clear_controller_endpoint(self) -> None:
        return None

    @property
    def agent_session_identity_snapshot(self) -> None:
        return None

    async def call_recovery_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name == "change.transaction_lookup":
            return {
                "transaction_checkpoint": {
                    "transaction_id": self.native_transaction_id,
                    "plan_digest": "f" * 64,
                    "workspace_id": self.workspace_id,
                }
            }
        if name == "change.restore_verified_quarantined_backup":
            if arguments.get("expected_plan_digest") != "f" * 64:
                raise AssertionError("Host used its plan digest in the native restore call")
            return {
                "transaction_id": self.native_transaction_id,
                "host_transaction_id": arguments["host_transaction_id"],
                "plan_digest": "f" * 64,
                "workspace_id": self.workspace_id,
                "observed_current_project_hash": "d" * 64,
                "preserved_current_project_hash": "d" * 64,
                "verified_backup_project_hash": "b" * 64,
                "restored_project_hash": "b" * 64,
                "preservation_ref": "private-preservation:route-test",
                "preservation_digest": "e" * 64,
                "restored": True,
            }
        raise AssertionError(f"unexpected recovery tool: {name}")

    async def call_read_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name != "xg5000.instances":
            raise AssertionError(f"unexpected read tool: {name}")
        return {"instances": []}

    async def call_workspace_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name != "xg5000.open_project":
            raise AssertionError(f"unexpected workspace tool: {name}")
        raise MCPToolError("forced relaunch failure after durable restore")


def _route_plan() -> dict[str, Any]:
    return {
        "target": {"programId": "program:MAIN", "tokenOffset": 14},
        "current": {"value": "M0001"},
        "proposed": {"value": "M0002"},
        "rollback": ["Restore the verified transaction backup."],
    }


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

    def test_verified_backup_restore_requires_the_reviewed_current_hash_and_relaunch(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected_current_project_hash"):
            ReconcileChangeTransactionRequestV1(
                expected_plan_digest="c" * 64,
                pid=1234,
                hwnd=5678,
                restore_verified_backup=True,
                relaunch_if_unavailable=True,
            )
        with self.assertRaisesRegex(ValueError, "recovery-only XG5000 relaunch"):
            ReconcileChangeTransactionRequestV1(
                expected_plan_digest="c" * 64,
                pid=1234,
                hwnd=5678,
                restore_verified_backup=True,
                expected_current_project_hash="d" * 64,
            )

    def test_verified_backup_restore_receipt_is_bound_to_current_and_journal_hashes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovery-relaunch-") as raw:
            transaction = _transaction(_workspace(Path(raw)))
            receipt = _validated_verified_quarantined_backup_restore(
                {
                    "transaction_id": "native-transaction",
                    "host_transaction_id": transaction.id,
                    # Host approval and native journal digests bind different
                    # canonical plans and must never be conflated.
                    "plan_digest": "f" * 64,
                    "workspace_id": transaction.target.workspace_id,
                    "observed_current_project_hash": "d" * 64,
                    "preserved_current_project_hash": "d" * 64,
                    "verified_backup_project_hash": transaction.target.artifact_hash,
                    "restored_project_hash": transaction.target.artifact_hash,
                    "preservation_ref": "private-preservation:123",
                    "preservation_digest": "e" * 64,
                    "restored": True,
                },
                transaction=transaction,
                expected_current_project_hash="d" * 64,
                expected_native_transaction_id="native-transaction",
                expected_native_plan_digest="f" * 64,
            )
            self.assertTrue(receipt["restored"])
            receipt["restored_project_hash"] = "f" * 64
            with self.assertRaisesRegex(ChangeExecutionPreconditionError, "differs"):
                _validated_verified_quarantined_backup_restore(
                    receipt,
                    transaction=transaction,
                    expected_current_project_hash="d" * 64,
                    expected_native_transaction_id="native-transaction",
                    expected_native_plan_digest="f" * 64,
                )

    def test_restore_receipt_survives_a_relaunch_failure_and_replays_as_a_host_event(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovery-route-") as raw:
            root = Path(raw)
            workspace = _workspace(root)
            active_workspace = workspace.model_dump(mode="json")
            # The transaction artifact can be a reviewed modified revision,
            # not necessarily the workspace's original baseline.
            active_workspace["working_copy"]["baseline_sha256"] = "a" * 64
            active_workspace["working_copy"]["current_sha256"] = "d" * 64
            active_workspace["working_copy"]["state"] = "modified"
            config = HostConfig(
                database_path=root / "host.sqlite3",
                knowledge_database_path=root / "knowledge.sqlite3",
                static_dir=root / "missing-web-dist",
                launch_token="recovery-route-test-token-0123456789",
                release_channel="production",
            )
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", active_workspace)
            transaction, _created = store.create_change_transaction(
                operation_id="recovery-route-test",
                target=ChangeTargetIdentity(
                    workspace_id=workspace.workspace_id,
                    revision_id=workspace.revision_id,
                    artifact_hash="b" * 64,
                    source_artifact_hash="a" * 64,
                    project_path=workspace.working_copy.path,
                    vendor="ls",
                    engineering_system="XG5000",
                    engineering_session_id="journaled-session",
                    engineering_process_id=1234,
                    engineering_window_handle=5678,
                ),
                mutation_scope=f"workspace:{workspace.workspace_id}",
                risk_level=ChangeRiskLevel.HIGH,
                intent="Recover an interrupted offline change",
                plan=_route_plan(),
                plan_digest="c" * 64,
                requested_by_actor_id="test",
                approval_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            store.decide_change_transaction(
                transaction_id=transaction.id,
                decision=ApprovalDecision.APPROVE,
                expected_plan_digest=transaction.plan_digest,
                actor_id="test",
            )
            store.begin_change_execution(
                transaction_id=transaction.id,
                expected_plan_digest=transaction.plan_digest,
            )
            store.finish_change_execution(
                transaction_id=transaction.id,
                state=ChangeTransactionState.QUARANTINED,
                verification_evidence={"fixture": "quarantined"},
            )
            mcp = _RestoreThenRelaunchFailureMCP(
                native_transaction_id="native-recovery-route",
                workspace_id=workspace.workspace_id,
            )
            app = create_app(config, store=store, mcp=cast(Any, mcp))

            with TestClient(app, base_url="http://localhost:8765") as client:
                handoff = client.post(
                    "/api/launcher/handoff",
                    headers={"Authorization": f"Bearer {config.launch_token}"},
                )
                self.assertEqual(handoff.status_code, 201, handoff.text)
                session = client.post(
                    "/api/session",
                    headers={"Authorization": f"Bearer {handoff.json()['handoffTicket']}"},
                )
                self.assertEqual(session.status_code, 200, session.text)
                client.headers.update(
                    {"Authorization": f"Bearer {session.json()['sessionToken']}"}
                )
                bootstrap = client.get("/api/v1/bootstrap")
                self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
                response = client.post(
                    f"/api/v1/change-transactions/{transaction.id}/reconcile",
                    headers={"X-ControlStudio-CSRF": bootstrap.json()["csrfToken"]},
                    json={
                        "expectedPlanDigest": transaction.plan_digest,
                        "pid": 1234,
                        "hwnd": 5678,
                        "relaunchIfUnavailable": True,
                        "restoreVerifiedBackup": True,
                        "expectedCurrentProjectHash": "d" * 64,
                    },
                )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("forced relaunch failure", response.json()["detail"])

            refreshed_workspace = store.get_host_state("active_workspace")
            self.assertIsInstance(refreshed_workspace, dict)
            assert isinstance(refreshed_workspace, dict)
            self.assertEqual(refreshed_workspace["source"]["sha256"], "a" * 64)
            self.assertEqual(refreshed_workspace["working_copy"]["current_sha256"], "b" * 64)
            self.assertEqual(refreshed_workspace["working_copy"]["state"], "modified")
            lease = store.active_target_operation_lease()
            self.assertIsNotNone(lease)
            assert lease is not None
            self.assertEqual(lease["state"], "STALE_BLOCKED")
            self.assertEqual(lease["release_reason"], "recovery_target_identity_mismatch")
            restored_events = [
                event
                for event in store.list_events(limit=100)
                if event.event_type == "change.working_copy_restored"
            ]
            self.assertEqual(len(restored_events), 1)
            restored = restored_events[0]
            self.assertEqual(restored.payload["hostPlanDigest"], "c" * 64)
            self.assertEqual(restored.payload["nativePlanDigest"], "f" * 64)
            frame = _event_frame_v1(restored)
            payload = json.loads(frame.split("data: ", 1)[1])
            self.assertEqual(payload["eventType"], "change.working_copy_restored")
            self.assertEqual(
                payload["payload"]["restore"]["preservation_ref"],
                "private-preservation:route-test",
            )

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
