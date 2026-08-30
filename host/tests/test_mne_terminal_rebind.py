from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from controlstudio_host.app import _rebind_terminal_mne_session
from controlstudio_host.change_execution import ChangeExecutionPreconditionError
from controlstudio_host.mne_change_execution import (
    MNE_PLAN_KIND,
    validated_terminal_mne_reopen,
)
from controlstudio_host.models import (
    ApprovalDecision,
    ChangeApproval,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
)

_WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
_SOURCE_PID = 4101
_SOURCE_HWND = 4201
_REOPENED_PID = 5101
_REOPENED_HWND = 5201


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _terminal_transaction(project_path: Path, project_hash: str) -> ChangeTransaction:
    now = datetime.now(timezone.utc)
    transaction_id = str(uuid.uuid4())
    host_plan_digest = "a" * 64
    native_plan_digest = "b" * 64
    target_mne_hash = "c" * 64
    application_digest = "d" * 64
    native_plan = {
        "plan_id": "native-mne-plan-1",
        "plan_digest": native_plan_digest,
        "program_id": "program:MAIN",
        "program_name": "MAIN",
        "target_mne": {"payload_sha256": target_mne_hash},
    }
    reopen = {
        "verified": True,
        "source_pid": _SOURCE_PID,
        "source_hwnd": _SOURCE_HWND,
        "pid": _REOPENED_PID,
        "hwnd": _REOPENED_HWND,
        "process_is_independent": True,
        "project_path": str(project_path.resolve()),
        "project_sha256": project_hash,
        "program_id": "program:MAIN",
        "program_name": "MAIN",
        "mdi_hwnd": 5301,
        "editor_hwnd": 5302,
        "view_hwnd": 5303,
        "payload_sha256": target_mne_hash,
        "ld_payload_sha256": "e" * 64,
        "ld_raw_equal": True,
        "ld_canonical_sha256": "f" * 64,
        "stable_readback": True,
    }
    verification_unsigned = {
        "schema": "xg5000.program-replacement-verification/1.0",
        "plan_digest": native_plan_digest,
        "application_digest": application_digest,
        "program_check_verified": True,
        "save_verified": True,
        "project_sha256_after": project_hash,
        "reopen": reopen,
        "verified": True,
    }
    verification = {
        **verification_unsigned,
        "verification_digest": _digest(verification_unsigned),
    }
    return ChangeTransaction(
        id=transaction_id,
        operation_id="terminal-mne-rebind-0001",
        state=ChangeTransactionState.COMPLETED,
        target=ChangeTargetIdentity(
            workspace_id=_WORKSPACE_ID,
            revision_id="22222222-2222-4222-8222-222222222222",
            artifact_hash="0" * 64,
            source_artifact_hash="0" * 64,
            project_path=str(project_path.resolve()),
            vendor="ls",
            engineering_system="XG5000",
            engineering_session_id="source-session",
            engineering_process_id=_SOURCE_PID,
            engineering_window_handle=_SOURCE_HWND,
        ),
        mutation_scope=f"workspace:{_WORKSPACE_ID}",
        risk_level=ChangeRiskLevel.HIGH,
        intent="Apply one reviewed MNE candidate",
        plan={"plan_kind": MNE_PLAN_KIND},
        plan_digest=host_plan_digest,
        requested_by_actor_id="browser:requester",
        approval_expires_at=now + timedelta(minutes=15),
        approval=ChangeApproval(
            transaction_id=transaction_id,
            decision=ApprovalDecision.APPROVE,
            plan_digest=host_plan_digest,
            actor_id="browser:approver",
            decided_at=now,
        ),
        result={
            "apply": {
                "host_transaction_id": transaction_id,
                "host_plan_digest": host_plan_digest,
                "plan_kind": MNE_PLAN_KIND,
                "mcp_plan_id": native_plan["plan_id"],
                "mcp_plan_digest": native_plan_digest,
                "plan": native_plan,
                "baseline": {},
                "application": {"application_digest": application_digest},
            },
            "verification": {"verification": verification},
        },
        created_at=now,
        updated_at=now,
        execution_started_at=now,
        completed_at=now,
    )


class _ReopenMCP:
    def __init__(self, project_path: Path, project_hash: str) -> None:
        self.project_path = project_path
        self.project_hash = project_hash
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        return {
            "session_id": "reopened-read-session",
            "pid": _REOPENED_PID,
            "hwnd": _REOPENED_HWND,
            "project_path": str(self.project_path.resolve()),
            "project_hash": self.project_hash,
            "metadata": {
                "project_identity_read_only_bound_to_selected_pid": True,
            },
        }


class MneTerminalRebindTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_change_rebinds_the_verified_reopen_for_fresh_reads(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-mne-rebind-") as raw:
            root = Path(raw)
            project_path = root / _WORKSPACE_ID / "working" / "machine.xgwx"
            project_path.parent.mkdir(parents=True)
            project_path.write_bytes(b"saved reviewed MNE project")
            project_hash = hashlib.sha256(project_path.read_bytes()).hexdigest()
            transaction = _terminal_transaction(project_path, project_hash)
            workspace = {"workspace_id": _WORKSPACE_ID}
            mcp = _ReopenMCP(project_path, project_hash)

            reopen = validated_terminal_mne_reopen(transaction)
            session = await _rebind_terminal_mne_session(
                mcp,  # type: ignore[arg-type]
                transaction,
                workspace,
                expected_project_path=project_path.resolve(),
                expected_project_hash=project_hash,
            )

            self.assertEqual(reopen["pid"], _REOPENED_PID)
            self.assertEqual(session["session_id"], "reopened-read-session")
            self.assertEqual(
                mcp.calls,
                [
                    (
                        "xg5000.attach",
                        {
                            "pid": _REOPENED_PID,
                            "hwnd": _REOPENED_HWND,
                            "access_profile": "analysis",
                            "project_path": str(project_path.resolve()),
                            "project_hash": project_hash,
                            "workspace_id": _WORKSPACE_ID,
                        },
                    )
                ],
            )

    async def test_rebind_refuses_a_receipt_for_another_saved_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-mne-rebind-") as raw:
            root = Path(raw)
            project_path = root / _WORKSPACE_ID / "working" / "machine.xgwx"
            project_path.parent.mkdir(parents=True)
            project_path.write_bytes(b"saved reviewed MNE project")
            project_hash = hashlib.sha256(project_path.read_bytes()).hexdigest()
            transaction = _terminal_transaction(project_path, "9" * 64)
            mcp = _ReopenMCP(project_path, project_hash)

            with self.assertRaisesRegex(
                ChangeExecutionPreconditionError,
                "differs from the refreshed working copy",
            ):
                await _rebind_terminal_mne_session(
                    mcp,  # type: ignore[arg-type]
                    transaction,
                    {"workspace_id": _WORKSPACE_ID},
                    expected_project_path=project_path.resolve(),
                    expected_project_hash=project_hash,
                )

            self.assertEqual(mcp.calls, [])

    async def test_rebind_refuses_a_tampered_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-mne-rebind-") as raw:
            root = Path(raw)
            project_path = root / _WORKSPACE_ID / "working" / "machine.xgwx"
            project_path.parent.mkdir(parents=True)
            project_path.write_bytes(b"saved reviewed MNE project")
            project_hash = hashlib.sha256(project_path.read_bytes()).hexdigest()
            transaction = _terminal_transaction(project_path, project_hash)
            transaction.result["verification"]["verification"]["reopen"]["pid"] = 9999

            with self.assertRaisesRegex(
                ChangeExecutionPreconditionError,
                "digest is invalid",
            ):
                validated_terminal_mne_reopen(transaction)


if __name__ == "__main__":
    unittest.main()
