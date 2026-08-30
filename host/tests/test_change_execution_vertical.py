from __future__ import annotations

import base64
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from controlstudio_host.change_execution import (
    ChangeExecutionFailedSafely,
    ChangeRecoveryTarget,
    MCPChangeExecutionService,
    validated_terminal_ld_reopen,
)
from controlstudio_host.mcp_client import MCPGateway, MCPToolRejectedError
from controlstudio_host.models import (
    ApprovalDecision,
    ChangeApproval,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
)

_WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
_REVISION_ID = "22222222-2222-4222-8222-222222222222"
_SESSION_ID = "session-verified"
_PID = 1234
_HWND = 5678
_PROJECT_PATH = r"C:\workspaces\working\machine.xgwx"
_PROJECT_HASH = "a" * 64
_SAVED_HASH = "e" * 64
_NATIVE_TRANSACTION_ID = "33333333-3333-4333-8333-333333333333"
_NATIVE_PLAN_ID = "native-plan-1"


def _target() -> ChangeTargetIdentity:
    return ChangeTargetIdentity(
        workspace_id=_WORKSPACE_ID,
        revision_id=_REVISION_ID,
        artifact_hash=_PROJECT_HASH,
        source_artifact_hash=_PROJECT_HASH,
        project_path=_PROJECT_PATH,
        vendor="ls",
        engineering_system="XG5000",
        engineering_session_id=_SESSION_ID,
        engineering_process_id=_PID,
        engineering_window_handle=_HWND,
    )


def _plan() -> dict:
    return {
        "target": {
            "programId": "program:MAIN",
            "programName": "MainProgram",
            "tokenOffset": 14,
            "analysisDigest": "1" * 64,
            "hardwareContextDigest": "2" * 64,
            "networkId": "network:MAIN:0",
            "networkDigest": "3" * 64,
        },
        "current": {"value": "M0001"},
        "proposed": {"value": "M0002"},
        "verification_scope": "offline_native_reopen",
        "validation": [
            "structural_diff",
            "xg5000_program_check",
            "project_save",
            "project_reopen",
        ],
        "manual_follow_up": ["Reopen and compare before field use."],
    }


def _transaction() -> ChangeTransaction:
    now = datetime.now(timezone.utc)
    transaction_id = str(uuid.uuid4())
    host_plan_digest = "f" * 64
    return ChangeTransaction(
        id=transaction_id,
        operation_id="change-execution-vertical-0001",
        state=ChangeTransactionState.EXECUTING,
        target=_target(),
        mutation_scope=f"workspace:{_WORKSPACE_ID}",
        risk_level=ChangeRiskLevel.HIGH,
        intent="Replace one reviewed offline operand",
        plan=_plan(),
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
        result={},
        created_at=now,
        updated_at=now,
        execution_started_at=now,
    )


class _RecordingGateway:
    def __init__(
        self,
        *,
        invalidate_diff: bool = False,
        change_plan_before_recovery: bool = False,
        invalidate_fresh_reopen: bool = False,
        fail_plan: bool = False,
    ) -> None:
        self.invalidate_diff = invalidate_diff
        self.change_plan_before_recovery = change_plan_before_recovery
        self.invalidate_fresh_reopen = invalidate_fresh_reopen
        self.fail_plan = fail_plan
        self.calls: list[tuple[str, dict]] = []
        self.signed_claims: list[dict] = []
        self.host_transaction_id: str | None = None
        self.state = "UNBOUND"
        self.current_project_hash = _PROJECT_HASH
        self.active_session_id = _SESSION_ID
        self.active_pid = _PID
        self.active_hwnd = _HWND
        self.recovery_lease_status = "live"
        secret = base64.urlsafe_b64encode(b"x" * 48).decode("ascii").rstrip("=")
        self.signer = MCPGateway(authorization_secret=secret)

    def sign_mutation_authorization(self, claims: dict) -> dict:
        self.signed_claims.append(dict(claims))
        return self.signer.sign_mutation_authorization(claims)

    def _native_plan(self) -> dict:
        target = _target()
        plan = {
            "plan_id": _NATIVE_PLAN_ID,
            "session_id": self.active_session_id,
            "transaction_id": _NATIVE_TRANSACTION_ID,
            "workspace_id": _WORKSPACE_ID,
            "project_identity_digest": (
                MCPChangeExecutionService._project_identity_digest(target)
            ),
            "snapshot_id": "snapshot-1",
            "backup_id": "backup-1",
            "program_id": "program:MAIN",
            "token_offset": 14,
            "analysis_digest": "1" * 64,
            "hardware_context_digest": "2" * 64,
            "network_id": "network:MAIN:0",
            "network_digest": "3" * 64,
            "expected": "M0001",
            "replacement": "M0002",
            "payload_sha256_before": "4" * 64,
            "payload_sha256_planned": "5" * 64,
            "project_hash": _PROJECT_HASH,
        }
        plan["plan_digest"] = MCPChangeExecutionService._mcp_plan_digest(plan)
        return plan

    def _checkpoint(self) -> dict:
        plan = self._native_plan()
        terminal = self.state in {
            "ABORTED_NO_MUTATION",
            "COMMITTED",
            "ROLLED_BACK_SAVED",
        }
        if terminal:
            recovery_action = "already_terminal"
        elif self.state in {"SNAPSHOT_CREATED", "BACKUP_CREATED", "PLANNED"}:
            recovery_action = "abort_safe"
        else:
            recovery_action = "rollback_required"
        plan_id = (
            "different-native-plan"
            if self.change_plan_before_recovery and self.state == "QUARANTINED"
            else _NATIVE_PLAN_ID
        )
        return {
            "schema": "controlstudio-change-transaction-checkpoint/v1",
            "host_transaction_id": self.host_transaction_id,
            "transaction_id": _NATIVE_TRANSACTION_ID,
            "snapshot_id": "snapshot-1",
            "backup_id": "backup-1",
            "plan_id": plan_id,
            "plan_digest": plan["plan_digest"],
            "workspace_id": _WORKSPACE_ID,
            "project_identity_digest": plan["project_identity_digest"],
            "active_session_id": self.active_session_id,
            "pid": self.active_pid,
            "hwnd": self.active_hwnd,
            "project_path": _PROJECT_PATH,
            "current_project_hash": self.current_project_hash,
            "state": self.state,
            "program_id": plan["program_id"],
            "token_offset": plan["token_offset"],
            "analysis_digest": plan["analysis_digest"],
            "hardware_context_digest": plan["hardware_context_digest"],
            "network_id": plan["network_id"],
            "network_digest": plan["network_digest"],
            "expected": plan["expected"],
            "replacement": plan["replacement"],
            "mutation_possible": recovery_action not in {"abort_safe", "already_terminal"},
            "recovery_action": recovery_action,
            "terminal": terminal,
            "lease_status": "released" if terminal else self.recovery_lease_status,
        }

    def _verified(self, operation: str) -> dict:
        return {
            "ok": True,
            "verified": True,
            "operation": operation,
            "session_id": self.active_session_id,
            "transaction_id": _NATIVE_TRANSACTION_ID,
            "transaction_state": self.state,
            "transaction_checkpoint": self._checkpoint(),
        }

    async def call_change_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, dict(arguments)))
        if name == "project.snapshot":
            self.host_transaction_id = str(arguments["host_transaction_id"])
            self.state = "SNAPSHOT_CREATED"
            return {
                "snapshot_id": "snapshot-1",
                "transaction_id": _NATIVE_TRANSACTION_ID,
                "programs": [
                    {"program_id": "program:MAIN", "payload_sha256": "4" * 64}
                ],
            }
        if name == "project.backup":
            self.state = "BACKUP_CREATED"
            return {"backup_id": "backup-1"}
        if name == "program.plan_change":
            if self.fail_plan:
                raise MCPToolRejectedError("native plan rejected before mutation")
            self.state = "PLANNED"
            return {"plan": self._native_plan()}
        if name == "program.apply_ld_patch":
            self.state = "APPLIED"
            return {"transaction_state": self.state}
        if name == "project.diff":
            if self.invalidate_diff:
                self.state = "QUARANTINED"
                raise MCPToolRejectedError("native diff rejected")
            self.state = "DIFF_VERIFIED"
            return {
                **self._verified(name),
                "plan_id": _NATIVE_PLAN_ID,
                "matches_plan": True,
                "matches_token_plan": True,
                "matches_native_plan": True,
                "project_unchanged": True,
            }
        if name == "project.validate_change":
            self.state = "CHECKED"
            return {
                **self._verified(name),
                "plan_id": _NATIVE_PLAN_ID,
                "matches_native_plan": True,
                "status": "validated",
                "rollback": None,
                "project_unchanged": True,
                "program_check": {"ok": True, "verified": True},
            }
        if name == "project.commit_save":
            rollback = self.state == "ROLLED_BACK_EDITOR"
            self.state = "ROLLED_BACK_SAVED" if rollback else "SAVED"
            self.current_project_hash = _PROJECT_HASH if rollback else _SAVED_HASH
            return {
                **self._verified(name),
                "project_hash": self.current_project_hash,
                "project_path": _PROJECT_PATH,
            }
        if name == "project.verify_ld_operand_reopen":
            self.state = "SAVED"
            return {
                "ok": True,
                "verified": True,
                "operation": name,
                "transaction_id": _NATIVE_TRANSACTION_ID,
                "project_unchanged": True,
                "approval_binding": {
                    "approval_id": arguments["approval_id"],
                    "approval_plan_digest": arguments["approval_plan_digest"],
                    "result_digest": arguments["result_digest"],
                },
                "fresh_reopen": {
                    "source_pid": _PID,
                    "source_hwnd": _HWND,
                    "source_closed": True,
                    "pid": 4321,
                    "hwnd": 8765,
                    "process_create_time": 123.5,
                    "workspace_id": _WORKSPACE_ID,
                    "project_path": _PROJECT_PATH,
                    "project_sha256": _SAVED_HASH,
                },
                "program_readback": {
                    "program_id": "program:MAIN",
                    "program_name": "MainProgram",
                    "ld_payload_sha256": (
                        "6" * 64 if self.invalidate_fresh_reopen else "5" * 64
                    ),
                    "stable_repeat": True,
                    "final_view": "LD",
                },
                "program_check": {"ok": True, "verified": True},
            }
        if name == "change.transaction_status":
            return {
                "ok": True,
                "verified": True,
                "operation": name,
                "transaction": {
                    "transaction_id": _NATIVE_TRANSACTION_ID,
                    "state": self.state,
                    "active_session_id": self.active_session_id,
                    "active_pid": self.active_pid,
                    "active_hwnd": self.active_hwnd,
                    "project_path": _PROJECT_PATH,
                    "current_project_hash": self.current_project_hash,
                },
                "transaction_checkpoint": self._checkpoint(),
                "events": [],
            }
        if name == "change.abort":
            if arguments["expected_transaction_state"] != self.state:
                raise MCPToolRejectedError("native state changed before abort")
            if self.state not in {"SNAPSHOT_CREATED", "BACKUP_CREATED", "PLANNED"}:
                raise MCPToolRejectedError("native transaction is not abortable")
            self.state = "ABORTED_NO_MUTATION"
            return {
                **self._verified(name),
                "state_after": self.state,
                "native_mutation_performed": False,
                "lease_released": True,
            }
        if name == "project.rollback_change":
            self.state = "ROLLED_BACK_EDITOR"
            return {
                **self._verified(name),
                "plan_id": _NATIVE_PLAN_ID,
                "status": "rolled_back",
                "matches_original_canonical": True,
                "matches_original_logic_and_nonvolatile": True,
            }
        if name == "change.commit":
            self.state = "COMMITTED"
            return {
                **self._verified(name),
                "project_hash": self.current_project_hash,
                "project_unchanged": True,
            }
        raise AssertionError(f"unexpected change tool: {name}")

    async def call_read_tool(self, name: str, arguments: dict) -> dict:
        if name != "xg5000.attach":
            raise AssertionError(f"unexpected read tool: {name}")
        self.assert_reopen_attach(arguments)
        self.state = "REOPENED"
        self.active_session_id = "session-reopened"
        self.active_pid = 4321
        self.active_hwnd = 8765
        return {
            "session_id": self.active_session_id,
            "pid": self.active_pid,
            "hwnd": self.active_hwnd,
            "stage": "SAVED_REOPENED_UNVERIFIED",
            "project_path": _PROJECT_PATH,
            "project_hash": _SAVED_HASH,
            "metadata": {"active_change_transaction_id": _NATIVE_TRANSACTION_ID},
        }

    async def call_recovery_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, dict(arguments)))
        if name == "change.transaction_lookup":
            if arguments.get("host_transaction_id") != self.host_transaction_id:
                raise AssertionError("recovery lookup did not bind the Host transaction")
            return {"transaction_checkpoint": self._checkpoint()}
        if name == "xg5000.attach":
            if self.recovery_lease_status != "abandoned":
                raise AssertionError("live native leases must not be reattached")
            if arguments.get("recover_transaction_id") != _NATIVE_TRANSACTION_ID:
                raise AssertionError("recovery attach did not bind the native transaction")
            return {
                "session_id": self.active_session_id,
                "pid": self.active_pid,
                "hwnd": self.active_hwnd,
                "project_path": _PROJECT_PATH,
                "project_hash": self.current_project_hash,
                "plc_policy": "offline_rollback_recovery_only",
                "recovery": self._checkpoint(),
            }
        raise AssertionError(f"unexpected recovery tool: {name}")

    def bind_agent_session(self, session: dict) -> None:
        if session["session_id"] != "session-reopened":
            raise AssertionError("fresh reopen did not become the active session")

    def assert_reopen_attach(self, arguments: dict) -> None:
        if (
            arguments["pid"] != 4321
            or arguments["hwnd"] != 8765
            or arguments["project_hash"] != _SAVED_HASH
            or arguments["reopen_from_session_id"] != _SESSION_ID
        ):
            raise AssertionError("fresh reopen attach identity differed from receipt")


class ChangeExecutionVerticalTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_failure_aborts_native_transaction_and_releases_lease(self) -> None:
        transaction = _transaction()
        gateway = _RecordingGateway(fail_plan=True)
        service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

        with self.assertRaises(ChangeExecutionFailedSafely):
            await service.apply(transaction)

        self.assertEqual(gateway.state, "ABORTED_NO_MUTATION")
        self.assertEqual(
            [name for name, _arguments in gateway.calls],
            [
                "project.snapshot",
                "project.backup",
                "program.plan_change",
                "change.transaction_status",
                "change.abort",
            ],
        )

    async def test_verified_change_reaches_terminal_commit(self) -> None:
        transaction = _transaction()
        gateway = _RecordingGateway()
        service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

        applied = await service.apply(transaction)
        verifying = transaction.model_copy(
            update={"state": ChangeTransactionState.VERIFYING}
        )
        result = await service.verify(verifying, applied)

        self.assertIs(result.state, ChangeTransactionState.COMPLETED)
        self.assertEqual(
            [name for name, _arguments in gateway.calls],
            [
                "project.snapshot",
                "project.backup",
                "program.plan_change",
                "program.apply_ld_patch",
                "project.diff",
                "project.validate_change",
                "project.commit_save",
                "change.transaction_status",
                "project.verify_ld_operand_reopen",
                "change.commit",
            ],
        )
        self.assertEqual(len(gateway.signed_claims), 1)
        self.assertNotIn("confirm", gateway.calls[3][1])
        terminal = transaction.model_copy(
            update={
                "state": ChangeTransactionState.COMPLETED,
                "result": {"verification": result.evidence},
            }
        )
        reopened = validated_terminal_ld_reopen(terminal)
        self.assertIsNotNone(reopened)
        self.assertEqual(reopened["session_id"], "session-reopened")

    async def test_presave_verification_failure_rolls_back_and_saves_baseline(self) -> None:
        transaction = _transaction()
        gateway = _RecordingGateway(invalidate_diff=True)
        service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

        applied = await service.apply(transaction)
        verifying = transaction.model_copy(
            update={"state": ChangeTransactionState.VERIFYING}
        )
        result = await service.verify(verifying, applied)

        self.assertIs(result.state, ChangeTransactionState.ROLLED_BACK)
        self.assertEqual(result.error_code, "verification_failed_rolled_back")
        self.assertEqual(gateway.state, "ROLLED_BACK_SAVED")
        self.assertEqual(
            [name for name, _arguments in gateway.calls][-3:],
            [
                "change.transaction_status",
                "project.rollback_change",
                "project.commit_save",
            ],
        )
        rollback_arguments = gateway.calls[-2][1]
        self.assertEqual(rollback_arguments["project_hash"], _PROJECT_HASH)
        self.assertEqual(rollback_arguments["transaction_id"], _NATIVE_TRANSACTION_ID)
        self.assertEqual(rollback_arguments["plan_id"], _NATIVE_PLAN_ID)

    async def test_recovery_refuses_a_different_native_plan(self) -> None:
        transaction = _transaction()
        gateway = _RecordingGateway(
            invalidate_diff=True,
            change_plan_before_recovery=True,
        )
        service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

        applied = await service.apply(transaction)
        verifying = transaction.model_copy(
            update={"state": ChangeTransactionState.VERIFYING}
        )
        result = await service.verify(verifying, applied)

        self.assertIs(result.state, ChangeTransactionState.QUARANTINED)
        self.assertEqual(result.error_code, "rollback_outcome_uncertain")
        self.assertNotIn(
            "project.rollback_change",
            [name for name, _arguments in gateway.calls],
        )

    async def test_failed_fresh_reopen_quarantines_without_stale_source_rollback(self) -> None:
        transaction = _transaction()
        gateway = _RecordingGateway(invalidate_fresh_reopen=True)
        service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

        applied = await service.apply(transaction)
        verifying = transaction.model_copy(
            update={"state": ChangeTransactionState.VERIFYING}
        )
        result = await service.verify(verifying, applied)

        self.assertIs(result.state, ChangeTransactionState.QUARANTINED)
        self.assertEqual(result.error_code, "fresh_reopen_outcome_uncertain")
        self.assertTrue(result.evidence["fresh_reopen_attempted"])
        self.assertNotIn(
            "project.rollback_change",
            [name for name, _arguments in gateway.calls],
        )

    async def test_reconcile_live_lease_uses_existing_bound_session(self) -> None:
        transaction = _transaction().model_copy(
            update={"state": ChangeTransactionState.QUARANTINED}
        )
        gateway = _RecordingGateway()
        gateway.host_transaction_id = transaction.id
        gateway.state = "QUARANTINED"
        service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

        with patch.object(
            MCPChangeExecutionService,
            "_hash_project_file",
            return_value=_PROJECT_HASH,
        ):
            result = await service.reconcile(
                transaction,
                ChangeRecoveryTarget(pid=_PID, hwnd=_HWND),
            )

        self.assertIs(result.state, ChangeTransactionState.ROLLED_BACK)
        self.assertEqual(
            result.evidence["recovery_attach"]["mode"], "existing_bound_session"
        )
        self.assertNotIn(
            "xg5000.attach", [name for name, _arguments in gateway.calls]
        )
        self.assertEqual(
            gateway.calls[1][0], "change.transaction_status"
        )

    async def test_reconcile_live_lease_mismatch_quarantines_without_attach(self) -> None:
        transaction = _transaction().model_copy(
            update={"state": ChangeTransactionState.QUARANTINED}
        )
        gateway = _RecordingGateway()
        gateway.host_transaction_id = transaction.id
        gateway.state = "QUARANTINED"
        gateway.active_pid = _PID + 1
        service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

        with patch.object(
            MCPChangeExecutionService,
            "_hash_project_file",
            return_value=_PROJECT_HASH,
        ):
            result = await service.reconcile(
                transaction,
                ChangeRecoveryTarget(pid=_PID, hwnd=_HWND),
            )

        self.assertIs(result.state, ChangeTransactionState.QUARANTINED)
        self.assertEqual(result.error_code, "native_status_unavailable")
        self.assertIn(
            "differs from its durable checkpoint",
            result.evidence["recovery_attach_or_status_error"],
        )
        self.assertEqual(
            [name for name, _arguments in gateway.calls],
            ["change.transaction_lookup"],
        )

    async def test_reconcile_abandoned_lease_requires_recovery_attach(self) -> None:
        transaction = _transaction().model_copy(
            update={"state": ChangeTransactionState.QUARANTINED}
        )
        gateway = _RecordingGateway()
        gateway.host_transaction_id = transaction.id
        gateway.state = "QUARANTINED"
        gateway.recovery_lease_status = "abandoned"
        service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

        with patch.object(
            MCPChangeExecutionService,
            "_hash_project_file",
            return_value=_PROJECT_HASH,
        ):
            result = await service.reconcile(
                transaction,
                ChangeRecoveryTarget(pid=_PID, hwnd=_HWND),
            )

        self.assertIs(result.state, ChangeTransactionState.ROLLED_BACK)
        self.assertNotEqual(
            result.evidence["recovery_attach"].get("mode"), "existing_bound_session"
        )
        self.assertEqual(
            [name for name, _arguments in gateway.calls][:3],
            [
                "change.transaction_lookup",
                "xg5000.attach",
                "change.transaction_status",
            ],
        )


if __name__ == "__main__":
    unittest.main()
