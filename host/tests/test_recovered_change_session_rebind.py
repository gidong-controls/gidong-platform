from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from controlstudio_host.app import (
    _rebind_reconciled_change_session,
    _recover_locked_reconciled_change_session,
    _revalidate_exact_engineering_session,
    create_app,
)
from controlstudio_host.config import HostConfig
from controlstudio_host.mcp_client import MCPGateway, MCPStatus
from controlstudio_host.models import (
    ApprovalDecision,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
)
from controlstudio_host.store import HostStore

_WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
_REVISION_ID = "22222222-2222-4222-8222-222222222222"
_SESSION_ID = "recovered-session-5cd56749"
_PID = 10144
_HWND = 11339552


class _SessionStatusMCP:
    def __init__(self, session: dict[str, Any]) -> None:
        self.session = session
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.session_missing = False
        self.resumed_session: dict[str, Any] | None = None

    async def call_recovery_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name != "xg5000.session_status":
            raise AssertionError(f"unexpected recovery tool: {name}")
        if self.session_missing:
            from controlstudio_host.mcp_client import MCPToolError

            raise MCPToolError("session not found")
        return dict(self.session)

    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.call_recovery_tool(name, arguments)

    async def call_workspace_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name != "xg5000.resume_terminal_change_session" or self.resumed_session is None:
            from controlstudio_host.mcp_client import MCPToolError

            raise MCPToolError(f"unexpected workspace tool: {name}")
        return dict(self.resumed_session)


class _StartupRebindMCP:
    def __init__(self, workspace: dict[str, Any], session: dict[str, Any]) -> None:
        self.workspace = workspace
        self.session = session
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.status = MCPStatus(
            status="ready",
            server_name="recovered-session-startup-fixture",
            server_version="1.0.0",
            protocol_version="2025-11-25",
            tool_count=2,
            agent_tool_count=0,
        )
        self._agent_identity: dict[str, Any] | None = None

    @property
    def agent_session_identity_snapshot(self) -> dict[str, Any] | None:
        return dict(self._agent_identity) if self._agent_identity is not None else None

    async def start(self) -> MCPStatus:
        return self.status

    async def stop(self) -> None:
        return None

    async def call_workspace_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name != "workspace.status" or arguments != {
            "workspace_id": _WORKSPACE_ID
        }:
            raise AssertionError(f"unexpected workspace call: {name} {arguments}")
        return {"ok": True, "verified": True, "operation": name, **self.workspace}

    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name != "xg5000.session_status" or arguments != {
            "session_id": _SESSION_ID
        }:
            raise AssertionError(f"unexpected read call: {name} {arguments}")
        return dict(self.session)

    async def call_recovery_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.call_read_tool(name, arguments)

    def bind_agent_workspace(self, workspace: dict[str, Any]) -> None:
        self._agent_identity = dict(workspace)

    def bind_agent_session(self, session: dict[str, Any]) -> None:
        self._agent_identity = dict(session)

    def clear_agent_session(self) -> None:
        self._agent_identity = None

    def clear_controller_endpoint(self) -> None:
        return None

    def mne_capability_status(self) -> str:
        return "missing"


class _UnboundTerminalRebindTransport:
    def __init__(self, resumed_session: dict[str, Any]) -> None:
        self.resumed_session = resumed_session
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, dict(arguments)))
        if name == "xg5000.session_status":
            return SimpleNamespace(
                is_error=True,
                structured_content=None,
                data=None,
                content=[SimpleNamespace(text="session not found: prior MCP memory was reset")],
            )
        if name == "xg5000.resume_terminal_change_session":
            return SimpleNamespace(
                is_error=False,
                structured_content={"status": "ok", "data": self.resumed_session},
                data=None,
                content=[],
            )
        raise AssertionError(f"unexpected raw MCP tool: {name}")


def _transaction(
    project_path: Path,
    project_hash: str,
    *,
    checkpoint_project_path: str | None = None,
    target_project_path: str | None = None,
) -> ChangeTransaction:
    now = datetime.now(timezone.utc)
    checkpoint = {
        "active_session_id": _SESSION_ID,
        "transaction_id": "mcp-terminal-transaction",
        "plan_digest": "b" * 64,
        "pid": _PID,
        "hwnd": _HWND,
        "project_path": checkpoint_project_path or str(project_path.resolve()),
        "current_project_hash": project_hash,
        "state": "ROLLED_BACK_SAVED",
        "terminal": True,
        "recovery_action": "already_terminal",
    }
    return ChangeTransaction(
        id="33333333-3333-4333-8333-333333333333",
        operation_id="recovered-change-session-rebind",
        state=ChangeTransactionState.ROLLED_BACK,
        target=ChangeTargetIdentity(
            workspace_id=_WORKSPACE_ID,
            revision_id=_REVISION_ID,
            artifact_hash=project_hash,
            source_artifact_hash="a" * 64,
            project_path=target_project_path or str(project_path.resolve()),
            vendor="ls",
            engineering_system="XG5000",
            engineering_session_id="quarantined-source-session",
            engineering_process_id=4001,
            engineering_window_handle=4002,
        ),
        mutation_scope=f"workspace:{_WORKSPACE_ID}",
        risk_level=ChangeRiskLevel.HIGH,
        intent="Recover a quarantined LD transaction",
        plan={},
        plan_digest="b" * 64,
        requested_by_actor_id="test",
        approval_expires_at=now,
        result={
            "reconciliation": {
                "final_status": {"transaction_checkpoint": checkpoint},
            }
        },
        created_at=now,
        updated_at=now,
    )


def _session(project_path: Path, project_hash: str, *, session_id: str = _SESSION_ID) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "pid": _PID,
        "hwnd": _HWND,
        "project_path": str(project_path.resolve()),
        "project_hash": project_hash,
        "metadata": {
            "project_identity_verified_against_selected_pid": True,
            "project_identity_read_only_bound_to_selected_pid": False,
            "project_identity_assurance": "strong_readback",
            "project_identity_proof": {
                "pid": _PID,
                "main_hwnd": _HWND,
                "project_path": str(project_path.resolve()),
                "project_hash": project_hash,
                "process_create_time": 123.5,
            },
        },
    }


def _workspace(project_path: Path, project_hash: str) -> dict[str, Any]:
    source = project_path.parent / "source.xgwx"
    captured = project_path.parent / "captured.xgwx"
    source.write_bytes(b"source")
    captured.write_bytes(b"captured")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "workspace_id": _WORKSPACE_ID,
        "revision_id": _REVISION_ID,
        "source_kind": "local_file",
        "vendor": "ls",
        "engineering_system": "XG5000",
        "created_at": "2026-08-31T00:00:00+00:00",
        "source": {
            "original_path": str(source.resolve()),
            "captured_path": str(captured.resolve()),
            "sha256": source_hash,
            "original_state": "unchanged",
            "original_current_sha256": source_hash,
        },
        "working_copy": {
            "path": str(project_path.resolve()),
            "baseline_sha256": project_hash,
            "current_sha256": project_hash,
            "state": "clean",
        },
    }


def _seed_acknowledged_terminal_change(
    store: HostStore,
    *,
    project_path: Path,
    project_hash: str,
) -> ChangeTransaction:
    target_path = str(project_path.resolve()).upper()
    checkpoint_path = str(project_path.resolve()).lower()
    target = ChangeTargetIdentity(
        workspace_id=_WORKSPACE_ID,
        revision_id=_REVISION_ID,
        artifact_hash=project_hash,
        source_artifact_hash="a" * 64,
        project_path=target_path,
        vendor="ls",
        engineering_system="XG5000",
        engineering_session_id="quarantined-source-session",
        engineering_process_id=4001,
        engineering_window_handle=4002,
    )
    transaction, created = store.create_change_transaction(
        operation_id="startup-terminal-rebind",
        target=target,
        mutation_scope=f"workspace:{_WORKSPACE_ID}",
        risk_level=ChangeRiskLevel.HIGH,
        intent="Recover one terminal offline LD transaction",
        plan={},
        plan_digest="b" * 64,
        requested_by_actor_id="test",
        approval_expires_at=datetime.now(timezone.utc).replace(year=2099),
    )
    if not created:
        raise AssertionError("terminal transaction fixture was not created")
    approved, _ = store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="test",
    )
    if approved is None:
        raise AssertionError("terminal transaction fixture was not approved")
    executing = store.begin_change_execution(
        transaction_id=transaction.id,
        expected_plan_digest=transaction.plan_digest,
    )
    if executing is None:
        raise AssertionError("terminal transaction fixture did not start")
    quarantined = store.finish_change_execution(
        transaction_id=transaction.id,
        state=ChangeTransactionState.QUARANTINED,
        verification_evidence={"fixture": "quarantined"},
    )
    if quarantined is None:
        raise AssertionError("terminal transaction fixture was not quarantined")
    resolved = store.resolve_quarantined_change(
        transaction_id=transaction.id,
        state=ChangeTransactionState.ROLLED_BACK,
        evidence={
            "final_status": {
                "transaction_checkpoint": {
                    "active_session_id": _SESSION_ID,
                    "pid": _PID,
                    "hwnd": _HWND,
                    "project_path": checkpoint_path,
                    "current_project_hash": project_hash,
                    "state": "ROLLED_BACK_SAVED",
                    "terminal": True,
                    "recovery_action": "already_terminal",
                }
            }
        },
        actor_id="test",
    )
    if resolved is None:
        raise AssertionError("terminal transaction fixture was not resolved")
    acknowledged, recorded = store.acknowledge_change_receipt(
        transaction_id=transaction.id,
        actor_id="test",
    )
    if acknowledged is None or not recorded:
        raise AssertionError("terminal transaction fixture was not acknowledged")
    return acknowledged


class RecoveredChangeSessionRebindTests(unittest.IsolatedAsyncioTestCase):
    async def test_locked_working_copy_uses_only_terminal_checkpoint_and_fresh_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovered-session-") as raw:
            project = Path(raw) / "working" / "machine.xgwx"
            project.parent.mkdir(parents=True)
            project.write_bytes(b"rolled back saved project")
            project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
            mcp = _SessionStatusMCP(_session(project, project_hash))

            recovered = await _recover_locked_reconciled_change_session(
                mcp,  # type: ignore[arg-type]
                _transaction(project, project_hash),
                expected_project_path=str(project.resolve()),
            )

            self.assertIsNotNone(recovered)
            assert recovered is not None
            recovered_hash, session = recovered
            self.assertEqual(recovered_hash, project_hash)
            self.assertEqual(session["session_id"], _SESSION_ID)
            self.assertEqual(
                mcp.calls,
                [("xg5000.session_status", {"session_id": _SESSION_ID})],
            )

    async def test_terminal_checkpoint_binds_only_the_fresh_exact_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovered-session-") as raw:
            project = Path(raw) / "working" / "machine.xgwx"
            project.parent.mkdir(parents=True)
            project.write_bytes(b"rolled back saved project")
            project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
            mcp = _SessionStatusMCP(_session(project, project_hash))

            rebound = await _rebind_reconciled_change_session(
                mcp,  # type: ignore[arg-type]
                _transaction(project, project_hash),
                expected_project_path=str(project.resolve()),
                expected_project_hash=project_hash,
            )

            self.assertIsNotNone(rebound)
            assert rebound is not None
            self.assertEqual(rebound["session_id"], _SESSION_ID)
            self.assertEqual(
                mcp.calls,
                [("xg5000.session_status", {"session_id": _SESSION_ID})],
            )

    async def test_terminal_checkpoint_path_case_difference_rebinds_exact_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovered-session-") as raw:
            project = Path(raw) / "working" / "machine.xgwx"
            project.parent.mkdir(parents=True)
            project.write_bytes(b"rolled back saved project")
            project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
            checkpoint_path = str(project.resolve()).lower()
            target_path = str(project.resolve()).upper()
            mcp = _SessionStatusMCP(_session(project, project_hash))

            rebound = await _rebind_reconciled_change_session(
                mcp,  # type: ignore[arg-type]
                _transaction(
                    project,
                    project_hash,
                    checkpoint_project_path=checkpoint_path,
                    target_project_path=target_path,
                ),
                expected_project_path=target_path,
                expected_project_hash=project_hash,
            )

            self.assertIsNotNone(rebound)
            self.assertEqual(
                mcp.calls,
                [("xg5000.session_status", {"session_id": _SESSION_ID})],
            )

    async def test_missing_mcp_session_resumes_only_exact_rolled_back_terminal_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovered-session-") as raw:
            project = Path(raw) / "working" / "machine.xgwx"
            project.parent.mkdir(parents=True)
            project.write_bytes(b"rolled back saved project")
            project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
            mcp = _SessionStatusMCP(_session(project, project_hash))
            mcp.session_missing = True
            mcp.resumed_session = _session(
                project,
                project_hash,
                session_id="new-terminal-rebind-session",
            )

            rebound = await _rebind_reconciled_change_session(
                mcp,  # type: ignore[arg-type]
                _transaction(project, project_hash),
                expected_project_path=str(project.resolve()),
                expected_project_hash=project_hash,
            )

            self.assertIsNotNone(rebound)
            self.assertEqual(
                mcp.calls,
                [
                    ("xg5000.session_status", {"session_id": _SESSION_ID}),
                    (
                        "xg5000.resume_terminal_change_session",
                        {
                            "pid": _PID,
                            "hwnd": _HWND,
                            "project_path": str(project.resolve()),
                            "project_hash": project_hash,
                            "workspace_id": _WORKSPACE_ID,
                            "terminal_transaction_id": "mcp-terminal-transaction",
                            "terminal_expected_plan_digest": "b" * 64,
                        },
                    ),
                ],
            )

    async def test_unbound_gateway_reaches_terminal_resume_after_session_memory_reset(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovered-session-") as raw:
            project = Path(raw) / "working" / "machine.xgwx"
            project.parent.mkdir(parents=True)
            project.write_bytes(b"rolled back saved project")
            project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
            resumed = _session(
                project,
                project_hash,
                session_id="new-terminal-rebind-session",
            )
            transport = _UnboundTerminalRebindTransport(resumed)
            gateway = MCPGateway()
            gateway._client = transport  # type: ignore[assignment]
            gateway._status = MCPStatus(status="ready")

            rebound = await _rebind_reconciled_change_session(
                gateway,
                _transaction(project, project_hash),
                expected_project_path=str(project.resolve()),
                expected_project_hash=project_hash,
            )

            self.assertIsNotNone(rebound)
            self.assertEqual(
                rebound["session_id"] if rebound is not None else None,
                "new-terminal-rebind-session",
            )
            self.assertIsNone(gateway.agent_session_identity_snapshot)
            self.assertEqual(
                transport.calls,
                [
                    ("xg5000.session_status", {"session_id": _SESSION_ID}),
                    (
                        "xg5000.resume_terminal_change_session",
                        {
                            "pid": _PID,
                            "hwnd": _HWND,
                            "project_path": str(project.resolve()),
                            "project_hash": project_hash,
                            "workspace_id": _WORKSPACE_ID,
                            "terminal_transaction_id": "mcp-terminal-transaction",
                            "terminal_expected_plan_digest": "b" * 64,
                        },
                    ),
                ],
            )

    async def test_session_status_drift_fails_closed_without_promoting_cached_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovered-session-") as raw:
            project = Path(raw) / "working" / "machine.xgwx"
            project.parent.mkdir(parents=True)
            project.write_bytes(b"rolled back saved project")
            project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
            mcp = _SessionStatusMCP(
                _session(project, project_hash, session_id="different-session")
            )

            rebound = await _rebind_reconciled_change_session(
                mcp,  # type: ignore[arg-type]
                _transaction(project, project_hash),
                expected_project_path=str(project.resolve()),
                expected_project_hash=project_hash,
            )
            reused = await _revalidate_exact_engineering_session(
                mcp,  # type: ignore[arg-type]
                session_id=_SESSION_ID,
                pid=_PID,
                hwnd=_HWND,
                project_path=str(project.resolve()),
                project_hash=project_hash,
            )

            self.assertIsNone(rebound)
            self.assertIsNone(reused)
            self.assertEqual(
                mcp.calls,
                [
                    ("xg5000.session_status", {"session_id": _SESSION_ID}),
                    ("xg5000.session_status", {"session_id": _SESSION_ID}),
                ],
            )

    async def test_lifetime_proof_for_another_process_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovered-session-") as raw:
            project = Path(raw) / "working" / "machine.xgwx"
            project.parent.mkdir(parents=True)
            project.write_bytes(b"rolled back saved project")
            project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
            stale_session = _session(project, project_hash)
            stale_session["metadata"]["project_identity_proof"]["pid"] = 99999
            mcp = _SessionStatusMCP(stale_session)

            rebound = await _rebind_reconciled_change_session(
                mcp,  # type: ignore[arg-type]
                _transaction(project, project_hash),
                expected_project_path=str(project.resolve()),
                expected_project_hash=project_hash,
            )

            self.assertIsNone(rebound)


class RecoveredChangeSessionStartupTests(unittest.TestCase):
    def test_startup_rebinds_the_newest_acknowledged_terminal_checkpoint_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-recovered-session-startup-") as raw:
            root = Path(raw)
            project = root / "working" / "machine.xgwx"
            project.parent.mkdir(parents=True)
            project.write_bytes(b"rolled back saved project")
            project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
            workspace = _workspace(project, project_hash)
            config = HostConfig(
                database_path=root / "host.sqlite3",
                knowledge_database_path=root / "knowledge.sqlite3",
                static_dir=root / "missing-web-dist",
                launch_token="recovered-session-startup-test-token",
            )
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            transaction = _seed_acknowledged_terminal_change(
                store,
                project_path=project,
                project_hash=project_hash,
            )
            mcp = _StartupRebindMCP(workspace, _session(project, project_hash))
            app = create_app(config, store=store, mcp=mcp)  # type: ignore[arg-type]

            with TestClient(app, base_url="http://localhost:8767"):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    bound = mcp.agent_session_identity_snapshot
                    if bound is not None and bound.get("session_id") == _SESSION_ID:
                        break
                    time.sleep(0.01)
                else:
                    self.fail("startup did not rebind the acknowledged terminal session")

            self.assertEqual(
                mcp.calls,
                [
                    ("workspace.status", {"workspace_id": _WORKSPACE_ID}),
                    ("xg5000.session_status", {"session_id": _SESSION_ID}),
                ],
            )
            persisted = store.get_change_transaction(transaction.id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertIsNotNone(persisted.receipt_acknowledged_at)


if __name__ == "__main__":
    unittest.main()
